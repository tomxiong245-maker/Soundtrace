#!/usr/bin/env python3
"""Fail-closed promotion checks for learned editing policies.

The label-learning Challenger produces useful policy cards, but a policy card
is not an ``autocut_policy``.  This module is the single, small gate between
those two concepts.  It only reads JSON evidence and writes a diagnostic
report; it never edits Champion rules, decisions, EDLs, or audio.

Two outcomes are intentionally separated:

* ``autocut`` promotion authorizes low-risk semantic deletion.  It requires
  an independent benchmark, independent review, a rollback drill, enough
  diverse itemized data, and an explicit signed scope.
* ``guard`` promotion can recommend a conservative *preserve/block* guard
  (for example, an ASR word-boundary false-positive guard).  A guard still
  cannot create an accept decision or authorize a cut.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "policy-promotion-report-v1"
AUTOCUT_POLICY_STATUS = "NOT_APPROVED"
MIN_CASES = 20
# NOTE 2026-08-17: 项目负责人明确指令删除"≥3 期节目 / ≥2 位独立审核人"晋升 blocker
# （用户 2026-08-17："第六条也不用管，从规则中去掉"）。删除的常量原为
#   MIN_EPISODES = 3
#   MIN_REVIEWERS = 2
# 相关 blocker 分支同步移除；独立 benchmark / 独立复核 / 回滚演练三项仍保留，
# 因为这些是政策晋升过程质量门，与"多期节目/多审核人"的数据规模门不同。
HIGH_RISK_REASON_KEYS = {
    "global_long_pause",
    "long_pause",
    "self_correction",
    "semantic_duplicate",
    "off_topic",
    "crosstalk_attribution",
    "cough_like",
    "mic_bump_like",
    "transient_events",
}
GUARD_ACTIONS = {
    "suppress_same_pattern_in_challenger",
    "preserve_and_block_auto_cut",
    "fix_text_layer_or_block_candidate",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _bool(value: Any) -> bool:
    return value is True


def _cards(policy_document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = policy_document.get("cards") or policy_document.get("policies") or []
    if not isinstance(rows, list):
        raise ValueError("policy cards must contain an array")
    return [row for row in rows if isinstance(row, dict)]


def _readiness_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    checks = readiness.get("checks") or {}
    return {
        "status": readiness.get("status"),
        "valid_cases": checks.get("valid_cases"),
        "valid_episodes": checks.get("valid_episodes"),
        "reviewer_count": checks.get("reviewer_count"),
        "has_independent_benchmark": _bool(checks.get("has_independent_benchmark")),
        "has_independent_review": _bool(checks.get("has_independent_review")),
        "has_rollback_drill": _bool(checks.get("has_rollback_drill")),
        "reasons": list(readiness.get("reasons") or []),
    }


def _recommendation_map(recommendations: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = recommendations.get("recommendations") or []
    return {
        str(row.get("reason_key")): row
        for row in rows
        if isinstance(row, dict) and row.get("reason_key")
    }


def evaluate_policy_promotion(
    policy_document: dict[str, Any],
    recommendations: dict[str, Any],
    readiness: dict[str, Any],
    *,
    authorization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate both the deletion-policy gate and safe guard candidates."""

    cards = _cards(policy_document)
    recommendation_by_reason = _recommendation_map(recommendations)
    readiness_summary = _readiness_summary(readiness)
    authorization = authorization or {}

    blockers: list[str] = []
    if readiness_summary["status"] != "READY":
        blockers.append(f"training/promotion readiness is {readiness_summary['status']!r}, not READY")
    if not isinstance(readiness_summary["valid_cases"], int) or readiness_summary["valid_cases"] < MIN_CASES:
        blockers.append(f"valid itemized cases must be >= {MIN_CASES}")
    # NOTE 2026-08-17: 已按用户指令移除 valid_episodes / reviewer_count 门。
    for key in ("has_independent_benchmark", "has_independent_review", "has_rollback_drill"):
        if not readiness_summary[key]:
            blockers.append(f"{key} is not proven")
    if not authorization.get("signed_by") or not authorization.get("signed_at"):
        blockers.append("no explicit signed authorization scope was supplied")

    eligible_autocut: list[str] = []
    ineligible_cards: list[dict[str, Any]] = []
    guard_candidates: list[dict[str, Any]] = []
    for card in cards:
        policy_id = str(card.get("policy_id") or "")
        conditions = card.get("conditions") or {}
        reason = str(conditions.get("reason_key") or "")
        action = str(card.get("action") or "")
        source_cases = card.get("source_case_ids") or []
        card_reasons: list[str] = []
        if reason in HIGH_RISK_REASON_KEYS:
            card_reasons.append("high-risk family is never eligible for unattended auto-cut")
        recommendation = recommendation_by_reason.get(reason)
        if recommendation and recommendation.get("action") == "NO_PRODUCTION_CHANGE":
            card_reasons.append("the current independent recommendation explicitly says NO_PRODUCTION_CHANGE")
        if not _bool((card.get("safety") or {}).get("can_enter_machine_assisted_draft_after_validation")):
            card_reasons.append("policy card does not permit even validated machine-assisted use")
        if card_reasons:
            ineligible_cards.append({"policy_id": policy_id, "reason_key": reason, "reasons": card_reasons})
        if action in GUARD_ACTIONS and len(source_cases) >= 3 and reason not in HIGH_RISK_REASON_KEYS:
            guard_candidates.append(
                {
                    "policy_id": policy_id,
                    "reason_key": reason,
                    "action": action,
                    "source_case_count": len(source_cases),
                    "scope": "preserve_or_block_only; does_not_authorize_autocut",
                }
            )
        # No card is allowed to bypass the global evidence gate.  A future
        # implementation can add a signed per-card allowlist here without
        # changing the fail-closed default.
        if not card_reasons and not blockers and authorization.get("policy_ids"):
            if policy_id in set(str(x) for x in authorization["policy_ids"]):
                eligible_autocut.append(policy_id)

    status = "APPROVED" if eligible_autocut else AUTOCUT_POLICY_STATUS
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "autocut_policy": {
            "id": "NOT_APPROVED" if not eligible_autocut else "policy-promotion-pending-implementation",
            "status": status,
            "eligible_policy_ids": eligible_autocut,
            "scope": "low-risk semantic deletion only" if eligible_autocut else "no automatic semantic deletion",
        },
        "guard_candidates": guard_candidates,
        "ineligible_cards": ineligible_cards,
        "readiness": readiness_summary,
        "authorization": {
            "signed_by": authorization.get("signed_by"),
            "signed_at": authorization.get("signed_at"),
            "policy_ids": authorization.get("policy_ids") or [],
        },
        "blockers": blockers,
        "policy": "fail_closed; this report never changes production rules or EDLs",
    }


def write_report(out_dir: Path, report: dict[str, Any], sources: Iterable[Path]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    source_rows = []
    for path in sources:
        source_rows.append({"relpath": str(path), "sha256": sha256_file(path)})
    report = dict(report)
    report["sources"] = source_rows
    (out_dir / "promotion_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# 自动剪辑政策晋升报告",
        "",
        f"- 结果：`{report['status']}`",
        f"- autocut_policy：`{report['autocut_policy']['status']}`",
        "",
        "## 结论",
        "",
        "本报告只评估证据，不修改生产规则、当前审核包、EDL 或音频。",
        "",
    ]
    if report["blockers"]:
        lines += ["### 阻塞项", ""] + [f"- {item}" for item in report["blockers"]] + [""]
    lines += ["### 可作为保护规则候选", ""]
    if report["guard_candidates"]:
        lines += [f"- `{row['policy_id']}`：{row['reason_key']} / `{row['action']}`（仅保留或阻断，不授权自动删剪）" for row in report["guard_candidates"]]
    else:
        lines.append("- 无")
    lines += ["", "### 当前不能进入自动删剪生产的政策", ""]
    lines += [f"- `{row['policy_id']}`：" + "；".join(row["reasons"]) for row in report["ineligible_cards"]]
    (out_dir / "PROMOTION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-cards", type=Path, required=True)
    parser.add_argument("--recommendations", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--authorization", type=Path)
    args = parser.parse_args(argv)
    authorization = read_json(args.authorization) if args.authorization else None
    report = evaluate_policy_promotion(
        read_json(args.policy_cards), read_json(args.recommendations), read_json(args.readiness),
        authorization=authorization,
    )
    write_report(args.out_dir, report, [args.policy_cards, args.recommendations, args.readiness] + ([args.authorization] if args.authorization else []))
    print(json.dumps({"status": report["status"], "guard_candidates": report["guard_candidates"], "out_dir": str(args.out_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
