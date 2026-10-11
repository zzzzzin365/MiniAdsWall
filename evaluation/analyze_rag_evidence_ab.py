"""Create a readable report from preserved raw runs. No model calls.

Usage: python -m evaluation.analyze_rag_evidence_ab RUN.json [RUN2.json ...]
Distinct case IDs and repeated attempts are both reported. Multiple attempts are
not independent samples: the final comparison uses the first completed, graded
pair for each case, while all failures and costs remain visible.
"""
import argparse
import hashlib
import json
from pathlib import Path

from evaluation.run_rag_evidence_ab import summarize, usage_summary


def analyze(paths):
    runs = [json.loads(p.read_text()) for p in paths]
    if len({r["metadata"]["dataset_sha256"] for r in runs}) != 1:
        raise ValueError("Different datasets cannot be pooled")
    if len({(r["metadata"]["model"], r["metadata"]["threshold"]) for r in runs}) != 1:
        raise ValueError("Different experimental conditions cannot be pooled")
    rows = [row for run in runs for row in run["results"]]
    selected = {}
    for row in rows:
        if row["grading"]["status"] == "graded":
            selected.setdefault(row["case_id"], row)
    summary = summarize(list(selected.values()))
    summary["attempted_pairs_all_runs"] = len(rows)
    summary["unique_cases_attempted"] = len({r["case_id"] for r in rows})
    summary["ungraded_case_ids"] = sorted({r["case_id"] for r in rows} - set(selected))
    for name in ["baseline", "gated"]:
        calls = [c for r in rows for c in r[name]["calls"]]
        usage = usage_summary(calls)
        usage["known_prompt_tokens"] = sum((c.get("usage") or {}).get("prompt_tokens", 0) for c in calls)
        usage["known_completion_tokens"] = sum((c.get("usage") or {}).get("completion_tokens", 0) for c in calls)
        usage["known_reported_cost_usd"] = sum((c.get("usage") or {}).get("cost", 0) for c in calls)
        usage["calls_without_usage"] = sum(not c.get("usage") for c in calls)
        summary["arms"][name]["usage_all_attempts"] = usage
        summary["arms"][name]["pipeline_failures_all_attempts"] = sum(r[name]["status"] != "completed" for r in rows)
        summary["arms"][name]["usage_on_paired_cases"] = usage_summary([c for r in selected.values() for c in r[name]["calls"]])
    return runs[0]["metadata"], rows, selected, summary


def markdown(metadata, rows, selected, summary):
    baseline, gated = summary["arms"]["baseline"], summary["arms"]["gated"]
    n, answerable = summary["graded_pairs"], summary["answerable_graded"]
    text = ["# 回答前证据检查：实验结果", "",
            "本报告保留失败样本，不把超时、格式错误或评审失败算作正确拒答。", "",
            f"- 模型：`{metadata['model']}`；原配置模型返回 404，本次仅对实验进程覆盖。",
            f"- 输入：固定检索快照和合成业务数据；每个案例两组输入相同。",
            f"- 范围：只测回答阶段；不代表真实召回、HTTP 主链路或线上流量。",
            f"- 尝试：{summary['unique_cases_attempted']} 个问题、{summary['attempted_pairs_all_runs']} 次成对尝试；{n} 个不同问题有完整成对自动评分。",
            f"- 完整评分中，可回答 {answerable} 个，不可回答 {summary['unanswerable_graded']} 个。", "",
            "| 指标 | 当前生成方式 | 增加证据检查 |", "| --- | ---: | ---: |",
            f"| 自动评分事实错误 | {baseline['factual_errors']}/{n} | {gated['factual_errors']}/{n} |",
            f"| 自动评分误拒答 | {baseline['false_refusals']}/{answerable} | {gated['false_refusals']}/{answerable} |",
            f"| 自动评分正确 | {baseline['correct']}/{n} | {gated['correct']}/{n} |",
            f"| 完整样本调用耗时中位数 | {baseline['median_service_ms_on_graded']} ms | {gated['median_service_ms_on_graded']} ms |",
            f"| 全部尝试流程失败 | {baseline['pipeline_failures_all_attempts']} | {gated['pipeline_failures_all_attempts']} |",
            f"| 全部尝试模型调用数 | {baseline['usage_all_attempts']['calls']} | {gated['usage_all_attempts']['calls']} |", "",
            f"错误改善 {summary['error_pairs_improved']} 对，恶化 {summary['error_pairs_worsened']} 对；精确 McNemar 双侧 p={summary['exact_mcnemar_two_sided_p']}。",
            "20 个合成案例和同模型评审只能提供初步线索，不能证明统计或线上收益。", "",
            "## 消耗", "",
            "下面的完整样本消耗不含自动评审；失败请求可能没有返回 Token/成本，全部尝试的未知值不会填零。", "",
            "```json", json.dumps({name: summary['arms'][name]['usage_on_paired_cases'] for name in ['baseline', 'gated']}, ensure_ascii=False, indent=2), "```", "",
            "## 逐案例", ""]
    for cid in dict.fromkeys(r["case_id"] for r in rows):
        row = selected.get(cid) or next(r for r in rows if r["case_id"] == cid)
        text.extend([f"### {cid}：{row['question']}", "", f"参考：{row['reference']}", "",
                     "**当前方式**", "", row['baseline'].get('response') or f"调用失败：{row['baseline'].get('error')}", "",
                     "**增加检查**", "", row['gated'].get('response') or f"调用失败：{row['gated'].get('error')}", ""])
        if row["grading"]["status"] == "graded":
            for name in ["baseline", "gated"]:
                grade = row["grading"]["grades"][name]
                text.append(f"- {name} 自动评审：事实错误={grade['factual_error']}，误拒答={grade['false_refusal']}，正确={grade['correct']}；{grade['reason']}")
        else:
            text.append("没有完整成对评分，未计入质量比例。")
        text.append("")
    text.extend(["## 判断限制", "", "- 评审与生成使用相同模型，存在共同偏差；自动评分需要逐答案复核。",
                 "- 阈值 0.7 未校准，也没有在这批数据上调参。",
                 "- 暂未接入生产聊天，也未引入 Jev。", ""])
    return "\n".join(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/rag-evidence-ab-analysis.json"))
    parser.add_argument("--markdown", type=Path, default=Path("docs/rag-evidence-ab-results.md"))
    args = parser.parse_args()
    metadata, rows, selected, summary = analyze(args.runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"sources": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in args.runs],
                                       "selection": "First completed graded pair per case; all attempt failures and usage retained",
                                       "summary": summary}, ensure_ascii=False, indent=2) + "\n")
    args.markdown.write_text(markdown(metadata, rows, selected, summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
