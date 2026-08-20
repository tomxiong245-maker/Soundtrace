#!/usr/bin/env python3
"""Focused contract tests for text-first, optional-audio MVP review."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_mvp import validate_decisions  # noqa: E402


class TextFirstDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = {
            "candidate_id": "C001",
            "semantic_sha256": "candidate-sha",
            "previews": {
                "original_sha256": "original-sha",
                "proposed_cut_sha256": "cut-sha",
            },
        }
        self.package = {
            "package_id": "pkg-1",
            "review_manifest_sha256": "manifest-1",
            "candidates": [self.candidate],
        }

    def doc(self, listened: dict | None = None, basis: str = "text_only", feedback: str = "") -> dict:
        return {
            "schema_version": "human-decisions-mvp-v1",
            "package_id": "pkg-1",
            "review_manifest_sha256": "manifest-1",
            "reviewer": "测试审核人",
            "decisions": [{
                "candidate_id": "C001",
                "candidate_semantic_sha256": "candidate-sha",
                "decision": "accept",
                "reviewer": "测试审核人",
                "decided_at": "2026-08-11T12:00:00Z",
                "review_basis": basis,
                "listened_previews": listened or {},
                "feedback": feedback,
            }],
        }

    def test_text_only_decision_is_valid(self) -> None:
        self.assertEqual(validate_decisions(self.package, self.doc()), [])

    def test_completed_optional_audio_is_valid(self) -> None:
        listened = {
            "original_sha256": "original-sha",
            "original_listened_at": "2026-08-11T12:00:01Z",
            "proposed_cut_sha256": "cut-sha",
            "proposed_cut_listened_at": "2026-08-11T12:00:02Z",
        }
        self.assertEqual(
            validate_decisions(self.package, self.doc(listened, "text_and_audio")), []
        )

    def test_tampered_optional_audio_is_rejected(self) -> None:
        listened = {
            "original_sha256": "wrong-sha",
            "original_listened_at": "2026-08-11T12:00:01Z",
        }
        errors = validate_decisions(self.package, self.doc(listened, "text_with_audio"))
        self.assertIn("C001: original preview mismatch", errors)

    def test_audio_time_without_matching_asset_is_rejected(self) -> None:
        listened = {"original_listened_at": "2026-08-11T12:00:01Z"}
        errors = validate_decisions(self.package, self.doc(listened, "text_with_audio"))
        self.assertIn("C001: original preview mismatch", errors)

    def test_optional_feedback_is_valid_and_preserved_for_submission(self) -> None:
        self.assertEqual(
            validate_decisions(self.package, self.doc(feedback="应保留完整句语义。")), []
        )

    def test_feedback_length_is_bounded(self) -> None:
        errors = validate_decisions(self.package, self.doc(feedback="x" * 501))
        self.assertIn("C001: feedback exceeds 500 characters", errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
