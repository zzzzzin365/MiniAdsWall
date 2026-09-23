"""Opt-in against a DEDICATED disposable MySQL database. Does not drop existing tables."""
import concurrent.futures
import os
import tempfile
import unittest
import uuid
from hosting.repository import Repository
from hosting.objects import Objects

@unittest.skipUnless(os.getenv('HOSTING_TEST_MYSQL_URL'),'requires dedicated MySQL integration database')
class MySQLConcurrencyTests(unittest.TestCase):
    def test_threads_cannot_overclaim(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Repository(os.environ['HOSTING_TEST_MYSQL_URL'],Objects(temp),'test-mysql-cursor-secret-32-characters')
            repo.migrate(); identity=repo.provision('integration-'+uuid.uuid4().hex)
            for i in range(8):
                session=repo.create_session(identity['user_id'],identity['workspace_id'],str(i))
                repo.create_run(identity['user_id'],session['id'],str(uuid.uuid4()),{'message':'hello'})
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                claimed=[r for r in pool.map(lambda i:repo.claim('worker-'+str(i)),range(8)) if r and r['user_id']==identity['user_id']]
            self.assertEqual(len(claimed),2)
            self.assertEqual(len({r['id'] for r in claimed}),2)
            for r in claimed: repo.finish(r['id'],r['fence_token'],'succeeded','test')
            repo.engine.dispose()
