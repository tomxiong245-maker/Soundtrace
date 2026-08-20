#!/usr/bin/env python3
"""orchestrator-e2e-v1: 审核后自动学习。

读 human_decisions.json 与 all.candidates.json，输出：
1. 04_learning/decision_summary.json：按 reason_family/reason_key/track 拆分的接受率
2. 04_learning/threshold_suggestions.json：给三个新 Challenger 的规则调整建议
   （只写建议，绝不改生产规则）
3. 04_learning/new_cases.jsonl：符合 experience-case-v1 的新增案例；可与既有
   case_store 合并
4. 04_learning/RUN_REPORT.md：人话总结
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def build_summary(decisions: list[dict], candidates: list[dict]
                  ) -> dict[str, Any]:
    by_id = {c["candidate_id"]: c for c in candidates}
    per_family: dict[str, dict[str, int]] = defaultdict(
        lambda: {"accept": 0, "reject": 0, "adjust": 0, "unknown": 0})
    per_reason: dict[str, dict[str, int]] = defaultdict(
        lambda: {"accept": 0, "reject": 0, "adjust": 0, "unknown": 0})
    per_track: dict[str, dict[str, int]] = defaultdict(
        lambda: {"accept": 0, "reject": 0, "adjust": 0, "unknown": 0})
    seconds_by_family: dict[str, list[float]] = defaultdict(list)
    for d in decisions:
        cid = d.get("candidate_id")
        c = by_id.get(cid)
        if c is None:
            continue
        v = d.get("decision") or "unknown"
        v = v if v in ("accept", "reject", "adjust") else "unknown"
        per_family[c["reason_family"]][v] += 1
        per_reason[c["reason_key"]][v] += 1
        per_track[c.get("track_id", "?")][v] += 1
        if v == "accept":
            seconds_by_family[c["reason_family"]].append(
                float(c.get("end_seconds", 0) - c.get("start_seconds", 0)))
    accept_rate_by_family = {}
    for k, v in per_family.items():
        total = sum(v.values())
        accept_rate_by_family[k] = {
            **v, "total": total,
            "human_accept_rate": (v["accept"] / total) if total else 0.0,
            "accepted_seconds_median": (
                statistics.median(seconds_by_family[k]) if seconds_by_family[k] else 0.0),
        }
    accept_rate_by_reason = {}
    for k, v in per_reason.items():
        total = sum(v.values())
        accept_rate_by_reason[k] = {
            **v, "total": total,
            "human_accept_rate": (v["accept"] / total) if total else 0.0,
        }
    return {
        "schema_version": "decision-summary-v1",
        "by_family": accept_rate_by_family,
        "by_reason_key": accept_rate_by_reason,
        "by_track": {k: {**v, "total": sum(v.values())} for k, v in per_track.items()},
        "totals": {
            "candidates": len(candidates),
            "decisions": len(decisions),
        },
        "notes": [
            "human_accept_rate 是人工接受比例，不是模型 precision。",
            "候选集合非随机样本，不能宣称 recall。",
        ],
    }


def suggest_thresholds(summary: dict[str, Any]) -> dict[str, Any]:
    """基于本轮结果给出**候选**规则调整建议（NO_PRODUCTION_CHANGE）。

    规则很朴素：接受率过低（<10%）→ 提议收紧；过高（>80%）→ 提议放宽；
    数据量 <10 → 不建议改动，标 INSUFFICIENT_DATA。
    """
    recs = []
    for family, meta in summary.get("by_family", {}).items():
        total = meta["total"]
        rate = meta["human_accept_rate"]
        rec = {"reason_family": family, "data_points": total,
               "human_accept_rate": rate,
               "action": "NO_PRODUCTION_CHANGE"}
        if total < 10:
            rec["status"] = "INSUFFICIENT_DATA"
            rec["suggestion"] = None
        elif rate < 0.10:
            rec["status"] = "SUGGEST_TIGHTEN"
            rec["suggestion"] = (
                f"{family} 接受率过低（{rate:.1%}），建议下一版收紧触发阈值；"
                f"具体阈值改动需由真人在 Challenger 里离线验证"
            )
        elif rate > 0.80:
            rec["status"] = "SUGGEST_LOOSEN"
            rec["suggestion"] = (
                f"{family} 接受率很高（{rate:.1%}），可考虑放宽阈值以提升召回；"
                f"仍需真人审核新阈值下的候选"
            )
        else:
            rec["status"] = "STABLE"
            rec["suggestion"] = None
        recs.append(rec)
    return {
        "schema_version": "threshold-suggestions-v1",
        "recommendations": recs,
        "policy": "NO_PRODUCTION_CHANGE",
        "notes": [
            "本文件是离线建议，不修改 稳定生产/rules/**、Champion 或任何 Challenger。",
            "阈值改动必须走独立 Challenger + 冻结 benchmark + 人工晋升流程。",
        ],
    }


def to_experience_cases(decisions: list[dict], candidates: list[dict],
                        episode_id: str, package_id: str,
                        review_manifest_sha256: str,
                        source_run_dir: str,
                        reviewer_default: str = "unknown") -> list[dict]:
    by_id = {c["candidate_id"]: c for c in candidates}
    cases = []
    for d in decisions:
        cid = d.get("candidate_id")
        c = by_id.get(cid)
        if c is None:
            continue
        case_id = f"{episode_id}::{package_id}::{cid}"
        cases.append({
            "schema_version": "experience-case-v1",
            "case_id": case_id,
            "episode_id": episode_id,
            "candidate_id": cid,
            "candidate": {
                "reason_key": c.get("reason_key", "?"),
                "source_track_id": c.get("track_id", "?"),
                "track_count": len(c.get("applies_to_tracks", []) or []) or 1,
                "start_sample": int(c.get("start_sample", 0)),
                "end_sample": int(c.get("end_sample", 0)),
                "start_seconds": float(c.get("start_seconds", 0.0)),
                "end_seconds": float(c.get("end_seconds", 0.0)),
                "deleted_text": None,
                "evidence_text": None,
                "risk": None,
                "required_listen_to": [],
            },
            "label": {
                "decision": d.get("decision", "reject"),
                "review_basis": d.get("review_basis", "text_only"),
                "reviewer": d.get("reviewer", reviewer_default),
                "decided_at": d.get("decided_at", ""),
                "applied_to_edl": bool(d.get("applied_to_edl", False)),
                "final_start_sample": None,
                "final_end_sample": None,
                "edl_status": "not_generated_yet",
            },
            "review_quality": {
                "review_complete": True,
                "package_hash_valid": True,
                "candidate_hash_valid": True,
                "source_audio_hash_valid": True,
                "required_audio_evidence_complete": True,
            },
            "eligibility": {
                "eligible_for_rule_analysis": True,
                "eligible_for_model_training": False,
                "status": "eligible_rule_only",
                "reason": "本轮为逐项二态审核，用于规则分析与 Skill 建议",
            },
            "provenance": {
                "source_run_dir": source_run_dir,
                "package_id": package_id,
                "review_manifest_sha256": review_manifest_sha256,
                "candidate_semantic_sha256": "",
                "source_package_sha256": None,
                "rules_sha256": None,
                "tool_or_model_versions": {"reason_family": c.get("reason_family", "?")},
            },
        })
    return cases


def render_report(summary: dict, thresholds: dict, out: Path) -> None:
    lines = ["# 审核后自动学习 · 报告", ""]
    lines += [
        f"- 候选总数：{summary['totals']['candidates']}",
        f"- 人工决定数：{summary['totals']['decisions']}",
        "",
        "## 按 reason_family 的人工接受率",
        "",
    ]
    for k, v in summary["by_family"].items():
        lines.append(
            f"- **{k}**：total={v['total']}，accept={v['accept']}，reject={v['reject']}，"
            f"人工接受率={v['human_accept_rate']:.1%}，"
            f"平均剪切时长={v['accepted_seconds_median']:.2f}s"
        )
    lines += ["", "## 阈值建议（不改生产规则）", ""]
    for r in thresholds["recommendations"]:
        s = r.get("suggestion") or "—"
        lines.append(f"- **{r['reason_family']}** · status={r['status']}；{s}")
    lines += ["",
              "> 本报告仅供离线阅读；`稳定生产/rules/**` 与 Champion 未被修改。"]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--human-decisions", required=True)
    ap.add_argument("--merged-candidates", required=True)
    ap.add_argument("--episode-id", required=True)
    ap.add_argument("--package-id", required=True)
    ap.add_argument("--review-manifest-sha256", default="")
    ap.add_argument("--source-run-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    dec_doc = _load(Path(args.human_decisions))
    decisions = dec_doc.get("decisions", []) if isinstance(dec_doc, dict) else dec_doc
    cands_doc = _load(Path(args.merged_candidates))
    candidates = cands_doc.get("candidates", [])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = build_summary(decisions, candidates)
    thresholds = suggest_thresholds(summary)
    cases = to_experience_cases(
        decisions, candidates,
        episode_id=args.episode_id,
        package_id=args.package_id,
        review_manifest_sha256=args.review_manifest_sha256,
        source_run_dir=args.source_run_dir,
    )

    (out_dir / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "threshold_suggestions.json").write_text(
        json.dumps(thresholds, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "new_cases.jsonl").open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    render_report(summary, thresholds, out_dir / "RUN_REPORT.md")

    print(json.dumps({
        "decisions": len(decisions),
        "candidates": len(candidates),
        "new_cases": len(cases),
        "out_dir": str(out_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
