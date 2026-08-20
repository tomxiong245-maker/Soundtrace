#!/usr/bin/env python3
"""Conservative, executable policy guards for future audio-editing runs.

This module deliberately separates two ideas which older scripts conflated:

* a rule may automatically *preserve* a known false positive or force review;
* only an independently promoted ``autocut_policy`` could ever authorize a
  machine deletion.  The current policy is explicitly NOT_APPROVED.

The caller freezes the JSON policy inside a new run, applies it to candidates,
and records the resulting provenance.  Nothing here reads audio, changes an
EDL, or fabricates a human decision.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "production-edit-policy-v1"
APPLICATION_SCHEMA_VERSION = "production-edit-policy-application-v1"
ALLOWED_ACTIONS = {"auto_preserve", "human_review_required"}


def _normalize(value: Any) -> str:
    """Normalize only for policy matching; upstream ASR is never changed."""

    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def load_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid editing policy: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported editing-policy schema")
    if not value.get("policy_id") or not isinstance(value.get("rules"), list):
        raise ValueError("editing policy requires policy_id and rules")
    autocut = value.get("autocut_policy") or {}
    if autocut.get("status") != "NOT_APPROVED":
        # A future approved deletion policy must be evaluated by the dedicated
        # promotion gate, not smuggled in through this guard module.
        raise ValueError("only NOT_APPROVED autocut policies are valid for active guard policies")
    for rule in value["rules"]:
        if not isinstance(rule, dict) or not rule.get("rule_id"):
            raise ValueError("editing policy contains an invalid rule")
        if rule.get("action") not in ALLOWED_ACTIONS:
            raise ValueError(f"editing policy rule has unsupported action: {rule.get('rule_id')}")
    return value


def _candidate_token(candidate: dict[str, Any]) -> str:
    return _normalize(candidate.get("proposed_delete_text") or candidate.get("filler_token"))


def _neighbor(candidate: dict[str, Any], direction: str) -> tuple[str, float | None]:
    lexical = candidate.get("lexical_context") or {}
    row = lexical.get(direction) or {}
    if not isinstance(row, dict):
        return "", None
    gap = row.get("gap_seconds")
    try:
        gap_seconds = float(gap) if gap is not None else None
    except (TypeError, ValueError):
        gap_seconds = None
    return _normalize(row.get("text")), gap_seconds


def _has_latin(value: str) -> bool:
    return bool(re.search(r"[a-z]", value))


def _matches(rule: dict[str, Any], candidate: dict[str, Any]) -> bool:
    kind = str(candidate.get("candidate_kind") or candidate.get("reason_key") or "")
    if kind not in {str(value) for value in rule.get("candidate_kinds") or []}:
        return False
    rule_kind = str(rule.get("kind") or "")
    token = _candidate_token(candidate)
    if rule_kind == "acknowledgement_token":
        return token in {_normalize(value) for value in rule.get("tokens") or []}
    if rule_kind == "sentence_position":
        return str(candidate.get("clause_position") or "unknown") in set(rule.get("positions") or [])
    if rule_kind == "adjacent_compound":
        for pair in rule.get("pairs") or []:
            direction = str(pair.get("direction") or "after")
            neighbor, gap = _neighbor(candidate, direction)
            if (
                token == _normalize(pair.get("token"))
                and neighbor == _normalize(pair.get("neighbor"))
                and gap is not None
                and gap <= float(pair.get("max_gap_seconds", 0.0))
            ):
                return True
        return False
    if rule_kind == "english_fragment":
        if token not in {_normalize(value) for value in rule.get("tokens") or []}:
            return False
        maximum = float(rule.get("max_gap_seconds", 0.0))
        for direction in ("before", "after"):
            neighbor, gap = _neighbor(candidate, direction)
            if neighbor and _has_latin(neighbor) and gap is not None and gap <= maximum:
                return True
        return False
    if rule_kind == "repetition_without_stutter_signature":
        signature = candidate.get("repetition_signature") or {}
        return not bool(signature.get("has_signature"))
    if rule_kind == "high_risk_family":
        return True
    return False


def evaluate_candidate(candidate: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Return a frozen policy result without changing the candidate itself."""

    matches = [rule for rule in policy.get("rules") or [] if _matches(rule, candidate)]
    # Preserve always wins over review, because a preserve guard does not delete
    # content.  Otherwise a review guard wins.  The safe default stays in the
    # existing representative-calibration route.
    preserve = [rule for rule in matches if rule.get("action") == "auto_preserve"]
    review = [rule for rule in matches if rule.get("action") == "human_review_required"]
    if preserve:
        route = "auto_preserve"
        matched = preserve
    elif review:
        route = "human_review_required"
        matched = review
    else:
        route = "machine_calibration_eligible"
        matched = []
    return {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "policy_id": policy["policy_id"],
        "policy_version": policy.get("version"),
        "policy_status": policy.get("status"),
        "autocut_policy": dict(policy.get("autocut_policy") or {}),
        "route": route,
        "matched_rule_ids": [str(rule["rule_id"]) for rule in matched],
        "matched_rule_actions": [str(rule["action"]) for rule in matched],
        "policy_note": (
            "This result cannot create human_accept, auto_cut_eligible, an EDL action, or a delivery decision."
        ),
    }


def apply_policy(candidates: list[dict[str, Any]], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annotate a new in-memory candidate list and return a provenance report."""

    annotated: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    summary = {
        "auto_preserve": 0,
        "human_review_required": 0,
        "machine_calibration_eligible": 0,
        "auto_cut_eligible": 0,
    }
    for original in candidates:
        if not isinstance(original, dict) or not original.get("candidate_id"):
            raise ValueError("policy application requires candidates with candidate_id")
        candidate = dict(original)
        result = evaluate_candidate(candidate, policy)
        candidate["editing_policy"] = result
        annotated.append(candidate)
        route = result["route"]
        summary[route] += 1
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "candidate_kind": candidate.get("candidate_kind"),
                "reason_key": candidate.get("reason_key"),
                **result,
            }
        )
    report = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "policy_id": policy["policy_id"],
        "policy_version": policy.get("version"),
        "policy_status": policy.get("status"),
        "autocut_policy": dict(policy.get("autocut_policy") or {}),
        "summary": summary,
        "candidates": rows,
        "safety": "active guards can preserve or escalate only; current policy never automatically deletes semantic content",
    }
    return annotated, report

