"""Regression tests for event-level reviewed-candidate routing.

The EP04 fixture is metadata-only: no audio is opened or copied.  The tests
prove that a new candidate ID after boundary snapping still resolves to its
old human-reviewed event, while an execution-quality reject is not learned as
an unconditional semantic reject.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ORCHESTRATOR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import event_identity as ei  # noqa: E402


PROJECT = Path("/Users/renting/Desktop/minglue/剪辑项目")
EP04 = PROJECT / "main" / "runs" / "EP04"
V20 = EP04 / "EP04-v20-20260814-1617"
LABEL_LOOP = EP04 / "EP04-label-loop-v1-20260815-1805"
EXPECTATIONS = Path(__file__).with_name("event_identity_regression_expectations.json")


class EventIdentityUnitTests(unittest.TestCase):
    def test_event_key_ignores_candidate_id_and_normalizes_traditional_text(self) -> None:
        old = {
            "episode_id": "EP04",
            "source_audio_sha256": "audio-sha",
            "source_track_id": "track_01",
            "proposed_delete_text": "什麼",
            "start_seconds": 10.0,
            "end_seconds": 10.2,
            "candidate_id": "C001",
        }
        new = dict(old, proposed_delete_text="什么", candidate_id="C999")
        self.assertEqual(ei.canonical_event_identity(old).event_key, ei.canonical_event_identity(new).event_key)
        self.assertEqual(ei.normalize_event_text("什麼， OK"), "什么ok")

    def test_small_boundary_drift_is_exact_reuse(self) -> None:
        history = {
            "episode_id": "EP04",
            "source_audio_sha256": "audio-sha",
            "source_track_id": "track_01",
            "proposed_delete_text": "呃",
            "start_seconds": 10.0,
            "end_seconds": 10.4,
            "candidate_id": "OLD",
            "decision": "human_accept",
            "feedback": "很好",
        }
        current = dict(history, start_seconds=10.02, end_seconds=10.43, candidate_id="NEW", decision=None)
        result = ei.classify_candidate_against_history(current, [history])
        self.assertEqual(result.route, ei.EventRoute.ALREADY_REVIEWED_EXACT)
        self.assertEqual(result.category, ei.MatchCategory.EXACT)
        self.assertFalse(result.boundary_review_required)
        self.assertEqual(result.semantic_decision, "human_accept")

    def test_false_positive_feedback_is_suppressible(self) -> None:
        history = {
            "episode_id": "EP04",
            "source_audio_sha256": "audio-sha",
            "source_track_id": "track_01",
            "proposed_delete_text": "咳嗽",
            "start_seconds": 10.0,
            "end_seconds": 10.4,
            "candidate_id": "OLD",
            "decision": "human_reject",
            "feedback": "没有咳嗽，根本没有这个候选",
        }
        current = dict(history, candidate_id="NEW", decision=None, feedback=None)
        result = ei.classify_candidate_against_history(current, [history])
        self.assertEqual(result.route, ei.EventRoute.REJECTED_FALSE_POSITIVE)
        self.assertTrue(result.suppress_candidate)
        self.assertEqual(result.feedback_class, ei.FeedbackClass.FALSE_POSITIVE)

    def test_execution_reject_is_not_a_semantic_reject(self) -> None:
        history = {
            "episode_id": "EP04",
            "source_audio_sha256": "audio-sha",
            "source_track_id": "track_01",
            "proposed_delete_text": "也是",
            "start_seconds": 10.0,
            "end_seconds": 10.4,
            "candidate_id": "OLD",
            "decision": "human_reject",
            "feedback": "剪辑痕迹很重，声音明显小了",
        }
        current = dict(history, start_seconds=9.9, end_seconds=10.55, candidate_id="NEW", decision=None, feedback=None)
        result = ei.classify_candidate_against_history(current, [history])
        self.assertEqual(result.route, ei.EventRoute.REJECTED_EXECUTION_ISSUE)
        self.assertFalse(result.suppress_candidate)
        self.assertIsNone(result.semantic_decision)
        self.assertTrue(result.boundary_review_required)

    def test_missing_identity_is_new_event(self) -> None:
        history = {
            "episode_id": "EP04",
            "source_audio_sha256": "audio-sha",
            "source_track_id": "track_01",
            "proposed_delete_text": "呃",
            "start_seconds": 10.0,
            "end_seconds": 10.4,
            "decision": "human_accept",
        }
        current = dict(history, source_audio_sha256="different-audio")
        result = ei.classify_candidate_against_history(current, [history])
        self.assertEqual(result.route, ei.EventRoute.NEW_EVENT)
        self.assertEqual(result.category, ei.MatchCategory.NEW)

    def test_nested_experience_case_label_is_supported(self) -> None:
        historical_case = {
            "episode_id": "EP04",
            "source_audio_sha256": "audio-sha",
            "candidate": {
                "candidate_id": "OLD",
                "source_track_id": "track_01",
                "proposed_delete_text": "这个",
                "start_seconds": 10.0,
                "end_seconds": 10.4,
            },
            "label": {
                "decision": "reject",
                "feedback": "这是完整的词，应保留。",
            },
        }
        current = {
            "episode_id": "EP04",
            "source_audio_sha256": "audio-sha",
            "source_track_id": "track_01",
            "proposed_delete_text": "这个",
            "start_seconds": 10.01,
            "end_seconds": 10.39,
        }
        result = ei.classify_candidate_against_history(current, [historical_case])
        self.assertEqual(result.route, ei.EventRoute.REJECTED_FALSE_POSITIVE)
        self.assertTrue(result.suppress_candidate)


class EP04ReviewedEventRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not V20.is_dir() or not LABEL_LOOP.is_dir():
            raise unittest.SkipTest("EP04 regression runs are not available")
        cls.expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
        cls.report = ei.build_run_report(LABEL_LOOP, V20)
        cls.by_candidate = {
            row["candidate"]["candidate_id"]: row for row in cls.report["matches"]
        }

    def test_five_rekeyed_events_are_found(self) -> None:
        for expected in self.expectations["expected_matches"]:
            current_id = expected["current_candidate_id"]
            historical_id = expected["historical_candidate_id"]
            with self.subTest(current_id=current_id):
                row = self.by_candidate[current_id]
                self.assertIsNotNone(row["historical"])
                self.assertEqual(row["historical"]["candidate_id"], historical_id)
                self.assertGreaterEqual(row["overlap_ratio"], ei.OVERLAP_THRESHOLD)
                self.assertEqual(row["route"], expected["route"])
                self.assertEqual(row["historical_decision"], expected["historical_decision"])

    def test_accept_pairs_need_boundary_review_not_old_listening_approval(self) -> None:
        for current_id in ("C007", "C023", "C034"):
            row = self.by_candidate[current_id]
            self.assertEqual(row["route"], ei.EventRoute.SEMANTIC_REUSE_BOUNDARY_REVIEW.value)
            self.assertEqual(row["category"], ei.MatchCategory.BOUNDARY_REVIEW.value)
            self.assertEqual(row["semantic_decision"], "human_accept")
            self.assertTrue(row["boundary_review_required"])

    def test_execution_reject_pairs_stay_visible_for_rework(self) -> None:
        for current_id, old_feedback in (("C042", "剪辑的时候声音明显小了"), ("C044", "剪辑痕迹很重")):
            row = self.by_candidate[current_id]
            self.assertEqual(row["route"], ei.EventRoute.REJECTED_EXECUTION_ISSUE.value)
            self.assertFalse(row["suppress_candidate"])
            self.assertIsNone(row["semantic_decision"])
            self.assertEqual(row["historical_feedback"], old_feedback)
            self.assertEqual(row["feedback_class"], ei.FeedbackClass.EXECUTION_ISSUE.value)

    def test_report_is_explainable_and_has_frozen_thresholds(self) -> None:
        self.assertEqual(self.report["schema_version"], "event-identity-routing-v1")
        self.assertEqual(self.report["overlap_threshold"], self.expectations["thresholds"]["overlap_coefficient_minimum"])
        self.assertEqual(self.report["boundary_drift_threshold_ms"], self.expectations["thresholds"]["exact_boundary_drift_max_ms"])
        for row in self.report["matches"]:
            self.assertIn("candidate", row)
            self.assertIn("event_key", row["candidate"])
            self.assertIn("reasons", row)


if __name__ == "__main__":
    unittest.main()
