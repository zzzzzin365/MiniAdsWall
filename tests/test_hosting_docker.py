"""Opt-in isolation tests. Build hosting/Dockerfile.sandbox and set HOSTING_TEST_DOCKER=1."""
import asyncio
import json
import os
import tempfile
import unittest
from hosting.executor import Executor, Execution, command
from hosting.repository import Repository
from hosting.objects import Objects

@unittest.skipUnless(os.getenv('HOSTING_TEST_DOCKER')=='1','requires Docker and miniadswall-sandbox:1 image')
class DockerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.repo=Repository('sqlite://',Objects(self.temp.name),'sandbox-test-secret-32-characters',test=True); self.repo.migrate()
        self.user=self.repo.provision('sandbox')
        session=self.repo.create_session(self.user['user_id'],self.user['workspace_id'],'tools')
        self.repo.create_run(self.user['user_id'],session['id'],'sandbox-request-1234',{'message':'test'})
        self.r=self.repo.claim('w'); self.executor=Executor(self.repo)
    async def asyncTearDown(self):
        await self.executor.cleanup(self.r['id']); self.repo.engine.dispose(); self.temp.cleanup()
    async def invoke(self,script,timeout=3):
        argv=['python','-c',script]
        self.repo.tool_start(self.r['id'],self.r['fence_token'],'shell',{'argv':argv},timeout,f"agent-{self.r['id']}-{self.r['fence_token']}")
        return await self.executor.execute(Execution(run_id=self.r['id'],fence_token=self.r['fence_token'],argv=argv,timeout=timeout))
    async def test_readonly_root_and_no_network(self):
        result=await self.invoke("import os,socket; assert not os.path.exists('/var/run/docker.sock'); assert not os.access('/runner.py',os.W_OK); s=socket.socket(); s.settimeout(.2); assert s.connect_ex(('1.1.1.1',80)) != 0; print('isolated')")
        self.assertEqual(result['status'],'succeeded'); self.assertIn('isolated',result['output'])
    async def test_grandchild_timeout_and_cleanup(self):
        result=await self.invoke("import subprocess,time,signal; subprocess.Popen(['python','-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); time.sleep(60)",.2)
        self.assertEqual(result['status'],'timed_out')
        self.assertFalse((await command('ps','-aq','--filter',f"label=miniadswall.run={self.r['id']}")).strip())
    async def test_large_output_is_truncated_and_drained(self):
        result=await self.invoke("import sys; sys.stdout.write('x'*(12*1024*1024))")
        self.assertEqual(result['status'],'succeeded'); self.assertTrue(result['truncated']); self.assertEqual(result['output_bytes'],12*1024*1024)
    async def test_memory_limit_is_reported(self):
        result=await self.invoke("x=bytearray(900*1024*1024)")
        self.assertNotEqual(result['status'],'succeeded')
