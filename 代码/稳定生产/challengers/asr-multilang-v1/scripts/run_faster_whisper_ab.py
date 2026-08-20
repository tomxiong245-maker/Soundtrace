#!/usr/bin/env python3
"""Run an isolated faster-whisper-small language A/B Challenger.

The only experimental variable is the language argument supplied to the same
local ``faster-whisper small`` model:

* ``zh``: ``language="zh"`` (the existing EP04 v13 baseline setting);
* ``auto``: ``language=None`` (Whisper's multilingual auto-detection path).

Every output is new and lives under ``--out``. This script never calls or
modifies ``p0_mvp.py`` and it refuses to reuse a non-empty output directory.
It writes raw upstream word records first, then invokes the separate
``raw/match/display`` sidecar builder. No word-text correction, timestamp
repair, candidate generation, EDL generation, or production replacement is
performed here.

The ``.en`` variants are deliberately rejected: they are English-only models
and are not an A/B arm for a Chinese/English mixed podcast.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import time
from pathlib import Path
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
ORCHESTRATOR = HERE.parents[3] / "main" / "orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))
import transcript_text_layers as text_layers  # noqa: E402


SCHEMA_VERSION = "asr-multilang-ab-v1"
MODEL_REPOSITORY = "Systran/faster-whisper-small"
ENGINE_ID = "faster_whisper_small"
MODE_LANGUAGE: dict[str, str | None] = {"zh": "zh", "auto": None}


class ContractError(ValueError):
    """Raised when the Challenger input cannot be safely compared."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_local_small_model() -> Path:
    snapshots = Path.home() / ".cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots"
    candidates = sorted((path for path in snapshots.glob("*") if path.is_dir()), reverse=True)
    if not candidates:
        raise ContractError(
            "no audited local faster-whisper small snapshot found; this Challenger will not download a model"
        )
    return candidates[0]


def validate_model_reference(value: str | Path) -> Path:
    """Allow only an existing local multilingual small snapshot, never ``.en``."""

    path = Path(value).expanduser().resolve()
    text = str(path).lower()
    if re.search(r"(?:^|[._/-])(?:tiny|base|small|medium|large)\.en(?:$|[._/-])", text):
        raise ContractError(".en model variants are intentionally unsupported; use multilingual faster-whisper small")
    if "small" not in text:
        raise ContractError("this Challenger is fixed to faster-whisper small; another model is a separate experiment")
    if not path.is_dir():
        raise ContractError(f"model must be an existing local directory, not a downloadable alias: {path}")
    return path


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    beside_manifest = (manifest_path.parent / path).resolve()
    return beside_manifest if beside_manifest.exists() else path.resolve()


def wav_info(path: Path) -> dict[str, Any]:
    """Read PCM or PCM-extensible WAV metadata without ``wave`` limitations."""

    fmt: tuple[int, int, int, int] | None = None
    data_size: int | None = None
    with path.open("rb") as handle:
        if handle.read(4) != b"RIFF":
            raise ContractError(f"not a RIFF WAV: {path}")
        handle.seek(4, 1)
        if handle.read(4) != b"WAVE":
            raise ContractError(f"not a WAVE file: {path}")
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", header)
            start = handle.tell()
            if chunk_id == b"fmt ":
                raw = handle.read(size)
                if len(raw) < 16:
                    raise ContractError(f"damaged fmt chunk: {path}")
                tag, channels, sample_rate, _, block_align, bits = struct.unpack("<HHIIHH", raw[:16])
                if tag == 0xFFFE and len(raw) >= 26:
                    tag = struct.unpack("<H", raw[24:26])[0]
                if tag != 1:
                    raise ContractError(f"only PCM WAV is supported (format={tag}): {path}")
                fmt = (channels, sample_rate, block_align, bits)
            elif chunk_id == b"data":
                data_size = size
            handle.seek(start + size + (size & 1))
    if fmt is None or data_size is None:
        raise ContractError(f"WAV missing fmt/data chunk: {path}")
    channels, sample_rate, block_align, bits = fmt
    if block_align <= 0 or bits % 8:
        raise ContractError(f"unsupported PCM sample layout: {path}")
    frames = data_size // block_align
    return {
        "channels": channels,
        "sample_rate_hz": sample_rate,
        "sample_width_bits": bits,
        "frame_count": frames,
        "duration_seconds": frames / sample_rate,
    }


def load_tracks(manifest_path: Path | None, audio_paths: list[str]) -> tuple[Path, list[dict[str, Any]], str]:
    if bool(manifest_path) == bool(audio_paths):
        raise ContractError("choose exactly one input style: --manifest or one-or-more WAV paths")
    if manifest_path is not None:
        manifest_path = manifest_path.resolve()
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        tracks = raw.get("tracks")
        if not isinstance(tracks, list) or not tracks:
            raise ContractError("manifest.tracks must be a non-empty array")
        input_mode = "manifest"
    else:
        manifest_path = Path.cwd() / ".direct-asr-multilang-input.json"
        tracks = [
            {"track_id": f"track_{index:02d}", "label": Path(value).stem, "audio_path": value}
            for index, value in enumerate(audio_paths, 1)
        ]
        input_mode = "direct_audio_paths"

    resolved: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, track in enumerate(tracks):
        if not isinstance(track, Mapping):
            raise ContractError(f"tracks[{index}] must be an object")
        track_id = str(track.get("track_id", "")).strip()
        if not track_id or track_id in ids:
            raise ContractError("track_id must be non-empty and unique")
        ids.add(track_id)
        audio_value = track.get("audio_path")
        if not isinstance(audio_value, str) or not audio_value:
            raise ContractError(f"track {track_id} needs audio_path")
        audio_path = resolve_path(audio_value, manifest_path)
        if not audio_path.is_file():
            raise ContractError(f"missing audio for {track_id}: {audio_path}")
        info = wav_info(audio_path)
        if info["channels"] != 1:
            raise ContractError(f"{track_id} must be mono WAV")
        resolved.append(
            {
                "track_id": track_id,
                "label": str(track.get("label") or track_id),
                "audio_path": audio_path,
                "audio_info": info,
            }
        )
    return manifest_path, resolved, input_mode


def transcribe_kwargs(language: str | None, context_prompt: str) -> dict[str, Any]:
    """Return the full decode config, explicitly retaining ``None`` for auto."""

    return {
        "language": language,
        "beam_size": 5,
        "word_timestamps": True,
        "vad_filter": True,
        "condition_on_previous_text": False,
        "initial_prompt": context_prompt or None,
    }


def transcribe_track(
    model: Any,
    track: Mapping[str, Any],
    *,
    language: str | None,
    model_ref: Path,
    context_prompt: str,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    segments, info = model.transcribe(str(track["audio_path"]), **transcribe_kwargs(language, context_prompt))
    words: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(segments, 1):
        for word in segment.words or []:
            words.append(
                {
                    "word_id": f"{track['track_id']}:w{len(words) + 1:06d}",
                    "text": str(word.word),
                    "start_seconds": float(word.start),
                    "end_seconds": float(word.end),
                    "probability": float(word.probability) if word.probability is not None else None,
                    "source_segment_index": segment_index,
                }
            )
    wall = time.perf_counter() - started
    audio_info = track["audio_info"]
    return {
        "schema_version": "faster-whisper-raw-transcript-v1",
        "layer_kind": "upstream_raw_asr",
        "track_id": track["track_id"],
        "label": track["label"],
        "source_audio_path": str(track["audio_path"]),
        "source_audio_sha256": sha256_file(track["audio_path"]),
        "sample_rate_hz": audio_info["sample_rate_hz"],
        "frame_count": audio_info["frame_count"],
        "engine": ENGINE_ID,
        "model_repository": MODEL_REPOSITORY,
        "model_ref": str(model_ref),
        "decode_config": {
            "language_requested": language,
            "language_mode": "auto" if language is None else "explicit_zh",
            **transcribe_kwargs(language, context_prompt),
        },
        "detected_language": getattr(info, "language", None),
        "detected_language_probability": getattr(info, "language_probability", None),
        "raw_timestamp_policy": "verbatim_upstream_no_repair",
        "words": words,
        "out_of_scope": {
            "timestamp_repair": "NOT_INCLUDED",
            "text_correction": "NOT_INCLUDED",
            "candidate_generation": "NOT_INCLUDED",
            "deletion_decision": "NOT_INCLUDED",
        },
    }, wall


def write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise ContractError(f"refusing to overwrite an existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="N-track manifest, same shape as P0 input")
    parser.add_argument("--out", type=Path, required=True, help="new, empty Challenger output directory")
    parser.add_argument("--model", type=Path, help="audited local faster-whisper-small snapshot (default: local cache)")
    parser.add_argument("--context-prompt", default="")
    parser.add_argument(
        "--modes", nargs="+", choices=sorted(MODE_LANGUAGE), default=["zh", "auto"],
        help="arms to run; both are required before calling the result an A/B comparison",
    )
    parser.add_argument("audio_paths", nargs="*", help="one or more mono WAV paths when --manifest is omitted")
    args = parser.parse_args()

    try:
        output_root = args.out.resolve()
        if output_root.exists() and any(output_root.iterdir()):
            raise ContractError(f"--out must be new or empty: {output_root}")
        model_ref = validate_model_reference(args.model or find_local_small_model())
        manifest_path, tracks, input_mode = load_tracks(args.manifest, args.audio_paths)
        if len(set(args.modes)) != len(args.modes):
            raise ContractError("--modes contains a duplicate arm")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ContractError(
                "faster-whisper is not installed in this Python; invoke an audited local ASR venv. "
                "This Challenger will not pip-install or download a runtime."
            ) from exc

        model = WhisperModel(str(model_ref), device="cpu", compute_type="int8")
        results: dict[str, list[dict[str, Any]]] = {}
        for mode in args.modes:
            arm_dir = output_root / mode
            results[mode] = []
            for track in tracks:
                raw_doc, wall = transcribe_track(
                    model,
                    track,
                    language=MODE_LANGUAGE[mode],
                    model_ref=model_ref,
                    context_prompt=str(args.context_prompt or ""),
                )
                raw_path = arm_dir / f"{track['track_id']}.raw.transcript.json"
                write_new_json(raw_path, raw_doc)
                layers = text_layers.build_text_layers(raw_doc, raw_path)
                layers_path = arm_dir / f"{track['track_id']}.text_layers.json"
                text_layers.write_json(layers_path, layers)
                duration = float(track["audio_info"]["duration_seconds"])
                results[mode].append(
                    {
                        "track_id": track["track_id"],
                        "source_audio_sha256": raw_doc["source_audio_sha256"],
                        "raw_transcript_relpath": str(raw_path.relative_to(output_root)),
                        "raw_transcript_sha256": sha256_file(raw_path),
                        "text_layers_relpath": str(layers_path.relative_to(output_root)),
                        "text_layers_sha256": sha256_file(layers_path),
                        "word_count": len(raw_doc["words"]),
                        "detected_language": raw_doc["detected_language"],
                        "wall_seconds": wall,
                        "rtf": wall / duration if duration else None,
                    }
                )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "status": "PASS",
            "input_mode": input_mode,
            "input_manifest_path": str(manifest_path),
            "model": {
                "repository": MODEL_REPOSITORY,
                "local_ref": str(model_ref),
                "device": "cpu",
                "compute_type": "int8",
                "english_only_en_model": "NOT_USED",
            },
            "arms": {
                mode: {
                    "language_requested": MODE_LANGUAGE[mode],
                    "description": "language=None automatic multilingual detection" if mode == "auto" else "language='zh' baseline",
                    "tracks": results[mode],
                }
                for mode in args.modes
            },
            "comparison_status": "READY_FOR_SAME_GOLD_COMPARISON" if set(args.modes) == {"zh", "auto"} else "INCOMPLETE_NEEDS_BOTH_ARMS",
            "safety": {
                "production_transcript_overwritten": False,
                "p0_mvp_modified": False,
                "human_gold_required_for_model_choice": True,
                "auto_promotion": "NOT_ALLOWED",
                "candidate_or_edl_generation": "NOT_INCLUDED",
            },
        }
        write_new_json(output_root / "ab_manifest.json", manifest)
    except (OSError, json.JSONDecodeError, ContractError, ImportError) as exc:
        raise SystemExit(f"ASR multilingual Challenger failed: {exc}") from exc
    print(json.dumps({
        "status": "PASS",
        "out": str(output_root),
        "arms": list(args.modes),
        "comparison_status": manifest["comparison_status"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
