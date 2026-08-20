#!/usr/bin/env python3
"""Standard-library unit tests for the isolated ASR multilingual Challenger."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_faster_whisper_ab as ab  # noqa: E402
import text_layers  # noqa: E402


def transcript(words: list[dict]) -> dict:
    return {
        "schema_version": "ntrack-transcript-v1",
        "track_id": "track_01",
        "label": "physical mic A",
        "engine": "faster_whisper_small",
        "model_ref": "/audited/faster-whisper-small/snapshots/test",
        "source_audio_sha256": "a" * 64,
        "sample_rate_hz": 48000,
        "frame_count": 480000,
        "words": words,
    }


def word(index: int, text: str, start: float, end: float) -> dict:
    return {
        "word_id": f"track_01:w{index:06d}",
        "text": text,
        "start_seconds": start,
        "end_seconds": end,
        "start_sample": round(start * 48000),
        "end_sample": round(end * 48000),
        "probability": 0.9,
    }


def test_raw_document_ids_and_timestamps_are_unchanged() -> None:
    source = transcript([
        word(1, " 我們", 0.0, 0.2),
        word(2, "討論", 0.2, 0.5),
        word(3, "MCP", 0.5, 0.8),
    ])
    before = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    output = text_layers.build_text_layers(source)
    after = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert after == before
    assert output["raw_text"] == " 我們討論MCP"
    assert output["match_text"] == "我们讨论mcp"
    assert output["word_layers"][0]["source_word_ids"] == ["track_01:w000001"]
    assert output["integrity"]["source_word_id_order_preserved"] is True
    assert output["integrity"]["timestamps_rewritten"] is False
    assert output["integrity"]["raw_word_evidence_sha256"]


def test_traditional_and_ascii_normalize_only_in_match_and_display_layers() -> None:
    source = transcript([
        word(1, " 什麼", 0.0, 0.2),
        word(2, "MCP", 0.2, 0.4),
        word(3, "，", 0.4, 0.42),
    ])
    output = text_layers.build_text_layers(source)
    assert source["words"][0]["text"] == " 什麼"  # raw evidence remains Traditional.
    assert output["word_layers"][0]["match_text"] == "什么"
    assert output["word_layers"][0]["display_text"] == "什么"
    assert output["match_text"] == "什么mcp"
    assert output["display_text"] == "什么MCP，"
    assert output["out_of_scope"]["punctuation_invention"] == "NOT_INCLUDED"


def test_ascii_subword_display_span_has_all_source_ids_but_no_new_timestamp() -> None:
    source = transcript([
        word(1, "S", 0.00, 0.02),
        word(2, "oph", 0.02, 0.04),
        word(3, "ie", 0.04, 0.06),
    ])
    output = text_layers.build_text_layers(source)
    assert output["display_text"] == "Sophie"
    assert len(output["display_spans"]) == 1
    span = output["display_spans"][0]
    assert span["kind"] == "ascii_subword_display_merge"
    assert span["source_word_ids"] == [
        "track_01:w000001", "track_01:w000002", "track_01:w000003"
    ]
    assert "start_seconds" not in span and "end_seconds" not in span
    assert [entry["source_word_ids"] for entry in output["word_layers"]] == [
        ["track_01:w000001"], ["track_01:w000002"], ["track_01:w000003"]
    ]


def test_duplicate_source_word_id_fails_closed() -> None:
    source = transcript([
        word(1, "一", 0.0, 0.2),
        word(1, "二", 0.2, 0.4),
    ])
    try:
        text_layers.build_text_layers(source)
    except text_layers.ContractError:
        return
    raise AssertionError("duplicate word_id was accepted")


def test_sidecar_cli_writes_independent_file_without_touching_raw_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        raw_path = root / "track_01.raw.transcript.json"
        sidecar_path = root / "track_01.text_layers.json"
        source = transcript([word(1, "這個", 0.0, 0.2), word(2, "test", 0.2, 0.4)])
        raw_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raw_sha_before = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "text_layers.py"), "--input", str(raw_path), "--out", str(sidecar_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == raw_sha_before
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar["source_transcript"]["sha256"] == raw_sha_before
        assert sidecar["display_text"] == "这个test"


def test_challenger_compatibility_layer_and_runner_use_canonical_module() -> None:
    canonical = (SCRIPTS.parents[3] / "main" / "orchestrator" / "transcript_text_layers.py").resolve()
    assert Path(text_layers.build_text_layers.__code__.co_filename).resolve() == canonical
    assert Path(ab.text_layers.__file__).resolve() == canonical


def test_auto_arm_passes_language_none_and_en_model_is_rejected() -> None:
    assert ab.transcribe_kwargs(None, "")["language"] is None
    assert ab.transcribe_kwargs("zh", "prompt")["language"] == "zh"
    try:
        ab.validate_model_reference(Path("/tmp/faster-whisper-small.en"))
    except ab.ContractError as exc:
        assert ".en" in str(exc)
        return
    raise AssertionError("English-only .en model was accepted")


TESTS = [value for name, value in list(globals().items()) if name.startswith("test_") and callable(value)]


def main() -> int:
    results: list[tuple[str, str]] = []
    for test in TESTS:
        try:
            test()
            results.append((test.__name__, "PASS"))
        except Exception as exc:  # pragma: no cover - gives useful terminal detail
            results.append((test.__name__, f"FAIL: {exc}"))
    for name, result in results:
        print(f"[{result.split(':', 1)[0]}] {name}")
        if result.startswith("FAIL:"):
            print(result)
    passed = sum(result == "PASS" for _, result in results)
    print(f"tests: {passed}/{len(results)} pass")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
