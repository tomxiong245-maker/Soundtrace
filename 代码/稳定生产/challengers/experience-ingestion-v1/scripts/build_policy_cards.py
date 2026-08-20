#!/usr/bin/env python3
"""Build human-readable Challenger policy cards from classified cases."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from classify_feedback import classify_record


def normalize_match_text(value: Any) -> str:
    """Matching helper for policy grouping; raw ASR remains untouched."""
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


POLICY_SCHEMA = "editing-policy-card-v1"


ACTION_BY_CLASS = {
    "asr_error": "fix_text_layer_or_block_candidate",
    "semantic_keep": "preserve_and_block_auto_cut",
    "semantic_cut": "propose_for_human_review",
    "execution_issue": "route_to_boundary_or_rendering_review",
    "false_positive": "suppress_same_pattern_in_challenger",
    "unknown": "keep_human_review_required",
}


def _sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _classification_classes(classification: dict[str, Any]) -> list[str]:
    """Read v1 single-label and additive multi-label classifications alike."""
    raw_classes = classification.get("classes")
    if isinstance(raw_classes, list):
        classes = [str(label) for label in raw_classes if str(label) in ACTION_BY_CLASS]
        if classes:
            return classes
    legacy = str(classification.get("primary_class") or classification.get("class") or "unknown")
    return [legacy] if legacy in ACTION_BY_CLASS else ["unknown"]


def _class_confidence(classification: dict[str, Any], label: str) -> float:
    by_class = classification.get("confidence_by_class") or {}
    if isinstance(by_class, dict) and label in by_class:
        return float(by_class[label] or 0.0)
    return float(classification.get("confidence") or 0.0)


def build_policy_cards(records: Iterable[dict[str, Any]], *, snapshot_id: str) -> list[dict[str, Any]]:
    rows = [classify_record(dict(row)) for row in records]
    # Group by feedback pattern and candidate family, not exact literal text.
    # Literal text remains evidence, but grouping it into the policy identity
    # would turn every one-off comment into a false “rule”.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        classification = row.get("feedback_classification") or {}
        candidate = row.get("candidate") or {}
        reason = str(candidate.get("reason_key") or "unknown")
        # One reviewer comment can provide both a semantic instruction and an
        # execution diagnosis.  Make a separate Challenger card for each
        # evidenced class rather than silently discarding all but the legacy
        # primary projection.
        for label in _classification_classes(classification):
            groups[(label, reason)].append(row)

    cards: list[dict[str, Any]] = []
    for index, ((label, reason), group) in enumerate(sorted(groups.items()), start=1):
        match_texts = sorted({
            normalize_match_text((row.get("candidate") or {}).get("proposed_text") or (row.get("candidate") or {}).get("deleted_text") or "")
            for row in group
        } - {""})
        # Unknown/no-feedback records remain useful in the case store and
        # benchmark, but cannot be promoted into a policy card without an
        # explainable condition.  Keeping them out prevents a noisy “rule”
        # that merely restates “ask a human”.
        if label == "unknown":
            continue
        decisions = Counter(str((row.get("label") or {}).get("decision") or "unknown") for row in group)
        examples = [
            {
                "case_id": row.get("case_id"),
                "feedback": (row.get("label") or {}).get("feedback", ""),
                "decision": (row.get("label") or {}).get("decision"),
                "primary_feedback_class": (row.get("feedback_classification") or {}).get("primary_class")
                or (row.get("feedback_classification") or {}).get("class"),
                "feedback_classes": _classification_classes(row.get("feedback_classification") or {}),
                "event_key": None,
            }
            for row in group[:8]
        ]
        counterexamples = []
        for row in rows:
            if row in group:
                continue
            candidate = row.get("candidate") or {}
            if str(candidate.get("reason_key") or "unknown") != reason:
                continue
            counterexamples.append({
                "case_id": row.get("case_id"),
                "feedback_class": (row.get("feedback_classification") or {}).get("class"),
                "feedback_classes": _classification_classes(row.get("feedback_classification") or {}),
                "feedback": (row.get("label") or {}).get("feedback", ""),
            })
            if len(counterexamples) >= 8:
                break
        confidence_values = [_class_confidence(row.get("feedback_classification") or {}, label) for row in group]
        avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
        conditions = {
            "reason_key": reason,
            "observed_match_texts": match_texts[:12],
            "text_constraint": "context-sensitive; literal text alone is insufficient",
            "requires_current_context_check": True,
            "requires_cross_track_safety": True,
        }
        card_body = {
            "schema_version": POLICY_SCHEMA,
            "policy_id": f"POL-{index:03d}-{label}-{_sha((label, reason))[:10]}",
            "snapshot_id": snapshot_id,
            "version": "challenger-20260816",
            "status": "challenger" if len(group) >= 2 else "candidate",
            "feedback_class": label,
            "conditions": conditions,
            "action": ACTION_BY_CLASS[label],
            "examples": examples,
            "counterexamples": counterexamples,
            "source_case_ids": [row.get("case_id") for row in group if row.get("case_id")],
            "decision_counts": dict(decisions),
            "classification_confidence": round(avg_confidence, 3),
            "safety": {
                "can_create_human_approved": False,
                "can_change_autocut_policy": False,
                "can_enter_machine_assisted_draft_after_validation": len(group) >= 2 and label in {"semantic_keep", "semantic_cut", "false_positive"},
            },
            "rollback": "remove this policy card from the Challenger snapshot; never mutate historical cases",
        }
        cards.append(card_body)
    return cards


def write_policy_outputs(cards: list[dict[str, Any]], out_dir: Path, *, snapshot_id: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "policy_cards.json").write_text(json.dumps({
        "schema_version": "policy-card-collection-v1",
        "snapshot_id": snapshot_id,
        "status": "challenger_only",
        "cards": cards,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# 政策卡（Challenger） {snapshot_id}",
        "",
        "> 这些卡片是可读、可回滚的假设，不是自动剪辑授权；不能生成 human_approved，也不能修改 autocut_policy。",
        "",
    ]
    for card in cards:
        lines.extend([
            f"## `{card['policy_id']}` · {card['feedback_class']} · `{card['conditions']['reason_key']}`",
            "",
            f"- 状态：`{card['status']}`；版本：`{card['version']}`",
            f"- 已观察文本：`{', '.join(card['conditions']['observed_match_texts']) or '(空)'}`",
            f"- 行动：`{card['action']}`",
            f"- 案例：{', '.join(card['source_case_ids']) or '无'}",
            f"- 置信度：`{card['classification_confidence']}`",
            "- 反例：" + ("；".join(f"{x.get('case_id')}: {x.get('feedback_class')}" for x in card['counterexamples']) or "当前没有同文本反例"),
            "",
        ])
    (out_dir / "policies.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=Path, required=True, help="JSON array or JSONL records")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    args = parser.parse_args(argv)
    raw = args.records.read_text(encoding="utf-8")
    try:
        doc = json.loads(raw)
        records = list(doc.get("records") or doc) if isinstance(doc, (dict, list)) else []
    except json.JSONDecodeError:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    cards = build_policy_cards(records, snapshot_id=args.snapshot_id)
    write_policy_outputs(cards, args.out_dir, snapshot_id=args.snapshot_id)
    print(json.dumps({"snapshot_id": args.snapshot_id, "policy_count": len(cards), "out_dir": str(args.out_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
