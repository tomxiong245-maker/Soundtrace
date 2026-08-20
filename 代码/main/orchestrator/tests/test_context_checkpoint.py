"""Tests for the small context view; no project state or media is touched."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "context_checkpoint.py"
SPEC = importlib.util.spec_from_file_location("context_checkpoint", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ContextCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = {
            "schema_version": "current-delivery-facts-v1",
            "current_review": {
                "run_id": "EPTEST-run",
                "state": "CALIBRATION_REVIEW_REQUIRED",
                "run_relpath": "main/runs/EPTEST/EPTEST-run",
                "candidate_count": 4,
                "review_package_candidate_count": 3,
                "frontend_visible_candidate_count": 3,
                "candidate_rules_version": "filler-global-pause-v18",
                "reuse_analysis_from": "EPTEST-analysis",
                "reuse_semantic_from": "EPTEST-semantic",
                "experience_snapshot_id": "snapshot-test",
            },
            "best_local_delivery": {
                "run_id": "EPTEST-best",
                "state": "DELIVERY_DECISION_RECORDED",
                "approval_mode": "human_whole_episode_audition",
            },
            "asr": {"display_name": "faster-whisper small", "reused_from_run": "EPTEST-analysis"},
            "music": {
                "template_id": "reference-linear-v1",
                "voice_start_seconds": 5.0,
                "intro_fade_out_end_seconds": 16.0,
                "outro_fade_in_lead_seconds": 22.0,
                "outro_music_tail_seconds": 37.976,
            },
            "development_benchmark": {
                "contract": "editing-e2e-v1",
                "scorecard_status": "INCOMPLETE_HUMAN_REVIEW_REQUIRED",
            },
            "learning_loop": {
                "status": "CHALLENGER_EVIDENCE_GENERATED__ACTIVE_GUARDS_IN_PRODUCTION",
                "evidence_run_relpath": "main/runs/LABEL-LEARNING-v3-test",
                "evidence_manifest_sha256": "c" * 64,
                "autocut_policy": "NOT_APPROVED",
                "snapshot": {"records": 65, "policy_cards": 20},
                "production_guard_policy": {
                    "policy_id": "editing-policy-guards-v1",
                    "policy_sha256": "d" * 64,
                    "status": "ACTIVE_GUARDS_ONLY",
                },
                "production_promotion": {
                    "evidence_run_relpath": "main/runs/POLICY-PROMOTION-v1-test",
                    "report_status": "NOT_APPROVED",
                },
            },
        }

    def test_summary_is_short_and_binds_source_sha(self) -> None:
        summary = MODULE.build_summary(self.facts, "a" * 64)
        self.assertIn("EPTEST-run", summary)
        self.assertIn("source_sha256：`" + "a" * 64 + "`", summary)
        self.assertIn("机器预测不得伪装成人工标签", summary)
        self.assertIn("editing-policy-guards-v1", summary)
        self.assertLess(len(summary), 7000)

    def test_checkpoint_preserves_hard_requirements(self) -> None:
        checkpoint = MODULE.build_checkpoint(self.facts, "b" * 64)
        self.assertEqual(checkpoint["source"]["status_sha256"], "b" * 64)
        self.assertEqual(checkpoint["current_review"]["run_id"], "EPTEST-run")
        self.assertEqual(checkpoint["hard_requirements"]["feedback_max_chars"], 500)
        self.assertTrue(checkpoint["hard_requirements"]["human_decision_required"])
        self.assertEqual(checkpoint["hard_requirements"]["autocut_policy"], "NOT_APPROVED")
        self.assertEqual(
            checkpoint["learning_loop"]["production_guard_policy"]["policy_id"],
            "editing-policy-guards-v1",
        )


if __name__ == "__main__":
    unittest.main()
