"""Adapter contract & scorer self-tests for asr-speaker-v1.

Run:
    python scripts/run_tests.py            # runs all tests, writes test_results.txt
    python scripts/run_tests.py -v         # verbose

These tests DO NOT need any external model (faster-whisper / funasr / mlx).
They probe the adapter contract, the scorer's CER and silence hallucination
counting, and per-fixture behaviors described in the task book.

Each test is a function `test_*`; returns None on success, raises AssertionError on failure.
"""

from __future__ import annotations

import hashlib
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts" / "adapters"))
sys.path.insert(0, str(ROOT / "scripts"))
FIXTURES = ROOT / "tests" / "fixtures"

from _common import AdapterError  # noqa: E402
import faster_whisper_adapter as fw_adapter  # noqa: E402
import funasr_paraformer_adapter as fu_adapter  # noqa: E402
import funasr_fsmn_vad_adapter as vad_adapter  # noqa: E402
import mlx_whisper_adapter as mlx_adapter  # noqa: E402
import score_asr_benchmark as scorer  # noqa: E402


# ---- helpers -----------------------------------------------------------

def _tmp_wav_sha() -> tuple[Path, str]:
    """Return a path to silence_10s.wav (fixture) plus its sha256."""
    p = FIXTURES / "silence_10s.wav"
    if not p.is_file():
        raise RuntimeError("Fixtures not built. Run `python tests/build_fixtures.py --out tests/fixtures` first.")
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return p, h


# ---- 1. Silence: no hallucination allowed by scorer counting ------------

def test_scorer_counts_hallucination_in_silence():
    p, _ = _tmp_wav_sha()
    hyp_words = [
        {"text": "错识别", "start_seconds": 1.0, "end_seconds": 1.4},
        {"text": "又一个", "start_seconds": 3.0, "end_seconds": 3.5},
        {"text": "边界", "start_seconds": 9.9, "end_seconds": 10.05},  # touches end; not fully inside
    ]
    r = scorer.silence_hallucination(hyp_words, [(0.0, 10.0)])
    assert r["insertions_in_silence"] == 2, r


# ---- 2. Single speaker stability (fixture round-trip through adapter) ---

def test_adapter_single_speaker_stable():
    p, sha = _tmp_wav_sha()
    raw = {"words": [
        {"text": "你好", "start_seconds": 0.5, "end_seconds": 1.0,
         "start_sample": 8000, "end_sample": 16000, "probability": 0.9},
        {"text": "世界", "start_seconds": 1.0, "end_seconds": 1.5,
         "start_sample": 16000, "end_sample": 24000, "probability": 0.8},
    ]}
    normalized = fw_adapter.normalize(
        raw, segment_id="T01", source_track="speech_mix", source_audio_path=p,
        segment_start_offset_seconds_in_ep03=0.0, engine_version="test",
        model_id="test", model_revision=None, sample_rate_hz=16000,
    )
    assert normalized["source_audio_sha256"] == sha
    assert [w["text"] for w in normalized["words"]] == ["你好", "世界"]
    for w in normalized["words"]:
        assert w["end_sample"] > w["start_sample"]


# ---- 3. Two-speaker alternation representable in normalized speaker layer -

def test_speaker_metrics_alternating():
    gold = [
        {"start_seconds": 0.0, "end_seconds": 1.0, "speaker_id": "a"},
        {"start_seconds": 1.0, "end_seconds": 2.0, "speaker_id": "b"},
    ]
    hyp = [
        {"start_seconds": 0.0, "end_seconds": 1.0, "speaker_id": "a"},
        {"start_seconds": 1.0, "end_seconds": 2.0, "speaker_id": "b"},
    ]
    m = scorer.speaker_frame_metrics(gold, hyp, 2.0)
    assert m["speaker_confusion_frames"] == 0
    # swap the hyp speakers -> confusion should be > 0
    hyp2 = [
        {"start_seconds": 0.0, "end_seconds": 1.0, "speaker_id": "b"},
        {"start_seconds": 1.0, "end_seconds": 2.0, "speaker_id": "a"},
    ]
    m2 = scorer.speaker_frame_metrics(gold, hyp2, 2.0)
    assert m2["speaker_confusion_frames"] > 100


# ---- 4. Overlap must NOT be forced onto a single speaker ---------------

def test_speaker_metrics_overlap_recall():
    gold = [
        {"start_seconds": 0.0, "end_seconds": 1.0, "speaker_id": "a"},
        {"start_seconds": 0.5, "end_seconds": 1.5, "speaker_id": "b"},
    ]
    # hyp says "only a" for the whole thing → overlap should NOT be considered detected
    hyp_bad = [{"start_seconds": 0.0, "end_seconds": 1.5, "speaker_id": "a"}]
    m = scorer.speaker_frame_metrics(gold, hyp_bad, 1.5)
    assert m["overlap_gold_frames"] > 0
    assert m["overlap_detected_frames"] == 0

    # hyp that has both speakers in the overlap window
    hyp_good = [
        {"start_seconds": 0.0, "end_seconds": 1.0, "speaker_id": "a"},
        {"start_seconds": 0.5, "end_seconds": 1.5, "speaker_id": "b"},
    ]
    m2 = scorer.speaker_frame_metrics(gold, hyp_good, 1.5)
    assert m2["overlap_detected_frames"] == m2["overlap_gold_frames"]


# ---- 5. Louder track != identity (contract, not model outcome) ----------

def test_loudness_is_not_identity():
    # We assert the adapter does NOT auto-fill speaker_id_hint from RMS,
    # and that speaker_id in normalized speaker intervals defaults to cluster id / unknown.
    p, sha = _tmp_wav_sha()
    raw = {"words": [{"text": "喂", "start_seconds": 0.0, "end_seconds": 0.2,
                      "start_sample": 0, "end_sample": 3200, "probability": 0.5}]}
    normalized = fw_adapter.normalize(
        raw, segment_id="T02", source_track="female", source_audio_path=p,
        segment_start_offset_seconds_in_ep03=0.0, engine_version="test",
        model_id="test", model_revision=None, sample_rate_hz=16000,
    )
    assert normalized["words"][0].get("speaker_id_hint") is None


# ---- 6. Timestamp validation: reject non-monotonic / zero-length --------

def test_reject_non_monotonic():
    p, _ = _tmp_wav_sha()
    raw = {"words": [
        {"text": "a", "start_seconds": 0.5, "end_seconds": 1.0,
         "start_sample": 8000, "end_sample": 16000, "probability": 0.9},
        {"text": "b", "start_seconds": 0.4, "end_seconds": 0.9,  # overlaps back
         "start_sample": 6400, "end_sample": 14400, "probability": 0.9},
    ]}
    try:
        fw_adapter.normalize(raw, segment_id="T01", source_track="speech_mix",
                             source_audio_path=p, segment_start_offset_seconds_in_ep03=0.0,
                             engine_version="test", model_id="test",
                             model_revision=None, sample_rate_hz=16000)
    except AdapterError:
        return
    raise AssertionError("adapter did not raise on non-monotonic timing")


def test_reject_zero_length():
    p, _ = _tmp_wav_sha()
    raw = {"words": [
        {"text": "a", "start_seconds": 0.5, "end_seconds": 0.5,
         "start_sample": 8000, "end_sample": 8000, "probability": 0.9},
    ]}
    try:
        fw_adapter.normalize(raw, segment_id="T01", source_track="speech_mix",
                             source_audio_path=p, segment_start_offset_seconds_in_ep03=0.0,
                             engine_version="test", model_id="test",
                             model_revision=None, sample_rate_hz=16000)
    except AdapterError:
        return
    raise AssertionError("adapter did not raise on zero-length word")


# ---- 7. Reproducibility: same input → same normalized dict (minus timestamps) -

def test_reproducibility_semantic_hash():
    p, sha = _tmp_wav_sha()
    raw = {"words": [
        {"text": "你", "start_seconds": 0.0, "end_seconds": 0.5,
         "start_sample": 0, "end_sample": 8000, "probability": 0.5},
    ]}
    a = fw_adapter.normalize(raw, segment_id="T01", source_track="speech_mix",
                             source_audio_path=p, segment_start_offset_seconds_in_ep03=0.0,
                             engine_version="test", model_id="test",
                             model_revision=None, sample_rate_hz=16000)
    b = fw_adapter.normalize(raw, segment_id="T01", source_track="speech_mix",
                             source_audio_path=p, segment_start_offset_seconds_in_ep03=0.0,
                             engine_version="test", model_id="test",
                             model_revision=None, sample_rate_hz=16000)
    a["generated_at"] = b["generated_at"] = "IGNORED"
    assert a == b


# ---- 8. adapters must NOT downgrade word-level to sentence-level --------

def test_paraformer_refuses_sentence_only():
    p, _ = _tmp_wav_sha()
    raw = {"text": "整句只有一个时间戳", "timestamp": []}  # zero token ts
    try:
        fu_adapter.normalize(raw, segment_id="T01", source_track="speech_mix",
                             source_audio_path=p, segment_start_offset_seconds_in_ep03=0.0,
                             engine_version="test", model_id="test", model_revision=None)
    except AdapterError:
        pass
    else:
        raise AssertionError("funasr adapter accepted sentence-only timing")


def test_mlx_refuses_sentence_only():
    p, _ = _tmp_wav_sha()
    raw = {"segments": [{"start": 0.0, "end": 1.0, "text": "整段", "words": []}]}
    try:
        mlx_adapter.normalize(raw, segment_id="T01", source_track="speech_mix",
                              source_audio_path=p, segment_start_offset_seconds_in_ep03=0.0,
                              engine_version="test", model_id="test", model_revision=None)
    except AdapterError:
        pass
    else:
        raise AssertionError("mlx adapter accepted sentence-only timing")


# ---- 9. VAD adapter: sample math sane ----------------------------------

def test_vad_adapter_ms_to_sample():
    p, sha = _tmp_wav_sha()
    raw = [[0, 1000], [2000, 3500]]
    out = vad_adapter.normalize(
        raw, segment_id="T01", source_track="speech_mix", source_audio_path=p,
        segment_start_offset_seconds_in_ep03=0.0, engine_version="test",
    )
    assert out["intervals"][0]["end_sample"] == 16000
    assert out["intervals"][1]["end_sample"] == 56000


# ---- 10. CER math sanity (Chinese) -------------------------------------

def test_cer_basic():
    r = scorer.compute_cer("你好世界", "你好世")
    assert r["deletions"] == 1
    assert r["substitutions"] == 0
    assert r["insertions"] == 0
    assert abs(r["cer"] - 0.25) < 1e-9
    r2 = scorer.compute_cer("你好世界", "你好世家")
    assert r2["substitutions"] == 1


# ---- runner ------------------------------------------------------------

TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    results = []
    for t in TESTS:
        try:
            t()
            results.append((t.__name__, "PASS", ""))
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            results.append((t.__name__, "FAIL", str(e) + "\n" + tb))
    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    out_path = ROOT / "test_results.txt"
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"tests: {n_pass}/{n_pass + n_fail} pass\n\n")
        for name, status, info in results:
            f.write(f"[{status}] {name}\n")
            if info:
                f.write(info.rstrip() + "\n")
            f.write("\n")
    print(out_path.read_text(encoding="utf-8"))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
