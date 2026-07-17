"""
MiniAddwall 广告数据工具。

这些工具接收 /chat 请求中携带的结构化广告快照，为 AdsAgent 提供可复用的
业务分析结果，避免只把广告摘要塞进 prompt。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


SCORE_COEFFICIENT = 0.42


def _ads(context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not context:
        return []
    value = context.get("ads", [])
    return value if isinstance(value, list) else []


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _title(ad: Dict[str, Any]) -> str:
    return str(ad.get("title") or ad.get("name") or ad.get("id") or "未命名广告")


def _videos(ad: Dict[str, Any]) -> List[Any]:
    videos = ad.get("videos") or []
    return videos if isinstance(videos, list) else []


def _score(ad: Dict[str, Any], coefficient: float = SCORE_COEFFICIENT) -> float:
    price = _num(ad.get("price") or ad.get("pricing"))
    clicks = _num(ad.get("clicks"))
    return price + (price * clicks * coefficient)


def _shape_ad(ad: Dict[str, Any], coefficient: float = SCORE_COEFFICIENT) -> Dict[str, Any]:
    price = _num(ad.get("price") or ad.get("pricing"))
    clicks = _num(ad.get("clicks"))
    videos = _videos(ad)
    return {
        "id": ad.get("id"),
        "title": _title(ad),
        "price": round(price, 2),
        "clicks": int(clicks),
        "videos": len(videos),
        "score": round(_score(ad, coefficient), 2),
        "description": ad.get("description") or ad.get("content") or "",
    }


async def ads_summary_handler(params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    coefficient = _num(params.get("score_coefficient"), SCORE_COEFFICIENT)
    ads = _ads(context)
    shaped = [_shape_ad(ad, coefficient) for ad in ads]
    total_clicks = sum(item["clicks"] for item in shaped)
    total_videos = sum(item["videos"] for item in shaped)
    avg_price = sum(item["price"] for item in shaped) / len(shaped) if shaped else 0.0
    sorted_by_score = sorted(shaped, key=lambda item: item["score"], reverse=True)
    sorted_by_clicks = sorted(shaped, key=lambda item: item["clicks"], reverse=True)
    no_video = [item for item in shaped if item["videos"] == 0]

    return {
        "ad_count": len(shaped),
        "total_clicks": total_clicks,
        "total_videos": total_videos,
        "avg_price": round(avg_price, 2),
        "score_formula": f"price + price * clicks * {coefficient}",
        "top_by_score": sorted_by_score[:5],
        "top_by_clicks": sorted_by_clicks[:5],
        "no_video_ads": no_video[:10],
    }


async def ad_performance_search_handler(params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    query = str(params.get("query", "")).lower()
    top_k = int(_num(params.get("top_k"), 5))
    coefficient = _num(params.get("score_coefficient"), SCORE_COEFFICIENT)
    shaped = [_shape_ad(ad, coefficient) for ad in _ads(context)]
    if not shaped:
        return []

    avg_price = sum(item["price"] for item in shaped) / len(shaped)
    avg_clicks = sum(item["clicks"] for item in shaped) / len(shaped)

    def reasons(item: Dict[str, Any]) -> List[str]:
        result: List[str] = []
        title = str(item["title"]).lower()
        desc = str(item.get("description", "")).lower()
        if query and (query in title or query in desc):
            result.append("命中标题或描述")
        if item["videos"] == 0:
            result.append("无视频素材")
        if item["price"] >= avg_price and item["clicks"] < avg_clicks:
            result.append("高出价低点击")
        if item["price"] <= avg_price and item["clicks"] > avg_clicks:
            result.append("低出价高点击")
        if item["clicks"] == max(ad["clicks"] for ad in shaped):
            result.append("点击最高")
        return result

    ranked = []
    for item in shaped:
        rs = reasons(item)
        if rs:
            ranked.append({**item, "reasons": rs})

    if not ranked:
        ranked = sorted(shaped, key=lambda item: item["score"], reverse=True)

    return ranked[:top_k]


async def bid_simulation_handler(params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    coefficient = _num(params.get("score_coefficient"), SCORE_COEFFICIENT)
    increase_pct = _num(params.get("increase_pct"), 10.0)
    top_k = int(_num(params.get("top_k"), 5))
    shaped = [_shape_ad(ad, coefficient) for ad in _ads(context)]
    if not shaped:
        return {
            "strategy": "no_data",
            "message": "当前没有结构化广告数据，无法进行出价模拟。",
            "candidates": [],
        }

    avg_price = sum(item["price"] for item in shaped) / len(shaped)
    avg_clicks = sum(item["clicks"] for item in shaped) / len(shaped)
    candidates = []
    for item in shaped:
        current_price = item["price"]
        simulated_price = round(current_price * (1 + increase_pct / 100), 2)
        simulated_score = round(simulated_price + (simulated_price * item["clicks"] * coefficient), 2)
        if item["clicks"] >= avg_clicks and item["price"] <= avg_price:
            action = "可小幅加价"
            reason = "点击表现高于均值且当前出价不高"
        elif item["clicks"] < avg_clicks and item["price"] >= avg_price:
            action = "先改素材/文案"
            reason = "出价已高但点击偏低，继续加价效率可能较差"
        elif item["videos"] == 0:
            action = "先补素材"
            reason = "缺少视频素材，建议补齐后再调整出价"
        else:
            action = "观察"
            reason = "当前数据没有明显加价或降价信号"

        candidates.append({
            **item,
            "simulated_price": simulated_price,
            "simulated_score": simulated_score,
            "action": action,
            "reason": reason,
        })

    priority = {"可小幅加价": 0, "先改素材/文案": 1, "先补素材": 2, "观察": 3}
    candidates.sort(key=lambda item: (priority.get(item["action"], 9), -item["clicks"], item["price"]))
    return {
        "strategy": "incremental_bid_test",
        "increase_pct": increase_pct,
        "score_formula": f"price + price * clicks * {coefficient}",
        "candidates": candidates[:top_k],
    }
