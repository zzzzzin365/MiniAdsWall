"""Run memory lifecycle checks on a private disposable MySQL server, never business data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from tests.test_memory_lifecycle import LifecycleTests
from hosting.repository import Repository
from hosting.objects import Objects

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mysqld',default='/opt/homebrew/opt/mysql@8.4/bin/mysqld')
    parser.add_argument('--output',default='docs/validation/agent-memory-experience/mysql-lifecycle.json')
    args=parser.parse_args();started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='memory-mysql-',dir='/tmp') as root:
        sock=root+'/mysql.sock';log=root+'/mysql.log'
        subprocess.run([args.mysqld,'--no-defaults','--initialize-insecure','--datadir='+root+'/db','--log-error='+log],check=True)
        server=subprocess.Popen([args.mysqld,'--no-defaults','--datadir='+root+'/db','--socket='+sock,
            '--skip-networking','--mysqlx=OFF','--pid-file='+root+'/mysql.pid','--log-error='+log,
            '--innodb-buffer-pool-size=128M'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        engine=create_engine('mysql+pymysql://root@localhost/mysql?unix_socket='+sock)
        try:
            deadline=time.monotonic()+40
            while True:
                try:
                    with engine.connect() as c:c.exec_driver_sql('SELECT 1')
                    break
                except Exception:
                    if time.monotonic()>deadline or server.poll() is not None:raise RuntimeError('isolated_mysql_start_failed')
                    time.sleep(.2)
            class MySQLMemoryTests(LifecycleTests):
                def setup_repo(self,**kwargs):
                    import uuid
                    self.database='memory_'+uuid.uuid4().hex
                    with engine.begin() as c:c.exec_driver_sql('CREATE DATABASE '+self.database+' CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
                    self.temp=tempfile.TemporaryDirectory(dir=root)
                    self.repo=Repository(engine.url.set(database=self.database).render_as_string(hide_password=False),Objects(self.temp.name),'s'*32,**kwargs)
                    self.repo.migrate();self.identity=self.repo.provision('alice');self.user=self.identity['user_id']
                    self.session=self.repo.create_session(self.user,self.identity['workspace_id'],'hello')
                def teardown_repo(self):
                    self.repo.engine.dispose();self.temp.cleanup()
                    with engine.begin() as c:c.exec_driver_sql('DROP DATABASE '+self.database)
            from tests.test_sync_memory_gateway import AliasTests
            class MySQLCompleteTests(MySQLMemoryTests,AliasTests):pass
            result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(MySQLCompleteTests))
            report={'database':'private MySQL, skip-networking, disposable databases','tests':result.testsRun,
                'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
                'elapsed_seconds':round(time.monotonic()-started,3),'scope':'repository lifecycle, extraction mock; not model semantic quality or load capacity'}
            Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            if not result.wasSuccessful():raise SystemExit(1)
        finally:
            engine.dispose();server.terminate()
            try:server.wait(timeout=20)
            except subprocess.TimeoutExpired:server.kill();server.wait()
if __name__=='__main__':main()
