"""Bounded model clients; OpenRouter uses its native Chat Completions API."""
import asyncio
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
from anthropic import AsyncAnthropic
from core.model_gate import get_model_gate

MODEL_TIMEOUT_SECONDS = 18.0


class ModelResponseError(RuntimeError):
    """The provider returned no usable answer (including HTTP 200 errors)."""


def client_options(api_key: str, base_url=None):
    options = {"api_key": api_key, "timeout": MODEL_TIMEOUT_SECONDS, "max_retries": 0}
    if base_url:
        options["base_url"] = base_url
        if urlparse(base_url).hostname == "openrouter.ai":
            options["default_headers"] = {"Authorization": f"Bearer {api_key}"}
    return options


class OpenRouterClient:
    """Adapter for the text-only messages.create calls used by this project."""

    def __init__(self, api_key, transport=None):
        self.api_key = api_key
        self.transport = transport
        self.messages = self

    async def create(self, *, model, messages, max_tokens, system=None,
                     temperature=0.2, timeout=MODEL_TIMEOUT_SECONDS):
        timeout = min(float(timeout), MODEL_TIMEOUT_SECONDS)
        outgoing = ([{"role": "system", "content": system}] if system else []) + messages
        payload = {"model": model, "messages": outgoing, "max_tokens": max_tokens,
                   "temperature": temperature, "reasoning": {"enabled": False}}
        # A wall-clock bound also covers slow trickles that reset socket timeouts.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
                response.raise_for_status()
                try:
                    data = response.json()
                    if data.get("error"):
                        raise ModelResponseError("模型服务返回错误")
                    choice = data["choices"][0]
                    text = choice["message"]["content"]
                    if choice.get("finish_reason") == "length":
                        raise ModelResponseError("模型输出被截断")
                    if not isinstance(text, str) or not text.strip():
                        raise ModelResponseError("模型未返回有效文本")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                    raise ModelResponseError("模型响应格式无效") from exc
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text.strip())])


class GovernedClient:
    def __init__(self, client):
        self.client = client
        self.messages = self

    async def close(self):
        if hasattr(self.client, "close"):
            await self.client.close()

    async def create(self, **kwargs):
        async with get_model_gate().slot():
            return await self.client.messages.create(**kwargs)


def create_model_client(api_key, base_url=None):
    if base_url and urlparse(base_url).hostname == "openrouter.ai":
        return GovernedClient(OpenRouterClient(api_key))
    return GovernedClient(AsyncAnthropic(**client_options(api_key, base_url)))
