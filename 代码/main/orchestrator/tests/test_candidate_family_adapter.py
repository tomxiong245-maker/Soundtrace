from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys


ORCHESTRATOR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import candidate_family_adapter as adapter  # noqa: E402


class CandidateFamilyAdapterTests(unittest.TestCase):
    def test_self_correction_normalizes_to_high_risk_global_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            detector = root / "self.py"
            rules = root / "rules.json"
            detector.write_text("detector", encoding="utf-8")
            rules.write_text("rules", encoding="utf-8")
            rows = adapter.normalize_self_correction_rows(
                [
                    {
                        "start_sample": 48000,
                        "end_sample": 72000,
                        "abandoned_span": {"text": "先说错"},
                        "retry_span": {"text": "重新说"},
                        "cut_scope": "both_spans",
                    }
                ],
                track_id="track_01",
                sample_rate_hz=48000,
                detector_path=detector,
                rules_path=rules,
            )
            row = rows[0]
            self.assertEqual(row["candidate_kind"], "self_correction")
            self.assertEqual(row["cut_scope"], "abandoned_span_only")
            self.assertTrue(row["review_display"]["requires_audio_review"])
            self.assertEqual(row["default_action"], "human_review_required")

    def test_transient_adapter_keeps_only_cough_and_marks_source_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            detector = root / "transient.py"
            rules = root / "rules.json"
            detector.write_text("detector", encoding="utf-8")
            rules.write_text("rules", encoding="utf-8")
            rows = adapter.normalize_transient_rows(
                [
                    {"reason_key": "cough_like", "start_sample": 100, "end_sample": 200},
                    {"reason_key": "mic_bump_like", "start_sample": 300, "end_sample": 400},
                    {"reason_key": "thump_like", "start_sample": 500, "end_sample": 600},
                ],
                track_id="track_02",
                sample_rate_hz=48000,
                detector_path=detector,
                rules_path=rules,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reason_key"], "cough_like")
            self.assertEqual(rows[0]["action_type"], "source_track_gate")
            self.assertEqual(rows[0]["cut_scope"], "source_track_gate_only")


if __name__ == "__main__":
    unittest.main()
