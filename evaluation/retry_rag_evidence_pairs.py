"""Replay once only pairs that did not get a complete comparison.

Preserves the initial run. Replays BOTH arms, not only the unsuccessful answer.
Never selects cases by correctness and never tunes the prompt or threshold.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from core.skill_loader import SkillManager
from evaluation.run_rag_evidence_ab import ROOT, Pacer, resolve_config, run_arm, grade_pair, write_report


async def main(source, output):
    original = json.loads(source.read_text())
    metadata = dict(original["metadata"])
    for path, digest in metadata["code_sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Experimental code changed: {path}")
    for path, digest in metadata["skills_sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Skill changed: {path}")
    dataset_path = ROOT / "evaluation/rag_evidence_cases.json"
    if hashlib.sha256(dataset_path.read_bytes()).hexdigest() != metadata["dataset_sha256"]:
        raise ValueError("Frozen dataset changed")
    selected_ids = {r["case_id"] for r in original["results"] if r["grading"]["status"] != "graded"}
    if not selected_ids:
        print("All pairs already graded; no retries")
        return
    metadata.update(retry_of=str(source), retry_policy="One replay of both arms for incomplete or ungraded pairs, regardless of answer quality",
                    retry_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    retry_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    config = resolve_config(metadata["model"])
    os.environ["MODEL_GATE_MODE"] = "local"
    pacer = Pacer(metadata["minimum_request_interval_seconds"])
    skills = SkillManager(str(ROOT / "skills"), max_prompt_chars=5000)
    skills.load()
    rows = []
    cases = [c for c in json.loads(dataset_path.read_text())["cases"] if c["id"] in selected_ids]
    for index, case in enumerate(cases):
        order = ["gated", "baseline"] if index % 2 == 0 else ["baseline", "gated"]
        arms = {}
        for name in order:
            arms[name] = await run_arm(case, name == "gated", config, pacer, skills, metadata["threshold"],
                                       metadata.get("gate_version", "pilot-v1"))
        grading = await grade_pair(case, arms, config, pacer, metadata["seed"] + 1000 + index)
        rows.append({"case_id": case["id"], "round": 2, "category": case["category"], "question": case["question"],
                     "answerable": case["answerable"], "reference": case["reference"], "execution_order": order,
                     **arms, "grading": grading})
        write_report(output, metadata, rows)
        print(json.dumps({"completed": len(rows), "planned": len(cases), "case_id": case["id"],
                          "baseline": arms["baseline"]["status"], "gated": arms["gated"]["status"],
                          "grading": grading["status"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evidence/rag-evidence-ab-pilot-v1-retry.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Preserve previous attempts; choose a new output")
    asyncio.run(main(args.source, args.output))
