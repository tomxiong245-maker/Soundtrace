"""test_session_feedback · Q4 · 备注记忆机制契约 (v20.6, 2026-08-18)"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

# 让 test 能 import main.orchestrator.session_feedback
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from main.orchestrator.session_feedback import (  # noqa: E402
    _pattern_matches,
    candidate_has_needs_extension_feedback,
    candidate_has_never_cut_feedback,
    inject_into_candidates,
    load_lake_feedback,
    load_session_feedback,
    match_feedback_to_candidate,
)


class TestSessionFeedbackLoad(unittest.TestCase):
    def test_ep04_seed_ten_feedback_entries(self):
        """EP04.session_feedback.jsonl seed 有 10 条反馈."""
        fbs = load_session_feedback("EP04", project_root=ROOT)
        self.assertGreaterEqual(len(fbs), 10)
        # 关键几条必须存在
        kinds = [fb.get("kind") for fb in fbs]
        self.assertIn("boundary", kinds)  # 呃扩边界
        self.assertIn("english_fragment", kinds)  # GoGoFlow 挡
        self.assertIn("host_backchannel", kinds)  # 主持人 backchannel
        self.assertIn("pronoun_exemption", kinds)  # 代词豁免

    def test_unknown_episode_returns_empty(self):
        self.assertEqual(load_session_feedback("EP99", project_root=ROOT), [])


class TestPatternMatching(unittest.TestCase):
    def test_any_pattern_always_matches(self):
        self.assertTrue(_pattern_matches({"any": True}, {"filler_token": "呃"}))

    def test_single_value_exact_match(self):
        self.assertTrue(_pattern_matches({"filler_token": "呃"}, {"filler_token": "呃"}))
        self.assertFalse(_pattern_matches({"filler_token": "呃"}, {"filler_token": "然后"}))

    def test_list_value_any_match(self):
        self.assertTrue(_pattern_matches(
            {"filler_token": ["嗯", "啊", "对"]},
            {"filler_token": "啊"},
        ))
        self.assertFalse(_pattern_matches(
            {"filler_token": ["嗯", "啊", "对"]},
            {"filler_token": "呃"},
        ))

    def test_context_field_skips_when_candidate_missing(self):
        # pattern-only field: candidate 没 context 时不阻止匹配
        self.assertTrue(_pattern_matches(
            {"filler_token": "go", "context": "english_compound"},
            {"filler_token": "go"},  # 无 context
        ))


class TestFeedbackToCandidate(unittest.TestCase):
    def test_er_boundary_feedback_matches_er_filler_candidate(self):
        fbs = load_session_feedback("EP04", project_root=ROOT)
        cand = {"filler_token": "呃", "reason_key": "filler_hesitation"}
        matched = match_feedback_to_candidate(cand, fbs)
        self.assertGreaterEqual(len(matched), 1)
        self.assertTrue(any("呃" in (fb.get("note") or "") for fb in matched))

    def test_gogoflow_go_matches_never_cut(self):
        fbs = load_session_feedback("EP04", project_root=ROOT)
        cand = {"filler_token": "go", "reason_key": "immediate_repetition"}
        matched = match_feedback_to_candidate(cand, fbs)
        self.assertTrue(any(fb.get("verdict") == "never_cut" for fb in matched))

    def test_pronoun_shenme_matches_never_cut(self):
        fbs = load_session_feedback("EP04", project_root=ROOT)
        cand = {"filler_token": "什么", "reason_key": "immediate_repetition"}
        matched = match_feedback_to_candidate(cand, fbs)
        self.assertTrue(any(fb.get("verdict") == "never_cut" for fb in matched))


class TestInjectAndGateHelpers(unittest.TestCase):
    def test_inject_writes_previous_user_feedback(self):
        fbs = load_session_feedback("EP04", project_root=ROOT)
        cands = [
            {"candidate_id": "C001", "filler_token": "呃", "reason_key": "filler_hesitation"},
            {"candidate_id": "C002", "filler_token": "go", "reason_key": "immediate_repetition"},
        ]
        updated, summary = inject_into_candidates(cands, fbs, lake_feedback=[])
        self.assertGreater(summary["total_hits"], 0)
        self.assertIn("previous_user_feedback", updated[0])
        self.assertTrue(candidate_has_never_cut_feedback(updated[1]))

    def test_never_cut_detector(self):
        cand = {"previous_user_feedback": [{"verdict": "never_cut"}]}
        self.assertTrue(candidate_has_never_cut_feedback(cand))
        cand2 = {"previous_user_feedback": [{"verdict": "needs_extension"}]}
        self.assertFalse(candidate_has_never_cut_feedback(cand2))
        self.assertTrue(candidate_has_needs_extension_feedback(cand2))


class TestLakeFeedbackLoad(unittest.TestCase):
    def test_lake_feedback_load_no_crash(self):
        """现在 lake 里 entries[].feedback 可能空, 但 load 不该 crash."""
        fbs = load_lake_feedback(project_root=ROOT)
        self.assertIsInstance(fbs, list)


if __name__ == "__main__":
    unittest.main()
