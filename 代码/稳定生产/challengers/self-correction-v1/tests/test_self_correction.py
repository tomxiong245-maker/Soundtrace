"""self-correction-v1 契约测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CHALLENGER = HERE.parent
sys.path.insert(0, str(CHALLENGER / "scripts"))

sc = __import__("detect_self_correction")

RULES = json.loads((CHALLENGER / "rules" / "self-correction.v1.json").read_text(
    encoding="utf-8"))


def _w(text: str, s: float, e: float, cls: str = "primary") -> dict:
    return {"text": text, "start_seconds": s, "end_seconds": e,
            "activity": {"classification": cls}}


def _seq(chars: str, t0: float, dur: float = 0.15, gap: float = 0.02) -> list[dict]:
    ws = []
    t = t0
    for c in chars:
        ws.append(_w(c, t, t + dur))
        t = t + dur + gap
    return ws


def test_01_detect_basic_self_correction():
    # "我叫张三" [停] "我叫李四"
    words = _seq("我叫张三", 1.0) + _seq("我叫李四", 3.0)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    assert len(cands) == 1
    c = cands[0]
    assert c["reason_key"] == "self_correction"
    assert c["shared_prefix"] == "我叫"
    assert c["abandoned_span"]["text"] == "我叫张三"


def test_02_ignore_pure_repetition():
    # "我叫张三" 后紧接 "我叫张三"（完全重复，非自我更正）
    words = _seq("我叫张三", 1.0) + _seq("我叫张三", 3.0)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    assert cands == []


def test_03_shared_prefix_too_short():
    # 前缀只有 "我"，一字，不达标
    words = _seq("我说错了", 1.0) + _seq("你说对了", 3.0)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    assert cands == []


def test_04_interrupt_word_bridges_short_gap():
    # 两句间 gap ~0.9s（> sentence_split 0.6s, < interrupt_max 2.5s），
    # 且尾部有打断词 "不对"。
    words = _seq("我叫张三不对", 1.0) + _seq("我叫李四", 3.9)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    assert len(cands) == 1
    assert cands[0]["interrupt_hit"] in ("不对", "错了")  # 允许其中之一命中


def test_05_ignore_when_a_too_long():
    long_a = "今天我们要讨论一个非常复杂的话题"  # > max_abandoned_chars
    words = _seq(long_a, 1.0) + _seq("今天我们讨论另一个", 5.0)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    assert cands == []


def test_06_ignore_protected_opening():
    words = _seq("大家好我是A", 1.0) + _seq("大家好我叫A", 3.0)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    assert cands == []


def test_07_review_only_policy():
    words = _seq("我叫张三", 1.0) + _seq("我叫李四", 3.0)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    assert cands and cands[0]["policy"] == "review_only_no_automatic_accept"


def test_08_output_schema_stable():
    words = _seq("我叫张三", 1.0) + _seq("我叫李四", 3.0)
    cands = sc.detect_self_corrections(words, RULES, "track_01", 48000)
    c = cands[0]
    for k in ("reason_key", "track_id", "start_sample", "end_sample",
              "start_seconds", "end_seconds", "abandoned_span", "retry_span",
              "shared_prefix", "edit_ratio", "interrupt_gap_seconds", "policy"):
        assert k in c
    assert c["start_sample"] < c["end_sample"]
