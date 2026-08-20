#!/usr/bin/env python3
"""P0 MVP: transcribe and inspect an arbitrary number of WAV tracks.

This is deliberately smaller than the research Challenger. It answers the
question needed by today's demo: can every physical track be read, transcribed
and kept on one shared sample timeline? Track identity comes from the input
manifest, never from a gender classifier.
"""

from __future__ import annotations

import argparse
import audioop
import hashlib
import json
import math
import struct
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
_MODEL_CACHE = {}


def resolve_path(value: str, manifest_path: Path) -> Path:
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    from_manifest = (manifest_path.parent / p).resolve()
    if from_manifest.exists():
        return from_manifest
    return (PROJECT_ROOT / p).resolve()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def wav_info(path: Path) -> dict:
    """Read PCM/PCM-extensible WAV metadata without Python wave limitations."""
    fmt = None
    data_offset = data_size = None
    with path.open("rb") as f:
        if f.read(4) != b"RIFF":
            raise SystemExit(f"不是 RIFF WAV: {path}")
        f.seek(4, 1)
        if f.read(4) != b"WAVE":
            raise SystemExit(f"不是 WAVE: {path}")
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", header)
            chunk_start = f.tell()
            if chunk_id == b"fmt ":
                raw = f.read(size)
                if len(raw) < 16:
                    raise SystemExit(f"损坏的 fmt chunk: {path}")
                tag, channels, sr, _, block_align, bits = struct.unpack("<HHIIHH", raw[:16])
                if tag == 0xFFFE and len(raw) >= 26:
                    tag = struct.unpack("<H", raw[24:26])[0]
                if tag not in (1,):
                    raise SystemExit(f"当前 MVP 只支持 PCM WAV，format={tag}: {path}")
                fmt = (channels, sr, block_align, bits)
            elif chunk_id == b"data":
                data_offset, data_size = chunk_start, size
            f.seek(chunk_start + size + (size & 1))
    if fmt is None or data_offset is None or data_size is None:
        raise SystemExit(f"WAV 缺少 fmt/data chunk: {path}")
    channels, sr, block_align, bits = fmt
    if bits % 8 or block_align <= 0:
        raise SystemExit(f"不支持的 PCM 位深/对齐: {path}")
    frames = data_size // block_align
    return {
        "channels": channels,
        "sample_rate_hz": sr,
        "sample_width_bytes": bits // 8,
        "block_align": block_align,
        "frame_count": frames,
        "duration_seconds": frames / sr,
        "data_offset": data_offset,
        "data_size": data_size,
    }


def find_local_model() -> str:
    root = Path.home() / ".cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots"
    snapshots = sorted((p for p in root.glob("*") if p.is_dir()), reverse=True)
    return str(snapshots[0]) if snapshots else "small"


def normalize_existing(path: Path, track_id: str) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    words = []
    for i, word in enumerate(doc.get("words", []), 1):
        s = float(word.get("start_seconds", word.get("start", 0.0)))
        e = float(word.get("end_seconds", word.get("end", s)))
        words.append({
            "word_id": f"{track_id}:w{i:06d}",
            "text": str(word.get("text", word.get("word", ""))),
            "start_seconds": s,
            "end_seconds": e,
            "probability": word.get("probability"),
        })
    return {"words": words, "language": doc.get("language", "zh"), "source": "existing_transcript"}


def transcribe(path: Path, track_id: str, model_ref: str, context_prompt: str = "") -> tuple[dict, float]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit("缺少 faster-whisper。请先运行：bash pipeline/install_mvp.sh") from exc

    model = _MODEL_CACHE.get(model_ref)
    if model is None:
        model = WhisperModel(model_ref, device="cpu", compute_type="int8")
        _MODEL_CACHE[model_ref] = model
    started = time.perf_counter()
    segments, info = model.transcribe(
        str(path), language="zh", beam_size=5, word_timestamps=True,
        vad_filter=True, condition_on_previous_text=False,
        initial_prompt=context_prompt or None,
    )
    words = []
    idx = 0
    for segment in segments:
        for word in segment.words or []:
            idx += 1
            words.append({
                "word_id": f"{track_id}:w{idx:06d}",
                "text": word.word,
                "start_seconds": float(word.start),
                "end_seconds": float(word.end),
                "probability": float(word.probability) if word.probability is not None else None,
            })
    wall = time.perf_counter() - started
    return {"words": words, "language": info.language, "source": "faster_whisper_small"}, wall


def validate_words(words: list[dict], duration: float) -> list[str]:
    errors = []
    previous = -1.0
    for word in words:
        s, e = float(word["start_seconds"]), float(word["end_seconds"])
        if s < -0.05 or e <= s or e > duration + 0.25:
            errors.append(f"invalid interval {s:.3f}-{e:.3f}")
        if s + 0.25 < previous:
            errors.append(f"non-monotonic start {s:.3f} after {previous:.3f}")
        previous = max(previous, s)
    return errors


def repair_zero_duration_words(words: list[dict], duration: float) -> list[dict]:
    """Repair only Whisper's zero-duration word timestamps.

    The raw ASR transcript remains the source artifact.  A zero-duration token
    is not useful for highlighting or candidate boundaries, so the derived P0
    transcript gives it a small, bounded display interval.  Negative or other
    invalid intervals are deliberately not repaired and still fail closed.
    """
    repaired = []
    for index, word in enumerate(words):
        start = float(word["start_seconds"])
        end = float(word["end_seconds"])
        if end != start:
            continue
        strict_next_start = None
        for later in words[index + 1 :]:
            candidate = float(later["start_seconds"])
            if candidate > start:
                strict_next_start = candidate
                break
        bounded_end = min(duration, start + 0.02)
        if strict_next_start is not None:
            bounded_end = min(bounded_end, strict_next_start)
        if bounded_end <= start:
            continue
        word["raw_start_seconds"] = start
        word["raw_end_seconds"] = end
        word["timestamp_repair"] = "zero_duration_expanded_bounded"
        word["end_seconds"] = bounded_end
        repaired.append({
            "word_id": word.get("word_id"),
            "text": word.get("text", ""),
            "raw_start_seconds": start,
            "raw_end_seconds": end,
            "repaired_end_seconds": bounded_end,
            "duration_seconds": bounded_end - start,
        })
    return repaired


def energy_summary(track_paths: list[tuple[str, Path]], frame_ms: int = 20) -> dict:
    infos = [wav_info(path) for _, path in track_paths]
    handles = [path.open("rb") for _, path in track_paths]
    try:
        sample_rates = {info["sample_rate_hz"] for info in infos}
        widths = {info["sample_width_bytes"] for info in infos}
        channels = {info["channels"] for info in infos}
        if len(sample_rates) != 1 or len(widths) != 1 or channels != {1}:
            return {"status": "SKIPPED_FORMAT_MISMATCH"}
        sr = next(iter(sample_rates))
        width = next(iter(widths))
        chunk_frames = max(1, round(sr * frame_ms / 1000))
        for handle, info in zip(handles, infos):
            handle.seek(info["data_offset"])
        counts = {track_id: 0 for track_id, _ in track_paths}
        silence = ambiguous = total = 0
        full_scale = float((1 << (8 * width - 1)) - 1)
        while True:
            chunks = [
                handle.read(chunk_frames * info["block_align"])
                for handle, info in zip(handles, infos)
            ]
            if not chunks or any(not c for c in chunks):
                break
            db = []
            for chunk in chunks:
                rms = audioop.rms(chunk, width)
                db.append(20 * math.log10(max(rms / full_scale, 1e-9)))
            total += 1
            ranked = sorted(range(len(db)), key=lambda i: db[i], reverse=True)
            if db[ranked[0]] < -50.0:
                silence += 1
            elif len(ranked) > 1 and db[ranked[0]] - db[ranked[1]] < 3.0:
                ambiguous += 1
            else:
                counts[track_paths[ranked[0]][0]] += 1
        return {
            "status": "HEURISTIC_ONLY",
            "warning": "能量比较只用于主轨/串音提示，不是说话人真值或 benchmark gold。",
            "frame_ms": frame_ms,
            "frames": total,
            "primary_frame_counts": counts,
            "silence_frames": silence,
            "ambiguous_frames": ambiguous,
        }
    finally:
        for w in handles:
            w.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--context-prompt", default="")
    ap.add_argument("audio_paths", nargs="*")
    args = ap.parse_args()

    if bool(args.manifest) == bool(args.audio_paths):
        raise SystemExit("请二选一：--manifest，或直接传入 1 条以上 WAV 路径")
    if args.manifest:
        manifest_path = args.manifest.resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_mode = "manifest"
    else:
        manifest_path = Path.cwd() / ".direct-input.json"
        manifest = {
            "schema_version": "ntrack-input-v1",
            "tracks": [
                {
                    "track_id": f"track_{idx:02d}",
                    "label": Path(value).stem,
                    "audio_path": str(Path(value).expanduser().resolve()),
                }
                for idx, value in enumerate(args.audio_paths, 1)
            ],
        }
        input_mode = "direct_audio_paths"
    tracks = manifest.get("tracks") or []
    if not tracks:
        raise SystemExit("manifest.tracks 不能为空")
    ids = [str(t["track_id"]) for t in tracks]
    if len(ids) != len(set(ids)):
        raise SystemExit("track_id 必须唯一")

    args.out.mkdir(parents=True, exist_ok=True)
    model_ref = args.model or find_local_model()
    context_prompt = str(args.context_prompt or manifest.get("context_prompt", "")).strip()
    results = []
    common_sr = common_frames = None
    resolved_tracks = []
    for track in tracks:
        track_id = str(track["track_id"])
        audio_path = resolve_path(track["audio_path"], manifest_path)
        if not audio_path.is_file():
            raise SystemExit(f"找不到轨道 {track_id}: {audio_path}")
        info = wav_info(audio_path)
        if info["channels"] != 1:
            raise SystemExit(f"{track_id} 不是 mono WAV")
        common_sr = info["sample_rate_hz"] if common_sr is None else common_sr
        common_frames = info["frame_count"] if common_frames is None else common_frames
        if info["sample_rate_hz"] != common_sr or info["frame_count"] != common_frames:
            raise SystemExit(f"{track_id} 与其他轨道不在同一采样时间线")
        resolved_tracks.append((track_id, audio_path))

        transcript_value = track.get("transcript_path")
        if transcript_value:
            transcript_path = resolve_path(transcript_value, manifest_path)
            doc = normalize_existing(transcript_path, track_id)
            wall = 0.0
        else:
            doc, wall = transcribe(audio_path, track_id, model_ref, context_prompt)
        timestamp_repairs = repair_zero_duration_words(
            doc["words"], info["duration_seconds"]
        )
        errors = validate_words(doc["words"], info["duration_seconds"])
        out_doc = {
            "schema_version": "ntrack-transcript-v1",
            "track_id": track_id,
            "label": track.get("label", track_id),
            "source_audio_path": str(audio_path),
            "source_audio_sha256": sha256_file(audio_path),
            "sample_rate_hz": info["sample_rate_hz"],
            "frame_count": info["frame_count"],
            "engine": doc["source"],
            "model_ref": model_ref if wall else None,
            "timestamp_repair_policy": "zero_duration_only_bounded_20ms",
            "timestamp_repairs": timestamp_repairs,
            "words": doc["words"],
        }
        out_path = args.out / f"{track_id}.transcript.json"
        out_path.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results.append({
            "track_id": track_id,
            "label": track.get("label", track_id),
            "word_count": len(doc["words"]),
            "invalid_timestamp_count": len(errors),
            "timestamp_repair_count": len(timestamp_repairs),
            "wall_seconds": wall,
            "rtf": wall / info["duration_seconds"] if wall else 0.0,
            "transcript_path": str(out_path),
            "status": "PASS" if doc["words"] and not errors else "FAIL",
        })

    fresh_rtfs = [r["rtf"] for r in results if r["wall_seconds"] > 0]
    report = {
        "schema_version": "p0-mvp-report-v1",
        "input_mode": input_mode,
        "track_count": len(tracks),
        "track_ids": ids,
        "sample_rate_hz": common_sr,
        "frame_count": common_frames,
        "engineering_gate": "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL",
        "speed_gate": (
            "PASS" if fresh_rtfs and max(fresh_rtfs) <= 1.0
            else "FAIL" if fresh_rtfs
            else "NOT_APPLICABLE_REUSED_TRANSCRIPTS"
        ),
        "quality_gate": "WAITING_FOR_SMALL_HUMAN_SPOT_CHECK",
        "timestamp_repair_policy": "zero_duration_only_bounded_20ms",
        "benchmark_note": "CER 需要人工正确文本；今天只用工程门 + 试听抽查，不用能量银标伪装准确率。",
        "tracks": results,
        "activity_hint": energy_summary(resolved_tracks),
    }
    report_path = args.out / "p0_mvp_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["engineering_gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
