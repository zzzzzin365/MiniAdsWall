"""Create reproducible synthetic business fixtures and labeled RAG questions.

No model calls, credentials or real customer records. The output is a new frozen
dataset, compatible with run_rag_evidence_ab.py; existing datasets are preserved.
"""
import argparse
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261011


def money(value):
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def percentage(numerator, denominator):
    return money(Decimal(numerator) / Decimal(denominator) * 100)


def ranking(price, clicks):
    # Match the existing advertising tool's float arithmetic and round policy.
    value = float(price)
    return round(value + value * clicks * .42, 2)


DOCS = {
    "ctr": {"id": "synthetic:ctr", "title": "评测口径：CTR", "content":
            "CTR=点击次数/曝光次数。必须是同一广告、同一统计窗口和相同计数口径。缺曝光、窗口不一致或曝光为零时，不能报确定的CTR。"},
    "ranking": {"id": "synthetic:ranking", "title": "项目演示排序公式", "content":
                "演示排序分=price+price*clicks*0.42。加价10%的模拟先将price乘1.10，保留当前点击数再算分。这不是未来点击、排名位置、收益或实际竞价结果；模拟不修改广告。"},
    "cpc": {"id": "synthetic:cpc", "title": "评测口径：固定CPC结算", "content":
            "固定CPC单价条件下，费用=有效计费点击数*合同固定单价。页面clicks与有效计费点击不同；竞价price与固定结算单价不同。有冲突且无可信优先级的单价记录时，需核实当前合同或结算流水。"},
    "cpm": {"id": "synthetic:cpm", "title": "评测口径：CPM", "content":
            "固定CPM费用=有效曝光数/1000*CPM单价。曝光不是点击，也不能用广告出价代替合同CPM单价。"},
    "roas": {"id": "synthetic:roas", "title": "评测口径：历史ROAS", "content":
             "本评测ROAS=归因收入/广告消耗。必须来自同一广告、相同统计窗口和已明确的归因口径。ROAS不是净利润；历史ROAS不证明加价会产生增量收益。"},
    "future": {"id": "synthetic:future", "title": "评测口径：增量收益", "content":
               "历史点击、曝光、收入和消耗不能直接证明未来加价的增量利润。需要可识别加价效果的对照实验或经验证的预测模型，并明确收入与成本口径。演示排名分变化不能当作收益变化。"},
    "creative": {"id": "synthetic:creative", "title": "素材测试建议", "content":
                 "测试素材时尽量一次只改变一个变量，保留对照。视频文件已上传不证明发生了曝光或成交。"},
}


def build():
    rng = random.Random(SEED)
    snapshots, cases = [], []

    def add(group, suffix, category, question, doc_keys, data, answerable, reference,
            expected=None, missing=None, gold_keys=None):
        cases.append({
            "id": f"{group['snapshot_id']}-{suffix}", "group_id": group["snapshot_id"],
            "split": group["split"], "category": category, "question": question,
            "chunks": [dict(DOCS[key]) for key in doc_keys], "business_data": data,
            # These labels stay outside chunks/business_data and are judge-only.
            "answerable": answerable, "reference": reference,
            "expected_values": expected or {}, "expected_missing_information": missing or [],
            "gold_chunk_ids": [DOCS[key]["id"] for key in gold_keys or []],
        })

    for index in range(10):
        day = date(2026, 10, 1) + timedelta(days=index)
        ads = []
        for offset in range(5):
            impressions = rng.randrange(20, 201) * 100
            clicks = max(1, impressions * rng.randrange(5, 81) // 1000)
            billable = rng.randrange(max(1, clicks * 8 // 10), clicks + 1)
            price, cpc = rng.randrange(80, 401) / 100, rng.randrange(10, 61) / 100
            purchases = rng.randrange(0, max(1, clicks // 12) + 1)
            revenue = money(Decimal(purchases) * Decimal(rng.randrange(20, 301)))
            ads.append({
                "id": f"mock-ad-{index + 1:02d}-{offset + 1:02d}",
                "title": f"模拟广告{index + 1}-{offset + 1}",
                "price": price, "clicks": clicks, "video_count": rng.randrange(0, 5), "videos": [],
                "simulated_measurements": {
                    "window": str(day), "metric_scope": "same_ad", "impressions": impressions,
                    "settled_valid_clicks": billable, "fixed_cpc_yuan": cpc,
                    "paid_orders": purchases, "ad_spend_yuan": money(Decimal(billable) * Decimal(str(cpc))),
                    "attributed_revenue_yuan": revenue, "attribution_window": "24h_click",
                    "currency": "CNY", "settlement_policy": "fixed_cpc",
                },
            })
        group = {"snapshot_id": f"S{index + 1:02d}", "window": str(day),
                 "split": "calibration" if index < 3 else "test", "scope": "provided_snapshot",
                 "ads": ads}
        snapshots.append(group)
        ad = ads[index % len(ads)]
        metrics = ad["simulated_measurements"]
        identity = {"ad_id": ad["id"], "window": str(day), "scope": "provided_snapshot"}
        click_data = {**identity, "clicks": ad["clicks"], "impressions": metrics["impressions"]}
        ctr = percentage(ad["clicks"], metrics["impressions"])
        add(group, "CTR", "supported_data", f"{ad['id']} 在 {day} 的 CTR 是多少？", ["ctr", "ranking", "cpm"],
            click_data, True, f"CTR={ad['clicks']}/{metrics['impressions']}，约{ctr}%；只代表给定广告快照。",
            {"ctr_percent": ctr}, gold_keys=["ctr"])

        new_price = round(ad["price"] * (1 + 10 / 100), 2)
        current_score, new_score = ranking(ad["price"], ad["clicks"]), ranking(new_price, ad["clicks"])
        add(group, "BID", "supported_data",
            f"{ad['id']} 出价增加10%，保持已有点击数不变，演示排序分会从多少变成多少？", ["ranking", "ctr", "creative"],
            {**identity, "price": ad["price"], "clicks": ad["clicks"], "increase_pct": 10,
             "simulated_price_yuan": new_price}, True,
            f"使用提供的模拟出价：{ad['price']}→{new_price}元；排序分{current_score}→{new_score}。"
            "排序分计算接受0.01的浮点舍入差异。这里只是给定公式的模拟，不是排名位置、收益或实际修改。",
            {"current_price": ad["price"], "simulated_price": new_price, "current_score": current_score,
             "simulated_score": new_score, "score_absolute_tolerance": .01}, gold_keys=["ranking"])

        add(group, "CPC", "supported_data",
            f"{ad['id']} 在 {day} 按固定CPC规则已结算的费用是多少？", ["cpc", "ranking", "cpm"],
            {**identity, "page_clicks": ad["clicks"], "bid_price_yuan": ad["price"],
             "settled_valid_clicks": metrics["settled_valid_clicks"], "fixed_cpc_yuan": metrics["fixed_cpc_yuan"],
             "currency": "CNY", "settlement_policy": "fixed_cpc"}, True,
            f"已结算费用={metrics['settled_valid_clicks']}×{metrics['fixed_cpc_yuan']}={metrics['ad_spend_yuan']}元。"
            "不能用页面点击数乘竞价出价。", {"settled_cost_yuan": metrics["ad_spend_yuan"]}, gold_keys=["cpc"])

        roas = money(Decimal(str(metrics["attributed_revenue_yuan"])) / Decimal(str(metrics["ad_spend_yuan"])))
        add(group, "ROAS", "supported_data", f"{ad['id']} 在 {day} 的历史 ROAS 是多少？", ["roas", "ctr", "cpc"],
            {**identity, "ad_spend_yuan": metrics["ad_spend_yuan"], "attributed_revenue_yuan": metrics["attributed_revenue_yuan"],
             "attribution_window": "24h_click", "revenue_scope": "same_ad", "spend_scope": "same_ad", "currency": "CNY"}, True,
            f"历史ROAS={metrics['attributed_revenue_yuan']}/{metrics['ad_spend_yuan']}，约{roas}倍。"
            "仅对应给定窗口和归因口径，不是净利润或加价效果。", {"roas": roas}, gold_keys=["roas"])

        add(group, "NO_IMPRESSIONS", "missing_business_data", f"{ad['id']} 在 {day} 的 CTR 是多少？", ["ctr", "ranking", "creative"],
            {**identity, "clicks": ad["clicks"]}, False, "缺同一广告、同一窗口的曝光次数，不能算确定CTR。",
            missing=["同一广告、同一统计窗口的曝光次数"], gold_keys=["ctr"])

        add(group, "FUTURE_PROFIT", "missing_causal_evidence",
            f"{ad['id']} 从明天开始加价10%，一天能多赚多少元？", ["ranking", "future", "roas"],
            {**identity, "price": ad["price"], "clicks": ad["clicks"], "impressions": metrics["impressions"],
             "past_ad_spend_yuan": metrics["ad_spend_yuan"], "past_attributed_revenue_yuan": metrics["attributed_revenue_yuan"]},
            False, "不能根据历史快照推断未来加价的增量利润。需要对照实验或经验证的预测模型，"
            "并明确增量收入、广告消耗及其他成本；可以做排序分模拟，但不能当收益预测。",
            missing=["加价效果的对照实验或经验证的预测模型", "增量收入和成本口径"], gold_keys=["future"])

        add(group, "WINDOW_CONFLICT", "window_conflict", f"{ad['id']} 在 {day} 当天的 CTR 是多少？", ["ctr", "cpm", "ranking"],
            {"ad_id": ad["id"], "scope": "provided_snapshot", "clicks": ad["clicks"], "clicks_window": str(day),
             "impressions": metrics["impressions"] * 7,
             "impressions_window": {"start": str(day - timedelta(days=6)), "end": str(day)}}, False,
            "当天点击与七天曝光不能混算当天CTR，需补当天曝光次数。",
            missing=["与当天点击相同窗口的曝光次数"], gold_keys=["ctr"])

        conflict_chunks = [
            {"id": f"synthetic:cpc-record-A:{index}", "title": "单价记录甲", "content":
             f"该广告当前固定CPC单价为{metrics['fixed_cpc_yuan']}元。未注明时间、版本、环境或权威优先级。"},
            {"id": f"synthetic:cpc-record-B:{index}", "title": "单价记录乙", "content":
             f"该广告当前固定CPC单价为{money(Decimal(str(metrics['fixed_cpc_yuan'])) + Decimal('.11'))}元。"
             "未注明时间、版本、环境或权威优先级。"}, dict(DOCS["cpc"]),
        ]
        add(group, "PRICE_CONFLICT", "rule_conflict", f"{ad['id']} 在 {day} 的固定CPC结算费用是多少？", ["cpc"],
            {**identity, "settled_valid_clicks": metrics["settled_valid_clicks"], "settlement_policy": "fixed_cpc"},
            False, "固定CPC单价记录冲突且没有可确定的优先级，不能报一个结算金额；需当前合同或结算流水。",
            missing=["可确定当前单价的合同或结算流水"], gold_keys=["cpc"])
        cases[-1]["chunks"] = conflict_chunks

    return {
        "version": "synthetic-business-evidence-v1", "frozen": True, "seed": SEED,
        "scope": "Synthetic fixed-candidate business RAG evaluation; not live traffic or retrieval evaluation",
        "annotation": "Developer-defined references with deterministic arithmetic; not independent human labels",
        "provenance": {"business_data": "entirely synthetic", "ranking_formula": "existing project demo rule",
                       "measurement_and_billing_rules": "explicit evaluation fixture assumptions, not live project contracts"},
        "limitations": ["50 simulated ads, 10 snapshots, 8 repeated templates; 80 cases are not 80 independent business scenarios",
                        "Split by snapshot: 24 calibration cases and 56 held-out test cases; templates still overlap",
                        "simulated_measurements are evaluation-only; existing app tools do not automatically ingest these fields",
                        "Golden labels/answers must never enter model input; reviewers need to check labels independently",
                        "No Jev calls or model comparison have been performed"],
        "snapshots": snapshots, "cases": cases,
    }


def validate(dataset):
    cases = dataset["cases"]
    assert len(cases) == 80 and len({case["id"] for case in cases}) == 80
    assert Counter(case["answerable"] for case in cases) == {True: 40, False: 40}
    assert Counter(case["split"] for case in cases) == {"calibration": 24, "test": 56}
    assert not ({c["group_id"] for c in cases if c["split"] == "calibration"} &
                {c["group_id"] for c in cases if c["split"] == "test"})
    for snapshot in dataset["snapshots"]:
        for ad in snapshot["ads"]:
            values = ad["simulated_measurements"]
            assert 0 <= values["paid_orders"] <= values["settled_valid_clicks"] <= ad["clicks"] <= values["impressions"]
            assert values["ad_spend_yuan"] == money(Decimal(values["settled_valid_clicks"]) * Decimal(str(values["fixed_cpc_yuan"])))
    for case in cases:
        ids = {c["id"] for c in case["chunks"]}
        assert len(ids) == len(case["chunks"]) == 3
        assert set(case["gold_chunk_ids"]) <= ids
        assert bool(case["expected_missing_information"]) != case["answerable"]
        assert all(len(c["content"]) <= 600 for c in case["chunks"])
        assert not any(key in case["business_data"] for key in ("reference", "answerable", "expected_values"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/rag_evidence_business_cases_v1.json")
    args = parser.parse_args()
    dataset = build()
    validate(dataset)
    payload = json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(payload)
    print(json.dumps({"output": str(args.output), "snapshots": len(dataset["snapshots"]),
                      "ads": sum(len(s["ads"]) for s in dataset["snapshots"]), "cases": len(dataset["cases"]),
                      "sha256": hashlib.sha256(payload.encode()).hexdigest()}, ensure_ascii=False))
