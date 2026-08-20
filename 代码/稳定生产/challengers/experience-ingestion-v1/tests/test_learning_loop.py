"""Contract tests for feedback classification and Challenger policy cards."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
PROJECT = HERE.parents[3]
ORCHESTRATOR = PROJECT / "main" / "orchestrator"
for path in (SCRIPTS, ORCHESTRATOR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from apply_preference_snapshot import annotate_candidates  # noqa: E402
from build_policy_cards import build_policy_cards  # noqa: E402
from classify_feedback import classify_feedback  # noqa: E402
import event_identity as run_events  # noqa: E402


def _record(case_id: str, *, decision: str = "reject", feedback: str = "",
            start: int = 1000, end: int = 2000, text: str = "这个",
            episode: str = "EP04") -> dict:
    return {
        "case_id": case_id,
        "episode_id": episode,
        "candidate": {
            "reason_key": "filler_hesitation",
            "source_track_id": "track_01",
            "start_sample": start,
            "end_sample": end,
            "proposed_text": text,
            "clause_position": "clause-mid",
        },
        "label": {
            "decision": decision,
            "reviewer": "reviewer",
            "decided_at": "2026-08-16T00:00:00Z",
            "feedback": feedback,
        },
        "quality": {"rule_analysis_eligible": True},
    }


class LearningLoopTests(unittest.TestCase):
    def test_feedback_classes_keep_asr_execution_and_semantics_separate(self) -> None:
        self.assertEqual(classify_feedback("英文专名识别错了")["class"], "asr_error")
        self.assertEqual(classify_feedback("剪辑痕迹很重，声音明显小了")["class"], "execution_issue")
        self.assertEqual(classify_feedback("剪得不自然")["class"], "execution_issue")
        self.assertEqual(classify_feedback("保留活人感，对别人的认可")["class"], "semantic_keep")
        self.assertEqual(classify_feedback("这个重复可以剪")["class"], "semantic_cut")
        self.assertEqual(classify_feedback("一个完整的单词，为什么要剪")["class"], "false_positive")

    def test_feedback_multilabel_preserves_keep_when_execution_is_also_reported(self) -> None:
        result = classify_feedback("这是完整句，不要剪；剪辑痕迹很明显，声音变小了")
        self.assertEqual(result["class"], "semantic_keep")  # legacy projection
        self.assertEqual(result["primary_class"], "semantic_keep")
        self.assertEqual(result["classes"], ["semantic_keep", "execution_issue"])
        self.assertIn("完整句", result["evidence_by_class"]["semantic_keep"])
        self.assertIn("剪辑痕迹", result["evidence_by_class"]["execution_issue"])
        self.assertEqual(result["primary_selection"]["policy"], "multilabel-safety-and-semantic-primary-v1")

    def test_feedback_negated_transient_event_is_false_positive(self) -> None:
        direct = classify_feedback("没有咳嗽，根本没有碰麦")
        self.assertEqual(direct["primary_class"], "false_positive")
        self.assertIn("false_positive", direct["classes"])
        self.assertTrue(any("否定事件" in item for item in direct["evidence_by_class"]["false_positive"]))

        shorthand = classify_feedback("根本没有", reason_key="mic_bump_like")
        self.assertEqual(shorthand["class"], "false_positive")
        self.assertTrue(any("瞬态候选否定" in item for item in shorthand["evidence"]))

        # The shorthand has no reliable meaning without the candidate family.
        self.assertEqual(classify_feedback("根本没有")["class"], "unknown")

    def test_policy_cards_are_challenger_only_and_preserve_examples(self) -> None:
        records = [
            _record("case-keep", decision="reject", feedback="保留活人感"),
            _record("case-exec", decision="reject", feedback="剪辑痕迹很重", text="然后"),
        ]
        cards = build_policy_cards(records, snapshot_id="test-snapshot")
        keep = next(card for card in cards if card["feedback_class"] == "semantic_keep")
        self.assertEqual(keep["status"], "candidate")
        self.assertFalse(keep["safety"]["can_create_human_approved"])
        self.assertIn("case-keep", keep["source_case_ids"])

    def test_policy_cards_keep_all_evidenced_feedback_classes(self) -> None:
        cards = build_policy_cards([
            _record("case-multilabel", feedback="完整句，不要剪；但剪辑痕迹很重"),
        ], snapshot_id="test-snapshot")
        by_class = {card["feedback_class"]: card for card in cards}
        self.assertEqual(set(by_class), {"semantic_keep", "execution_issue"})
        self.assertEqual(by_class["semantic_keep"]["source_case_ids"], ["case-multilabel"])
        self.assertEqual(by_class["execution_issue"]["source_case_ids"], ["case-multilabel"])
        self.assertEqual(
            by_class["semantic_keep"]["examples"][0]["feedback_classes"],
            ["semantic_keep", "execution_issue"],
        )

    def test_preference_snapshot_never_adds_a_decision(self) -> None:
        records = [
            _record("case-reject", feedback="保留活人感"),
            _record("case-accept", decision="accept", feedback="很好", text="呃"),
        ]
        candidates, report = annotate_candidates(
            [
                {"candidate_id": "C1", "reason_key": "filler_hesitation", "proposed_text": "这个", "clause_position": "clause-mid"},
                {"candidate_id": "C2", "reason_key": "filler_hesitation", "proposed_text": "呃", "clause_position": "clause-mid"},
            ], {}, records, "snapshot-sha", episode_id="EP04",
        )
        self.assertEqual(report["policy"], "review_priority_only; no decision, no auto-cut, no filtering")
        self.assertNotIn("decision", candidates[0]["experience_signal"])

    def test_event_identity_is_fail_closed_and_keeps_execution_separate(self) -> None:
        historical = {
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
        current = dict(historical, start_seconds=9.9, end_seconds=10.55, candidate_id="NEW", decision=None)
        result = run_events.classify_candidate_against_history(current, [historical])
        self.assertEqual(result.route, run_events.EventRoute.REJECTED_EXECUTION_ISSUE)
        self.assertFalse(result.suppress_candidate)
        self.assertTrue(result.boundary_review_required)
        self.assertIsNone(result.semantic_decision)

    def test_event_identity_normalizes_traditional_only_for_matching(self) -> None:
        raw = "這個什麼"
        self.assertEqual(raw, "這個什麼")
        self.assertEqual(run_events.normalize_event_text(raw), run_events.normalize_event_text("这个什么"))


if __name__ == "__main__":
    unittest.main()
