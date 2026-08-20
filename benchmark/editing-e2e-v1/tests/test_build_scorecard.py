#!/usr/bin/env python3
"""Small JSON-only tests for the development scorecard."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from build_scorecard import (  # noqa: E402
    ScorecardError,
    build_scorecard,
    check_scorecard_bundle,
    write_scorecard_bundle,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_run(root: Path, *, state: str = "CALIBRATION_REVIEW_REQUIRED") -> tuple[Path, dict[str, str]]:
    run_dir = root / "main" / "runs" / "EPTEST" / "EPTEST-v1"
    run_dir.mkdir(parents=True)
    identity = {"schema_version": "run-identity-v1", "episode_id": "EPTEST", "run_id": "EPTEST-v1"}
    identity_path = run_dir / "run_identity.json"
    write_json(identity_path, identity)
    identity_sha = sha256(identity_path)
    write_json(
        run_dir / "state.json",
        {"schema_version": "delivery-state-v1", "episode_id": "EPTEST", "run_id": "EPTEST-v1", "state": state},
    )
    input_manifest = {
        "schema_version": "delivery-input-manifest-v1",
        "episode_id": "EPTEST",
        "run_id": "EPTEST-v1",
        "run_identity_sha256": identity_sha,
        "sample_rate_hz": 100,
        "frame_count": 360000,
        "tracks": [
            {"track_id": "track_01", "input_relpath": "fake/not-opened.wav", "sample_rate_hz": 100, "frame_count": 360000},
            {"track_id": "track_02", "input_relpath": "fake/not-opened.wav", "sample_rate_hz": 100, "frame_count": 360000},
        ],
    }
    input_path = run_dir / "input_manifest.json"
    write_json(input_path, input_manifest)
    candidates = {
        "schema_version": "delivery-all-candidates-v1",
        "episode_id": "EPTEST",
        "run_id": "EPTEST-v1",
        "run_identity_sha256": identity_sha,
        "candidates": [
            {"candidate_id": "C001", "candidate_kind": "filler_hesitation", "risk_level": "low"},
            {"candidate_id": "C002", "candidate_kind": "immediate_repetition", "risk_level": "low"},
        ],
    }
    candidates_path = run_dir / "all_candidates.json"
    write_json(candidates_path, candidates)
    calibration_candidates = [
        {"candidate_id": "C001"},
        {"candidate_id": "C002"},
    ]
    write_json(
        run_dir / "calibration_source.json",
        {
            "schema_version": "delivery-calibration-source-v1",
            "episode_id": "EPTEST",
            "run_id": "EPTEST-v1",
            "candidates": calibration_candidates,
            "counts": {"blocked": 3, "blocked_acoustic": 1},
            "delivery_calibration_selection": {
                "selection_report": {"review_budget": 20, "remaining_budget": 18, "selected_total": 2}
            },
        },
    )
    package = {
        "schema_version": "review-package-v1",
        "episode_id": "EPTEST",
        "run_id": "EPTEST-v1",
        "package_id": "package-1",
        "review_manifest_sha256": "a" * 64,
        "candidates": [
            {"candidate_id": "C001", "semantic_sha256": "b" * 64},
            {"candidate_id": "C002", "semantic_sha256": "c" * 64},
        ],
    }
    write_json(run_dir / "review_bundle" / "review_package.json", package)
    return run_dir, {
        "identity": identity_sha,
        "input": sha256(input_path),
        "candidates": sha256(candidates_path),
    }


def make_audit(root: Path, hashes: dict[str, str]) -> Path:
    path = root / "benchmark" / "editing-e2e-v1" / "audits" / "audit.json"
    write_json(
        path,
        {
            "schema_version": "no-candidate-window-audit-v1",
            "audit_id": "audit-1",
            "provenance": {
                "run_id": "EPTEST-v1",
                "episode_id": "EPTEST",
                "run_identity_sha256": hashes["identity"],
                "input_manifest_sha256": hashes["input"],
                "all_candidates_sha256": hashes["candidates"],
            },
            "windows": [
                {"window_id": "NC001", "human_review_status": "PENDING_HUMAN_LISTENING", "human_finding": None},
                {"window_id": "NC002", "human_review_status": "PENDING_HUMAN_LISTENING", "human_finding": None},
            ],
        },
    )
    return path


def make_catalog(root: Path) -> Path:
    path = root / "benchmark" / "editing-e2e-v1" / "mentor-feedback-regression-v1" / "catalog.json"
    write_json(
        path,
        {
            "schema_version": "mentor-feedback-regression-v1",
            "benchmark_scope": {"split": "development", "frozen": False, "training_gold": False},
            "entries": [
                {"human_decision": {"decision": "accept"}, "feedback": {"verbatim": "自然"}},
                {"human_decision": {"decision": "reject"}, "feedback": {"verbatim": ""}},
            ],
            "summary": {"total_decisions": 2, "accept": 1, "reject": 1, "feedback_nonempty": 1},
        },
    )
    return path


def write_transition_reports(run_dir: Path, identity_sha: str) -> None:
    for variant in ("human_approved", "machine_assisted_draft"):
        write_json(
            run_dir / f"render_{variant}" / "transition_qc.json",
            {
                "schema_version": "rendered-transition-qc-v1",
                "status": "OBJECTIVE_ANOMALY_RANKING_SUBJECTIVE_LISTENING_REQUIRED",
                "episode_id": "EPTEST",
                "run_id": "EPTEST-v1",
                "variant": variant,
                "run_identity_sha256": identity_sha,
                "transition_count": 1,
                "priority_relisten_count": 1,
                "ranked_transition_ids": ["render-cut-0001"],
                "transitions": [{"transition_id": "render-cut-0001"}],
            },
        )


class ScorecardTests(unittest.TestCase):
    def test_pending_review_audit_and_render_are_not_quality_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, hashes = make_run(root)
            document = build_scorecard(
                run_dir=run_dir,
                no_candidate_audit_path=make_audit(root, hashes),
                feedback_catalog_path=make_catalog(root),
                root=root,
            )
            self.assertEqual(document["scorecard_status"]["status"], "INCOMPLETE_HUMAN_REVIEW_REQUIRED")
            self.assertFalse(document["scorecard_status"]["quality_pass"])
            self.assertEqual(document["candidate_burden"]["all_candidate_count"], 2)
            self.assertEqual(document["current_human_review"]["status"], "PENDING_HUMAN_REVIEW")
            self.assertEqual(document["no_candidate_audit"]["status"], "NOT_MEASURED")
            self.assertEqual(document["transition_qc"]["status"], "NOT_MEASURED")
            self.assertEqual(document["quality_metrics"]["candidate_recall_against_human_edit_map"]["status"], "NOT_MEASURED")

    def test_transition_ranking_stays_objective_only_and_check_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, hashes = make_run(root, state="FINAL_QC_REQUIRED")
            write_transition_reports(run_dir, hashes["identity"])
            document = build_scorecard(
                run_dir=run_dir,
                no_candidate_audit_path=make_audit(root, hashes),
                feedback_catalog_path=make_catalog(root),
                root=root,
            )
            self.assertEqual(document["transition_qc"]["status"], "MEASURED_OBJECTIVE_PRIORITY_ONLY")
            self.assertEqual(document["transition_qc"]["subjective_naturalness"]["status"], "NOT_MEASURED")
            output_dir = root / "benchmark" / "editing-e2e-v1" / "scorecards" / "current"
            write_scorecard_bundle(output_dir, document, replace=False)
            check_scorecard_bundle(output_dir, document)
            changed = dict(document)
            changed["scorecard_status"] = dict(document["scorecard_status"], status="STALE")
            with self.assertRaises(ScorecardError):
                check_scorecard_bundle(output_dir, changed)


if __name__ == "__main__":
    unittest.main()
