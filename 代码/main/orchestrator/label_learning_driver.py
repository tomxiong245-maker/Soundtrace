#!/usr/bin/env python3
"""Create explainable machine suggestions from itemized human edit labels.

This module is intentionally a *driver*, not a training model and not an
automatic editor.  It reads an immutable preference snapshot plus a new
candidate source, then writes a hash-bound, read-only prediction document.

Every output is one of:

* ``MACHINE_CUT_SUGGESTED`` — historical semantic evidence points to cutting;
* ``MACHINE_PRESERVE_SUGGESTED`` — history points to retaining / a false hit;
* ``HUMAN_REVIEW_REQUIRED`` — evidence is absent, conflicting, or execution
  quality makes the semantic result unsafe to act on.

Suggestions are never human decisions, EDL actions, or autocut permission.
The driver is deliberately conservative: a historic execution-only rejection
does not become a semantic keep rule, and every suggestion remains marked
``requires_human_review=true`` until a separately promoted policy changes
that contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from event_identity import normalize_event_text  # noqa: E402


SCHEMA_VERSION = "label-learning-prediction-v1"
BACKTEST_SCHEMA_VERSION = "label-learning-backtest-v1"
EVIDENCE_SCHEMA_VERSION = "label-learning-evidence-v1"
TARGET_INTEGRITY_SCHEMA_VERSION = "label-learning-target-integrity-v1"
DRIVER_ID = "transparent-pattern-evidence-v1"
MACHINE_CUT = "MACHINE_CUT_SUGGESTED"
MACHINE_PRESERVE = "MACHINE_PRESERVE_SUGGESTED"
HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"
MIN_INDEPENDENT_SOURCE_BUNDLE_COUNT = 3
MIN_INDEPENDENT_EVENT_GROUP_COUNT = 3
# NOTE 2026-08-17: 项目负责人明确指令删除"≥3 期节目 / ≥2 位独立审核人"跨期泛化 blocker
# （用户 2026-08-17："第六条也不用管，从规则中去掉"）。source_bundle / event_group
# 身份完整性门保留，这两个是防泄漏门而非外部资源门。删除的常量原为
#   MIN_CROSS_EPISODE_COUNT = 3
#   MIN_INDEPENDENT_REVIEWER_COUNT = 2
# 相关 blocker 分支在 required_evidence_failures 与 generalization_blockers 中同步移除。


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


def driver_source_sha256() -> str:
    """Hash the exact driver source used for a report.

    Reports without this value are historical evidence only: a later edit to
    the driver must never silently inherit an older report's predictions.
    """

    return sha256_file(Path(__file__).resolve())


def _string(value: Any, default: str = "") -> str:
    return str(value).strip() if value not in (None, "") else default


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ""):
            return value
    return None


def candidate_text(candidate: Mapping[str, Any]) -> str:
    direct = _first(candidate, "proposed_delete_text", "proposed_text", "filler_token", "candidate_text", "text")
    if direct is not None:
        return str(direct)
    words = candidate.get("proposed_delete_words") or candidate.get("evidence_words") or []
    if isinstance(words, list):
        text = "".join(_string(word.get("text")) for word in words if isinstance(word, Mapping))
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
    if start_sample is not None and end_sample is not None and sample_rate and sample_rate > 0 and end_sample >= start_sample:
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


def _feedback_info(record: Mapping[str, Any]) -> tuple[str, set[str]]:
    raw = record.get("feedback_classification")
    if not isinstance(raw, Mapping):
        return "unknown", set()
    primary = _string(_first(raw, "primary_class", "class"), "unknown")
    classes = {str(item) for item in raw.get("classes") or [] if item not in (None, "")}
    if primary and primary != "unknown":
        classes.add(primary)
    return primary, classes


def feature_view(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return a small, stable feature view; raw/display text is never rewritten."""

    text = candidate_text(candidate)
    reason_key = _string(_first(candidate, "reason_key", "candidate_kind"), "unknown")
    clause = _string(candidate.get("clause_position"), "unknown")
    subtype = _string(_first(candidate, "filler_subtype", "repetition_signature"), "unknown")
    artifact = candidate.get("artifact_risk")
    artifact_verdict = _string(artifact.get("verdict") if isinstance(artifact, Mapping) else None, "unknown")
    source_track = _string(_first(candidate, "source_track_id", "source_track", "track_id"), "unknown")
    source_audio_sha = _string(_first(candidate, "source_audio_sha256", "audio_sha256", "raw_audio_sha256"), "")
    return {
        "reason_key": reason_key,
        "candidate_kind": _string(candidate.get("candidate_kind"), reason_key),
        "match_text": normalize_event_text(text),
        "proposed_text": text,
        "filler_subtype": subtype,
        "clause_position": clause,
        "duration_seconds": duration_seconds(candidate),
        "duration_bin": duration_bin(duration_seconds(candidate)),
        "source_track_id": source_track,
        "source_audio_sha256": source_audio_sha or None,
        "safety_status": _string(candidate.get("safety_status"), "unknown"),
        "artifact_risk_verdict": artifact_verdict,
        "has_lexical_context": bool(candidate.get("lexical_context") or candidate.get("text_tracks")),
    }


def missing_feature_names(features: Mapping[str, Any]) -> list[str]:
    """Name missing evidence instead of quietly treating it as a negative.

    The driver is intentionally transparent: lack of an audio identity, word
    context, or boundary-quality result must be visible to the next Agent and
    must never be mistaken for evidence that a cut is safe.
    """

    required = (
        "reason_key",
        "match_text",
        "source_track_id",
        "source_audio_sha256",
        "clause_position",
        "duration_seconds",
        "artifact_risk_verdict",
    )
    missing: list[str] = []
    for key in required:
        value = features.get(key)
        if value in (None, "", "unknown"):
            missing.append(key)
    if not features.get("has_lexical_context"):
        missing.append("lexical_context")
    return missing


def _eligible_record(record: Mapping[str, Any]) -> bool:
    quality = record.get("quality") or {}
    decision = _string((record.get("label") or {}).get("decision"))
    return bool(quality.get("rule_analysis_eligible")) and decision in {"accept", "reject"}


def _record_features(record: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(record.get("candidate") or {})
    provenance = record.get("provenance") or {}
    if isinstance(provenance, Mapping):
        candidate.setdefault("source_audio_sha256", provenance.get("source_audio_sha256"))
    return feature_view(candidate)


def _logical_episode_id(record: Mapping[str, Any]) -> str:
    """Return a stable episode grouping even for early records named `runs`.

    The original EP03 importer stored `episode_id=runs` for part of the
    evidence set.  Its run IDs still carry the true episode prefix, so use
    that only when the explicit episode value is not an episode-like ID.
    """

    raw = _string(record.get("episode_id"))
    if re.fullmatch(r"EP\d+", raw, flags=re.IGNORECASE):
        return raw.upper()
    run_id = _string(record.get("run_id"))
    match = re.match(r"(EP\d+)(?:[-_].*)?$", run_id, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return raw or "UNKNOWN_EPISODE"


def _record_reviewer(record: Mapping[str, Any]) -> str:
    label = record.get("label") or {}
    if isinstance(label, Mapping):
        return _string(label.get("reviewer"), "UNKNOWN_REVIEWER")
    return "UNKNOWN_REVIEWER"


def _record_audio_identity(record: Mapping[str, Any]) -> str | None:
    provenance = record.get("provenance") or {}
    if isinstance(provenance, Mapping):
        value = _string(provenance.get("source_audio_sha256"))
        if value:
            return value
    return _record_features(record).get("source_audio_sha256")


def _record_source_bundle_identity(record: Mapping[str, Any]) -> str | None:
    provenance = record.get("provenance") or {}
    if isinstance(provenance, Mapping):
        value = _string(provenance.get("source_bundle_sha256"))
        if value:
            return value
    return None


def _record_run_identity_sha256(record: Mapping[str, Any]) -> str | None:
    provenance = record.get("provenance") or {}
    if isinstance(provenance, Mapping):
        value = _string(provenance.get("run_identity_sha256"))
        if value:
            return value
    return None


def _record_identity_complete(record: Mapping[str, Any]) -> bool:
    """Whether one historical label may support cross-program pattern evidence.

    A historical decision remains useful as an archived case when this is
    false.  It is simply not safe to count it as an independent training vote:
    without a source bundle, track identity and canonical episode it could be a
    boundary variant of the very audio now being predicted.
    """

    quality = record.get("quality") or {}
    marked = quality.get("generalization_eligible")
    if marked is False:
        return False
    features = _record_features(record)
    return bool(
        _logical_episode_id(record) != "UNKNOWN_EPISODE"
        and _record_source_bundle_identity(record)
        and _record_audio_identity(record)
        and features.get("source_track_id") not in (None, "", "unknown")
        and features.get("match_text")
        and duration_seconds(features) is not None
    )


def _record_event_group_id(record: Mapping[str, Any]) -> str | None:
    """Stable grouping for one source event family inside one input bundle.

    The full interval-overlap event router remains the authority for exact
    same-audio reuse.  This group is deliberately coarser and is used only to
    stop several re-runs of the same source bundle from inflating support for a
    generic text pattern.
    """

    if not _record_identity_complete(record):
        return None
    features = _record_features(record)
    return canonical_sha256({
        "bundle": _record_source_bundle_identity(record),
        "audio": _record_audio_identity(record),
        "track": features.get("source_track_id"),
        "reason_key": features.get("reason_key"),
        "match_text": features.get("match_text"),
    })[:20]


def _source_bundle_sha_from_manifest(manifest: Mapping[str, Any] | None) -> str | None:
    if not isinstance(manifest, Mapping):
        return None
    rows = manifest.get("tracks") or []
    if isinstance(rows, Mapping):
        rows = rows.values()
    members = sorted(
        {
            (_string(_first(row, "track_id", "source_key")), _string(_first(row, "audio_sha256", "source_audio_sha256", "raw_audio_sha256")))
            for row in rows
            if isinstance(row, Mapping)
            and _string(_first(row, "track_id", "source_key"))
            and _string(_first(row, "audio_sha256", "source_audio_sha256", "raw_audio_sha256"))
        }
    )
    return canonical_sha256(members) if members else None


def _read_target_context(
    *,
    source: Mapping[str, Any],
    input_manifest: Mapping[str, Any] | None,
    target_run_dir: str | Path | None,
    target_run_id: str | None,
) -> dict[str, Any]:
    """Load the target identity used to exclude same-audio learning leakage."""

    identity: dict[str, Any] = {}
    target_path = Path(target_run_dir).expanduser().resolve() if target_run_dir else None
    manifest = dict(input_manifest or {})
    if target_path:
        identity_path = target_path / "run_identity.json"
        manifest_path = target_path / "input_manifest.json"
        if not identity_path.is_file() or not manifest_path.is_file():
            raise ValueError("target run must provide run_identity.json and input_manifest.json")
        identity = read_json(identity_path)
        manifest = read_json(manifest_path)
    run_id = _string(target_run_id or identity.get("run_id") or source.get("run_id"))
    episode_id = _logical_episode_id({
        "episode_id": identity.get("episode_id") or source.get("episode_id"),
        "run_id": run_id,
    })
    if not run_id or episode_id == "UNKNOWN_EPISODE":
        raise ValueError("target run identity is incomplete")
    source_episode = _string(source.get("episode_id"))
    if source_episode and _logical_episode_id({"episode_id": source_episode, "run_id": run_id}) != episode_id:
        raise ValueError("candidate source episode does not match target run identity")
    source_run = _string(source.get("run_id"))
    if source_run and source_run != run_id:
        raise ValueError("candidate source run_id does not match target run identity")
    bundle = _source_bundle_sha_from_manifest(manifest)
    audio_by_track = _input_audio_sha_by_track(manifest)
    if not bundle or not audio_by_track:
        raise ValueError("target input manifest lacks complete per-track audio identity")
    return {
        "run_id": run_id,
        "episode_id": episode_id,
        "run_identity_sha256": sha256_file(target_path / "run_identity.json") if target_path else None,
        "input_manifest_sha256": sha256_file(target_path / "input_manifest.json") if target_path else None,
        "source_bundle_sha256": bundle,
        "source_audio_sha256_by_track": audio_by_track,
        "target_run_dir": str(target_path) if target_path else None,
    }


def target_integrity_document(target_run_dir: str | Path) -> dict[str, Any]:
    """Return a read-only hash map for a target run.

    The map intentionally includes the review package and draft when they
    exist.  A shadow run may only write outside the target run; callers can
    compare two maps to prove that the frozen review work was untouched.
    """

    target = Path(target_run_dir).expanduser().resolve()
    identity_path = target / "run_identity.json"
    manifest_path = target / "input_manifest.json"
    if not identity_path.is_file() or not manifest_path.is_file():
        raise ValueError("target run must provide run_identity.json and input_manifest.json")
    identity = read_json(identity_path)
    target_context = _read_target_context(
        source={
            "episode_id": identity.get("episode_id"),
            "run_id": identity.get("run_id"),
        },
        input_manifest=None,
        target_run_dir=target,
        target_run_id=_string(identity.get("run_id")),
    )
    relpaths = (
        "run_identity.json",
        "input_manifest.json",
        "state.json",
        "candidates/candidate_source.json",
        "calibration_source.json",
        "all_candidates.json",
        "review_bundle/review_package.json",
        "review_bundle/index.html",
        "review_draft.json",
        "human_decisions.json",
    )
    artifacts: dict[str, dict[str, Any]] = {}
    for relpath in relpaths:
        path = target / relpath
        artifacts[relpath] = {
            "exists": path.is_file(),
            "sha256": sha256_file(path) if path.is_file() else None,
        }
    return {
        "schema_version": TARGET_INTEGRITY_SCHEMA_VERSION,
        "driver_source_sha256": driver_source_sha256(),
        "target_identity": target_context,
        "artifacts": artifacts,
    }


def _input_audio_sha_by_track(input_manifest: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(input_manifest, Mapping):
        return {}
    result: dict[str, str] = {}
    tracks = input_manifest.get("tracks") or []
    if isinstance(tracks, Mapping):
        tracks = tracks.values()
    for row in tracks:
        if not isinstance(row, Mapping):
            continue
        track_id = _string(_first(row, "track_id", "id"))
        audio_sha = _string(_first(row, "audio_sha256", "source_audio_sha256", "raw_audio_sha256"))
        if track_id and audio_sha:
            result[track_id] = audio_sha
    return result


def _merge_candidate_overlays(
    source: Mapping[str, Any],
    overlay: Mapping[str, Any] | None,
    input_manifest: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    overlay_by_id = {
        _string(item.get("candidate_id")): item
        for item in ((overlay or {}).get("candidates") or [])
        if isinstance(item, Mapping) and _string(item.get("candidate_id"))
    }
    input_audio_by_track = _input_audio_sha_by_track(input_manifest)
    rows: list[dict[str, Any]] = []
    for item in source.get("candidates") or []:
        if not isinstance(item, Mapping):
            continue
        merged = dict(item)
        extra = overlay_by_id.get(_string(item.get("candidate_id")))
        if isinstance(extra, Mapping):
            merged.update({key: value for key, value in extra.items() if value is not None})
        track_id = _string(_first(merged, "source_track_id", "source_track", "track_id"))
        if track_id and not _string(merged.get("source_audio_sha256")):
            merged["source_audio_sha256"] = input_audio_by_track.get(track_id)
        rows.append(merged)
    return rows


def load_snapshot(snapshot_dir: str | Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = Path(snapshot_dir).expanduser().resolve()
    manifest_path = path if path.name == "snapshot_manifest.json" else path / "snapshot_manifest.json"
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
    records = read_json(aggregate_path).get("records") or []
    if not isinstance(records, list):
        raise ValueError("snapshot records must be an array")
    return root, manifest, [dict(record) for record in records if isinstance(record, Mapping)]


def _match_tier(target: Mapping[str, Any], historical: Mapping[str, Any]) -> str | None:
    if target["reason_key"] == "unknown" or target["reason_key"] != historical["reason_key"]:
        return None
    if target["match_text"] and target["match_text"] == historical["match_text"]:
        return "exact_text"
    structural_keys = ("filler_subtype", "clause_position", "duration_bin")
    if all(target[key] != "unknown" and target[key] == historical[key] for key in structural_keys):
        return "structural_pattern"
    return None


def _semantic_direction(record: Mapping[str, Any]) -> tuple[str | None, bool, str]:
    """Return direction, execution-warning, feedback class.

    A reject caused solely by a click / bad boundary is deliberately not a
    semantic preserve vote.  An accept with execution feedback can still be a
    semantic cut reference, but the execution warning keeps the next case in
    human review.
    """

    decision = _string((record.get("label") or {}).get("decision"))
    primary, classes = _feedback_info(record)
    execution_warning = "execution_issue" in classes
    # A reject with no explanation is not reliable evidence that this type of
    # language should be retained.  It may be a transcription failure, a bad
    # preview, an accidental click, or a decision whose rationale was simply
    # not saved.  Only an explicit semantic-retain / false-positive note may
    # contribute a generic preserve vote.
    if decision == "accept":
        return "cut", execution_warning, primary
    if decision == "reject":
        if classes & {"false_positive", "semantic_keep"}:
            return "preserve", execution_warning, primary
        return None, execution_warning or bool(classes & {"asr_error", "execution_issue"}), primary
    return None, execution_warning, primary


def _evidence_rows(target: Mapping[str, Any], records: Iterable[Mapping[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    tiers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        tier = _match_tier(target, _record_features(record))
        if tier:
            tiers[tier].append(dict(record))
    if tiers.get("exact_text"):
        return "exact_text", tiers["exact_text"]
    if tiers.get("structural_pattern"):
        return "structural_pattern", tiers["structural_pattern"]
    return None, []


def _prediction_for_candidate(candidate: Mapping[str, Any], records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    candidate_id = _string(candidate.get("candidate_id"))
    if not candidate_id:
        raise ValueError("candidate_id is required")
    features = feature_view(candidate)
    tier, evidence = _evidence_rows(features, records)
    semantic: list[tuple[dict[str, Any], str, bool, str]] = []
    execution_only: list[dict[str, Any]] = []
    for row in evidence:
        direction, execution_warning, feedback_class = _semantic_direction(row)
        if direction is None:
            execution_only.append(row)
        else:
            semantic.append((row, direction, execution_warning, feedback_class))
    directions = {direction for _, direction, _, _ in semantic}
    execution_warning = bool(execution_only) or any(flag for _, _, flag, _ in semantic)
    matched_cases = [
        {
            "case_id": row.get("case_id"),
            "episode_id": _logical_episode_id(row),
            "run_id": row.get("run_id"),
            "source_bundle_sha256": _record_source_bundle_identity(row),
            "event_group_id": _record_event_group_id(row),
            "decision": (row.get("label") or {}).get("decision"),
            "feedback_class": _feedback_info(row)[0],
        }
        for row in evidence
    ]
    votes = Counter(direction for _, direction, _, _ in semantic)
    unique_cases = {str(row.get("case_id")) for row in evidence if row.get("case_id")}
    unique_runs = {str(row.get("run_id")) for row in evidence if row.get("run_id")}
    unique_episodes = {_logical_episode_id(row) for row in evidence}
    unique_source_bundles = {
        value for row in evidence if (value := _record_source_bundle_identity(row))
    }
    unique_event_groups = {
        value for row in evidence if (value := _record_event_group_id(row))
    }
    unique_reviewers = {
        value for row in evidence if (value := _record_reviewer(row)) and value != "UNKNOWN_REVIEWER"
    }
    common = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "feature_view": features,
        "match_tier": tier or "none",
        "matched_cases": matched_cases,
        "matched_case_count": len(matched_cases),
        "semantic_vote_counts": {"cut": votes["cut"], "preserve": votes["preserve"]},
        "independent_case_count": len(unique_cases),
        "independent_run_count": len(unique_runs),
        "independent_episode_count": len(unique_episodes),
        "independent_source_bundle_count": len(unique_source_bundles),
        "independent_event_group_count": len(unique_event_groups),
        "independent_reviewer_count": len(unique_reviewers),
        "evidence_scope": "cross_program" if len(unique_source_bundles) >= MIN_INDEPENDENT_SOURCE_BUNDLE_COUNT else "insufficient_cross_program_evidence",
        "missing_features": missing_feature_names(features),
        "execution_warning": execution_warning,
        "creates_human_decision": False,
        "creates_edl_action": False,
        "creates_autocut_permission": False,
        "requires_human_review": True,
    }
    if candidate.get("_label_learning_identity_status") not in (None, "COMPLETE"):
        return {
            **common,
            "machine_label": HUMAN_REVIEW,
            "confidence": "none",
            "review_priority": 4,
            "reason": "target candidate lacks a verified physical-track audio identity; do not apply historical pattern evidence",
        }
    if features["artifact_risk_verdict"] in {"BLOCK", "HUMAN_REVIEW"}:
        return {
            **common,
            "machine_label": HUMAN_REVIEW,
            "confidence": "none",
            "review_priority": 4,
            "reason": "current snapped boundary has an execution/artifact risk; do not infer a safe edit from semantic history",
        }
    if tier is None:
        return {
            **common,
            "machine_label": HUMAN_REVIEW,
            "confidence": "none",
            "review_priority": 3,
            "reason": "no sufficiently similar historical case; do not generalize by candidate family alone",
        }
    if not directions:
        return {
            **common,
            "machine_label": HUMAN_REVIEW,
            "confidence": "none",
            "review_priority": 4,
            "reason": "matched history only describes execution quality; semantic keep/cut cannot be inferred",
        }
    if len(directions) > 1:
        return {
            **common,
            "machine_label": HUMAN_REVIEW,
            "confidence": "none",
            "review_priority": 4,
            "reason": "historical semantic decisions conflict; human must resolve the pattern",
        }
    direction = next(iter(directions))
    required_evidence_failures = []
    if len(unique_event_groups) < MIN_INDEPENDENT_EVENT_GROUP_COUNT:
        required_evidence_failures.append(f"event_groups={len(unique_event_groups)} < {MIN_INDEPENDENT_EVENT_GROUP_COUNT}")
    if len(unique_source_bundles) < MIN_INDEPENDENT_SOURCE_BUNDLE_COUNT:
        required_evidence_failures.append(f"source_bundles={len(unique_source_bundles)} < {MIN_INDEPENDENT_SOURCE_BUNDLE_COUNT}")
    # NOTE 2026-08-17: 已按用户指令移除 episodes / reviewers 门。
    if execution_warning:
        required_evidence_failures.append("execution_or_asr_warning")
    if required_evidence_failures:
        return {
            **common,
            "machine_label": HUMAN_REVIEW,
            "confidence": "none",
            "review_priority": 3,
            "reason": "unanimous historical direction is not yet independently validated: " + "; ".join(required_evidence_failures),
        }
    label = MACHINE_CUT if direction == "cut" else MACHINE_PRESERVE
    return {
        **common,
        "machine_label": label,
        "confidence": "medium",
        "review_priority": 1,
        "reason": (
            f"{tier} historical semantic evidence is unanimous for {direction} across independently identified source bundles; "
            "this is a machine suggestion only and still requires human review"
        ),
    }


def predict_document(
    *,
    snapshot_dir: str | Path,
    candidate_source: str | Path,
    candidate_overlay: str | Path | None = None,
    input_manifest: str | Path | None = None,
    target_run_dir: str | Path | None = None,
    exclude_episodes: Iterable[str] = (),
    exclude_runs: Iterable[str] = (),
    target_run_id: str | None = None,
) -> dict[str, Any]:
    snapshot_root, snapshot_manifest, all_records = load_snapshot(snapshot_dir)
    source_path = Path(candidate_source).expanduser().resolve()
    source = read_json(source_path)
    overlay_path = Path(candidate_overlay).expanduser().resolve() if candidate_overlay else None
    overlay = read_json(overlay_path) if overlay_path else None
    input_manifest_path = Path(input_manifest).expanduser().resolve() if input_manifest else None
    input_manifest_document = read_json(input_manifest_path) if input_manifest_path else None
    target = _read_target_context(
        source=source,
        input_manifest=input_manifest_document,
        target_run_dir=target_run_dir,
        target_run_id=target_run_id,
    )
    excluded_episode_set = {
        _logical_episode_id({"episode_id": value, "run_id": ""})
        for value in exclude_episodes if str(value)
    }
    excluded_episode_set.add(target["episode_id"])
    requested_excluded_runs = {str(value) for value in exclude_runs if str(value)}
    requested_excluded_runs.add(target["run_id"])
    same_target_run_records = [
        row for row in all_records
        if str(row.get("run_id")) == target["run_id"]
        or (
            target.get("run_identity_sha256")
            and _record_run_identity_sha256(row) == target["run_identity_sha256"]
        )
    ]
    if same_target_run_records:
        raise ValueError("target run labels are present in the learning snapshot; refuse target leakage")
    incomplete_records = [row for row in all_records if _eligible_record(row) and not _record_identity_complete(row)]
    same_episode_records = [
        row for row in all_records
        if _eligible_record(row) and _logical_episode_id(row) == target["episode_id"]
    ]
    same_bundle_records = [
        row for row in all_records
        if _eligible_record(row) and _record_source_bundle_identity(row) == target["source_bundle_sha256"]
    ]
    records = [
        row for row in all_records
        if _eligible_record(row)
        and _record_identity_complete(row)
        and _logical_episode_id(row) not in excluded_episode_set
        and str(row.get("run_id")) not in requested_excluded_runs
        and _record_source_bundle_identity(row) != target["source_bundle_sha256"]
    ]
    candidates = _merge_candidate_overlays(source, overlay, input_manifest_document)
    for candidate in candidates:
        track_id = _string(_first(candidate, "source_track_id", "source_track", "track_id"))
        candidate["source_audio_sha256"] = target["source_audio_sha256_by_track"].get(track_id)
        candidate["_label_learning_identity_status"] = (
            "COMPLETE" if candidate.get("source_audio_sha256") else "TARGET_TRACK_IDENTITY_MISSING"
        )
    predictions = [_prediction_for_candidate(candidate, records) for candidate in candidates]
    counts = Counter(row["machine_label"] for row in predictions)
    return {
        "schema_version": SCHEMA_VERSION,
        "driver_id": DRIVER_ID,
        "driver_source_sha256": driver_source_sha256(),
        "snapshot": {
            "snapshot_id": snapshot_manifest.get("snapshot_id"),
            "snapshot_manifest_sha256": sha256_file(snapshot_root / "snapshot_manifest.json"),
            "aggregated_sha256": sha256_file(snapshot_root / "aggregated.json"),
            "eligible_records_used": len(records),
            "excluded_episode_ids": sorted(excluded_episode_set),
            "excluded_run_ids": sorted(requested_excluded_runs),
            "invalid_legacy_identity_record_count": len(incomplete_records),
            "excluded_same_episode_count": len(same_episode_records),
            "excluded_same_source_bundle_count": len(same_bundle_records),
        },
        "learning_status": "SHADOW_PATTERN_EVIDENCE_ONLY",
        "target_identity": target,
        "leakage_audit": {
            "target_run_records_found": len(same_target_run_records),
            "same_episode_records_excluded": len(same_episode_records),
            "same_source_bundle_records_excluded": len(same_bundle_records),
            "legacy_identity_incomplete_records_excluded": len(incomplete_records),
            "rule": "same target episode/source bundle never contributes a generic pattern vote",
        },
        "candidate_input": {
            "path": str(source_path),
            "sha256": sha256_file(source_path),
            "overlay_path": str(overlay_path) if overlay_path else None,
            "overlay_sha256": sha256_file(overlay_path) if overlay_path else None,
            "input_manifest_path": str(input_manifest_path) if input_manifest_path else None,
            "input_manifest_sha256": sha256_file(input_manifest_path) if input_manifest_path else None,
            "episode_id": source.get("episode_id"),
            "run_id": target["run_id"],
            "target_run_identity_sha256": target.get("run_identity_sha256"),
            "target_input_manifest_sha256": target.get("input_manifest_sha256"),
            "candidate_count": len(predictions),
        },
        "prediction_counts": dict(sorted(counts.items())),
        "policy": {
            "machine_suggestions_only": True,
            "all_suggestions_require_human_review": True,
            "never_creates_human_decision": True,
            "never_creates_edl": True,
            "never_creates_autocut_permission": True,
            "autocut_policy": "NOT_APPROVED",
        },
        "predictions": predictions,
    }


def _record_candidate(record: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(record.get("candidate") or {})
    candidate.setdefault("candidate_id", record.get("case_id") or record.get("candidate_id"))
    candidate.setdefault("reason_key", (record.get("candidate") or {}).get("reason_key"))
    provenance = record.get("provenance") or {}
    if isinstance(provenance, Mapping):
        candidate.setdefault("source_audio_sha256", provenance.get("source_audio_sha256"))
    candidate.setdefault("source_track_id", _first(candidate, "source_track_id", "source_track", "track_id"))
    return candidate


def backtest_document(*, snapshot_dir: str | Path) -> dict[str, Any]:
    snapshot_root, snapshot_manifest, records = load_snapshot(snapshot_dir)
    eligible = [row for row in records if _eligible_record(row)]
    episodes = sorted({_logical_episode_id(row) for row in eligible})
    if len(episodes) < 2:
        raise ValueError("episode-level backtest requires labels from at least two episodes")
    folds: list[dict[str, Any]] = []
    complete_records = [row for row in eligible if _record_identity_complete(row)]
    incomplete_records = [row for row in eligible if not _record_identity_complete(row)]
    for held_out in episodes:
        holdout = [row for row in eligible if _logical_episode_id(row) == held_out]
        held_audio_ids = {_record_audio_identity(row) for row in holdout if _record_audio_identity(row)}
        held_bundle_ids = {
            _record_source_bundle_identity(row)
            for row in holdout
            if _record_source_bundle_identity(row)
        }
        training = [
            row for row in complete_records
            if _logical_episode_id(row) != held_out
            and _record_audio_identity(row) not in held_audio_ids
            and _record_source_bundle_identity(row) not in held_bundle_ids
        ]
        training_cases = {str(row.get("case_id")) for row in training}
        held_cases = {str(row.get("case_id")) for row in holdout}
        if training_cases & held_cases:
            raise ValueError("case leakage detected between training and holdout")
        predicted = []
        for row in holdout:
            candidate = _record_candidate(row)
            candidate["_label_learning_identity_status"] = (
                "COMPLETE" if _record_identity_complete(row) else "LEGACY_IDENTITY_INCOMPLETE"
            )
            predicted.append(_prediction_for_candidate(candidate, training))
        directed = 0
        correct = 0
        harmful = 0
        review_required = 0
        for record, prediction in zip(holdout, predicted):
            actual = _string((record.get("label") or {}).get("decision"))
            label = prediction["machine_label"]
            if label == HUMAN_REVIEW:
                review_required += 1
            else:
                directed += 1
                expected = "accept" if label == MACHINE_CUT else "reject"
                if actual == expected:
                    correct += 1
                else:
                    harmful += 1
        folds.append({
            "held_out_episode_id": held_out,
            "training_episode_ids": sorted({_logical_episode_id(row) for row in training}),
            "training_record_count": len(training),
            "holdout_record_count": len(holdout),
            "holdout_identity_incomplete_count": sum(
                1 for row in holdout if not _record_identity_complete(row)
            ),
            "case_id_overlap": sorted(training_cases & held_cases),
            "held_out_source_audio_sha256": sorted(held_audio_ids),
            "held_out_source_bundle_sha256": sorted(held_bundle_ids),
            "machine_suggestion_count": directed,
            "human_review_required_count": review_required,
            "suggestion_correct_count": correct,
            "harmful_suggestion_count": harmful,
            "suggestion_precision": (correct / directed) if directed else None,
            "predictions": predicted,
        })
    directed_total = sum(fold["machine_suggestion_count"] for fold in folds)
    correct_total = sum(fold["suggestion_correct_count"] for fold in folds)
    harmful_total = sum(fold["harmful_suggestion_count"] for fold in folds)
    total = sum(fold["holdout_record_count"] for fold in folds)
    distinct_reviewers = sorted({_record_reviewer(row) for row in eligible if _record_reviewer(row) != "UNKNOWN_REVIEWER"})
    audio_identity_coverage = sum(1 for row in eligible if _record_audio_identity(row))
    source_bundle_identity_coverage = sum(
        1 for row in eligible if _record_source_bundle_identity(row)
    )
    generalization_blockers: list[str] = []
    # NOTE 2026-08-17: 已按用户指令移除 episodes / independent_reviewers 门；
    # 保留 source_audio / source_bundle 身份覆盖门（防泄漏）。
    if audio_identity_coverage < len(eligible):
        generalization_blockers.append(
            f"source_audio_sha256 coverage={audio_identity_coverage}/{len(eligible)}; legacy records cannot prove cross-audio independence"
        )
    if source_bundle_identity_coverage < len(eligible):
        generalization_blockers.append(
            f"source_bundle_sha256 coverage={source_bundle_identity_coverage}/{len(eligible)}; same-source reruns cannot be excluded safely"
        )
    quality_status = (
        "INSUFFICIENT_DATA_FOR_CROSS_EPISODE_GENERALIZATION"
        if generalization_blockers
        else "CROSS_EPISODE_CHALLENGER_ONLY"
    )
    raw_precision = (correct_total / directed_total) if directed_total else None
    return {
        "schema_version": BACKTEST_SCHEMA_VERSION,
        "driver_id": DRIVER_ID,
        "driver_source_sha256": driver_source_sha256(),
        "snapshot": {
            "snapshot_id": snapshot_manifest.get("snapshot_id"),
            "snapshot_manifest_sha256": sha256_file(snapshot_root / "snapshot_manifest.json"),
            "eligible_record_count": len(eligible),
        },
        "method": {
            "split": "leave_one_episode_out",
            "forbidden": ["held-out episode labels", "held-out source-audio labels", "same case_id", "machine labels as truth"],
            "note": "A zero-suggestion fold is a conservative result, not a failed test or a quality pass.",
        },
        "data_quality": {
            "status": quality_status,
            "episode_ids": episodes,
            "independent_reviewer_ids": distinct_reviewers,
            "source_audio_sha256_coverage": {
                "records_with_identity": audio_identity_coverage,
                "eligible_records": len(eligible),
            },
            "source_bundle_sha256_coverage": {
                "records_with_identity": source_bundle_identity_coverage,
                "eligible_records": len(eligible),
            },
            "complete_identity_record_count": len(complete_records),
            "legacy_identity_incomplete_record_count": len(incomplete_records),
            "blockers": generalization_blockers,
            "rule": "diagnostic results are not a claim of cross-program accuracy until every blocker is cleared",
        },
        "summary": {
            "episode_fold_count": len(folds),
            "holdout_record_count": total,
            "machine_suggestion_count": directed_total,
            "human_review_required_count": total - directed_total,
            "suggestion_coverage": (directed_total / total) if total else 0.0,
            "suggestion_precision": raw_precision if quality_status == "CROSS_EPISODE_CHALLENGER_ONLY" else None,
            "raw_diagnostic_suggestion_precision": raw_precision,
            "harmful_suggestion_count": harmful_total,
            "autocut_policy": "NOT_APPROVED",
        },
        "folds": folds,
    }


def _markdown_report(document: Mapping[str, Any]) -> str:
    if document.get("schema_version") == BACKTEST_SCHEMA_VERSION:
        summary = document.get("summary") or {}
        lines = [
            "# 标签学习驱动器 v1：防泄漏回测",
            "",
            f"- 切分：`{(document.get('method') or {}).get('split')}`",
            f"- 留出案例：{summary.get('holdout_record_count')}",
            f"- 机器建议：{summary.get('machine_suggestion_count')}",
            f"- 仍需人工：{summary.get('human_review_required_count')}",
            f"- 验证状态：`{(document.get('data_quality') or {}).get('status')}`",
            f"- 可报告的建议正确率：{summary.get('suggestion_precision')}",
            f"- 原始诊断命中率（不可当泛化结论）：{summary.get('raw_diagnostic_suggestion_precision')}",
            f"- 危险反向建议：{summary.get('harmful_suggestion_count')}",
            "",
            "结论：这是 Challenger 的离线回测，不构成自动删剪授权；若验证状态为数据不足，原始命中率不得当作准确率或审核缩减依据。无论结果如何，`autocut_policy` 仍为 `NOT_APPROVED`。",
        ]
        return "\n".join(lines) + "\n"
    counts = document.get("prediction_counts") or {}
    source = document.get("candidate_input") or {}
    lines = [
        "# 标签学习驱动器 v1：Shadow 预测",
        "",
        f"- 目标 run：`{source.get('run_id')}`",
        f"- 候选数：{source.get('candidate_count')}",
        f"- 建议剪：{counts.get(MACHINE_CUT, 0)}",
        f"- 建议保留：{counts.get(MACHINE_PRESERVE, 0)}",
        f"- 必须人审：{counts.get(HUMAN_REVIEW, 0)}",
        "",
        "所有结果均为机器建议；不写入真人决定、EDL 或自动剪辑政策。",
    ]
    return "\n".join(lines) + "\n"


def write_json(path: str | Path, document: Mapping[str, Any]) -> None:
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_document(path: str | Path, document: Mapping[str, Any]) -> None:
    out = Path(path).expanduser().resolve()
    write_json(out, document)
    out.with_suffix(".md").write_text(_markdown_report(document), encoding="utf-8")


def _evidence_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    integrity = manifest.get("target_integrity") or {}
    return "\n".join([
        "# 标签学习驱动器：可复核证据包",
        "",
        f"- 驱动器 SHA：`{(manifest.get('driver') or {}).get('source_sha256')}`",
        f"- 防泄漏回测：{summary.get('backtest_status')}",
        f"- Shadow：剪 {summary.get('machine_cut_suggested', 0)} / 保留 {summary.get('machine_preserve_suggested', 0)} / 人审 {summary.get('human_review_required', 0)}",
        f"- 冻结目标前后哈希一致：`{integrity.get('comparison')}`",
        "",
        "该包只读目标审核 run。机器建议不写入真人决定、EDL、音频或自动删剪政策。",
        "",
    ])


def evidence_document(
    *,
    snapshot_dir: str | Path,
    target_run_dir: str | Path,
    out_dir: str | Path,
) -> dict[str, Any]:
    """Build a self-contained, read-only backtest + shadow evidence package.

    The output directory must be outside the target run.  The target is hashed
    before and after work; any concurrent or accidental mutation is a hard
    failure and no manifest is issued as proof of a clean shadow run.
    """

    target = Path(target_run_dir).expanduser().resolve()
    output = Path(out_dir).expanduser().resolve()
    if output == target or target in output.parents:
        raise ValueError("evidence output must be outside the target run")
    if output.exists():
        raise ValueError(f"evidence output directory already exists: {output}")
    source_path = target / "candidates/candidate_source.json"
    input_manifest_path = target / "input_manifest.json"
    overlay_path = target / "all_candidates.json"
    if not source_path.is_file() or not input_manifest_path.is_file():
        raise ValueError("target run lacks candidate_source.json or input_manifest.json")
    output.mkdir(parents=True, exist_ok=False)
    before = target_integrity_document(target)
    write_json(output / "target_integrity.before.json", before)
    backtest = backtest_document(snapshot_dir=snapshot_dir)
    write_document(output / "backtest_report.json", backtest)
    shadow = predict_document(
        snapshot_dir=snapshot_dir,
        candidate_source=source_path,
        candidate_overlay=overlay_path if overlay_path.is_file() else None,
        input_manifest=input_manifest_path,
        target_run_dir=target,
        target_run_id=_string(before["target_identity"].get("run_id")),
    )
    write_document(output / "shadow_prediction_manifest.json", shadow)
    after = target_integrity_document(target)
    write_json(output / "target_integrity.after.json", after)
    if before != after:
        raise ValueError("target run changed while creating shadow evidence; refuse manifest")
    snapshot_root, snapshot_manifest, _ = load_snapshot(snapshot_dir)
    counts = shadow.get("prediction_counts") or {}
    manifest = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "driver": {
            "id": DRIVER_ID,
            "source_path": str(Path(__file__).resolve()),
            "source_sha256": driver_source_sha256(),
        },
        "snapshot": {
            "path": str(snapshot_root),
            "snapshot_manifest_sha256": sha256_file(snapshot_root / "snapshot_manifest.json"),
            "aggregated_sha256": sha256_file(snapshot_root / "aggregated.json"),
            "snapshot_id": snapshot_manifest.get("snapshot_id"),
        },
        "target_identity": before["target_identity"],
        "inputs": {
            "candidate_source_sha256": sha256_file(source_path),
            "input_manifest_sha256": sha256_file(input_manifest_path),
            "overlay_sha256": sha256_file(overlay_path) if overlay_path.is_file() else None,
        },
        "commands": {
            "backtest": {"subcommand": "backtest", "snapshot_dir": str(snapshot_root)},
            "shadow": {
                "subcommand": "predict",
                "snapshot_dir": str(snapshot_root),
                "target_run_dir": str(target),
                "candidate_source": str(source_path),
                "candidate_overlay": str(overlay_path) if overlay_path.is_file() else None,
            },
        },
        "outputs": {
            "backtest_report_sha256": sha256_file(output / "backtest_report.json"),
            "shadow_prediction_sha256": sha256_file(output / "shadow_prediction_manifest.json"),
            "target_integrity_before_sha256": sha256_file(output / "target_integrity.before.json"),
            "target_integrity_after_sha256": sha256_file(output / "target_integrity.after.json"),
        },
        "target_integrity": {
            "comparison": "PASS",
            "before_relpath": "target_integrity.before.json",
            "after_relpath": "target_integrity.after.json",
        },
        "summary": {
            "backtest_status": (backtest.get("data_quality") or {}).get("status"),
            "machine_cut_suggested": counts.get(MACHINE_CUT, 0),
            "machine_preserve_suggested": counts.get(MACHINE_PRESERVE, 0),
            "human_review_required": counts.get(HUMAN_REVIEW, 0),
            "autocut_policy": "NOT_APPROVED",
        },
        "safety": {
            "machine_suggestions_only": True,
            "never_creates_human_decision": True,
            "never_creates_edl": True,
            "never_creates_autocut_permission": True,
        },
    }
    write_json(output / "evidence_manifest.json", manifest)
    (output / "RUN_REPORT.md").write_text(_evidence_markdown(manifest), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    predict = sub.add_parser("predict", help="write machine suggestions for a new candidate source")
    predict.add_argument("--snapshot-dir", type=Path, required=True)
    predict.add_argument("--candidate-source", type=Path, required=True)
    predict.add_argument("--candidate-overlay", type=Path)
    predict.add_argument("--input-manifest", type=Path)
    predict.add_argument(
        "--target-run-dir",
        type=Path,
        required=True,
        help="immutable target run used to bind identity and prevent same-audio leakage",
    )
    predict.add_argument("--exclude-episode", action="append", default=[])
    predict.add_argument("--exclude-run", action="append", default=[])
    predict.add_argument("--target-run-id")
    predict.add_argument("--out", type=Path, required=True)
    backtest = sub.add_parser("backtest", help="run leave-one-episode-out leakage-safe backtest")
    backtest.add_argument("--snapshot-dir", type=Path, required=True)
    backtest.add_argument("--out", type=Path, required=True)
    evidence = sub.add_parser(
        "evidence",
        help="write a read-only backtest and shadow package with target before/after hashes",
    )
    evidence.add_argument("--snapshot-dir", type=Path, required=True)
    evidence.add_argument("--target-run-dir", type=Path, required=True)
    evidence.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "predict":
        document = predict_document(
            snapshot_dir=args.snapshot_dir,
            candidate_source=args.candidate_source,
            candidate_overlay=args.candidate_overlay,
            input_manifest=args.input_manifest,
            target_run_dir=args.target_run_dir,
            exclude_episodes=args.exclude_episode,
            exclude_runs=args.exclude_run,
            target_run_id=args.target_run_id,
        )
        write_document(args.out, document)
        print(json.dumps({
            "schema_version": document.get("schema_version"),
            "out": str(args.out.expanduser().resolve()),
            "summary": document.get("summary") or document.get("prediction_counts"),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "backtest":
        document = backtest_document(snapshot_dir=args.snapshot_dir)
        write_document(args.out, document)
        print(json.dumps({
            "schema_version": document.get("schema_version"),
            "out": str(args.out.expanduser().resolve()),
            "summary": document.get("summary"),
        }, ensure_ascii=False, indent=2))
        return 0
    document = evidence_document(
        snapshot_dir=args.snapshot_dir,
        target_run_dir=args.target_run_dir,
        out_dir=args.out_dir,
    )
    print(json.dumps({
        "schema_version": document.get("schema_version"),
        "out_dir": str(args.out_dir.expanduser().resolve()),
        "summary": document.get("summary"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
