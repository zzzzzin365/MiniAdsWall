from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from sqlalchemy import create_engine, inspect, insert, select, update
from sqlalchemy.engine import make_url
from pydantic import ValidationError
from hosting import initial_schema as old, schema as s
from hosting.migrations import upgrade, status, definitions, ledger, attempts, MigrationError
from hosting.repository import Repository, Conflict
from hosting.objects import Objects
from hosting.reliability import load_reliability, FLAGS
from hosting.api import RunInput
from hosting.executor import Execution

class HostingMigrationTests(unittest.TestCase):
    mysql = False
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        if self.mysql:
            self.admin = create_engine(os.environ['HOSTING_TEST_MYSQL_URL'])
            self.database = 'hosting_migration_verify_' + uuid.uuid4().hex
            with self.admin.begin() as c: c.exec_driver_sql('CREATE DATABASE ' + self.database)
            self.engine = create_engine(make_url(os.environ['HOSTING_TEST_MYSQL_URL']).set(database=self.database))
        else: self.engine = create_engine('sqlite:///' + self.tmp.name + '/db.sqlite')
    def tearDown(self):
        self.engine.dispose()
        if self.mysql:
            with self.admin.begin() as c: c.exec_driver_sql('DROP DATABASE ' + self.database)
            self.admin.dispose()
        self.tmp.cleanup()
    def test_status_is_read_only_and_upgrade_is_repeatable(self):
        self.assertEqual([v['state'] for v in status(self.engine)], ['pending', 'pending'])
        self.assertEqual(inspect(self.engine).get_table_names(), [])
        first = upgrade(self.engine)
        second = upgrade(self.engine)
        self.assertEqual([v['state'] for v in second['migrations']], ['applied', 'applied'])
        with self.engine.connect() as c:
            rows = c.execute(select(attempts)).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual({v.batch_id for v in rows}, {first['batch_id']})
            self.assertEqual(c.execute(select(s.versions.c.version)).scalar_one(), 1)
    def test_parallel_migration_commands_serialize(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(upgrade,self.engine) for _ in range(2)]
            for future in futures: self.assertEqual(future.result()['migrations'][1]['state'],'applied')
        with self.engine.connect() as c: self.assertEqual(len(c.execute(select(attempts)).all()),2)
    def test_migration_lock_timeout_does_not_write_schema(self):
        if self.mysql:
            import hashlib
            from sqlalchemy import text
            with self.engine.connect() as c:
                database=c.execute(text('SELECT DATABASE()')).scalar_one()
                name='hosting:'+hashlib.sha256(database.encode()).hexdigest()[:50]
                self.assertEqual(c.execute(text('SELECT GET_LOCK(:name,0)'),{'name':name}).scalar_one(),1)
                try:
                    with self.assertRaisesRegex(MigrationError,'lock_timeout'): upgrade(self.engine,lock_timeout=0)
                finally: c.execute(text('SELECT RELEASE_LOCK(:name)'),{'name':name})
        else:
            import fcntl
            with open(self.engine.url.database+'.migration-lock','a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                try:
                    with self.assertRaisesRegex(MigrationError,'lock_timeout'): upgrade(self.engine,lock_timeout=0)
                finally: fcntl.flock(lock,fcntl.LOCK_UN)
        self.assertEqual(inspect(self.engine).get_table_names(),[])
    def test_legacy_database_adoption_preserves_input_and_fingerprint(self):
        # Simulate the old create_all path: no new migration ledger or protocol columns.
        old.metadata.create_all(self.engine)
        objects = Objects(self.tmp.name + '/objects')
        original = '{"message":"legacy","ads":[{"id":"original"}]}'
        ref = objects.put(original)
        now = time.time()
        with self.engine.begin() as c:
            c.execute(insert(old.capacity).values(scope='global',last_dispatch_at=0))
            c.execute(insert(old.versions).values(version=1,created_at=now))
            c.execute(insert(old.users).values(id=1,auth_subject='legacy',status='active',created_at=now))
            c.execute(insert(old.workspaces).values(id=2,owner_id=1,name='legacy',version=1,created_at=now))
            c.execute(insert(old.members).values(workspace_id=2,user_id=1,role='owner'))
            c.execute(insert(old.sessions).values(id=3,user_id=1,workspace_id=2,title='legacy',status='active',active_run_id=4,next_seq=0,version=1,updated_at=now,created_at=now))
            c.execute(insert(old.runs).values(id=4,user_id=1,workspace_id=2,session_id=3,status='queued',idempotency_key='legacy-key-123456',request_hash='a'*64,input_ref=ref,fence_token=0,queue_deadline=now+300,next_dispatch_at=now,workspace_version=1,event_seq=0,output_bytes=0,created_at=now))
        upgrade(self.engine)
        repo = Repository(self.engine.url.render_as_string(hide_password=False),objects,'k'*32,test=True)
        try:
            r = repo.get_run(1,4)
            self.assertEqual(r['request_hash'], 'a'*64)
            self.assertEqual(objects.get(r['input_ref']).decode(), original)
            self.assertEqual(repo.claim('compatible-worker')['id'], 4)
        finally: repo.engine.dispose()
    def test_partial_ddl_failure_is_resumable_and_attempts_are_retained(self):
        upgrade(self.engine,target=1)
        def fail(version,index):
            if version==2 and index==0: raise RuntimeError('test secret must not be recorded')
        with self.assertRaises(RuntimeError): upgrade(self.engine,after_statement=fail)
        result = status(self.engine)
        self.assertEqual(result[1]['state'],'failed')
        self.assertEqual(result[1]['error_code'],'RuntimeError')
        self.assertIn('input_protocol_version',{c['name'] for c in inspect(self.engine).get_columns('agent_runs')})
        # Abrupt process death would leave applying instead of failed: same resume path.
        with self.engine.begin() as c: c.execute(update(ledger).where(ledger.c.version==2).values(state='applying'))
        self.assertEqual(upgrade(self.engine)['migrations'][1]['state'],'applied')
        with self.engine.connect() as c: self.assertEqual(len(c.execute(select(attempts).where(attempts.c.version==2)).all()),2)
    def test_checksum_mismatch_blocks_before_new_ddl(self):
        upgrade(self.engine,target=1)
        changed = definitions(); changed[0] = replace(changed[0],checksum='0'*64)
        with self.assertRaisesRegex(MigrationError,'checksum_mismatch'): upgrade(self.engine,migrations=changed)
        self.assertNotIn('input_protocol_version',{c['name'] for c in inspect(self.engine).get_columns('agent_runs')})
    def test_future_schema_and_downgrade_are_rejected(self):
        upgrade(self.engine)
        with self.assertRaisesRegex(MigrationError,'downgrade'): upgrade(self.engine,target=1)
        with self.engine.begin() as c: c.execute(insert(s.versions).values(version=99,created_at=time.time()))
        with self.assertRaisesRegex(MigrationError,'unsupported_schema'): upgrade(self.engine)
    def test_unknown_input_is_not_created_or_claimed_and_legacy_retry_matches(self):
        upgrade(self.engine)
        repo = Repository(self.engine.url.render_as_string(hide_password=False),Objects(self.tmp.name+'/objects'),'k'*32,test=True)
        try:
            identity=repo.provision('alice'); session=repo.create_session(identity['user_id'],identity['workspace_id'],'test')
            args=(identity['user_id'],session['id'],'idempotent-key-1234')
            r=repo.create_run(*args,{'message':'same'})
            self.assertEqual(repo.create_run(*args,{'message':'same','input_protocol_version':1})['id'],r['id'])
            self.assertEqual(repo.create_run(*args,{'message':'same','input_protocol_version':1,'ad_context':None,'conditions':[]})['id'],r['id'])
            with self.assertRaises(Conflict): repo.create_run(*args,{'message':'same','input_protocol_version':2})
            with self.engine.begin() as c: c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(input_protocol_version=99))
            self.assertIsNone(repo.claim('old-reader'))
            with self.engine.begin() as c: c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(input_protocol_version=None))
            self.assertEqual(repo.claim('legacy-reader')['id'],r['id'])
        finally: repo.engine.dispose()

@unittest.skipUnless(os.getenv('HOSTING_TEST_MYSQL_URL'),'requires isolated real MySQL')
class MySQLMigrationTests(HostingMigrationTests):
    mysql = True

class ReleaseGuardTests(unittest.TestCase):
    def test_defaults_and_no_premature_enablement(self):
        config=load_reliability({})
        self.assertEqual(config.deployment_mode,'development')
        self.assertFalse(any(config.features.values()))
        for flag in FLAGS:
            if flag=='AD_CONTEXT_V1_ENABLED': continue  # compatible reader supplied by the recall integration
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError,'not_implemented'): load_reliability({flag:'true'})
        self.assertTrue(load_reliability({'AD_CONTEXT_V1_ENABLED':'true'}).features['AD_CONTEXT_V1_ENABLED'])
        for env in [{'BACKEND_DEPLOYMENT_MODE':'multi_node'},{'BACKEND_DEPLOYMENT_MODE':'typo'},
                    {'HOSTING_INPUT_PROTOCOL_VERSION':'2'},{'HOSTING_EXECUTOR_PROTOCOL_VERSION':'2'},
                    {'SSE_SHARED_READER_ENABLED':'maybe'}]:
            with self.subTest(env=env), self.assertRaises(ValueError): load_reliability(env)
    def test_wire_protocols_reject_unknown_versions(self):
        self.assertEqual(RunInput(message='legacy').input_protocol_version,1)
        for value in [2,True,'1']:
            with self.subTest(value=value), self.assertRaises(ValidationError): RunInput(message='unknown',input_protocol_version=value)
            with self.assertRaises(ValidationError): Execution(run_id=1,fence_token=1,argv=['true'],timeout=1,executor_protocol_version=value)
        with self.assertRaises(ValidationError): RunInput(message='unknown',ad_context={'version':1})
