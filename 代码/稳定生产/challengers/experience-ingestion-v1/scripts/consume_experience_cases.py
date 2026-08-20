#!/usr/bin/env python3
"""experience-ingestion-v1: 只读经验消费者。

只读 case_store，输出：
- reports/experience_summary.json / .md
- reports/rule_recommendations.json
- reports/training_readiness.json（由 check_training_readiness.py 独立生成，
  这里只输出 stub 供 adapter 引用）

它不会修改任何生产规则、Champion、审核前端或 case_store。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_MIN_CASES_PER_REASON = 20
# NOTE 2026-08-17: 项目负责人明确指令删除"≥3 期节目 / ≥2 位独立审核人"门
# （用户 2026-08-17："第六条也不用管，从规则中去掉"）。build_rule_recommendations
# 的 min_episodes / min_reviewers 参数保留但默认值不再阻断（设为 0），
# 调用方仍可显式传入需要的阈值以便向前兼容。
DEFAULT_MIN_EPISODES = 0
DEFAULT_MIN_REVIEWERS = 0


def load_index(case_store: Path) -> dict[str, Any]:
    with (case_store / "index.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_cases(case_store: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    cdir = case_store / "cases"
    if cdir.exists():
        for p in sorted(cdir.glob("*.jsonl")):
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    cases.append(json.loads(line))
    return cases


def load_exclusions(case_store: Path) -> list[dict[str, Any]]:
    p = case_store / "exclusions.jsonl"
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def load_quarantine(case_store: Path) -> list[dict[str, Any]]:
    p = case_store / "quarantine.jsonl"
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def build_summary(cases: list[dict[str, Any]],
                  exclusions: list[dict[str, Any]],
                  quarantine: list[dict[str, Any]]) -> dict[str, Any]:
    n_total = len(cases)
    by_ep = Counter(c["episode_id"] for c in cases)
    by_reason = Counter(c["candidate"]["reason_key"] for c in cases)
    by_decision = Counter(c["label"]["decision"] for c in cases)
    by_basis = Counter(c["label"].get("review_basis", "unknown") for c in cases)
    with_edl = sum(1 for c in cases if c["label"]["applied_to_edl"])
    without_edl = n_total - with_edl
    audio_evidence_complete = sum(
        1 for c in cases if c["review_quality"]["required_audio_evidence_complete"]
    )
    audio_evidence_rate = (audio_evidence_complete / n_total) if n_total else 0.0

    accept_rate_by_reason: dict[str, dict[str, Any]] = {}
    reason_to_eps: dict[str, set[str]] = defaultdict(set)
    for c in cases:
        rk = c["candidate"]["reason_key"]
        reason_to_eps[rk].add(c["episode_id"])
    reason_group: dict[str, dict[str, Counter]] = defaultdict(lambda: {"decisions": Counter()})
    for c in cases:
        rk = c["candidate"]["reason_key"]
        reason_group[rk]["decisions"][c["label"]["decision"]] += 1
    for rk, g in reason_group.items():
        total = sum(g["decisions"].values())
        accept = g["decisions"].get("accept", 0)
        accept_rate_by_reason[rk] = {
            "total": total,
            "accept": accept,
            "reject": g["decisions"].get("reject", 0),
            "adjust": g["decisions"].get("adjust", 0),
            "human_accept_rate": (accept / total) if total else 0.0,
            "episodes": sorted(reason_to_eps[rk]),
            "note": "口径为人工接受率，不是模型 precision（候选集合非随机样本）",
        }

    excluded_bulk = sum(1 for x in exclusions
                        if x.get("eligibility", {}).get("status") == "excluded_bulk_accept")

    reviewers = sorted({c["label"]["reviewer"] for c in cases if c["label"].get("reviewer")})

    summary = {
        "schema_version": "experience-summary-v1",
        "counts": {
            "total_cases": n_total,
            "by_episode": dict(by_ep),
            "by_reason": dict(by_reason),
            "by_decision": dict(by_decision),
            "by_review_basis": dict(by_basis),
            "with_edl": with_edl,
            "without_edl": without_edl,
            "excluded_bulk_accept": excluded_bulk,
            "quarantine": len(quarantine),
        },
        "audio_evidence_complete_rate": audio_evidence_rate,
        "audio_evidence_complete_count": audio_evidence_complete,
        "human_accept_rate_by_reason": accept_rate_by_reason,
        "reviewers": reviewers,
        "eligibility_notes": [
            "所有已导入案例可用于案例检索、Skill/规则分析；当前均不作为模型训练或生产变更依据。",
            "review_mode 仅保留为兼容元数据，不阻塞当前案例入库与规则分析。",
            "人工接受率只反映候选被人工采纳的比例，不等于模型 precision。",
            "候选集合不是完整随机样本，不能据此宣称 precision 或 recall。",
        ],
    }
    return summary


def summary_markdown(summary: dict[str, Any]) -> str:
    c = summary["counts"]
    lines = [
        "# Challenger 经验案例库 · 摘要",
        "",
        f"- 总案例数：{c['total_cases']}",
        f"- 按 episode：{c['by_episode']}",
        f"- 按 reason_key：{c['by_reason']}",
        f"- 按决定：{c['by_decision']}",
        f"- 按 review_basis：{c['by_review_basis']}",
        f"- 有 EDL：{c['with_edl']}；无 EDL：{c['without_edl']}",
        f"- 音频证据完整率：{summary['audio_evidence_complete_rate']:.2%}"
        f"（{summary['audio_evidence_complete_count']}/{c['total_cases']}）",
        f"- 已排除 bulk_accept：{c['excluded_bulk_accept']}；quarantine：{c['quarantine']}",
        f"- 审核人：{summary['reviewers']}",
        "",
        "## 按规则的人工接受率（不是模型 precision）",
        "",
    ]
    for rk, g in summary["human_accept_rate_by_reason"].items():
        lines.append(
            f"- `{rk}`：总 {g['total']}，accept {g['accept']}，reject {g['reject']}，"
            f"adjust {g['adjust']}；人工接受率 {g['human_accept_rate']:.2%}；"
            f"节目 {g['episodes']}"
        )
    lines += [
        "",
        "## 口径",
        "",
        "- 所有已导入案例可用于案例检索、Skill/规则分析；当前不作为模型训练或生产变更依据。",
        "- `review_mode` 仅保留为兼容元数据，不阻塞当前案例入库与规则分析。",
        "- 候选集合不是随机样本，不能宣称 precision/recall。",
        "- 无候选区域的漏剪召回率未在本报告统计。",
    ]
    return "\n".join(lines) + "\n"


def build_rule_recommendations(cases: list[dict[str, Any]],
                               *, min_cases: int = DEFAULT_MIN_CASES_PER_REASON,
                               min_episodes: int = DEFAULT_MIN_EPISODES,
                               min_reviewers: int = DEFAULT_MIN_REVIEWERS) -> dict[str, Any]:
    by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cases:
        by_reason[c["candidate"]["reason_key"]].append(c)

    recs = []
    for rk, group in by_reason.items():
        n = len(group)
        eps = sorted({c["episode_id"] for c in group})
        reviewers = sorted({c["label"]["reviewer"] for c in group if c["label"].get("reviewer")})
        accepts = sum(1 for c in group if c["label"]["decision"] == "accept")
        rejects = sum(1 for c in group if c["label"]["decision"] == "reject")
        adjusts = sum(1 for c in group if c["label"]["decision"] == "adjust")
        evidence_ids = [c["case_id"] for c in group]
        threshold_ok = (
            n >= min_cases
            and len(eps) >= min_episodes
            and len(reviewers) >= min_reviewers
        )
        rec = {
            "reason_key": rk,
            "data_points": n,
            "episodes": eps,
            "reviewers": reviewers,
            "distribution": {"accept": accepts, "reject": rejects, "adjust": adjusts},
            "human_accept_rate": (accepts / n) if n else 0.0,
            "suggestion": None,
            "evidence_case_ids": evidence_ids[:10],
            "risks": [
                "候选集合非随机样本，接受率不等于 precision",
                "当前仅有少量节目与审核人，证据不足以改生产规则或训练模型",
            ],
            "meets_thresholds": bool(threshold_ok),
            "status": "SUFFICIENT" if threshold_ok else "INSUFFICIENT_DATA",
            "action": "NO_PRODUCTION_CHANGE",
            "notes": "本 Challenger 只提供建议；生产规则一律不修改",
        }
        # 给一条“方向性建议”（仍标记为 NO_PRODUCTION_CHANGE）
        if rk == "immediate_repetition":
            if accepts > 0 and rejects > 0:
                rec["suggestion"] = (
                    "对 immediate_repetition 保留细化子规则的可能性：接受与拒绝并存，"
                    "建议未来把上下文（是否含数字/否定/结论/说话人切换）纳入规则；"
                    "当前数据量不足以更改生产规则"
                )
        elif rk == "filler_hesitation":
            if accepts > 0 and rejects > 0:
                rec["suggestion"] = (
                    "filler_hesitation 存在少量误剪风险，建议未来在候选阶段保留末词/末字："
                    "当前样本不足，禁止直接改生产规则"
                )
        elif rk == "global_long_pause":
            rec["suggestion"] = (
                "global_long_pause 需 must_listen_to 完整试听；当前样本数太少，"
                "禁止改压缩阈值"
            )
        recs.append(rec)

    return {
        "schema_version": "rule-recommendations-v1",
        "thresholds": {
            "min_cases_per_reason": min_cases,
            "min_episodes": min_episodes,
            "min_reviewers": min_reviewers,
        },
        "recommendations": recs,
        "notes": [
            "本文件仅供离线阅读；不修改 稳定生产/rules/**",
            "所有建议 action=NO_PRODUCTION_CHANGE",
        ],
    }


def write_reports(case_store: Path, reports_dir: Path,
                  min_cases: int = DEFAULT_MIN_CASES_PER_REASON,
                  min_episodes: int = DEFAULT_MIN_EPISODES,
                  min_reviewers: int = DEFAULT_MIN_REVIEWERS) -> dict[str, Any]:
    cases = iter_cases(case_store)
    exclusions = load_exclusions(case_store)
    quarantine = load_quarantine(case_store)

    summary = build_summary(cases, exclusions, quarantine)
    recs = build_rule_recommendations(
        cases, min_cases=min_cases, min_episodes=min_episodes, min_reviewers=min_reviewers,
    )

    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "experience_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (reports_dir / "experience_summary.md").write_text(
        summary_markdown(summary), encoding="utf-8")
    (reports_dir / "rule_recommendations.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"summary": summary, "recommendations": recs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-store", required=True)
    ap.add_argument("--out-dir", required=True, help="reports 输出目录")
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args(argv)

    case_store = Path(args.case_store).resolve()
    reports_dir = Path(args.out_dir).resolve()

    out = write_reports(case_store, reports_dir)

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        for name in ("experience_summary.json", "experience_summary.md",
                     "rule_recommendations.json"):
            (run_dir / name).write_text(
                (reports_dir / name).read_text(encoding="utf-8"), encoding="utf-8"
            )

    print(json.dumps({
        "cases": out["summary"]["counts"]["total_cases"],
        "reports": [str(reports_dir / n) for n in
                    ("experience_summary.json", "experience_summary.md",
                     "rule_recommendations.json")],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
