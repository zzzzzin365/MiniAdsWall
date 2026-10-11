"""Build synthetic paired generation cases from repository knowledge.

Run once to create the frozen JSON before any model outputs are observed.
The golden answers and labels are never sent to the gate or generator.
"""
import json
from pathlib import Path

from mcp.advertising_knowledge import ARTICLE_KNOWLEDGE


def build():
    docs = {d["knowledge_id"]: {"id": d["knowledge_id"], "title": d["title"], "content": d["content"]} for d in ARTICLE_KNOWLEDGE}
    docs.update({
        "ranking": {"id": "ranking", "title": "广告排序规则", "content": "当前演示规则：score = price + price * clicks * 0.42。它是排序分，不是收益预测或真实广告竞价算法。"},
        "asset": {"id": "asset", "title": "素材与 A/B 测试", "content": "素材测试一次只改一个变量，保留对照组；视频上传成功只表示文件保存，不能证明展示或成交。"},
        "old_rank": {"id": "old_rank", "title": "排序配置记录甲", "content": "当前排序系数为 0.42。记录未注明时间、版本和环境。"},
        "other_rank": {"id": "other_rank", "title": "排序配置记录乙", "content": "当前排序系数为 0.50。记录未注明时间、版本和环境。"},
    })
    cases = []
    def add(cid, category, question, candidates, data, answerable, reference):
        cases.append({"id": cid, "category": category, "question": question,
                      "chunks": [docs[k] for k in candidates], "business_data": data,
                      "answerable": answerable, "reference": reference})
    add("F01", "supported_rule", "按这里的规则，广告排序分怎么算？", ["ranking", "cpc", "asset"], {}, True,
        "score=price+price*clicks*0.42；这是项目演示排序分，不是收益或计费金额。")
    add("F02", "supported_rule", "CPC 固定单价计费时，费用怎么算？", ["cpc", "cpm", "cpv"], {}, True,
        "费用=有效计费点击数×固定单价；页面点击数、出价不能自动当计费点击数、成交单价。")
    add("F03", "supported_rule", "按提供的文档，DSP 和 SSP 分别为谁服务？", ["roles", "formats", "media"], {}, True,
        "DSP服务买方/广告主采买，SSP服务媒体管理供给。不能声称项目已接入这些平台。")
    add("F04", "supported_rule", "资料中的 CTR 分母是曝光次数还是落地页到达次数？", ["ctr", "arrival", "cvr"], {}, True,
        "CTR分母是曝光次数；落地页到达次数不是CTR分母。")
    add("D01", "supported_data", "A 广告今天的 CTR 是多少？", ["ctr", "cvr", "ranking"],
        {"ad_id": "A", "window": "今天", "clicks": 20, "impressions": 1000}, True, "20/1000=2%。")
    add("D02", "supported_data", "出价 2 元、点击 10 次，按演示规则排序分是多少？", ["ranking", "cpc", "asset"],
        {"price": 2, "clicks": 10}, True, "排序分=2+2×10×0.42=10.4；不是广告收益或消耗。")
    add("D03", "supported_data", "固定 CPM 单价 10 元，有效曝光 20000 次，费用多少？", ["cpm", "cpc", "cpv"],
        {"valid_impressions": 20000, "fixed_cpm_yuan": 10}, True, "20000/1000×10=200元。")
    add("D04", "supported_data", "100 次落地页到达、5 次支付，按到达为分母的支付转化率多少？", ["cvr", "ctr", "arrival"],
        {"window": "同一天", "arrivals": 100, "payments": 5}, True, "5/100=5%，明确到达为分母、支付为转化事件。")
    add("M01", "missing_business_data", "A 广告今天有 20 次点击，今天 CTR 是多少？", ["ctr", "ranking", "asset"],
        {"ad_id": "A", "window": "今天", "clicks": 20}, False, "缺少同一窗口曝光次数；不能计算CTR，不能把20次点击当20%。")
    add("M02", "missing_business_data", "A 出价 2 元、有 10 次页面点击，已经结算的消耗是多少元？", ["cpc", "fraud", "ranking"],
        {"ad_id": "A", "price": 2, "page_clicks": 10}, False, "缺有效计费点击数、实际结算单价或账务流水；不能断言消耗20元。")
    add("M03", "missing_business_data", "A 广告加价 10% 后，收益会增加多少元？", ["ranking", "revenue", "experiment"],
        {"ad_id": "A", "price": 2, "clicks": 10}, False, "缺增量曝光、转化、收入、成本及实验数据，不能预测收益；排序分变化不等于收益变化。")
    add("M04", "missing_business_data", "A 有 100 次点击，支付转化率是多少？", ["cvr", "ctr", "arrival"],
        {"ad_id": "A", "clicks": 100}, False, "缺支付转化次数，且应明确点击或到达分母；不能凭点击数计算支付转化率。")
    add("T01", "topic_only", "我们今天签的 CPV 合同要求看满几秒才算有效观看？", ["cpv", "cpc", "asset"], {}, False,
        "缺当前合同有效观看条款；通用CPV概念不能证明3秒、5秒、15秒等具体阈值。")
    add("T02", "topic_only", "我们这份 CPS 合同的佣金比例是多少？", ["cpa_cps", "cpc", "media"], {}, False,
        "缺当前合同佣金条款，不能给具体比例或把行业惯例当合同事实。")
    add("T03", "topic_only", "昨天 A 广告到底在哪个环节被过滤、没有展示？", ["delivery", "index", "reliability"],
        {"ad_id": "A", "day": "昨天", "saved_successfully": True, "impressions": 0}, False,
        "缺投放、索引、召回、排序日志；只能给排查步骤，不能认定具体故障环节。")
    add("T04", "topic_only", "刚上传的 A 视频开头出现了什么文字？", ["asset", "cpv", "modules"],
        {"ad_id": "A", "video_count": 1, "upload_succeeded": True}, False,
        "没有视频帧、OCR或字幕，不能声称看到画面或编造视频开头文字。")
    add("B01", "scope_window_conflict", "A 今天 CTR 多少？这里是 A 今天点击 20 次和 B 今天曝光 1000 次。", ["ctr", "arrival", "ranking"],
        {"clicks": {"ad_id": "A", "window": "今天", "count": 20}, "impressions": {"ad_id": "B", "window": "今天", "count": 1000}}, False,
        "范围不同，缺A今天曝光，不能用B曝光算A的CTR为2%。")
    add("B02", "scope_window_conflict", "A 今天点击 20 次、昨天曝光 1000 次，今天 CTR 是多少？", ["ctr", "arrival", "ranking"],
        {"ad_id": "A", "clicks": {"window": "今天", "count": 20}, "impressions": {"window": "昨天", "count": 1000}}, False,
        "窗口不一致，缺A今天曝光，不能把20/1000=2%作为今天CTR。")
    add("B03", "scope_window_conflict", "当前线上排序系数到底是多少？请给出唯一数值。", ["old_rank", "other_rank", "asset"], {}, False,
        "两份资料分别为0.42和0.50且都无环境时间版本，无法确定线上唯一系数；不能以排名或常识选一份。")
    add("B04", "scope_window_conflict", "A 曝光为零、点击为零，CTR 是多少？", ["ctr", "cvr", "ranking"],
        {"ad_id": "A", "window": "今天", "impressions": 0, "clicks": 0}, False,
        "分母为零，CTR未定义，不能回答0%、100%或其他数值；说明暂无有效CTR。")
    return {"version": "rag-evidence-pilot-v1", "frozen": True,
            "annotation": "Synthetic author-defined references; no independent human annotation",
            "scope": "Frozen retrieval snapshots; generation-stage experiment, not retrieval or HTTP end-to-end evaluation",
            "cases": cases}


if __name__ == "__main__":
    path = Path(__file__).with_name("rag_evidence_cases.json")
    if path.exists():
        raise SystemExit("Frozen dataset already exists; create a new version instead of overwriting")
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(f"Saved {len(build()['cases'])} cases to {path}")
