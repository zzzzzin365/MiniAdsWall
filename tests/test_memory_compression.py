import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import redis
from memory.conversation_memory import MemoryManager, Message, MsgRole

@unittest.skipUnless(shutil.which('redis-server'), 'redis-server required')
class CompressionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory()
        cls.socket=str(Path(cls.folder.name)/'redis.sock')
        cls.server=subprocess.Popen(['redis-server','--port','0','--unixsocket',cls.socket,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.redis=redis.Redis(unix_socket_path=cls.socket,decode_responses=True)
        for _ in range(100):
            try:
                cls.redis.ping();break
            except redis.RedisError:time.sleep(.01)
    @classmethod
    def tearDownClass(cls):
        cls.redis.close();cls.server.terminate();cls.server.wait(timeout=5);cls.folder.cleanup()
    async def asyncSetUp(self):
        self.redis.flushdb()
        self.manager=MemoryManager.__new__(MemoryManager)
        self.manager._redis=self.redis
        self.manager._model='mock'
        self.create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text='合并后的有界摘要')]))
        self.manager._client=SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.manager._episodic=SimpleNamespace(add=Mock())
        self.key=self.manager._wm_key('u','c')
        for i in range(30):
            m=Message(MsgRole.USER,'消息'+str(i))
            self.redis.lpush(self.key,json.dumps({'role':m.role.value,'content':m.content,'ts':m.timestamp.isoformat(),'metadata':{}}))
    async def test_archive_preserves_all_source_and_recent_order(self):
        await self.manager._compress('u','c')
        self.assertEqual(self.redis.llen(self.key),5)
        stored=self.manager._episodic.add.call_args.kwargs['metadatas'][0]['full_text']
        self.assertIn('消息0\n',stored);self.assertIn('消息24',stored)
        remaining=await self.manager._get_working_memory('u','c')
        self.assertEqual([m.content for m in remaining],['消息'+str(i) for i in range(25,30)])
    async def test_old_summary_is_merged_instead_of_appended(self):
        self.redis.set(self.manager._summary_key('u','c'),'旧摘要')
        await self.manager._compress('u','c')
        self.assertIn('旧摘要',self.create.call_args.kwargs['messages'][0]['content'])
        self.assertEqual(self.redis.get(self.manager._summary_key('u','c')),'合并后的有界摘要')
    async def test_over_budget_and_archive_failure_keep_messages(self):
        self.create.return_value=SimpleNamespace(content=[SimpleNamespace(text='长'*1000)])
        await self.manager._compress('u','c')
        self.assertEqual(self.redis.llen(self.key),30)
        self.create.return_value=SimpleNamespace(content=[SimpleNamespace(text='短摘要')])
        self.manager._episodic.add.side_effect=RuntimeError('offline')
        await self.manager._compress('u','c')
        self.assertEqual(self.redis.llen(self.key),30)
        self.assertIsNone(self.redis.get(self.manager._summary_key('u','c')))
    async def test_concurrent_new_message_is_never_deleted(self):
        async def generate(**kwargs):
            m=Message(MsgRole.USER,'并发消息')
            self.redis.lpush(self.key,json.dumps({'role':'user','content':m.content,'ts':m.timestamp.isoformat(),'metadata':{}}))
            return SimpleNamespace(content=[SimpleNamespace(text='摘要')])
        self.create.side_effect=generate
        await self.manager._compress('u','c')
        self.assertEqual(self.redis.llen(self.key),31)
        self.assertEqual(json.loads(self.redis.lindex(self.key,0))['content'],'并发消息')
