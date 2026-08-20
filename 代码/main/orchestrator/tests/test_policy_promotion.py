#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))

from policy_promotion import evaluate_policy_promotion, write_report  # noqa: E402


def card(policy_id="POL-007", reason="filler_hesitation", action="suppress_same_pattern_in_challenger", *, count=3, can_machine=True):
    return {
        "policy_id": policy_id,
        "action": action,
        "conditions": {"reason_key": reason},
        "source_case_ids": [f"case-{i}" for i in range(count)],
        "safety": {"can_enter_machine_assisted_draft_after_validation": can_machine},
    }


class PolicyPromotionTests(unittest.TestCase):
    def test_current_evidence_fails_closed_and_keeps_autocut_disabled(self):
        report = evaluate_policy_promotion(
            {"cards": [card()]},
            {"recommendations": [{"reason_key": "filler_hesitation", "action": "NO_PRODUCTION_CHANGE"}]},
            {"status": "NOT_READY", "checks": {
                "valid_cases": 27, "valid_episodes": 2, "reviewer_count": 1,
                "has_independent_benchmark": False, "has_independent_review": False,
                "has_rollback_drill": False,
            }},
        )
        self.assertEqual(report["status"], "NOT_APPROVED")
        self.assertEqual(report["autocut_policy"]["eligible_policy_ids"], [])
        self.assertTrue(report["blockers"])
        self.assertEqual(len(report["guard_candidates"]), 1)

    def test_high_risk_card_can_never_be_autocut(self):
        report = evaluate_policy_promotion(
            {"cards": [card("POL-PAUSE", "global_long_pause", "propose_for_human_review", count=30)]},
            {"recommendations": []},
            {"status": "READY", "checks": {
                "valid_cases": 500, "valid_episodes": 10, "reviewer_count": 2,
                "has_independent_benchmark": True, "has_independent_review": True,
                "has_rollback_drill": True,
            }},
            authorization={"signed_by": "owner", "signed_at": "2026-08-16", "policy_ids": ["POL-PAUSE"]},
        )
        self.assertEqual(report["status"], "NOT_APPROVED")
        self.assertEqual(report["autocut_policy"]["eligible_policy_ids"], [])
        self.assertTrue(any("high-risk" in reason for reason in report["ineligible_cards"][0]["reasons"]))

    def test_report_is_human_readable_and_provenance_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            source = out / "source.json"
            source.write_text(json.dumps({"ok": True}), encoding="utf-8")
            write_report(out / "report", {"status": "NOT_APPROVED", "autocut_policy": {"status": "NOT_APPROVED", "eligible_policy_ids": []}, "blockers": ["x"], "guard_candidates": [], "ineligible_cards": []}, [source])
            self.assertTrue((out / "report" / "promotion_report.json").is_file())
            self.assertIn("NOT_APPROVED", (out / "report" / "PROMOTION_REPORT.md").read_text())


if __name__ == "__main__":
    unittest.main()
