"""Creative/strategy model calls belong to the Python service."""
from core.model_gate import get_model_gate, ModelBusy
import json
import os
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

router = APIRouter(prefix="/generate")


class GenerationInput(BaseModel):
    adDescription: str = Field(min_length=1, max_length=10000)
    industry: str = Field(min_length=1, max_length=200)
    tone: str = "neutral"


class Creative(BaseModel):
    titles: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=3)
    scripts: list[str] = Field(min_length=1, max_length=3)
    keywords: list[str] = Field(min_length=1, max_length=5)


class Strategy(BaseModel):
    targetUsers: list[str] = Field(min_length=1, max_length=5)
    bidSuggestion: float | None = Field(default=None, ge=0)
    recommendedObjective: str = Field(default="需明确推广目标", max_length=500)
    interests: list[str] = Field(min_length=1, max_length=5)
    reason: str = Field(min_length=1)


async def generate(kind: str, body: GenerationInput, request_id: str):
    from core.model_client import business_model_config
    qwen = business_model_config()
    if kind == 'strategy':
        qwen = {'api_key': os.getenv('ARK_API_KEY', ''), 'base_url': os.getenv('ARK_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/'), 'model': 'deepseek-v4-1-flash-260910', 'provider': 'seed'}
    key = qwen["api_key"] if qwen else os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise HTTPException(503, "ARK_API_KEY、DASHSCOPE_API_KEY 或 OPENROUTER_API_KEY 未配置")
    schema = Creative if kind == "creative" else Strategy
    instruction = (
        '生成广告创意，输出 titles、texts、scripts 各 3 条，keywords 5 条。'
        if kind == "creative" else
        '生成投放建议，输出 recommendedObjective、targetUsers、bidSuggestion、interests、reason。不要生成标题、文案或素材。没有成本、预算、渠道或转化依据时bidSuggestion必须为null；不要编造出价。建议不代表已执行。'
    )
    example = (
        {"titles": ["广告标题"], "texts": ["广告正文"], "scripts": ["视频脚本"], "keywords": ["关键词"]}
        if kind == "creative" else
        {"targetUsers": ["目标用户"], "bidSuggestion": 10, "interests": ["兴趣"], "reason": "建议理由"}
    )
    try:
        async with get_model_gate().slot(), httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                qwen["base_url"] + "/chat/completions" if qwen else os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions"),
                headers={"Authorization": f"Bearer {key}", "X-Request-ID": request_id},
                json={
                    "model": qwen["model"] if qwen else os.getenv("OPENROUTER_MODEL", "mistralai/devstral-2512:free"),
                    "messages": [
                        {"role": "system", "content": instruction + "根据用户商品描述填写真实文案，只返回 JSON 对象，不要输出格式定义，不要照抄示例占位文字。每条文案不超过60字，不得编造未提供的效果数据。输出示例：" + json.dumps(example, ensure_ascii=False)},
                        {"role": "user", "content": body.model_dump_json()},
                    ],
                    "temperature": 0.2, "max_tokens": 1000,
                    **({"thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}} if qwen and qwen.get("provider") == "seed" else {"enable_thinking": False, "response_format": {"type": "json_object"}} if qwen else {"reasoning": {"enabled": False}}),
                },
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
            result = schema.model_validate_json(raw)
            if kind == 'strategy':
                result.bidSuggestion = None  # Legacy request provides no defensible bidding basis.
            return result
    except ModelBusy as exc:
        raise HTTPException(503, str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "模型生成超时") from exc
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
        raise HTTPException(502, "模型生成失败或返回格式不合法") from exc


@router.post("/creative", response_model=Creative)
async def creative(body: GenerationInput, request: Request):
    return await generate("creative", body, request.state.request_id)


@router.post("/strategy", response_model=Strategy)
async def strategy(body: GenerationInput, request: Request):
    return await generate("strategy", body, request.state.request_id)
