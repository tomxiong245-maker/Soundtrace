#!/usr/bin/env python3
"""Build a read-only, explainable similar-case memory sidecar.

This module is deliberately narrower than ``label_learning_driver.py``.  It
does not predict, train, approve, render, or modify candidates.  For every
candidate it deterministically retrieves the most similar itemized historical
human decisions from a frozen preference snapshot and explains *why* they are
similar.  The result is a review aid only:

* no current ``human_accept`` / ``human_reject`` is created;
* no EDL action or automatic-cut permission is created;
* legacy cases with incomplete audio identity remain visible as historical
  references, but are explicitly marked as non-independent evidence;
* records from the current run, current episode, or demonstrably same source
  audio/bundle are excluded from generic cross-episode memory.  Same-audio
  event reuse continues to live in ``review_event_routes.py`` and requires
  explicit historical-run provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "case-memory-v1"
DEFAULT_MAX_CASES = 3
SAFE_DECISIONS = {"accept", "reject", "human_accept", "human_reject"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _string(value: Any, default: str = "") -> str:
    return str(value).strip() if value not in (None, "") else default


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize_text(value: Any) -> str:
    """Normalize only for retrieval; raw/display evidence is never overwritten."""

    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", "", text).strip()


def candidate_text(candidate: Mapping[str, Any]) -> str:
    direct = _first(
        candidate,
        "proposed_delete_text",
        "proposed_text",
        "filler_token",
        "candidate_text",
        "text",
    )
    if direct is not None:
        return str(direct)
    words = candidate.get("proposed_delete_words") or candidate.get("evidence_words") or []
    if isinstance(words, list):
        text = "".join(
            _string(word.get("text")) for word in words if isinstance(word, Mapping)
        )
        if text:
            return text
    return ""


def duration_seconds(candidate: Mapping[str, Any]) -> float | None:
    direct = _number(candidate.get("duration_seconds"))
    if direct is not None and direct >= 0:
        return direct
    start = _number(candidate.get("start_seconds"))
    end = _number(candidate.get("end_seconds"))
    if start is not None and end is not None and end >= start:
        return end - start
    start_sample = _number(candidate.get("start_sample"))
    end_sample = _number(candidate.get("end_sample"))
    sample_rate = _number(candidate.get("sample_rate_hz"))
    if (
        start_sample is not None
        and end_sample is not None
        and sample_rate
        and sample_rate > 0
        and end_sample >= start_sample
    ):
        return (end_sample - start_sample) / sample_rate
    return None


def duration_bin(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.25:
        return "under_250ms"
    if value < 0.75:
        return "250_750ms"
    if value < 1.5:
        return "750ms_1.5s"
    if value < 4.0:
        return "1.5_4s"
    return "over_4s"


def feature_view(candidate: Mapping[str, Any]) -> dict[str, Any]:
    duration = duration_seconds(candidate)
    return {
        "reason_key": _string(_first(candidate, "reason_key", "candidate_kind"), "unknown"),
        "candidate_kind": _string(candidate.get("candidate_kind"), "unknown"),
        "match_text": normalize_text(candidate_text(candidate)),
        "proposed_text": candidate_text(candidate),
        "filler_subtype": _string(_first(candidate, "filler_subtype", "repetition_signature"), "unknown"),
        "clause_position": _string(candidate.get("clause_position"), "unknown"),
        "duration_seconds": duration,
        "duration_bin": duration_bin(duration),
        "source_track_id": _string(_first(candidate, "source_track_id", "source_track", "track_id"), "unknown"),
        "source_audio_sha256": _string(
            _first(candidate, "source_audio_sha256", "audio_sha256", "raw_audio_sha256"), ""
        )
        or None,
    }


def _logical_episode_id(record: Mapping[str, Any]) -> str:
    raw = _string(record.get("episode_id"))
    if re.fullmatch(r"EP\d+", raw, flags=re.IGNORECASE):
        return raw.upper()
    run_id = _string(record.get("run_id"))
    match = re.match(r"(EP\d+)(?:[-_].*)?$", run_id, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return raw or "UNKNOWN_EPISODE"


def _record_features(record: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(record.get("candidate") or {})
    provenance = record.get("provenance") or {}
    if isinstance(provenance, Mapping):
        candidate.setdefault("source_audio_sha256", provenance.get("source_audio_sha256"))
    return feature_view(candidate)


def _record_decision(record: Mapping[str, Any]) -> str | None:
    raw = _string((record.get("label") or {}).get("decision"))
    if raw == "human_accept":
        raw = "accept"
    elif raw == "human_reject":
        raw = "reject"
    return raw if raw in {"accept", "reject"} else None


def _record_source_bundle_identity(record: Mapping[str, Any]) -> str | None:
    provenance = record.get("provenance") or {}
    value = _string(provenance.get("source_bundle_sha256")) if isinstance(provenance, Mapping) else ""
    return value or None


def _record_audio_identity(record: Mapping[str, Any]) -> str | None:
    provenance = record.get("provenance") or {}
    value = _string(provenance.get("source_audio_sha256")) if isinstance(provenance, Mapping) else ""
    return value or _record_features(record).get("source_audio_sha256")


def _record_run_identity_sha256(record: Mapping[str, Any]) -> str | None:
    provenance = record.get("provenance") or {}
    value = _string(provenance.get("run_identity_sha256")) if isinstance(provenance, Mapping) else ""
    return value or None


def _record_identity_complete(record: Mapping[str, Any]) -> bool:
    features = _record_features(record)
    return bool(
        _logical_episode_id(record) != "UNKNOWN_EPISODE"
        and _record_source_bundle_identity(record)
        and _record_audio_identity(record)
        and features.get("source_track_id") not in (None, "", "unknown")
    )


def _feedback_info(record: Mapping[str, Any]) -> tuple[str, str]:
    classification = record.get("feedback_classification") or {}
    primary = _string(classification.get("primary_class"), "unknown") if isinstance(classification, Mapping) else "unknown"
    label = record.get("label") or {}
    feedback = _string(label.get("feedback")) if isinstance(label, Mapping) else ""
    return primary, feedback


def _source_bundle_sha_from_manifest(manifest: Mapping[str, Any]) -> str | None:
    rows = manifest.get("tracks") or []
    if isinstance(rows, Mapping):
        rows = rows.values()
    members = sorted(
        {
            (
                _string(_first(row, "track_id", "source_key")),
                _string(_first(row, "audio_sha256", "source_audio_sha256", "raw_audio_sha256")),
            )
            for row in rows
            if isinstance(row, Mapping)
            and _string(_first(row, "track_id", "source_key"))
            and _string(_first(row, "audio_sha256", "source_audio_sha256", "raw_audio_sha256"))
        }
    )
    return canonical_sha256(members) if members else None


def _input_audio_sha_by_track(manifest: Mapping[str, Any]) -> dict[str, str]:
    rows = manifest.get("tracks") or []
    if isinstance(rows, Mapping):
        rows = rows.values()
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        track_id = _string(_first(row, "track_id", "source_key"))
        audio_sha = _string(_first(row, "audio_sha256", "source_audio_sha256", "raw_audio_sha256"))
        if track_id and audio_sha:
            result[track_id] = audio_sha
    return result


def _target_context(target_run_dir: str | Path) -> dict[str, Any]:
    target = Path(target_run_dir).expanduser().resolve()
    identity_path = target / "run_identity.json"
    manifest_path = target / "input_manifest.json"
    if not identity_path.is_file() or not manifest_path.is_file():
        raise ValueError("target run must provide run_identity.json and input_manifest.json")
    identity = read_json(identity_path)
    manifest = read_json(manifest_path)
    run_id = _string(identity.get("run_id") or manifest.get("run_id") or target.name)
    episode_id = _logical_episode_id({
        "episode_id": identity.get("episode_id") or manifest.get("episode_id"),
        "run_id": run_id,
    })
    bundle = _source_bundle_sha_from_manifest(manifest)
    audio_by_track = _input_audio_sha_by_track(manifest)
    if not run_id or episode_id == "UNKNOWN_EPISODE" or not bundle or not audio_by_track:
        raise ValueError("target run identity/input manifest is incomplete")
    return {
        "run_dir": str(target),
        "run_id": run_id,
        "episode_id": episode_id,
        "run_identity_sha256": sha256_file(identity_path),
        "input_manifest_sha256": sha256_file(manifest_path),
        "source_bundle_sha256": bundle,
        "source_audio_sha256_by_track": audio_by_track,
    }


def load_snapshot(snapshot_dir: str | Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    raw = Path(snapshot_dir).expanduser().resolve()
    manifest_path = raw if raw.name == "snapshot_manifest.json" else raw / "snapshot_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"snapshot manifest is missing: {manifest_path}")
    root = manifest_path.parent
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != "preference-snapshot-manifest-v1":
        raise ValueError("unsupported preference snapshot schema")
    aggregate_path = root / "aggregated.json"
    if not aggregate_path.is_file():
        raise FileNotFoundError("snapshot aggregated.json is missing")
    expected = (manifest.get("artifacts") or {}).get("aggregated.json")
    if expected and expected != sha256_file(aggregate_path):
        raise ValueError("snapshot aggregated.json SHA mismatch")
    rows = read_json(aggregate_path).get("records") or []
    if not isinstance(rows, list):
        raise ValueError("snapshot records must be an array")
    return root, manifest, [dict(row) for row in rows if isinstance(row, Mapping)]


def _merge_candidates(
    source: Mapping[str, Any],
    overlay: Mapping[str, Any] | None,
    package: Mapping[str, Any] | None,
    target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    overlay_by_id = {
        _string(row.get("candidate_id")): row
        for row in ((overlay or {}).get("candidates") or [])
        if isinstance(row, Mapping) and _string(row.get("candidate_id"))
    }
    package_by_id = {
        _string(row.get("candidate_id")): row
        for row in ((package or {}).get("candidates") or [])
        if isinstance(row, Mapping) and _string(row.get("candidate_id"))
    }
    source_rows = source.get("candidates") or []
    if not isinstance(source_rows, list):
        raise ValueError("candidate source candidates must be an array")
    merged_rows: list[dict[str, Any]] = []
    for source_row in source_rows:
        if not isinstance(source_row, Mapping):
            continue
        candidate = dict(source_row)
        candidate_id = _string(candidate.get("candidate_id"))
        if not candidate_id:
            raise ValueError("candidate source row is missing candidate_id")
        for extra in (overlay_by_id.get(candidate_id), package_by_id.get(candidate_id)):
            if isinstance(extra, Mapping):
                candidate.update({key: value for key, value in extra.items() if value is not None})
        track_id = _string(_first(candidate, "source_track_id", "source_track", "track_id"))
        candidate["source_audio_sha256"] = (target.get("source_audio_sha256_by_track") or {}).get(track_id)
        merged_rows.append(candidate)
    if package_by_id:
        source_ids = {str(row.get("candidate_id")) for row in merged_rows}
        package_ids = set(package_by_id)
        if not package_ids <= source_ids:
            raise ValueError("review package contains a candidate outside the frozen candidate source")
        return [row for row in merged_rows if str(row.get("candidate_id")) in package_ids]
    return merged_rows


def _char_jaccard(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_chars = set(left)
    right_chars = set(right)
    union = left_chars | right_chars
    return len(left_chars & right_chars) / len(union) if union else 0.0


def _match_case(target: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any] | None:
    historical = _record_features(record)
    if target["reason_key"] == "unknown" or target["reason_key"] != historical["reason_key"]:
        return None

    reasons = [f"候选类型同为 {target['reason_key']}"]
    score = 20
    exact_text = bool(target["match_text"] and target["match_text"] == historical["match_text"])
    lexical_overlap = _char_jaccard(str(target["match_text"]), str(historical["match_text"]))
    structural_matches = 0
    if exact_text:
        score += 60
        reasons.append(f"拟删文本均为「{target['proposed_text']}」")
    elif lexical_overlap >= 0.60:
        score += round(20 * lexical_overlap)
        reasons.append(f"拟删文本字符相似度 {lexical_overlap:.0%}")
    for key, label, points in (
        ("filler_subtype", "细分类型", 10),
        ("clause_position", "句内位置", 8),
        ("duration_bin", "时长档", 7),
    ):
        if target[key] != "unknown" and target[key] == historical[key]:
            structural_matches += 1
            score += points
            reasons.append(f"{label}一致（{target[key]}）")
    target_duration = target.get("duration_seconds")
    historical_duration = historical.get("duration_seconds")
    if isinstance(target_duration, (int, float)) and isinstance(historical_duration, (int, float)):
        if abs(float(target_duration) - float(historical_duration)) <= 0.12:
            score += 5
            reasons.append("时长差不超过 120ms")

    if exact_text:
        tier = "exact_text"
    elif lexical_overlap >= 0.60 and structural_matches >= 1:
        tier = "lexical_and_context"
    elif structural_matches >= 2:
        tier = "structural_pattern"
    else:
        return None
    if score < 42:
        return None

    decision = _record_decision(record)
    if decision is None:
        return None
    feedback_class, feedback = _feedback_info(record)
    candidate = record.get("candidate") or {}
    provenance = record.get("provenance") or {}
    return {
        "case_id": _string(record.get("case_id")),
        "episode_id": _logical_episode_id(record),
        "run_id": _string(record.get("run_id")),
        "candidate_id": _string(record.get("candidate_id")),
        "decision": decision,
        "decision_display": "当时接受剪切" if decision == "accept" else "当时保留原音频",
        "reviewer": _string((record.get("label") or {}).get("reviewer")),
        "decided_at": _string((record.get("label") or {}).get("decided_at")),
        "feedback": feedback,
        "feedback_class": feedback_class,
        "historical_proposed_text": _string(candidate.get("proposed_text")),
        "match_tier": tier,
        "similarity_score": min(100, int(score)),
        "matching_reasons": reasons,
        "identity_status": "COMPLETE" if _record_identity_complete(record) else "LEGACY_IDENTITY_INCOMPLETE",
        "source_bundle_sha256": _record_source_bundle_identity(record),
        "source_audio_sha256": _record_audio_identity(record),
        "provenance": {
            "decision_file_relpath": provenance.get("decision_file_relpath") if isinstance(provenance, Mapping) else None,
            "decision_file_sha256": provenance.get("decision_file_sha256") if isinstance(provenance, Mapping) else None,
            "package_file_relpath": provenance.get("package_file_relpath") if isinstance(provenance, Mapping) else None,
            "package_file_sha256": provenance.get("package_file_sha256") if isinstance(provenance, Mapping) else None,
            "review_manifest_sha256": provenance.get("review_manifest_sha256") if isinstance(provenance, Mapping) else None,
        },
    }


def _safe_history_records(records: Iterable[Mapping[str, Any]], target: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    kept: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    target_audio = set((target.get("source_audio_sha256_by_track") or {}).values())
    for row in records:
        if _record_decision(row) is None:
            counts["non_itemized_or_invalid_decision"] += 1
            continue
        if _string(row.get("run_id")) == target["run_id"] or (
            target.get("run_identity_sha256")
            and _record_run_identity_sha256(row) == target["run_identity_sha256"]
        ):
            counts["target_run"] += 1
            continue
        if _logical_episode_id(row) == target["episode_id"]:
            counts["same_episode"] += 1
            continue
        if _record_source_bundle_identity(row) == target["source_bundle_sha256"]:
            counts["same_source_bundle"] += 1
            continue
        if _record_audio_identity(row) in target_audio:
            counts["same_source_audio"] += 1
            continue
        if not _record_identity_complete(row):
            counts["legacy_identity_incomplete_included"] += 1
        kept.append(dict(row))
    return kept, dict(sorted(counts.items()))


def _memory_row(candidate: Mapping[str, Any], history: Iterable[Mapping[str, Any]], max_cases: int) -> dict[str, Any]:
    candidate_id = _string(candidate.get("candidate_id"))
    if not candidate_id:
        raise ValueError("candidate_id is required")
    features = feature_view(candidate)
    matched = [match for record in history if (match := _match_case(features, record)) is not None]
    matched.sort(
        key=lambda row: (-int(row["similarity_score"]), row["case_id"], row["run_id"], row["candidate_id"])
    )
    decision_counts = Counter(row["decision"] for row in matched)
    independent_counts = Counter(
        row["decision"] for row in matched if row["identity_status"] == "COMPLETE"
    )
    if not matched:
        signal = "no_similar_case"
        priority = 0
        summary = "65 条冻结人审案例中没有达到可解释相似度门槛的案例；请按本轮证据独立判断。"
    elif decision_counts["accept"] and decision_counts["reject"]:
        signal = "mixed_historical_memory"
        priority = 3
        summary = "相似历史案例的 accept/reject 有冲突；优先由真人结合本轮上下文和试听裁决。"
    elif decision_counts["reject"]:
        signal = "historical_preserve_memory"
        priority = 3
        summary = "相似历史案例均选择保留；这是复听提示，不是本轮 reject。"
    else:
        signal = "historical_cut_memory"
        priority = 1
        summary = "相似历史案例均接受剪切；这只增强机器辅助草稿/排序的证据说明，本轮仍需真人明确决定。"
    return {
        "candidate_id": candidate_id,
        "candidate_feature_view": features,
        "signal": signal,
        "review_priority": priority,
        "summary": summary,
        "similar_case_count": len(matched),
        "shown_case_count": min(len(matched), max_cases),
        "historical_decision_counts": {"accept": decision_counts["accept"], "reject": decision_counts["reject"]},
        "independent_identity_decision_counts": {
            "accept": independent_counts["accept"],
            "reject": independent_counts["reject"],
        },
        "matches": matched[:max_cases],
        "policy": "reference_and_review_priority_only; never creates a current decision, EDL action or autocut permission",
        "current_decision": None,
        "creates_edl_action": False,
        "creates_autocut_permission": False,
    }


def build_case_memory(
    *,
    snapshot_dir: str | Path,
    candidate_source: str | Path,
    target_run_dir: str | Path,
    candidate_overlay: str | Path | None = None,
    review_package: str | Path | None = None,
    max_cases: int = DEFAULT_MAX_CASES,
) -> dict[str, Any]:
    if max_cases < 1 or max_cases > 20:
        raise ValueError("max_cases must be between 1 and 20")
    snapshot_root, snapshot_manifest, records = load_snapshot(snapshot_dir)
    source_path = Path(candidate_source).expanduser().resolve()
    source = read_json(source_path)
    target = _target_context(target_run_dir)
    if _string(source.get("run_id")) and _string(source.get("run_id")) != target["run_id"]:
        raise ValueError("candidate source run_id does not match target run identity")
    source_episode = _string(source.get("episode_id"))
    if source_episode and _logical_episode_id({"episode_id": source_episode, "run_id": target["run_id"]}) != target["episode_id"]:
        raise ValueError("candidate source episode_id does not match target run identity")
    overlay_path = Path(candidate_overlay).expanduser().resolve() if candidate_overlay else None
    package_path = Path(review_package).expanduser().resolve() if review_package else None
    overlay = read_json(overlay_path) if overlay_path else None
    package = read_json(package_path) if package_path else None
    if package and _string(package.get("run_id")) not in ("", target["run_id"]):
        raise ValueError("review package run_id does not match target run identity")
    target_run_records = [
        row
        for row in records
        if _string(row.get("run_id")) == target["run_id"]
        or (
            target.get("run_identity_sha256")
            and _record_run_identity_sha256(row) == target["run_identity_sha256"]
        )
    ]
    if target_run_records:
        raise ValueError("target run labels are present in the case-memory snapshot; refuse target leakage")
    history, exclusions = _safe_history_records(records, target)
    candidates = _merge_candidates(source, overlay, package, target)
    rows = [_memory_row(candidate, history, max_cases) for candidate in candidates]
    by_id = {row["candidate_id"]: row for row in rows}
    candidate_ids = [str(row["candidate_id"]) for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("case-memory candidates must have unique IDs")
    package_fingerprint = None
    if package:
        package_fingerprint = [
            {
                "candidate_id": candidate.get("candidate_id"),
                "semantic_sha256": candidate.get("semantic_sha256"),
                "source_track_id": candidate.get("source_track_id"),
                "start_sample": candidate.get("start_sample"),
                "end_sample": candidate.get("end_sample"),
            }
            for candidate in package.get("candidates") or []
            if isinstance(candidate, Mapping)
        ]
    document = {
        "schema_version": SCHEMA_VERSION,
        "purpose": "explainable similar historical human cases for review/reference only",
        "episode_id": target["episode_id"],
        "run_id": target["run_id"],
        "target_identity": target,
        "snapshot": {
            "snapshot_id": snapshot_manifest.get("snapshot_id"),
            "snapshot_manifest_sha256": sha256_file(snapshot_root / "snapshot_manifest.json"),
            "aggregated_sha256": sha256_file(snapshot_root / "aggregated.json"),
            "total_snapshot_records": len(records),
            "retrievable_human_decision_records": len(history),
            "exclusions": exclusions,
            "retrieval_rule": "all eligible frozen itemized human decisions are searchable as reference; incomplete identity is visible but never treated as independent cross-audio proof",
        },
        "candidate_input": {
            "path": str(source_path),
            "sha256": sha256_file(source_path),
            "overlay_path": str(overlay_path) if overlay_path else None,
            "overlay_sha256": sha256_file(overlay_path) if overlay_path else None,
            "candidate_count": len(rows),
        },
        "source_review_package_relpath": "review_bundle/review_package.json" if package else None,
        "source_review_package_sha256": sha256_file(package_path) if package_path else None,
        "source_review_manifest_sha256": package.get("review_manifest_sha256") if package else None,
        "candidate_fingerprint": package_fingerprint,
        "candidate_fingerprint_sha256": canonical_sha256(package_fingerprint) if package_fingerprint is not None else None,
        "max_cases_per_candidate": max_cases,
        "candidate_memory": by_id,
        "memory_summary": dict(sorted(Counter(row["signal"] for row in rows).items())),
        "safety": {
            "mutates_candidate_source": False,
            "mutates_review_package": False,
            "creates_current_human_decision": False,
            "creates_edl": False,
            "creates_autocut_permission": False,
            "same_episode_or_audio_history_is_generic_memory": False,
        },
    }
    errors = validate_case_memory(document, package)
    if errors:
        raise ValueError("case-memory document is invalid: " + "; ".join(errors))
    return document


def validate_case_memory(document: Mapping[str, Any], package: Mapping[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported case-memory schema")
    memory = document.get("candidate_memory")
    if not isinstance(memory, Mapping):
        errors.append("case memory has no per-candidate mapping")
        return errors
    if package is not None:
        package_candidates = package.get("candidates") or []
        package_ids = {
            str(row.get("candidate_id")) for row in package_candidates if isinstance(row, Mapping)
        }
        if set(map(str, memory)) != package_ids:
            errors.append("case memory does not cover exactly the review-package candidates")
        if document.get("source_review_manifest_sha256") != package.get("review_manifest_sha256"):
            errors.append("case memory is bound to a different review manifest")
    for candidate_id, row in memory.items():
        if not isinstance(row, Mapping):
            errors.append(f"{candidate_id}: invalid memory row")
            continue
        if row.get("candidate_id") != candidate_id:
            errors.append(f"{candidate_id}: candidate ID mismatch")
        if row.get("current_decision") is not None:
            errors.append(f"{candidate_id}: case memory must not create a current decision")
        if row.get("creates_edl_action") is not False or row.get("creates_autocut_permission") is not False:
            errors.append(f"{candidate_id}: case memory must not authorize EDL/autocut")
        matches = row.get("matches") or []
        if not isinstance(matches, list):
            errors.append(f"{candidate_id}: matches must be an array")
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                errors.append(f"{candidate_id}: invalid match")
                continue
            if match.get("decision") not in {"accept", "reject"}:
                errors.append(f"{candidate_id}: match has invalid historical decision")
            if not match.get("matching_reasons"):
                errors.append(f"{candidate_id}: match is missing explainable reasons")
    safety = document.get("safety") or {}
    for key in ("creates_current_human_decision", "creates_edl", "creates_autocut_permission"):
        if safety.get(key) is not False:
            errors.append(f"case-memory safety flag is invalid: {key}")
    return errors


def write_case_memory(
    *,
    snapshot_dir: str | Path,
    candidate_source: str | Path,
    target_run_dir: str | Path,
    out_path: str | Path,
    candidate_overlay: str | Path | None = None,
    review_package: str | Path | None = None,
    max_cases: int = DEFAULT_MAX_CASES,
    overwrite: bool = False,
) -> Path:
    target = Path(target_run_dir).expanduser().resolve()
    output = Path(out_path).expanduser().resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite case-memory sidecar: {output}")
    if review_package is not None:
        if (target / "review_draft.json").exists():
            raise ValueError("refusing to enrich a review bundle with an active reviewer draft")
        if (target / "human_decisions.json").exists():
            raise ValueError("refusing to enrich a review bundle after human decisions exist")
        bundle = (target / "review_bundle").resolve()
        try:
            output.relative_to(bundle)
        except ValueError as exc:
            raise ValueError("review case-memory output must stay inside target review_bundle") from exc
    document = build_case_memory(
        snapshot_dir=snapshot_dir,
        candidate_source=candidate_source,
        target_run_dir=target,
        candidate_overlay=candidate_overlay,
        review_package=review_package,
        max_cases=max_cases,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--candidate-source", type=Path, required=True)
    parser.add_argument("--target-run-dir", type=Path, required=True)
    parser.add_argument("--candidate-overlay", type=Path)
    parser.add_argument("--review-package", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=DEFAULT_MAX_CASES)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = write_case_memory(
        snapshot_dir=args.snapshot_dir,
        candidate_source=args.candidate_source,
        target_run_dir=args.target_run_dir,
        candidate_overlay=args.candidate_overlay,
        review_package=args.review_package,
        out_path=args.out,
        max_cases=args.max_cases,
        overwrite=args.overwrite,
    )
    document = read_json(output)
    print(json.dumps({
        "status": "PASS",
        "out": str(output),
        "candidate_count": len(document.get("candidate_memory") or {}),
        "memory_summary": document.get("memory_summary") or {},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
