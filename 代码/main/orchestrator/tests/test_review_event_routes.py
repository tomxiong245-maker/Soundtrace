"""Contract tests for future review-package event-route sidecars."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ORCHESTRATOR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import review_event_routes as rer  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_run(
    root: Path,
    run_id: str,
    candidates: list[dict],
    *,
    decisions: list[dict] | None = None,
    audio_sha: str = "audio-sha",
) -> Path:
    run = root / run_id
    run.mkdir(parents=True)
    tracks = [{
        "track_id": "track_01",
        "audio_sha256": audio_sha,
        "sample_rate_hz": 48_000,
        "frame_count": 1_000_000,
    }]
    write_json(run / "run_identity.json", {
        "schema_version": "run-identity-v1", "episode_id": "EPTEST", "run_id": run_id,
    })
    write_json(run / "input_manifest.json", {
        "schema_version": "delivery-input-manifest-v1", "episode_id": "EPTEST", "run_id": run_id,
        "sample_rate_hz": 48_000, "frame_count": 1_000_000, "tracks": tracks,
    })
    package = {
        "schema_version": "review-product-mvp-v2",
        "episode_id": "EPTEST", "run_id": run_id,
        "package_id": f"package-{run_id}", "review_manifest_sha256": f"manifest-{run_id}",
        "candidates": candidates,
    }
    write_json(run / "review_bundle/review_package.json", package)
    if decisions is not None:
        write_json(run / "human_decisions.json", {
            "schema_version": "human-decisions-mvp-v1", "episode_id": "EPTEST", "run_id": run_id,
            "decisions": decisions,
        })
    return run


class ReviewEventRouteTests(unittest.TestCase):
    def test_sidecar_has_per_candidate_routes_and_never_creates_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            historical_candidates = [
                {"candidate_id": "OLD-EXACT", "source_track_id": "track_01", "proposed_delete_text": "呃", "start_seconds": 10.0, "end_seconds": 10.4},
                {"candidate_id": "OLD-BOUND", "source_track_id": "track_01", "proposed_delete_text": "然后", "start_seconds": 20.0, "end_seconds": 20.4},
                {"candidate_id": "OLD-FP", "source_track_id": "track_01", "proposed_delete_text": "咳嗽", "start_seconds": 30.0, "end_seconds": 30.4},
                {"candidate_id": "OLD-EXEC", "source_track_id": "track_01", "proposed_delete_text": "也是", "start_seconds": 40.0, "end_seconds": 40.4},
            ]
            decisions = [
                {"candidate_id": "OLD-EXACT", "decision": "human_accept", "feedback": "很好"},
                {"candidate_id": "OLD-BOUND", "decision": "human_accept", "feedback": ""},
                {"candidate_id": "OLD-FP", "decision": "human_reject", "feedback": "根本没有这个咳嗽候选"},
                {"candidate_id": "OLD-EXEC", "decision": "human_reject", "feedback": "剪辑痕迹很重，声音明显小了"},
            ]
            historical = make_run(root, "EPTEST-history", historical_candidates, decisions=decisions)
            current_candidates = [
                {"candidate_id": "NEW-EXACT", "source_track_id": "track_01", "proposed_delete_text": "呃", "start_seconds": 10.02, "end_seconds": 10.43},
                {"candidate_id": "NEW-BOUND", "source_track_id": "track_01", "proposed_delete_text": "然后", "start_seconds": 19.85, "end_seconds": 20.55},
                {"candidate_id": "NEW-FP", "source_track_id": "track_01", "proposed_delete_text": "咳嗽", "start_seconds": 30.01, "end_seconds": 30.39},
                {"candidate_id": "NEW-EXEC", "source_track_id": "track_01", "proposed_delete_text": "也是", "start_seconds": 39.9, "end_seconds": 40.55},
                {"candidate_id": "NEW-EVENT", "source_track_id": "track_01", "proposed_delete_text": "全新", "start_seconds": 80.0, "end_seconds": 80.3},
            ]
            current = make_run(root, "EPTEST-current", current_candidates)
            package_before = (current / "review_bundle/review_package.json").read_bytes()

            document = rer.build_event_routes(current, historical_runs=[historical])
            self.assertEqual(document["route_summary"], {
                "already_reviewed_exact": 1,
                "semantic_reuse_boundary_review": 1,
                "rejected_false_positive": 1,
                "rejected_execution_issue": 1,
                "new_event": 1,
            })
            routes = document["routes"]
            self.assertEqual(routes["NEW-EXACT"]["route"], "already_reviewed_exact")
            self.assertEqual(routes["NEW-EXACT"]["matched_case_id"], "EPTEST::EPTEST-history::OLD-EXACT")
            self.assertEqual(routes["NEW-BOUND"]["route"], "semantic_reuse_boundary_review")
            self.assertTrue(routes["NEW-BOUND"]["boundary_review_required"])
            self.assertEqual(routes["NEW-FP"]["route"], "rejected_false_positive")
            self.assertTrue(routes["NEW-FP"]["suppress_candidate"])
            self.assertEqual(routes["NEW-EXEC"]["route"], "rejected_execution_issue")
            self.assertFalse(routes["NEW-EXEC"]["suppress_candidate"])
            self.assertEqual(routes["NEW-EVENT"]["route"], "new_event")
            for row in routes.values():
                self.assertIsNone(row["current_decision"])
                self.assertEqual(row["current_decision_authority"], "NONE__HUMAN_REVIEW_REQUIRED")
                self.assertFalse(row["creates_edl_action"])
                self.assertFalse(row["creates_autocut_permission"])

            output = rer.write_event_routes(current, historical_runs=[historical])
            self.assertEqual(output, (current / "review_bundle/event_routes.json").resolve())
            self.assertEqual((current / "review_bundle/review_package.json").read_bytes(), package_before)
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(rer.validate_event_routes(persisted, json.loads((current / "review_bundle/review_package.json").read_text())), [])
            with self.assertRaises(FileExistsError):
                rer.write_event_routes(current, historical_runs=[historical])

    def test_empty_history_is_safe_new_event_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = make_run(root, "EPTEST-new", [{
                "candidate_id": "C1", "source_track_id": "track_01", "proposed_delete_text": "呃",
                "start_seconds": 1.0, "end_seconds": 1.2,
            }])
            document = rer.build_event_routes(current)
            self.assertEqual(document["route_summary"], {"new_event": 1})
            self.assertIsNone(document["routes"]["C1"]["matched_case_id"])

    def test_manifest_mismatch_is_rejected_before_frontend_consumes_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = make_run(root, "EPTEST-new", [{
                "candidate_id": "C1", "source_track_id": "track_01", "proposed_delete_text": "呃",
                "start_seconds": 1.0, "end_seconds": 1.2,
            }])
            document = rer.build_event_routes(current)
            package = json.loads((current / "review_bundle/review_package.json").read_text())
            document["source_review_manifest_sha256"] = "different"
            self.assertTrue(rer.validate_event_routes(document, package))

    def test_write_refuses_an_active_reviewer_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = make_run(root, "EPTEST-new", [{
                "candidate_id": "C1", "source_track_id": "track_01", "proposed_delete_text": "呃",
                "start_seconds": 1.0, "end_seconds": 1.2,
            }])
            write_json(current / "review_draft.json", {"decisions": []})
            with self.assertRaisesRegex(ValueError, "active reviewer draft"):
                rer.write_event_routes(current)


if __name__ == "__main__":
    unittest.main()
