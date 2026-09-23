"""Actual provider streams. No chunking a completed response into fake tokens."""
import asyncio
import json
import os
import httpx
from core.model_client import ModelResponseError
from core.model_gate import get_model_gate

async def stream_text(client, *, model, messages, system, max_tokens=1024, total=120, idle=30):
    """client is the existing GovernedClient. Release provider connection on cancellation."""
    from core.model_client import OpenRouterClient
    raw = client.client
    async with get_model_gate().slot():
        async with asyncio.timeout(total):
            seen = False
            if isinstance(raw, OpenRouterClient):
                payload = dict(model=model, messages=[{'role':'system','content':system}, *messages],
                               max_tokens=max_tokens, stream=True, reasoning={'enabled':False})
                timeout = httpx.Timeout(idle, connect=10)
                async with httpx.AsyncClient(timeout=timeout, transport=raw.transport) as http:
                    async with http.stream('POST', 'https://openrouter.ai/api/v1/chat/completions',
                                           headers={'Authorization':f'Bearer {raw.api_key}'}, json=payload) as response:
                        response.raise_for_status()
                        # Split with a bounded buffer; a malicious/malformed provider cannot send an unbounded line.
                        buf = b''; finished = False
                        async for chunk in response.aiter_bytes(chunk_size=1024):
                            buf += chunk
                            if len(buf)>65536: raise ModelResponseError('provider_event_too_large')
                            while b'\n' in buf:
                                line,buf=buf.split(b'\n',1); line=line.strip()
                                if not line.startswith(b'data:'): continue
                                data=line[5:].strip()
                                if data==b'[DONE]':
                                    finished=True; break
                                try:
                                    event=json.loads(data)
                                    if event.get('error'): raise ModelResponseError('provider_stream_error')
                                    choices=event.get('choices',[])
                                    if not choices: continue
                                    choice=choices[0]
                                    reason=choice.get('finish_reason')
                                    if reason in ('length','content_filter','error'): raise ModelResponseError('provider_incomplete_output')
                                    text=choice.get('delta',{}).get('content')
                                    if text is not None and not isinstance(text,str): raise ModelResponseError('provider_invalid_text')
                                    if text:
                                        seen=seen or bool(text.strip()); yield text
                                except (ValueError,TypeError,KeyError,AttributeError,IndexError) as exc:
                                    raise ModelResponseError('provider_invalid_event') from exc
                            if finished: break
                        if not finished: raise ModelResponseError('provider_stream_disconnected')
            else:
                async with raw.messages.stream(model=model,messages=messages,system=system,max_tokens=max_tokens,
                                               timeout=idle) as response:
                    iterator=response.text_stream.__aiter__()
                    while True:
                        try:
                            text=await asyncio.wait_for(iterator.__anext__(),idle)
                        except StopAsyncIteration: break
                        seen=seen or bool(text.strip()); yield text
                    message=await response.get_final_message()
                    if message.stop_reason not in ('end_turn','stop_sequence'):
                        raise ModelResponseError('provider_incomplete_output')
            if not seen: raise ModelResponseError('provider_empty_output')
