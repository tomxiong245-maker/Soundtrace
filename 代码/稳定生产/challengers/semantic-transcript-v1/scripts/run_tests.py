#!/usr/bin/env python3
"""Self-tests for semantic-transcript-v1; uses only the Python standard library."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_semantic_transcript as semantic  # noqa: E402


def transcript(words: list[dict], *, track_id: str = "track_01") -> dict:
    return {
        "schema_version": "ntrack-transcript-v1",
        "track_id": track_id,
        "label": track_id,
        "engine": "test",
        "model_ref": "test",
        "sample_rate_hz": 48000,
        "frame_count": 480000,
        "source_audio_path": "/tmp/source.wav",
        "source_audio_sha256": "0" * 64,
        "timestamp_repair_policy": "none",
        "timestamp_repairs": [],
        "words": words,
    }


def word(index: int, text: str, start: float, end: float) -> dict:
    return {
        "word_id": f"track_01:w{index:06d}",
        "text": text,
        "start_seconds": start,
        "end_seconds": end,
        "probability": 0.9,
    }


def build(doc: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "track_01.transcript.json"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return semantic.build_track_semantic(
            doc, path, expected_track_id="track_01", expected_rate=48000,
            expected_frames=480000
        )


def test_preserves_every_word_id_and_order():
    doc = transcript([
        word(1, "我", 0.0, 0.1),
        word(2, "觉得", 0.1, 0.3),
        word(3, "嗯", 0.3, 0.4),
        word(4, "很重要", 0.4, 0.7),
    ])
    out = build(doc)
    assert out["integrity"]["word_order_and_coverage_matches"] is True
    assert out["sentences"][0]["word_ids"] == [item["word_id"] for item in doc["words"]]
    assert out["word_context_index"]["track_01:w000003"]["sentence_id"] == "track_01:s000001"
    assert out["out_of_scope"]["deletion_decision"] == "NOT_INCLUDED"


def test_long_pause_forms_sentence_without_losing_mapping():
    doc = transcript([
        word(1, "第一句", 0.0, 0.2),
        word(2, "结束", 0.2, 0.4),
        word(3, "第二句", 1.6, 1.8),
    ])
    out = build(doc)
    assert len(out["sentences"]) == 2
    assert out["sentences"][0]["text_punctuated"].endswith("。")
    assert out["sentences"][0]["boundary_after"]["reason"] == "timing_gap_sentence"
    assert out["word_context_index"]["track_01:w000003"]["sentence_id"] == "track_01:s000002"


def test_source_question_punctuation_is_high_confidence():
    doc = transcript([
        word(1, "你觉得吗？", 0.0, 0.5),
        word(2, "我觉得", 0.6, 0.9),
    ])
    out = build(doc)
    first = out["sentences"][0]
    assert first["boundary_after"]["punctuation"] == "？"
    assert first["boundary_after"]["confidence"] == "high"


def test_discourse_clause_keeps_word_ids_and_exposes_clause_context():
    doc = transcript([
        word(1, "我", 0.0, 0.1),
        word(2, "觉得", 0.1, 0.2),
        word(3, "这个", 0.2, 0.3),
        word(4, "事情", 0.3, 0.4),
        word(5, "很重要", 0.4, 0.5),
        word(6, "然后", 0.5, 0.6),
        word(7, "继续", 0.6, 0.7),
    ])
    out = build(doc)
    sentence = out["sentences"][0]
    assert len(sentence["clauses"]) == 2
    assert sentence["clauses"][0]["boundary_after"]["reason"] == "discourse_leader_clause"
    assert out["word_context_index"]["track_01:w000003"]["clause_id"] == "track_01:s000001:c001"
    assert out["word_context_index"]["track_01:w000006"]["clause_id"] == "track_01:s000001:c002"


def test_is_deterministic_except_generated_at_and_file_sha():
    doc = transcript([
        word(1, "Hello", 0.0, 0.2),
        word(2, "world", 0.2, 0.4),
        word(3, "大家好", 1.5, 1.8),
    ])
    a = build(doc)
    b = build(doc)
    assert a["semantic_content_sha256"] == b["semantic_content_sha256"]
    assert a["sentences"] == b["sentences"]


def test_duplicate_word_id_fails_closed():
    doc = transcript([
        word(1, "一", 0.0, 0.1),
        word(1, "二", 0.1, 0.2),
    ])
    try:
        build(doc)
    except semantic.ContractError:
        return
    raise AssertionError("duplicate word_id was accepted")


def test_invalid_timestamp_fails_closed():
    doc = transcript([word(1, "一", 0.5, 0.5)])
    try:
        build(doc)
    except semantic.ContractError:
        return
    raise AssertionError("zero-duration word was accepted")


def test_command_builds_independent_archive():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        transcript_path = root / "track_01.transcript.json"
        doc = transcript([word(1, "你好", 0.0, 0.2), word(2, "世界", 1.3, 1.5)])
        transcript_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        report_path = root / "p0_mvp_report.json"
        report = {
            "schema_version": "p0-mvp-report-v1",
            "engineering_gate": "PASS",
            "quality_gate": "WAITING_FOR_SMALL_HUMAN_SPOT_CHECK",
            "track_count": 1,
            "sample_rate_hz": 48000,
            "frame_count": 480000,
            "tracks": [{"track_id": "track_01", "transcript_path": str(transcript_path)}]
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        out = root / "semantic-run"
        command = [
            sys.executable, str(HERE / "build_semantic_transcript.py"),
            "--input-report", str(report_path), "--episode-id", "TEST",
            "--source-run-id", "SOURCE", "--run-id", "TEST-semantic-v1", "--out", str(out)
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        generated = json.loads((out / "semantic_transcripts" / "track_01.semantic.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "PASS"
        assert generated["out_of_scope"]["deletion_decision"] == "NOT_INCLUDED"
        assert generated["source_transcript"]["sha256"] == hashlib.sha256(transcript_path.read_bytes()).hexdigest()


TESTS = [value for name, value in list(globals().items()) if name.startswith("test_") and callable(value)]


def main() -> int:
    results = []
    for test in TESTS:
        try:
            test()
            results.append((test.__name__, "PASS", ""))
        except Exception as exc:  # pragma: no cover - runner output
            results.append((test.__name__, "FAIL", str(exc)))
    for name, status, detail in results:
        print(f"[{status}] {name}")
        if detail:
            print(detail)
    passed = sum(status == "PASS" for _, status, _ in results)
    print(f"tests: {passed}/{len(results)} pass")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
