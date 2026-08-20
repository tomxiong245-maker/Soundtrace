"""test_auto_speaker_role · Q2 (v20.6, 2026-08-18)"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from main.orchestrator.auto_speaker_role import (  # noqa: E402
    HOST_BACKCHANNEL_TOKENS,
    analyze_track,
    build_auto_speaker_map,
    infer_role,
)


def _word(text: str, s: float, e: float) -> dict:
    return {"text": text, "start_seconds": s, "end_seconds": e}


class TestAnalyzeTrack(unittest.TestCase):
    def test_host_heavy_backchannel_low_speaking_time(self):
        # 主持人: 大量 backchannel, 短说话时间
        words = [
            _word("嗯", 0.0, 0.2), _word("嗯", 5.0, 5.15),
            _word("对", 10.0, 10.15), _word("对对", 15.0, 15.2),
            _word("啊", 20.0, 20.2), _word("是", 25.0, 25.15),
            _word("嗯", 30.0, 30.2), _word("好", 35.0, 35.15),
            _word("嗯", 40.0, 40.2), _word("对", 45.0, 45.15),
            _word("嗯", 50.0, 50.2),
        ]
        stats = analyze_track(words, 60.0)
        self.assertEqual(stats["total_words"], 11)
        self.assertGreater(stats["backchannel_ratio"], 0.9)  # 全 backchannel
        self.assertLess(stats["total_speaking_fraction"], 0.10)  # <10% 说话时间

    def test_guest_long_utterance_no_backchannel(self):
        # 嘉宾: 长说话时间, 无 backchannel
        words = [_word(f"词{i}", i * 0.5, i * 0.5 + 0.4) for i in range(120)]  # 60s 说话
        stats = analyze_track(words, 60.0)
        self.assertEqual(stats["backchannel_ratio"], 0.0)
        self.assertGreater(stats["total_speaking_fraction"], 0.7)


class TestInferRole(unittest.TestCase):
    def test_host_case_low_speaking_time(self):
        stats = {"backchannel_ratio": 0.04, "total_speaking_fraction": 0.44}  # EP04 track_03 类似
        peer_stats = [
            {"backchannel_ratio": 0.03, "total_speaking_fraction": 0.88},
            {"backchannel_ratio": 0.04, "total_speaking_fraction": 0.85},
        ]
        role, _ = infer_role(stats, peer_stats)
        self.assertEqual(role, "host")

    def test_guest_case_high_speaking_time(self):
        stats = {"backchannel_ratio": 0.03, "total_speaking_fraction": 0.85}
        peer_stats = [
            {"backchannel_ratio": 0.04, "total_speaking_fraction": 0.44},
        ]
        role, _ = infer_role(stats, peer_stats)
        self.assertEqual(role, "guest")

    def test_host_by_absolute_low_speaking(self):
        # 无 peer 时看绝对阈值 (fallback)
        stats = {"backchannel_ratio": 0.20, "total_speaking_fraction": 0.30}
        role, _ = infer_role(stats)
        self.assertEqual(role, "host")


class TestBuildAutoSpeakerMap(unittest.TestCase):
    def test_three_track_ep04_like(self):
        """模拟 EP04 场景: track_01/02 是嘉宾, track_03 是主持人"""
        tracks = {
            "track_01": [_word(f"词{i}", i * 0.4, i * 0.4 + 0.3) for i in range(100)],
            "track_02": [_word(f"词{i}", i * 0.5, i * 0.5 + 0.4) for i in range(80)],
            "track_03": [_word("嗯", i * 8, i * 8 + 0.2) for i in range(6)]
                          + [_word("对", i * 8 + 4, i * 8 + 4.2) for i in range(6)],
        }
        result = build_auto_speaker_map("EP04", tracks, 60.0)
        self.assertEqual(result["map"]["track_03"]["role"], "host")
        # 嘉宾至少一个
        guest_count = sum(1 for _, info in result["map"].items() if info["role"] == "guest")
        self.assertGreaterEqual(guest_count, 2)

    def test_multiple_hosts_downgrade(self):
        """若多轨都判 host, 保 backchannel 最高的, 其他降 guest."""
        tracks = {
            "track_01": [_word("嗯", i, i + 0.2) for i in range(20)],  # 全 backchannel
            "track_02": [_word("啊", i, i + 0.2) for i in range(20)],  # 全 backchannel (相同频率)
        }
        result = build_auto_speaker_map("EP99", tracks, 100.0)
        hosts = [tid for tid, info in result["map"].items() if info["role"] == "host"]
        # 最多 1 个 host
        self.assertLessEqual(len(hosts), 1)


if __name__ == "__main__":
    unittest.main()
