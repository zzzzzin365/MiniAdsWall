"""Creative/strategy model calls belong to the Python service."""
from core.model_gate import get_model_gate, ModelBusy
import asyncio
from dataclasses import replace
from core.rag_evidence import EvidenceConfig
import json
import os
import re
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

router = APIRouter(prefix="/generate")
logger = logging.getLogger(__name__)

class BidSettings(BaseModel):
    goal: str = Field(pattern='^(clicks|orders|leads)$')
    dailyBudget: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    days: int = Field(ge=1, le=365)
    maxCpc: float = Field(ge=.01, le=100, allow_inf_nan=False)
    unitProfit: float | None = Field(default=None, gt=0, allow_inf_nan=False)

class DeliveryDay(BaseModel):
    day: str
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    spend_minor: int = Field(ge=0)

class DeliveryPerformance(BaseModel):
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    orders: int = Field(ge=0)
    spend_minor: int = Field(ge=0)
    material_changed: bool = False
    daily: list[DeliveryDay] = Field(default_factory=list, max_length=7)
    net_revenue_minor: int | None = None

class DeliverySnapshot(BaseModel):
    status: str = Field(pattern='^(launched|new)$')
    billing: str = Field(pattern='^CPC$')
    currentBid: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    window: list[str] = Field(default_factory=list, max_length=7)
    performance: DeliveryPerformance | None = None
    material: dict = Field(default_factory=dict)


class GenerationInput(BaseModel):
    adDescription: str = Field(min_length=1, max_length=10000)
    industry: str = Field(min_length=1, max_length=200)
    tone: str = "neutral"
    settings: BidSettings | None = None
    delivery: DeliverySnapshot | None = None


class Creative(BaseModel):
    titles: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=3)
    scripts: list[str] = Field(min_length=1, max_length=3)
    keywords: list[str] = Field(min_length=1, max_length=5)


class Strategy(BaseModel):
    targetUsers: list[str] = Field(min_length=1, max_length=5)
    bidSuggestion: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    recommendedObjective: str = Field(min_length=1, max_length=500)
    interests: list[str] = Field(min_length=1, max_length=5)
    reason: str = Field(min_length=1)
    biddingAdvice: str = Field(min_length=1, max_length=1500)
    missingInformation: list[str] = Field(default_factory=list, max_length=10)
    source_ids: list[str] = Field(min_length=1, max_length=3)
    knowledgeUsed: bool = False
    knowledgeSources: list[dict] = Field(default_factory=list)
    bidBasis: dict = Field(default_factory=dict)


def pricing_basis(body: GenerationInput) -> dict:
    if body.settings is None or body.delivery is None:
        raise HTTPException(422, '请补充推广目标、日预算、投放天数和最高点击出价')
    s, d = body.settings, body.delivery
    import math
    ceiling = math.floor(min(s.maxCpc, s.dailyBudget, 100) * 100 + 1e-8) / 100
    if ceiling < .01:
        raise HTTPException(422, '日预算不能低于0.01元')
    p = d.performance
    if d.status == 'launched' and (p is None or d.currentBid is None):
        raise HTTPException(422, '已投放广告缺少有效的出价或统计记录')
    cpc = p.spend_minor / 100 / p.clicks if p and p.clicks else None
    return {'status': d.status, 'billing': 'CPC', 'max_bid': ceiling,
            'current_bid': d.currentBid, 'historical_cpc': round(cpc, 4) if cpc is not None else None,
            'ctr': p.clicks / p.impressions if p and p.impressions else None,
            'order_rate': p.orders / p.clicks if p and p.clicks else None,
            'initial_test_bid': max(.01, round(ceiling * .5, 2)),
            'initial_test_rule': '没有投放历史时，以商家最高点击出价的50%作为起始试投价，受日预算上限约束，不代表市场最优价',
            'total_budget': round(s.dailyBudget * s.days, 2),
            'material_changed': p.material_changed if p else False}


def bid_explanation(body: GenerationInput, basis: dict, price: float) -> tuple[str, str]:
    """Keep the displayed arithmetic and observation scope independent of model prose."""
    s, d = body.settings, body.delivery
    goal = {'clicks':'商品点击','orders':'成交订单','leads':'咨询线索'}[s.goal]
    limit = f'日预算¥{s.dailyBudget:.2f}，计划{s.days}天，总预算¥{basis["total_budget"]:.2f}；最高点击出价¥{basis["max_bid"]:.2f}。'
    if d.status == 'new':
        return (f'起始试投价为¥{price:.2f}/点击。采用成本上限50%的试投规则，受日预算限制；它不是市场最优价。'+limit+f'首轮保持该出价和素材，记录曝光、点击与消耗，再按{goal}目标比较效果。',
                f'该广告尚无投放历史，本轮依据商家目标、预算及成本上限确定试投起点；营销知识库用于制定单变量测试方法，不代替商品的历史效果。')
    current, p = d.currentBid, d.performance
    delta = (price/current-1)*100
    direction = f'下调{abs(delta):.1f}%' if delta < -.01 else f'上调{delta:.1f}%' if delta > .01 else '保持不变'
    metrics = f'当前¥{current:.2f}/点击，建议¥{price:.2f}/点击，{direction}。'
    observed = f'统计窗口内有{p.impressions}次曝光、{p.clicks}次点击、¥{p.spend_minor/100:.2f}消耗、{p.orders}笔支付订单。'
    if basis['historical_cpc'] is not None:
        observed += f'平均点击成本¥{basis["historical_cpc"]:.2f}。'
    caution = '当前素材已变更，历史结果属于原素材，需重新观察。' if p.material_changed else '实际点击均价不等于最高竞价出价，不能单凭两者差额判断原价过高。'
    hold = '没有充分的调价依据，先沿用当前出价，并受本次成本上限约束。' if basis.get('amount_source')=='hold_current_bid' else ''
    trial = f'下一轮只调整出价，保留素材和其他条件，使用同等观察窗口比较{goal}、点击成本与消耗；效果变差时恢复原价。' if abs(delta)>.01 else f'下一轮先保持出价与素材，使用同等观察窗口比较{goal}、点击成本与消耗，再决定是否调整。'
    return (metrics+hold+limit+trial, observed+caution+'本轮金额用于小步验证，不预测点击或成交一定提升。')


async def generate_strategy(body: GenerationInput, request_id: str):
    # Reuse the running AdsAgent and its registered Chroma knowledge_search tool.
    from api import main
    if main._orchestrator is None:
        raise HTTPException(503, "AdsAgent 尚未就绪，请稍后重试")
    basis = pricing_basis(body)
    question = ("根据已有广告营销知识，给出一条可以先做的投放测试或优化建议。"
                "只需有依据的下一步建议，无需证明完整方案、精确出价或预测效果；缺少业务数据时明确列出。")
    # Retrieve marketing methods, rather than trying to find the merchant's product in a general corpus.
    query = f"{body.industry}广告投放 推广目标 目标人群 渠道选择 计费方式 CPC 小预算测试 有效点击"
    knowledge = await main._prepare_knowledge_context(question, top_k=8, request_id=request_id,
        retrieval_query=query, retrieval_stage_timeout=5,
        retrieval_queries=['品牌广告 效果广告 如何选择评价目标', '媒体方 广告渠道选择 目标人群',
                           '素材与 A/B 测试 单变量 对照组 观察窗口'],
        evidence_config=replace(EvidenceConfig.from_env(), timeout_seconds=16))
    if knowledge.evidence and knowledge.evidence.retrieval_error:
        raise HTTPException(503, "广告营销知识库检索失败，请稍后重试")
    if knowledge.evidence and knowledge.evidence.status == 'unavailable':
        raise HTTPException(503, "广告营销知识库证据检查失败，请稍后重试")
    if not knowledge.used or not knowledge.chunks:
        raise HTTPException(422, "广告营销知识库没有足够的相关依据，请补充商品描述或营销资料")
    try:
        brief = {**body.model_dump(), 'pricing_basis': basis}
        draft_schema = Strategy.model_json_schema()
        for key in ('knowledgeUsed', 'knowledgeSources', 'bidBasis'):
            draft_schema['properties'].pop(key, None)
        raw = await asyncio.wait_for(main._orchestrator.recommend_delivery(
            brief, knowledge.text, draft_schema, request_id), timeout=30)
        result = Strategy.model_validate_json(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        allowed = {item['id']: item for item in knowledge.chunks}
        if len(set(result.source_ids)) != len(result.source_ids) or any(i not in allowed for i in result.source_ids):
            raise ValueError('invalid_knowledge_reference')
        if body.delivery.status == 'new':
            result.bidSuggestion = basis['initial_test_bid']
            basis['amount_source'] = 'initial_test_rule'
        elif result.bidSuggestion is None or result.bidSuggestion < .01:
            # Preserve the known bid when there is no justified adjustment.
            result.bidSuggestion = body.delivery.currentBid
            basis['amount_source'] = 'hold_current_bid'
        else:
            basis['amount_source'] = 'ads_agent'
        basis['cap_applied'] = result.bidSuggestion > basis['max_bid']
        result.bidSuggestion = round(min(result.bidSuggestion, basis['max_bid']), 2)
        result.bidBasis = basis
        result.biddingAdvice, result.reason = bid_explanation(body, basis, result.bidSuggestion)
        if body.settings.goal == 'clicks':
            result.missingInformation = []
        result.knowledgeUsed = True
        result.knowledgeSources = [{"id": i, "title": allowed[i].get('title', ''),
                                    "content": allowed[i]['content'],
                                    "source_url": allowed[i].get('source_url', '')} for i in result.source_ids]
        return result
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "AdsAgent 投放建议生成超时，请稍后重试") from exc
    except ModelBusy as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        code = type(exc).__name__
        if isinstance(exc, ValidationError):
            code += ':' + ','.join(str(item['loc']) + '/' + item['type'] for item in exc.errors(include_input=False))
        elif isinstance(exc, ValueError):
            code += ':' + str(exc)[:80]
        logger.warning('delivery_advice_failed request_id=%s code=%s', request_id, code)
        raise HTTPException(502, "AdsAgent 投放建议生成失败或引用无效，请重试") from exc


async def generate(kind: str, body: GenerationInput, request_id: str):
    if kind == 'strategy':
        return await generate_strategy(body, request_id)
    from core.model_client import business_model_config
    qwen = business_model_config()
    key = qwen["api_key"] if qwen else os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise HTTPException(503, "ARK_API_KEY、DASHSCOPE_API_KEY 或 OPENROUTER_API_KEY 未配置")
    schema = Creative
    instruction = '生成广告创意，输出 titles、texts、scripts 各 3 条，keywords 5 条。'
    example = {"titles": ["广告标题"], "texts": ["广告正文"], "scripts": ["视频脚本"], "keywords": ["关键词"]}
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
