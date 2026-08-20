"""crosstalk-candidate-v1 契约测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CHALLENGER = HERE.parent
sys.path.insert(0, str(CHALLENGER / "scripts"))

ct = __import__("detect_crosstalk_candidates")

RULES = json.loads((CHALLENGER / "rules" / "crosstalk-candidate.v1.json").read_text(
    encoding="utf-8"))


def _w(text: str, s: float, e: float, cls: str) -> dict:
    return {"text": text, "start_seconds": s, "end_seconds": e,
            "activity": {"classification": cls}}


def _burst(text: str, t0: float, cls: str, count: int, dur: float = 0.15,
           gap: float = 0.05) -> list[dict]:
    ws = []
    t = t0
    for i in range(count):
        ws.append(_w(text, t, t + dur, cls))
        t += dur + gap
    return ws


def test_01_basic_crosstalk_high_confidence():
    # 5 秒窗口内：track_02 说话（5 个 primary），track_01 全 bleed（4 词），
    # track_03 静默。
    tracks = {
        "track_01": _burst("串", 10.0, "bleed", 4),
        "track_02": _burst("你", 10.0, "primary", 5),
        "track_03": [],
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    assert cands, cands
    assert cands[0]["reason_key"] == "crosstalk_on_source"
    assert cands[0]["track_id"] == "track_01"
    assert cands[0]["other_dominant_track_id"] == "track_02"
    assert cands[0]["confidence"] == "high"
    assert cands[0]["suggested_action"] == "gate_source_track"
    assert cands[0]["applies_to_tracks"] == ["track_01"]


def test_02_source_has_own_speech_downgrade_or_skip():
    # 源轨 3 bleed + 3 primary，bleed ratio 只有 0.5 < 0.7 → 不出候选
    tracks = {
        "track_01": _burst("我", 10.0, "primary", 3) + _burst("串", 11.0, "bleed", 3),
        "track_02": _burst("你", 10.0, "primary", 5),
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    for c in cands:
        assert c["track_id"] != "track_01" or c["bleed_words"] >= 3


def test_03_no_other_primary_no_candidate():
    # 源轨全是 bleed，但其他轨没人说话（没有 primary）→ 不出候选
    tracks = {
        "track_01": _burst("串", 10.0, "bleed", 5),
        "track_02": [],
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    assert cands == []


def test_04_downgrade_when_source_has_some_primary():
    # 源轨 4 bleed + 1 primary，ratio 0.8 满足；另一轨 primary=3；
    # 因源轨有 primary，应降为 medium + duck。
    tracks = {
        "track_01": _burst("串", 10.0, "bleed", 4) + _burst("嗯", 11.0, "primary", 1),
        "track_02": _burst("你", 10.0, "primary", 5),
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    ok = [c for c in cands if c["track_id"] == "track_01"]
    assert ok
    c = ok[0]
    assert c["confidence"] == "medium"
    assert c["suggested_action"] == "duck_source_track"


def test_05_review_only_and_no_global_cut():
    tracks = {
        "track_01": _burst("串", 10.0, "bleed", 4),
        "track_02": _burst("你", 10.0, "primary", 5),
        "track_03": [],
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    for c in cands:
        assert c["policy"] == "review_only_no_automatic_accept"
        assert c["applies_to_tracks"] == [c["track_id"]]  # 绝不是全轨


def test_06_min_bleed_words_threshold():
    # 只有 2 个 bleed 词，未达 min_bleed=3
    tracks = {
        "track_01": _burst("串", 10.0, "bleed", 2),
        "track_02": _burst("你", 10.0, "primary", 5),
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    assert cands == []


def test_07_output_schema_stable():
    tracks = {
        "track_01": _burst("串", 10.0, "bleed", 4),
        "track_02": _burst("你", 10.0, "primary", 5),
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    c = cands[0]
    for k in ("reason_key", "track_id", "source_track_id", "applies_to_tracks",
              "other_dominant_track_id", "start_seconds", "end_seconds",
              "start_sample", "end_sample", "bleed_words",
              "primary_words_on_source", "other_primary_words_total",
              "confidence", "suggested_action", "windows", "policy"):
        assert k in c


def test_08_merge_adjacent_windows():
    tracks = {
        "track_01": _burst("串", 10.0, "bleed", 8),  # 覆盖较长时间
        "track_02": _burst("你", 10.0, "primary", 8),
    }
    cands = ct.detect_crosstalk(tracks, RULES, 48000)
    # 相邻窗口应该合并成一个候选
    src01 = [c for c in cands if c["track_id"] == "track_01"]
    assert len(src01) == 1
    assert src01[0]["end_seconds"] - src01[0]["start_seconds"] > 1.5
