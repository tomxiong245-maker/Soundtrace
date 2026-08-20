#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))

from production_edit_policy import apply_policy, evaluate_candidate, load_policy  # noqa: E402


POLICY_PATH = ROOT / "orchestrator" / "editing_policy.guards-v1.json"


def candidate(candidate_id: str, *, kind: str = "filler_hesitation", text: str = "呃", **extra: object) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_kind": kind,
        "reason_key": kind,
        "proposed_delete_text": text,
        "clause_position": "clause-tail",
        **extra,
    }


class ProductionEditPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(POLICY_PATH)

    def test_known_compound_and_english_fragment_are_preserved(self) -> None:
        chinese = candidate(
            "C1", text="额", lexical_context={"after": {"text": "度", "gap_seconds": 0.04}}
        )
        english = candidate(
            "C2", text="er", lexical_context={"before": {"text": "serv", "gap_seconds": 0.02}}
        )
        self.assertEqual(evaluate_candidate(chinese, self.policy)["route"], "auto_preserve")
        self.assertEqual(evaluate_candidate(english, self.policy)["route"], "auto_preserve")

    def test_complete_sentence_and_ambiguous_repetition_do_not_auto_cut(self) -> None:
        internal = candidate("C1", text="然后", clause_position="clause-mid")
        repeated = candidate(
            "C2", kind="immediate_repetition", text="我们", repetition_signature={"has_signature": False}
        )
        self.assertEqual(evaluate_candidate(internal, self.policy)["route"], "auto_preserve")
        self.assertEqual(evaluate_candidate(repeated, self.policy)["route"], "human_review_required")

    def test_no_row_can_become_auto_cut_eligible_under_current_policy(self) -> None:
        rows, report = apply_policy(
            [candidate("C1", text="呃"), candidate("C2", kind="global_long_pause", text="")], self.policy
        )
        self.assertEqual(rows[0]["editing_policy"]["route"], "machine_calibration_eligible")
        self.assertEqual(rows[1]["editing_policy"]["route"], "human_review_required")
        self.assertEqual(report["summary"]["auto_cut_eligible"], 0)
        self.assertEqual(report["autocut_policy"]["status"], "NOT_APPROVED")


if __name__ == "__main__":
    unittest.main()
