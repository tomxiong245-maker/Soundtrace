"""transient-events-v1 契约测试（合成 WAV fixture）。"""

from __future__ import annotations

import json
import math
import struct
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
CHALLENGER = HERE.parent
sys.path.insert(0, str(CHALLENGER / "scripts"))

detect_mod = __import__("detect_transient_events")

SR = 48000


def write_wav_mono_int16(path: Path, x: np.ndarray, sr: int = SR) -> None:
    x = np.clip(x, -1.0, 1.0)
    xi = (x * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(xi.tobytes())


def _silence(secs: float) -> np.ndarray:
    return np.zeros(int(sr := SR * 1) * 0)  # placeholder; use below


def gen_silence(sec: float) -> np.ndarray:
    return np.zeros(int(SR * sec), dtype=np.float32)


def gen_tone(sec: float, freq: float, amp: float = 0.05) -> np.ndarray:
    t = np.arange(int(SR * sec)) / SR
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def gen_cough(pos_s: float, dur_s: float = 0.15) -> np.ndarray:
    n = int(SR * dur_s)
    env = np.exp(-np.linspace(0, 6, n)).astype(np.float32)
    noise = np.random.default_rng(1).standard_normal(n).astype(np.float32) * 0.6
    band = noise * env
    return band


def gen_mic_bump(dur_s: float = 0.05) -> np.ndarray:
    n = int(SR * dur_s)
    t = np.arange(n) / SR
    env = np.exp(-t * 80.0).astype(np.float32)
    low = 0.85 * np.sin(2 * math.pi * 120 * t).astype(np.float32) * env
    return low


def _rules():
    return json.loads((CHALLENGER / "rules"
                        / "transient-events.v1.json").read_text(encoding="utf-8"))


def _synthesize(events: list[tuple[str, float]], total_s: float = 3.0
                ) -> np.ndarray:
    # 底噪 + 轻微 tone 模拟房间
    x = gen_tone(total_s, 220.0, amp=0.005)
    for kind, pos in events:
        if kind == "cough":
            seg = gen_cough(pos, 0.15)
        elif kind == "mic_bump":
            seg = gen_mic_bump(0.05)
        elif kind == "thump":
            seg = gen_cough(pos, 0.10) * 0.6  # 略弱一点
        else:
            continue
        n_seg = seg.size
        i0 = int(SR * pos)
        i1 = i0 + n_seg
        if i1 > x.size:
            x = np.concatenate([x, np.zeros(i1 - x.size, dtype=np.float32)])
        x[i0:i1] += seg
    return x.astype(np.float32)


def _run(tmp_path: Path, x: np.ndarray, words=None) -> dict:
    wav = tmp_path / "in.wav"
    write_wav_mono_int16(wav, x, SR)
    rules = _rules()
    return detect_mod.detect(wav, rules, words=words, track_id="track_01")


def test_01_detect_cough(tmp_path):
    x = _synthesize([("cough", 1.0)])
    r = _run(tmp_path, x)
    kinds = [c["reason_key"] for c in r["candidates"]]
    assert "cough_like" in kinds or "thump_like" in kinds


def test_02_detect_mic_bump(tmp_path):
    x = _synthesize([("mic_bump", 1.5)])
    r = _run(tmp_path, x)
    kinds = [c["reason_key"] for c in r["candidates"]]
    assert "mic_bump_like" in kinds


def test_03_no_false_positive_on_silence(tmp_path):
    x = gen_tone(2.0, 220.0, amp=0.005)
    r = _run(tmp_path, x)
    assert r["candidates"] == []


def test_04_asr_veto_speech(tmp_path):
    x = _synthesize([("cough", 1.0)])
    words = [
        {"text": "你", "start_seconds": 0.95, "end_seconds": 1.05,
         "activity": {"classification": "primary"}},
        {"text": "好", "start_seconds": 1.05, "end_seconds": 1.15,
         "activity": {"classification": "primary"}},
    ]
    r = _run(tmp_path, x, words=words)
    # 应该被 veto，不出候选
    assert all(c.get("asr_veto") is None or False for c in r["candidates"])
    # 通过 veto 后 candidates 应减少：这里断言最终没有 cough_like
    kinds = [c["reason_key"] for c in r["candidates"]]
    assert "cough_like" not in kinds


def test_05_review_only_policy(tmp_path):
    x = _synthesize([("cough", 1.0)])
    r = _run(tmp_path, x)
    for c in r["candidates"]:
        assert c["policy"] == "review_only_no_automatic_accept"


def test_06_output_schema_stable(tmp_path):
    x = _synthesize([("cough", 1.0)])
    r = _run(tmp_path, x)
    for k in ("schema_version", "sample_rate_hz", "wav_sha256",
              "rules_sha256", "candidates"):
        assert k in r
    if r["candidates"]:
        c = r["candidates"][0]
        for k in ("reason_key", "track_id", "start_sample", "end_sample",
                  "start_seconds", "end_seconds", "peak_dbfs", "rms_dbfs",
                  "crest_db", "spectral_flux", "low_energy_ratio", "policy"):
            assert k in c
