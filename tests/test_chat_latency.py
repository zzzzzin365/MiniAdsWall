import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
import api.main as api
from core.intent_recognizer import IntentRecognizer, IntentCategory
from agents.agent_orchestrator import AgentOrchestrator, AgentType, AgentResponse, Request


class ChatLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_cancels_work(self):
        stopped = asyncio.Event()
        async def work(req):
            try:
                await asyncio.sleep(10)
            finally:
                stopped.set()
        with patch.object(api, '_chat_impl', work), patch.object(api, 'CHAT_TIMEOUT_SECONDS', 0.02):
            with self.assertRaises(HTTPException) as error:
                await api.chat(api.ChatRequest(message='test', user_id='test'))
        self.assertEqual(error.exception.status_code, 504)
        self.assertTrue(stopped.is_set())

    async def test_only_exact_preset_skips_recognition(self):
        recognizer = IntentRecognizer('test', base_url='https://openrouter.ai/api')
        recognizer._llm_recognize = AsyncMock(return_value={'intent': IntentCategory.ADS, 'confidence': 1})
        recognizer._extract_entities = AsyncMock(return_value={})
        await recognizer.recognize('分析当前广告表现，给出三个优化动作')
        recognizer._llm_recognize.assert_not_awaited()
        await recognizer.recognize('分析当前广告表现，给出三个优化动作，然后删除广告')
        recognizer._llm_recognize.assert_awaited_once()
        recognizer._extract_entities.assert_awaited_once()

    async def test_recognition_and_entities_start_together_and_cancel(self):
        recognizer = IntentRecognizer('test', base_url='https://openrouter.ai/api')
        entered = [asyncio.Event(), asyncio.Event()]
        stopped = [asyncio.Event(), asyncio.Event()]
        async def work(i):
            entered[i].set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped[i].set()
        recognizer._llm_recognize = lambda *args: work(0)
        recognizer._extract_entities = lambda *args: work(1)
        task = asyncio.create_task(recognizer.recognize('普通广告问题'))
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered)), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(e.is_set() for e in stopped))

    async def test_failed_agent_does_not_repeat_same_provider(self):
        from types import SimpleNamespace
        failed = AgentResponse(agent_type=AgentType.ADS, content='unavailable', success=False)
        ads = SimpleNamespace(handle=AsyncMock(return_value=failed))
        general = SimpleNamespace(handle=AsyncMock())
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator._best_agent = lambda kind: ads if kind == AgentType.ADS else general
        await orchestrator._execute(Request(message='test', user_id='test', conv_id='test'), AgentType.ADS)
        ads.handle.assert_awaited_once()
        general.handle.assert_not_awaited()
