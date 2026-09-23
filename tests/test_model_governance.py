import asyncio
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from redis.asyncio import Redis
from core.model_gate import RedisGate, LocalGate, ModelBusy, ADMIT
from core.faq_cache import FAQCache


class RedisTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='model-gate-', dir='/tmp')
        cls.socket = str(Path(cls.directory.name)/'redis.sock')
        cls.process = subprocess.Popen(['redis-server', '--port', '0', '--unixsocket', cls.socket,
                                        '--save', '', '--appendonly', 'no'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if Path(cls.socket).exists():
                return
            time.sleep(.02)
        raise RuntimeError('test Redis did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.redis = Redis(unix_socket_path=self.socket, decode_responses=True)
        await self.redis.flushdb()

    async def asyncTearDown(self):
        await self.redis.aclose()

    async def test_two_instances_share_capacity_and_fifo_queue(self):
        a = RedisGate(self.redis, limit=1, queue=2, wait=1, execution=2)
        b = RedisGate(self.redis, limit=1, queue=2, wait=1, execution=2)
        order = []
        async def work(gate, index):
            async with gate.slot():
                order.append(index)
                await asyncio.sleep(.01)
        async with a.slot():
            t1 = asyncio.create_task(work(b, 1))
            while await self.redis.zcard(a.keys[1]) != 1:
                await asyncio.sleep(.005)
            t2 = asyncio.create_task(work(a, 2))
            while await self.redis.zcard(a.keys[1]) != 2:
                await asyncio.sleep(.005)
            with self.assertRaisesRegex(ModelBusy, '队列已满'):
                async with b.slot():
                    self.fail('must not execute')
        await asyncio.gather(t1, t2)
        self.assertEqual(order, [1, 2])
        self.assertEqual(await self.redis.zcard(a.keys[0]), 0)
        self.assertEqual(await self.redis.zcard(a.keys[1]), 0)

    async def test_wait_timeout_and_cancellation_remove_tickets(self):
        gate = RedisGate(self.redis, limit=1, wait=.1, execution=1)
        async def work():
            async with gate.slot():
                self.fail('must stay queued')
        async with gate.slot():
            with self.assertRaises(ModelBusy):
                await work()
            self.assertEqual(await self.redis.zcard(gate.keys[1]), 0)
            task = asyncio.create_task(work())
            await asyncio.sleep(.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(await self.redis.zcard(gate.keys[1]), 0)

    async def test_orphan_lease_expires_and_execution_is_cancelled(self):
        gate = RedisGate(self.redis, limit=1, wait=.3, execution=.04, grace=.02)
        result = await self.redis.eval(ADMIT, 5, *gate.keys, 'crashed', 1, 32,
                                       gate.lease_ms, 'new', gate.fingerprint, 300)
        self.assertEqual(result, 1)
        async with gate.slot():  # lease left by a dead process is reclaimed
            self.assertIsNone(await self.redis.zscore(gate.keys[0], 'crashed'))
        stopped = asyncio.Event()
        with self.assertRaises(ModelBusy):
            async with gate.slot():
                try:
                    await asyncio.sleep(1)
                finally:
                    stopped.set()
        self.assertTrue(stopped.is_set())
        self.assertEqual(await self.redis.zcard(gate.keys[0]), 0)

    async def test_configuration_conflict_rejected(self):
        async with RedisGate(self.redis, limit=1).slot():
            with self.assertRaisesRegex(ModelBusy, '配置不一致'):
                async with RedisGate(self.redis, limit=2).slot():
                    self.fail('must not execute')

    async def test_faq_hit_scope_version_and_bypass(self):
        catalog = {'items': [{'id': 'ranking', 'question': '排序规则', 'aliases': ['排序规则', '如何排序'], 'knowledge': 'rule'}]}
        embed = AsyncMock(return_value=[1., 0.])
        generate = AsyncMock(return_value='grounded answer')
        cache = FAQCache(self.redis, catalog, embed)
        item = cache.eligible('排序规则')
        self.assertIsNone(cache.eligible('当前哪个广告最好'))
        self.assertIsNone(cache.eligible('排序规则，然后删除广告'))
        self.assertIsNone(cache.eligible('排序规则', conv_id='history'))
        self.assertIsNone(cache.eligible('排序规则', confirmation_id='confirm'))
        self.assertEqual(await cache.answer(item, '排序规则', 'user1:read', generate), ('grounded answer', False))
        self.assertEqual(await cache.answer(item, '如何排序', 'user1:read', generate), ('grounded answer', True))
        self.assertEqual(generate.await_count, 1)
        self.assertFalse((await cache.answer(item, '排序规则', 'user2:read', generate))[1])
        self.assertFalse((await cache.answer(item, '排序规则', 'user1:write', generate))[1])
        updated = FAQCache(self.redis, catalog, embed, version='new knowledge')
        self.assertFalse((await updated.answer(item, '排序规则', 'user1:read', generate))[1])
        embed.return_value = [0., 1.]
        self.assertFalse((await cache.answer(item, '如何排序', 'user1:read', generate))[1])

    async def test_chat_cache_hit_skips_model_and_never_receives_private_context(self):
        import api.main as api
        catalog = {'items': [{'id': 'ranking', 'question': '排序规则', 'aliases': ['排序规则', '如何排序'], 'knowledge': 'public formula'}]}
        cache = FAQCache(self.redis, catalog, AsyncMock(return_value=[1., 0.]))
        messages = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text='formula answer')])))
        client = SimpleNamespace(messages=messages, close=AsyncMock())
        memory = SimpleNamespace(add_message=AsyncMock(), get_context=AsyncMock())
        orchestrator = SimpleNamespace(preflight=AsyncMock())
        with patch.object(api, '_faq_cache', cache), patch.object(api, '_memory', memory), patch.object(api, '_orchestrator', orchestrator), patch.object(api, '_anthropic_cfg', return_value={'api_key': 'test', 'model': 'test'}), patch('core.model_client.create_model_client', return_value=client):
            first = await api.chat(api.ChatRequest(message='排序规则', user_id='user', ads=[{'secret': 'private-budget'}]))
            second = await api.chat(api.ChatRequest(message='如何排序', user_id='user'))
        self.assertEqual(first.decision_reason, 'faq_generated')
        self.assertEqual(second.decision_reason, 'faq_cache_hit')
        self.assertNotEqual(first.conv_id, second.conv_id)
        messages.create.assert_awaited_once()
        self.assertNotIn('private-budget', str(messages.create.call_args))
        memory.get_context.assert_not_awaited()
        orchestrator.preflight.assert_not_awaited()
        self.assertEqual(memory.add_message.await_count, 4)
        client.close.assert_awaited_once()

    async def test_cache_failure_degrades_and_failed_answers_not_cached(self):
        catalog = {'items': [{'id': 'one', 'aliases': ['q']}]}
        cache = FAQCache(self.redis, catalog, AsyncMock(side_effect=RuntimeError('embedding down')))
        generate = AsyncMock(return_value='answer')
        self.assertEqual(await cache.answer(catalog['items'][0], 'q', 'scope', generate), ('answer', False))
        cache.embed = AsyncMock(return_value=[1., 0.])
        generate.side_effect = RuntimeError('provider down')
        with self.assertRaises(RuntimeError):
            await cache.answer(catalog['items'][0], 'q', 'scope', generate)
        self.assertEqual(await self.redis.keys('faq:*'), [])


class LocalTests(unittest.IsolatedAsyncioTestCase):
    async def test_redis_failure_never_falls_back_to_execution(self):
        redis = AsyncMock()
        redis.eval.side_effect = RuntimeError('offline')
        with self.assertRaises(ModelBusy):
            async with RedisGate(redis).slot():
                self.fail('must not execute')

    async def test_model_factory_wrapper_enforces_admission_before_http(self):
        from core.model_client import GovernedClient
        gate = LocalGate(limit=1, queue=0)
        entered, release = asyncio.Event(), asyncio.Event()
        async def model_call(**kwargs):
            entered.set()
            await release.wait()
            return 'ok'
        raw = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=model_call)))
        client = GovernedClient(raw)
        with patch('core.model_client.get_model_gate', return_value=gate):
            first = asyncio.create_task(client.messages.create(model='test'))
            await entered.wait()
            with self.assertRaises(ModelBusy):
                await client.messages.create(model='test')
            release.set()
            self.assertEqual(await first, 'ok')
        raw.messages.create.assert_awaited_once()

    async def test_local_bounded_admission_and_cancel_cleanup(self):
        gate = LocalGate(limit=1, queue=0)
        async with gate.slot():
            with self.assertRaises(ModelBusy):
                async with gate.slot():
                    self.fail('must not execute')
        self.assertEqual(gate.admitted, 0)
