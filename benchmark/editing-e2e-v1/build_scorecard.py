#!/usr/bin/env python3
"""Build a strict, media-free development scorecard for one delivery run.

The scorecard is deliberately a *diagnostic summary*, not an editor.  It
reads JSON evidence only, never opens audio, never writes into a run, and
never creates an accept/reject decision or EDL.  Missing evidence is rendered
as ``NOT_MEASURED`` rather than zero or a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "editing-e2e-development-scorecard-v1"
TOOL_VERSION = "editing-e2e-development-scorecard-builder-v1"
SCORECARD_JSON_NAME = "scorecard.json"
SCORECARD_MARKDOWN_NAME = "SCORECARD.md"
TRANSITION_VARIANTS = ("human_approved", "machine_assisted_draft")
TRANSITION_STATUS = "OBJECTIVE_ANOMALY_RANKING_SUBJECTIVE_LISTENING_REQUIRED"
FINAL_AUDIT_FINDINGS = {
    "NO_CLEAR_ISSUE",
    "POSSIBLE_MISSED_EDIT",
    "CLEAR_MISSED_EDIT_NEEDS_NEW_CANDIDATE",
}
PENDING_AUDIT_STATUSES = {"PENDING_HUMAN_LISTENING", "", "NONE", "NULL"}


class ScorecardError(ValueError):
    """An evidence mismatch which must not be summarized as a valid metric."""


def project_root() -> Path:
    # benchmark/editing-e2e-v1/build_scorecard.py -> project root
    return Path(__file__).resolve().parents[2]


def read_object(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json":
        raise ScorecardError(f"scorecard only reads JSON evidence, not: {path}")
    if not path.is_file():
        raise ScorecardError(f"required JSON evidence is missing: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ScorecardError(f"cannot read JSON evidence {path}: {error}") from error
    if not isinstance(value, dict):
        raise ScorecardError(f"JSON evidence root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    """Hash one JSON evidence file.  Callers never pass media to this helper."""
    if path.suffix.lower() != ".json":
        raise ScorecardError(f"refusing to hash non-JSON source: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ScorecardError(f"cannot hash JSON evidence {path}: {error}") from error
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScorecardError(message)


def require_nonempty_string(value: Any, label: str) -> str:
    require(isinstance(value, str) and bool(value), f"{label} must be a non-empty string")
    return value


def require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    require(type(value) is int, f"{label} must be an integer")
    if minimum is not None:
        require(value >= minimum, f"{label} must be >= {minimum}")
    return value


def display_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def source_reference(path: Path, root: Path) -> dict[str, Any]:
    return {"relpath": display_path(path, root), "sha256": sha256_file(path)}


def absent_source(path: Path | None, root: Path, reason: str) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "ABSENT", "reason": reason}
    if path is not None:
        result["relpath"] = display_path(path, root)
    return result


def normalized_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def nested_decisions(document: dict[str, Any], path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Accept the two existing human-decision container shapes, not drafts."""
    container = document
    raw = document.get("decisions")
    if isinstance(raw, dict):
        container = raw
        raw = container.get("decisions")
    require(isinstance(raw, list), f"{path}: decisions must be an array")
    decisions: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        require(isinstance(item, dict), f"{path}: decisions[{index}] must be an object")
        decisions.append(item)
    return container, decisions


def load_run_evidence(run_dir: Path, root: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    require(run_dir.is_dir(), f"run directory does not exist: {run_dir}")
    paths = {
        "run_identity": run_dir / "run_identity.json",
        "state": run_dir / "state.json",
        "input_manifest": run_dir / "input_manifest.json",
        "all_candidates": run_dir / "all_candidates.json",
        "calibration_source": run_dir / "calibration_source.json",
        "review_package": run_dir / "review_bundle" / "review_package.json",
    }
    documents = {name: read_object(path) for name, path in paths.items()}
    references = {name: source_reference(path, root) for name, path in paths.items()}

    identity = documents["run_identity"]
    run_id = require_nonempty_string(identity.get("run_id"), "run_identity.run_id")
    episode_id = require_nonempty_string(identity.get("episode_id"), "run_identity.episode_id")
    identity_sha = references["run_identity"]["sha256"]

    for label in ("state", "input_manifest", "all_candidates", "calibration_source", "review_package"):
        document = documents[label]
        require(document.get("run_id") == run_id, f"{label}.run_id does not match run_identity.run_id")
        require(document.get("episode_id") == episode_id, f"{label}.episode_id does not match run_identity.episode_id")
    for label in ("input_manifest", "all_candidates"):
        require(
            documents[label].get("run_identity_sha256") == identity_sha,
            f"{label}.run_identity_sha256 does not match actual run_identity.json SHA-256",
        )

    state = require_nonempty_string(documents["state"].get("state"), "state.state")
    input_manifest = documents["input_manifest"]
    sample_rate_hz = require_int(input_manifest.get("sample_rate_hz"), "input_manifest.sample_rate_hz", minimum=1)
    frame_count = require_int(input_manifest.get("frame_count"), "input_manifest.frame_count", minimum=1)
    tracks = input_manifest.get("tracks")
    require(isinstance(tracks, list) and bool(tracks), "input_manifest.tracks must be a non-empty array")
    track_ids: list[str] = []
    for index, track in enumerate(tracks):
        require(isinstance(track, dict), f"input_manifest.tracks[{index}] must be an object")
        track_id = require_nonempty_string(track.get("track_id"), f"input_manifest.tracks[{index}].track_id")
        require(track.get("sample_rate_hz") == sample_rate_hz, f"track {track_id} has a mismatched sample rate")
        require(track.get("frame_count") == frame_count, f"track {track_id} has a mismatched frame count")
        track_ids.append(track_id)
    require(len(track_ids) == len(set(track_ids)), "input_manifest.tracks contains duplicate track IDs")

    all_candidates = documents["all_candidates"].get("candidates")
    require(isinstance(all_candidates, list), "all_candidates.candidates must be an array")
    candidate_by_id: dict[str, dict[str, Any]] = {}
    kind_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    for index, candidate in enumerate(all_candidates):
        require(isinstance(candidate, dict), f"all_candidates.candidates[{index}] must be an object")
        candidate_id = require_nonempty_string(candidate.get("candidate_id"), f"all_candidates.candidates[{index}].candidate_id")
        require(candidate_id not in candidate_by_id, f"all_candidates contains duplicate candidate_id={candidate_id}")
        candidate_by_id[candidate_id] = candidate
        kind = candidate.get("candidate_kind")
        risk = candidate.get("risk_level")
        kind_counts[kind if isinstance(kind, str) and kind else "UNSPECIFIED"] += 1
        risk_counts[risk if isinstance(risk, str) and risk else "UNSPECIFIED"] += 1

    review_candidates = documents["review_package"].get("candidates")
    require(isinstance(review_candidates, list), "review_package.candidates must be an array")
    review_candidate_ids: list[str] = []
    review_semantic_sha: dict[str, str] = {}
    for index, candidate in enumerate(review_candidates):
        require(isinstance(candidate, dict), f"review_package.candidates[{index}] must be an object")
        candidate_id = require_nonempty_string(candidate.get("candidate_id"), f"review_package.candidates[{index}].candidate_id")
        require(candidate_id not in review_candidate_ids, f"review_package has duplicate candidate_id={candidate_id}")
        require(candidate_id in candidate_by_id, f"review candidate {candidate_id} is missing from all_candidates")
        semantic_sha = candidate.get("semantic_sha256")
        require(isinstance(semantic_sha, str) and len(semantic_sha) == 64, f"review candidate {candidate_id} has no semantic SHA")
        review_candidate_ids.append(candidate_id)
        review_semantic_sha[candidate_id] = semantic_sha

    calibration_source = documents["calibration_source"]
    calibration_candidates = calibration_source.get("candidates")
    require(isinstance(calibration_candidates, list), "calibration_source.candidates must be an array")
    calibration_ids: list[str] = []
    for index, candidate in enumerate(calibration_candidates):
        require(isinstance(candidate, dict), f"calibration_source.candidates[{index}] must be an object")
        candidate_id = require_nonempty_string(candidate.get("candidate_id"), f"calibration_source.candidates[{index}].candidate_id")
        require(candidate_id not in calibration_ids, f"calibration_source has duplicate candidate_id={candidate_id}")
        calibration_ids.append(candidate_id)
    require(
        set(calibration_ids) == set(review_candidate_ids),
        "calibration_source candidates do not exactly match review_package candidates",
    )

    selection = (calibration_source.get("delivery_calibration_selection") or {}).get("selection_report") or {}
    review_budget = selection.get("review_budget")
    remaining_budget = selection.get("remaining_budget")
    if review_budget is not None:
        require_int(review_budget, "calibration selection review_budget", minimum=0)
    if remaining_budget is not None:
        require_int(remaining_budget, "calibration selection remaining_budget", minimum=0)
    selected_total = selection.get("selected_total")
    if selected_total is not None:
        require_int(selected_total, "calibration selection selected_total", minimum=0)
        require(selected_total == len(review_candidate_ids), "selection selected_total does not match review package count")

    counts = calibration_source.get("counts") or {}
    return {
        "run_dir": run_dir,
        "run_id": run_id,
        "episode_id": episode_id,
        "state": state,
        "identity_sha256": identity_sha,
        "sample_rate_hz": sample_rate_hz,
        "frame_count": frame_count,
        "track_ids": track_ids,
        "candidate_by_id": candidate_by_id,
        "review_candidate_ids": review_candidate_ids,
        "review_semantic_sha": review_semantic_sha,
        "candidate_kind_counts": normalized_counter(kind_counts),
        "candidate_risk_counts": normalized_counter(risk_counts),
        "review_budget": review_budget,
        "remaining_budget": remaining_budget,
        "blocked_candidate_count": counts.get("blocked") if type(counts.get("blocked")) is int else None,
        "blocked_acoustic_count": counts.get("blocked_acoustic") if type(counts.get("blocked_acoustic")) is int else None,
        "references": references,
        "review_package_id": documents["review_package"].get("package_id"),
        "review_manifest_sha256": documents["review_package"].get("review_manifest_sha256"),
    }


def current_human_review(run: dict[str, Any], root: Path) -> dict[str, Any]:
    path = run["run_dir"] / "human_decisions.json"
    draft_path = run["run_dir"] / "review_draft.json"
    expected_ids = set(run["review_candidate_ids"])
    result: dict[str, Any] = {
        "expected_review_candidate_count": len(expected_ids),
        "review_draft_present_but_not_counted": draft_path.is_file(),
        "draft_rule": "review_draft.json is never a final human decision record",
    }
    if not path.is_file():
        result.update(
            {
                "status": "PENDING_HUMAN_REVIEW",
                "decision_record": absent_source(path, root, "human_decisions.json does not exist yet"),
                "final_decision_count": 0,
                "accept_count": None,
                "reject_count": None,
                "decision_coverage_fraction": None,
                "observed_acceptance_rate": {
                    "status": "NOT_MEASURED",
                    "value": None,
                    "reason": "no complete real-human decision record exists for this run",
                },
                "feedback": {
                    "status": "NOT_MEASURED",
                    "nonempty_count": None,
                    "reason": "no complete real-human decision record exists for this run",
                },
            }
        )
        return result

    document = read_object(path)
    container, decisions = nested_decisions(document, path)
    package_id = run["review_package_id"]
    if isinstance(package_id, str) and package_id:
        require(container.get("package_id") == package_id, "human decisions package_id does not match review package")
    manifest_sha = run["review_manifest_sha256"]
    if isinstance(manifest_sha, str) and manifest_sha:
        require(
            container.get("review_manifest_sha256") == manifest_sha,
            "human decisions review_manifest_sha256 does not match review package",
        )

    seen: set[str] = set()
    decision_counts: Counter[str] = Counter()
    feedback_nonempty = 0
    for index, decision in enumerate(decisions):
        candidate_id = require_nonempty_string(decision.get("candidate_id"), f"human decisions[{index}].candidate_id")
        require(candidate_id not in seen, f"human decisions has duplicate candidate_id={candidate_id}")
        require(candidate_id in expected_ids, f"human decision {candidate_id} is not in current review package")
        seen.add(candidate_id)
        value = decision.get("decision")
        require(value in {"accept", "reject"}, f"human decision {candidate_id} must be accept or reject")
        decision_counts[value] += 1
        semantic_sha = decision.get("candidate_semantic_sha256")
        require(
            semantic_sha == run["review_semantic_sha"][candidate_id],
            f"human decision {candidate_id} semantic SHA does not match review package",
        )
        feedback = decision.get("feedback")
        require(feedback is None or isinstance(feedback, str), f"human decision {candidate_id} feedback must be string/null")
        if isinstance(feedback, str) and feedback.strip():
            feedback_nonempty += 1

    complete = seen == expected_ids
    final_count = len(decisions)
    result.update(
        {
            "status": "MEASURED_COMPLETE_HUMAN_REVIEW" if complete else "INCOMPLETE_HUMAN_REVIEW",
            "decision_record": source_reference(path, root),
            "final_decision_count": final_count,
            "accept_count": decision_counts["accept"],
            "reject_count": decision_counts["reject"],
            "decision_coverage_fraction": final_count / len(expected_ids) if expected_ids else None,
        }
    )
    if complete:
        result["observed_acceptance_rate"] = {
            "status": "MEASURED_CURRENT_REVIEW_PACKET_ONLY",
            "value": decision_counts["accept"] / final_count if final_count else None,
            "reason": "observed human accept/reject rate; it is not recall, overall quality, or automatic-edit authorization",
        }
        result["feedback"] = {
            "status": "MEASURED_CURRENT_REVIEW_PACKET_ONLY",
            "nonempty_count": feedback_nonempty,
            "empty_count": final_count - feedback_nonempty,
            "reason": "remarks explain human decisions only and never change boundaries or policy",
        }
    else:
        result["observed_acceptance_rate"] = {
            "status": "NOT_MEASURED",
            "value": None,
            "reason": "a partial decision file cannot stand in for a complete review packet",
        }
        result["feedback"] = {
            "status": "NOT_MEASURED",
            "nonempty_count": feedback_nonempty,
            "reason": "the current review packet is incomplete",
        }
    return result


def feedback_regression(catalog_path: Path | None, root: Path) -> dict[str, Any]:
    if catalog_path is None or not catalog_path.is_file():
        return {
            "status": "NOT_MEASURED",
            "catalog": absent_source(catalog_path, root, "development feedback catalog was not supplied or does not exist"),
            "reason": "there is no validated historical feedback regression source to summarize",
        }
    catalog = read_object(catalog_path)
    require(catalog.get("schema_version") == "mentor-feedback-regression-v1", "feedback catalog schema mismatch")
    scope = catalog.get("benchmark_scope") or {}
    require(scope.get("split") == "development", "feedback catalog must be development-only")
    require(scope.get("frozen") is False, "feedback catalog must not be labeled frozen")
    require(scope.get("training_gold") is False, "feedback catalog must not be labeled training gold")
    entries = catalog.get("entries")
    require(isinstance(entries, list), "feedback catalog entries must be an array")
    decisions: Counter[str] = Counter()
    feedback_nonempty = 0
    for index, entry in enumerate(entries):
        require(isinstance(entry, dict), f"feedback catalog entries[{index}] must be an object")
        human = entry.get("human_decision") or {}
        decision = human.get("decision")
        require(decision in {"accept", "reject"}, f"feedback catalog entry {index} has invalid human decision")
        decisions[decision] += 1
        feedback = (entry.get("feedback") or {}).get("verbatim")
        require(isinstance(feedback, str), f"feedback catalog entry {index} must preserve string feedback")
        if feedback.strip():
            feedback_nonempty += 1
    declared = catalog.get("summary") or {}
    require(declared.get("total_decisions") == len(entries), "feedback catalog declared total is inconsistent")
    require(declared.get("accept") == decisions["accept"], "feedback catalog declared accept count is inconsistent")
    require(declared.get("reject") == decisions["reject"], "feedback catalog declared reject count is inconsistent")
    require(declared.get("feedback_nonempty") == feedback_nonempty, "feedback catalog declared feedback count is inconsistent")
    return {
        "status": "AVAILABLE_DEVELOPMENT_ONLY",
        "catalog": source_reference(catalog_path, root),
        "historical_decision_count": len(entries),
        "accept_count": decisions["accept"],
        "reject_count": decisions["reject"],
        "nonempty_feedback_count": feedback_nonempty,
        "empty_feedback_count": len(entries) - feedback_nonempty,
        "scope_limit": "historical development regression only; it does not label current-run candidates, authorize edits, or form a frozen benchmark",
    }


def audit_finding(window: dict[str, Any]) -> str | None:
    status = window.get("human_review_status")
    finding = window.get("human_finding")
    if isinstance(finding, str) and finding in FINAL_AUDIT_FINDINGS:
        return finding
    if isinstance(status, str) and status in FINAL_AUDIT_FINDINGS:
        return status
    return None


def no_candidate_audit(audit_path: Path | None, run: dict[str, Any], root: Path) -> dict[str, Any]:
    if audit_path is None or not audit_path.is_file():
        return {
            "status": "NOT_MEASURED",
            "audit_plan": absent_source(audit_path, root, "no-candidate audit plan was not supplied or does not exist"),
            "reason": "no reproducible no-candidate audit plan/result is available",
        }
    audit = read_object(audit_path)
    require(audit.get("schema_version") == "no-candidate-window-audit-v1", "no-candidate audit schema mismatch")
    provenance = audit.get("provenance") or {}
    require(provenance.get("run_id") == run["run_id"], "no-candidate audit run_id does not match scorecard run")
    require(provenance.get("episode_id") == run["episode_id"], "no-candidate audit episode_id does not match scorecard run")
    expected_hashes = {
        "run_identity_sha256": run["references"]["run_identity"]["sha256"],
        "input_manifest_sha256": run["references"]["input_manifest"]["sha256"],
        "all_candidates_sha256": run["references"]["all_candidates"]["sha256"],
    }
    for key, expected in expected_hashes.items():
        require(provenance.get(key) == expected, f"no-candidate audit {key} does not bind to current run evidence")
    windows = audit.get("windows")
    require(isinstance(windows, list) and bool(windows), "no-candidate audit windows must be a non-empty array")
    findings: Counter[str] = Counter()
    pending = 0
    unknown = 0
    for index, window in enumerate(windows):
        require(isinstance(window, dict), f"no-candidate audit windows[{index}] must be an object")
        result = audit_finding(window)
        if result is not None:
            findings[result] += 1
            continue
        raw_status = window.get("human_review_status")
        normalized = raw_status.upper() if isinstance(raw_status, str) else "NULL"
        if normalized in PENDING_AUDIT_STATUSES:
            pending += 1
        else:
            unknown += 1
    completed = sum(findings.values())
    summary: dict[str, Any] = {
        "audit_plan": source_reference(audit_path, root),
        "audit_id": audit.get("audit_id"),
        "planned_window_count": len(windows),
        "completed_window_count": completed,
        "pending_window_count": pending,
        "unknown_window_status_count": unknown,
        "finding_counts": normalized_counter(findings),
        "result_protocol": "Every window must use NO_CLEAR_ISSUE, POSSIBLE_MISSED_EDIT, or CLEAR_MISSED_EDIT_NEEDS_NEW_CANDIDATE before the sample result is measured.",
    }
    if completed == len(windows):
        missed_or_possible = findings["POSSIBLE_MISSED_EDIT"] + findings["CLEAR_MISSED_EDIT_NEEDS_NEW_CANDIDATE"]
        summary.update(
            {
                "status": "MEASURED_DEVELOPMENT_SAMPLE_ONLY",
                "observed_problem_window_fraction": missed_or_possible / len(windows),
                "scope_limit": "This is a completed random sample only. Even zero observed problem windows does not prove candidate recall, no missed edits, or overall quality.",
            }
        )
    else:
        summary.update(
            {
                "status": "NOT_MEASURED",
                "observed_problem_window_fraction": None,
                "reason": "the plan exists, but not every sampled window has a recognized real-human outcome",
            }
        )
    return summary


def transition_qc(run: dict[str, Any], root: Path) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    valid_count = 0
    errors: list[str] = []
    for variant in TRANSITION_VARIANTS:
        path = run["run_dir"] / f"render_{variant}" / "transition_qc.json"
        if not path.is_file():
            reports[variant] = absent_source(path, root, "post-render transition QC has not been generated")
            continue
        report = read_object(path)
        variant_errors: list[str] = []
        if report.get("schema_version") != "rendered-transition-qc-v1":
            variant_errors.append("schema_version mismatch")
        if report.get("status") != TRANSITION_STATUS:
            variant_errors.append("status is not objective-ranking/subj-listening-required")
        if report.get("run_id") != run["run_id"] or report.get("episode_id") != run["episode_id"]:
            variant_errors.append("run identity mismatch")
        if report.get("variant") != variant or report.get("run_identity_sha256") != run["identity_sha256"]:
            variant_errors.append("variant or run identity SHA mismatch")
        transitions = report.get("transitions")
        ranked_ids = report.get("ranked_transition_ids")
        if not isinstance(transitions, list) or not isinstance(ranked_ids, list):
            variant_errors.append("missing transition ranking arrays")
        else:
            expected_ids = [item.get("transition_id") for item in transitions if isinstance(item, dict)]
            if report.get("transition_count") != len(transitions) or ranked_ids != expected_ids:
                variant_errors.append("transition count/ranking does not match report records")
        entry: dict[str, Any] = source_reference(path, root)
        entry.update(
            {
                "transition_count": report.get("transition_count"),
                "priority_relisten_count": report.get("priority_relisten_count"),
                "ranked_transition_ids": report.get("ranked_transition_ids"),
            }
        )
        if variant_errors:
            entry["status"] = "INVALID_NOT_MEASURED"
            entry["errors"] = variant_errors
            errors.extend(f"{variant}: {error}" for error in variant_errors)
        else:
            entry["status"] = "VALID_OBJECTIVE_RANKING_ONLY"
            valid_count += 1
        reports[variant] = entry

    post_render_states = {"MACHINE_ASSISTED_DRAFT_RENDERED", "FINAL_QC_REQUIRED", "DELIVERY_DECISION_RECORDED"}
    all_valid = valid_count == len(TRANSITION_VARIANTS)
    result: dict[str, Any] = {
        "reports": reports,
        "subjective_naturalness": {
            "status": "NOT_MEASURED",
            "reason": "transition_qc ranks objective anomalies for re-listening; it never measures naturalness, semantic correctness, or an automatic pass",
        },
    }
    if all_valid:
        result.update(
            {
                "status": "MEASURED_OBJECTIVE_PRIORITY_ONLY",
                "scope_limit": "Both reports are objective re-listen rankings only; a human still has to hear the cuts.",
            }
        )
    else:
        reason = (
            "this run is pre-render, so transition QC is not generated yet"
            if run["state"] not in post_render_states and valid_count == 0
            else "one or both expected post-render transition QC reports are absent or invalid"
        )
        result.update({"status": "NOT_MEASURED", "reason": reason})
        if errors:
            result["errors"] = errors
    return result


def candidate_burden(run: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = run["frame_count"] / run["sample_rate_hz"]
    duration_hours = duration_seconds / 3600.0
    total_candidates = len(run["candidate_by_id"])
    review_candidates = len(run["review_candidate_ids"])
    return {
        "status": "MEASURED_INPUT_METADATA_ONLY",
        "timeline_duration_seconds": duration_seconds,
        "timeline_duration_hours": duration_hours,
        "track_count": len(run["track_ids"]),
        "all_candidate_count": total_candidates,
        "review_candidate_count": review_candidates,
        "unreviewed_candidate_count": total_candidates - review_candidates,
        "all_candidates_per_timeline_hour": total_candidates / duration_hours,
        "review_candidates_per_timeline_hour": review_candidates / duration_hours,
        "candidate_kind_counts": run["candidate_kind_counts"],
        "candidate_risk_counts": run["candidate_risk_counts"],
        "review_budget": run["review_budget"],
        "remaining_review_budget": run["remaining_budget"],
        "blocked_candidate_count": run["blocked_candidate_count"],
        "blocked_acoustic_candidate_count": run["blocked_acoustic_count"],
        "scope_limit": "Candidate volume measures reviewer workload only. A lower number is not evidence of better recall, safer edits, or better audio.",
    }


def quality_metrics(human_review: dict[str, Any], audit: dict[str, Any], transition: dict[str, Any]) -> dict[str, Any]:
    audit_status = audit.get("status")
    return {
        "candidate_recall_against_human_edit_map": {
            "status": "NOT_MEASURED",
            "reason": "no validated human edit map/reference EDL is part of this development scorecard",
        },
        "no_candidate_region_missed_edit_signal": {
            "status": audit_status if audit_status == "MEASURED_DEVELOPMENT_SAMPLE_ONLY" else "NOT_MEASURED",
            "reason": (
                "completed sample observation only; it is not a recall estimate or quality pass"
                if audit_status == "MEASURED_DEVELOPMENT_SAMPLE_ONLY"
                else "no fully completed human no-candidate audit sample exists"
            ),
        },
        "rendered_transition_naturalness": transition["subjective_naturalness"],
        "serious_semantic_misdeletion": {
            "status": "NOT_MEASURED",
            "reason": "requires explicit human semantic/whole-episode evaluation; candidate count or acceptance rate cannot substitute",
        },
        "current_review_packet_completion": {
            "status": human_review["status"],
            "reason": "review workflow status, not a quality verdict",
        },
    }


def next_gates(run: dict[str, Any], human_review: dict[str, Any], audit: dict[str, Any], transition: dict[str, Any]) -> list[str]:
    gates: list[str] = []
    if human_review["status"] != "MEASURED_COMPLETE_HUMAN_REVIEW":
        gates.append("真实审核人完成当前 review package 的每一条 accept/reject；草稿不算决定。")
    if audit.get("status") != "MEASURED_DEVELOPMENT_SAMPLE_ONLY":
        gates.append("真人同步试听所有固定无候选窗口，并以规定 finding 写入结果；发现问题先形成新候选。")
    if transition.get("status") != "MEASURED_OBJECTIVE_PRIORITY_ONLY":
        gates.append("正常 resume 后生成两份 transition_qc，再把其优先项纳入人耳复听。")
    gates.append("单独记录整片语义/听感；本 scorecard 不产生发布或 Champion 晋升结论。")
    return gates


def build_scorecard(
    *,
    run_dir: Path,
    no_candidate_audit_path: Path | None = None,
    feedback_catalog_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Create one deterministic scorecard document in memory; write nothing."""
    source_root = (root or project_root()).resolve()
    run = load_run_evidence(run_dir, source_root)
    human_review = current_human_review(run, source_root)
    feedback = feedback_regression(feedback_catalog_path, source_root)
    audit = no_candidate_audit(no_candidate_audit_path, run, source_root)
    transitions = transition_qc(run, source_root)
    burden = candidate_burden(run)
    metrics = quality_metrics(human_review, audit, transitions)
    pre_review = run["state"] == "CALIBRATION_REVIEW_REQUIRED"
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "scorecard_id": f"{run['run_id']}-development-scorecard-v1",
        "benchmark_scope": {
            "split": "development",
            "frozen": False,
            "eligible_for_champion_comparison": False,
            "training_gold": False,
            "authorizes_automatic_accept_reject": False,
            "authorizes_edl_or_render": False,
            "real_media_opened_or_copied": False,
        },
        "run": {
            "run_id": run["run_id"],
            "episode_id": run["episode_id"],
            "delivery_state": run["state"],
            "run_identity_sha256": run["identity_sha256"],
            "run_dir": display_path(run["run_dir"], source_root),
        },
        "sources": run["references"],
        "candidate_burden": burden,
        "current_human_review": human_review,
        "historical_feedback_regression": feedback,
        "no_candidate_audit": audit,
        "transition_qc": transitions,
        "quality_metrics": metrics,
        "scorecard_status": {
            "status": "INCOMPLETE_HUMAN_REVIEW_REQUIRED" if pre_review else "DEVELOPMENT_EVIDENCE_INCOMPLETE_NOT_A_QUALITY_PASS",
            "quality_pass": False,
            "reason": (
                "run is still at CALIBRATION_REVIEW_REQUIRED; no human decision, EDL, render, or transition-QC result exists"
                if pre_review
                else "this development scorecard only summarizes available evidence and never grants a release, policy, or Champion pass"
            ),
            "next_gates": next_gates(run, human_review, audit, transitions),
        },
        "limits": [
            "NOT_MEASURED means evidence is absent, incomplete, invalid, or outside this scorecard; it never means zero problems or pass.",
            "Candidate burden is workload metadata, not a proxy for recall, semantic safety, or audio quality.",
            "Historical Mentor feedback is development-only regression context and never becomes a current-run decision or automatic policy.",
            "No-candidate windows require real human listening; an unlistened plan cannot support a missed-edit conclusion.",
            "Transition QC is an objective priority ranking only. It does not hear naturalness, validate meaning, or authorize edits.",
        ],
    }


def render_markdown(document: dict[str, Any]) -> str:
    run = document["run"]
    burden = document["candidate_burden"]
    review = document["current_human_review"]
    feedback = document["historical_feedback_regression"]
    audit = document["no_candidate_audit"]
    transition = document["transition_qc"]
    metrics = document["quality_metrics"]
    status = document["scorecard_status"]
    lines = [
        "# Development 剪辑 Benchmark Scorecard",
        "",
        f"- run: `{run['run_id']}` / `{run['episode_id']}`",
        f"- 交付状态：`{run['delivery_state']}`",
        f"- scorecard：`{status['status']}`",
        "- 质量通过：**否**（本文件不产生发布、Champion、自动删剪或 accept/reject 授权）",
        "",
        f"> {status['reason']}",
        "",
        "## 候选负担（可测，但不是质量）",
        "",
        f"- 时间线：{burden['timeline_duration_seconds']:.3f} 秒 / {burden['timeline_duration_hours']:.6f} 小时；对齐轨道：{burden['track_count']}。",
        f"- 全部候选：{burden['all_candidate_count']}（{burden['all_candidates_per_timeline_hour']:.3f} 条/节目小时）；审核包：{burden['review_candidate_count']}（{burden['review_candidates_per_timeline_hour']:.3f} 条/节目小时）。",
        f"- 候选类别：`{json.dumps(burden['candidate_kind_counts'], ensure_ascii=False, sort_keys=True)}`；风险：`{json.dumps(burden['candidate_risk_counts'], ensure_ascii=False, sort_keys=True)}`。",
        f"- 审核预算 / 剩余：`{burden['review_budget']}` / `{burden['remaining_review_budget']}`；安全阻断：`{burden['blocked_candidate_count']}`。",
        f"- 限制：{burden['scope_limit']}",
        "",
        "## 当前真人审核",
        "",
        f"- 状态：`{review['status']}`；正式决定：{review['final_decision_count']} / {review['expected_review_candidate_count']}。",
        f"- accept/reject 观察值：`{review['observed_acceptance_rate']['status']}`；备注：`{review['feedback']['status']}`。",
        f"- 草稿存在但不计入：`{review['review_draft_present_but_not_counted']}`。",
        "",
        "## 历史 Mentor 备注回归集",
        "",
    ]
    if feedback["status"] == "AVAILABLE_DEVELOPMENT_ONLY":
        lines.extend(
            [
                f"- 状态：`{feedback['status']}`；历史决定：{feedback['historical_decision_count']}（accept {feedback['accept_count']} / reject {feedback['reject_count']}），有备注 {feedback['nonempty_feedback_count']} 条。",
                f"- 限制：{feedback['scope_limit']}",
            ]
        )
    else:
        lines.append(f"- 状态：`NOT_MEASURED`；{feedback['reason']}")
    lines.extend(
        [
            "",
            "## 无候选区抽查",
            "",
            f"- 状态：`{audit['status']}`。",
        ]
    )
    if audit["status"] == "NOT_MEASURED":
        lines.append(f"- 原因：{audit.get('reason')}")
    else:
        lines.extend(
            [
                f"- 窗口：计划 {audit['planned_window_count']}，已完成 {audit['completed_window_count']}，待试听 {audit['pending_window_count']}，未知状态 {audit['unknown_window_status_count']}。",
                f"- 结果：`{audit.get('observed_problem_window_fraction')}`；{audit.get('scope_limit') or audit.get('reason')}",
            ]
        )
    lines.extend(
        [
            "",
            "## 渲染后剪口复听排序（transition QC）",
            "",
            f"- 客观排序：`{transition['status']}`；人耳自然度：`{transition['subjective_naturalness']['status']}`。",
        ]
    )
    if transition["status"] == "NOT_MEASURED":
        lines.append(f"- 原因：{transition.get('reason')}")
    else:
        lines.append(f"- 限制：{transition['scope_limit']}")
    lines.extend(
        [
            "",
            "## 仍未测量的质量门",
            "",
            "| 指标 | 状态 | 原因 |",
            "| --- | --- | --- |",
        ]
    )
    for label, metric in (
        ("候选召回（对人工 edit map）", metrics["candidate_recall_against_human_edit_map"]),
        ("无候选区漏检信号", metrics["no_candidate_region_missed_edit_signal"]),
        ("渲染剪口自然度", metrics["rendered_transition_naturalness"]),
        ("严重语义误删", metrics["serious_semantic_misdeletion"]),
    ):
        lines.append(f"| {label} | `{metric['status']}` | {metric['reason']} |")
    lines.extend(["", "## 下一道门", ""])
    lines.extend(f"- {gate}" for gate in status["next_gates"])
    lines.extend(["", "## 重要解释", ""])
    lines.extend(f"- {item}" for item in document["limits"])
    lines.append("")
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def write_scorecard_bundle(output_dir: Path, document: dict[str, Any], *, replace: bool) -> None:
    """Publish JSON+Markdown together; an explicit flag is needed to replace one."""
    output_dir = output_dir.resolve()
    if output_dir.exists() and not replace:
        raise ScorecardError(f"output directory already exists; use --replace for derived scorecard refresh: {output_dir}")
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{output_dir.name}.staging-{uuid.uuid4().hex}"
    backup: Path | None = None
    try:
        staging.mkdir()
        write_text(staging / SCORECARD_JSON_NAME, canonical_json(document))
        write_text(staging / SCORECARD_MARKDOWN_NAME, render_markdown(document))
        if output_dir.exists():
            backup = parent / f".{output_dir.name}.backup-{uuid.uuid4().hex}"
            os.replace(output_dir, backup)
        os.replace(staging, output_dir)
        if backup is not None:
            shutil.rmtree(backup)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup is not None and backup.exists() and not output_dir.exists():
            os.replace(backup, output_dir)
        raise


def check_scorecard_bundle(output_dir: Path, document: dict[str, Any]) -> None:
    expected_json = canonical_json(document)
    expected_markdown = render_markdown(document)
    json_path = output_dir / SCORECARD_JSON_NAME
    markdown_path = output_dir / SCORECARD_MARKDOWN_NAME
    if not json_path.is_file() or not markdown_path.is_file():
        raise ScorecardError(f"scorecard bundle is incomplete: expected {json_path} and {markdown_path}")
    actual_json = json_path.read_text(encoding="utf-8")
    actual_markdown = markdown_path.read_text(encoding="utf-8")
    if actual_json != expected_json:
        raise ScorecardError("scorecard.json is stale or does not exactly match current JSON evidence")
    if actual_markdown != expected_markdown:
        raise ScorecardError("SCORECARD.md is stale or does not exactly match scorecard.json/current evidence")


def must_stay_within(path: Path, boundary: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(boundary.resolve())
    except ValueError as error:
        raise ScorecardError(f"{label} must stay inside {boundary}: {resolved}") from error
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", action="store_true", help="write a derived JSON+Markdown scorecard bundle")
    action.add_argument("--check", action="store_true", help="rebuild in memory and require exact existing outputs")
    parser.add_argument("--run-dir", type=Path, required=True, help="delivery run directory; read-only")
    parser.add_argument("--output-dir", type=Path, required=True, help="derived scorecard directory below benchmark/editing-e2e-v1")
    parser.add_argument("--no-candidate-audit", type=Path, help="optional no_candidate_windows.json audit plan/result")
    parser.add_argument("--feedback-catalog", type=Path, help="optional mentor-feedback-regression-v1 catalog.json")
    parser.add_argument("--replace", action="store_true", help="allow replacing an existing derived scorecard bundle with a refreshed one")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = project_root()
    benchmark_root = root / "benchmark" / "editing-e2e-v1"
    try:
        run_dir = must_stay_within(args.run_dir, root, "run_dir")
        output_dir = must_stay_within(args.output_dir, benchmark_root, "output_dir")
        audit_path = must_stay_within(args.no_candidate_audit, benchmark_root, "no_candidate_audit") if args.no_candidate_audit else None
        catalog_path = args.feedback_catalog or benchmark_root / "mentor-feedback-regression-v1" / "catalog.json"
        catalog_path = must_stay_within(catalog_path, benchmark_root, "feedback_catalog")
        document = build_scorecard(
            run_dir=run_dir,
            no_candidate_audit_path=audit_path,
            feedback_catalog_path=catalog_path,
            root=root,
        )
        if args.build:
            write_scorecard_bundle(output_dir, document, replace=args.replace)
            print(
                f"PASS: wrote development scorecard {output_dir} "
                f"(status={document['scorecard_status']['status']}; quality_pass=false)"
            )
        else:
            require(not args.replace, "--replace is only valid with --build")
            check_scorecard_bundle(output_dir, document)
            print(
                f"PASS: scorecard exactly matches current evidence {output_dir} "
                f"(status={document['scorecard_status']['status']}; quality_pass=false)"
            )
    except (ScorecardError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
