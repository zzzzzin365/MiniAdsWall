"""Run isolated real-MySQL multiprocess checks; never uses an existing database.
Usage: .venv/bin/python scripts/verify_hosting_mysql.py [--mysqld PATH]
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func, text
from hosting import schema as s
from hosting.objects import Objects
from hosting.repository import Repository, LostLease

SECRET = 'mysql-verification-secret-at-least-32-characters'


def repository(url, root):
    return Repository(url, Objects(root), SECRET)


def contender(url, root, barrier, queue, action, args):
    repo = repository(url, root)
    try:
        with repo.engine.connect() as c:
            connection = c.execute(text('SELECT CONNECTION_ID()')).scalar()
        barrier.wait(timeout=30)
        if action == 'claim':
            value = repo.claim('process-' + str(os.getpid()))
        elif action == 'create':
            value = repo.create_run(*args)
        else:
            raise ValueError(action)
        queue.put({'pid': os.getpid(), 'connection': connection, 'value': value})
    except BaseException:
        queue.put({'error': traceback.format_exc()})
    finally:
        repo.engine.dispose()


def compete(url, root, action='claim', args=(), count=8):
    ctx = mp.get_context('spawn')
    barrier, queue = ctx.Barrier(count), ctx.Queue()
    children = [ctx.Process(target=contender, args=(url, root, barrier, queue, action, args)) for _ in range(count)]
    try:
        for child in children: child.start()
        results = [queue.get(timeout=60) for _ in children]
        for child in children:
            child.join(10)
            assert child.exitcode == 0, child.exitcode
        assert not any('error' in r for r in results), results
        assert len({r['pid'] for r in results}) == count
        assert len({r['connection'] for r in results}) == count
        return results
    finally:
        for child in children:
            if child.is_alive(): child.kill(); child.join()
        queue.close()


def crash_worker(url, root, queue):
    from hosting.worker import Worker
    repo = repository(url, root)
    async def pipeline(ctx):
        await ctx.stage('crash-test')
        repo.checkpoint(ctx.run['id'], ctx.run['fence_token'], {'step': 'before-crash'})
        queue.put({'pid': os.getpid(), 'run': ctx.run})
        await asyncio.sleep(120)
        return 'unexpected'
    asyncio.run(Worker(repo, pipeline).serve())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mysqld', default='/opt/homebrew/opt/mysql@8.4/bin/mysqld')
    parser.add_argument('--output', default='docs/hosting-mysql-validation.json')
    parser.add_argument('--verify-migrations', action='store_true')
    args = parser.parse_args()
    report = {'mysql': subprocess.check_output([args.mysqld, '--version'], text=True).strip(),
              'date': time.strftime('%Y-%m-%d'), 'cases': [],
              'scope': 'Local MySQL, independent OS processes, deterministic pipeline; no Docker/model/load-capacity validation.'}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='hosting-mysql-', dir='/tmp') as root:
        socket = root + '/mysql.sock'
        datadir = root + '/db'
        errorlog = root + '/mysql.log'
        subprocess.run([args.mysqld, '--no-defaults', '--initialize-insecure', '--datadir='+datadir, '--log-error='+errorlog], check=True)
        server = subprocess.Popen([args.mysqld, '--no-defaults', '--datadir='+datadir, '--socket='+socket,
                                   '--skip-networking', '--mysqlx=OFF', '--pid-file='+root+'/mysql.pid',
                                   '--log-error='+errorlog, '--innodb-buffer-pool-size=128M'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        repo = None
        try:
            import pymysql
            deadline = time.monotonic()+60
            while True:
                try:
                    conn = pymysql.connect(unix_socket=socket, user='root')
                    break
                except pymysql.Error:
                    if time.monotonic()>deadline or server.poll() is not None:
                        raise RuntimeError(Path(errorlog).read_text())
                    time.sleep(.2)
            with conn.cursor() as c: c.execute('CREATE DATABASE hosting_verify CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
            conn.close()
            url = 'mysql+pymysql://root@localhost/hosting_verify?unix_socket='+socket
            repo = repository(url, root+'/objects')
            if args.verify_migrations:
                subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_hosting_migrations.py','-v'],
                               env={**os.environ,'HOSTING_TEST_MYSQL_URL':url},check=True)
                report['migration_regressions_passed']=True
            def reset():
                # This database exists only inside the private disposable server above.
                s.metadata.drop_all(repo.engine)
                from hosting.migrations import metadata as migration_metadata
                migration_metadata.drop_all(repo.engine)
                repo.migrate()
            def setup(n=1, identity=None):
                identity = identity or repo.provision(uuid.uuid4().hex)
                rows=[]
                for i in range(n):
                    session=repo.create_session(identity['user_id'], identity['workspace_id'], str(i))
                    rows.append(repo.create_run(identity['user_id'],session['id'],uuid.uuid4().hex,{'message':'hello'}))
                return identity, rows
            def passed(name, **evidence):
                report['cases'].append({'name':name,'passed':True,**evidence})
                print(json.dumps(report['cases'][-1]), flush=True)
            reset(); _, rows=setup()
            results=compete(url,root+'/objects')
            claims=[r['value'] for r in results if r['value']]
            assert len(claims)==1 and claims[0]['id']==rows[0]['id']
            passed('single_run_claimed_once', processes=8, claims=1, pids=[r['pid'] for r in results])

            reset(); _, rows=setup(8)
            results=compete(url,root+'/objects')
            claims=[r['value'] for r in results if r['value']]
            assert len(claims)==2 and len({r['id'] for r in claims})==2
            passed('per_user_limit', processes=8, running=2, queued=6)

            reset()
            for _ in range(10): setup(2)
            results=compete(url,root+'/objects',count=20)
            claims=[r['value'] for r in results if r['value']]
            assert len(claims)==16 and len({r['id'] for r in claims})==16
            assert max(sum(x['user_id']==r['user_id'] for x in claims) for r in claims)<=2
            passed('global_limit', processes=20, users=10, running=16, queued=4)

            reset(); identity=repo.provision(uuid.uuid4().hex)
            session=repo.create_session(identity['user_id'],identity['workspace_id'],'idempotency')
            results=compete(url,root+'/objects','create',(identity['user_id'],session['id'],uuid.uuid4().hex,{'message':'hello'}))
            assert len({r['value']['id'] for r in results})==1
            with repo.engine.connect() as c:
                assert c.execute(select(func.count()).select_from(s.runs)).scalar()==1
                assert c.execute(select(func.count()).select_from(s.messages)).scalar()==1
            passed('concurrent_idempotent_creation', processes=8, runs=1, messages=1)

            reset(); identity, rows=setup(3)
            first=repo.claim('a'); second=repo.claim('b')
            repo.cancel(identity['user_id'],first['id'])
            assert repo.claim('c') is None
            repo.finish(first['id'],first['fence_token'],'succeeded','late result')
            assert repo.get_run(identity['user_id'],first['id'])['status']=='cancelled'
            assert repo.claim('c')['id']==rows[2]['id']
            passed('cancellation_holds_slot_until_finish', final_status='cancelled')

            reset(); identity, rows=setup()
            ctx=mp.get_context('spawn'); queue=ctx.Queue()
            child=ctx.Process(target=crash_worker,args=(url,root+'/objects',queue))
            child.start()
            try:
                info=queue.get(timeout=30); old=info['run']
                child.kill(); child.join(10)
                assert child.exitcode != 0
                killed=time.monotonic()
                from hosting.worker import Worker
                survivor=repository(url,root+'/objects')
                try:
                    worker=Worker(survivor,None)
                    while survivor.get_run(identity['user_id'],old['id'])['status']!='interrupted':
                        assert time.monotonic()-killed<35, 'recovery exceeded deadline'
                        asyncio.run(worker.maintain())
                        time.sleep(.2)
                    recovered=survivor.get_run(identity['user_id'],old['id'])
                    assert recovered['checkpoint_ref']
                    try:
                        survivor.event(old['id'],old['fence_token'],'assistant.delta',{'text':'stale'})
                    except LostLease: pass
                    else: raise AssertionError('stale worker write accepted')
                    resumed=survivor.create_run(identity['user_id'],old['session_id'],uuid.uuid4().hex,{'message':'continue'},resume=old['id'])
                    assert resumed['checkpoint_ref']==recovered['checkpoint_ref']
                    claimed=survivor.claim('survivor')
                    assert claimed['id']==resumed['id']
                    survivor.finish(claimed['id'],claimed['fence_token'],'succeeded','resumed')
                    passed('worker_sigkill_recovery_and_resume', killed_pid=info['pid'], signal=-child.exitcode,
                           recovery_seconds=round(time.monotonic()-killed,3), stale_write_rejected=True,
                           checkpoint_restored=True, resumed_status=survivor.get_run(identity['user_id'],claimed['id'])['status'])
                finally: survivor.engine.dispose()
            finally:
                if child.is_alive(): child.kill(); child.join()
                queue.close()
            report['passed']=True
        finally:
            if repo: repo.engine.dispose()
            server.terminate()
            try: server.wait(timeout=30)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('PASS: '+args.output,flush=True)

if __name__=='__main__': main()
