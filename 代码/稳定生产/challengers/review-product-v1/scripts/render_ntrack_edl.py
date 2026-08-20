#!/usr/bin/env python3
"""Render an approved sample-based EDL identically across aligned WAV tracks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def parse_track(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("track must use LABEL=/absolute/path")
    label, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser()
    if not label or not path.is_absolute():
        raise argparse.ArgumentTypeError("track must use LABEL=/absolute/path")
    return label, path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_ffmpeg(explicit: Path | None) -> str:
    if explicit and explicit.expanduser().is_file():
        return str(explicit.expanduser())
    override = os.environ.get("FFMPEG_BIN")
    if override and Path(override).is_file():
        return override
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    raise FileNotFoundError("ffmpeg was not found; pass --ffmpeg or set FFMPEG_BIN")


def wav_info(path: Path) -> dict[str, int]:
    fmt = None
    data_size = None
    with path.open("rb") as audio:
        if audio.read(4) != b"RIFF":
            raise ValueError(f"not RIFF WAV: {path}")
        audio.seek(4, 1)
        if audio.read(4) != b"WAVE":
            raise ValueError(f"not WAVE: {path}")
        while True:
            header = audio.read(8)
            if len(header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", header)
            start = audio.tell()
            if chunk_id == b"fmt ":
                raw = audio.read(size)
                tag, channels, sr, _, block_align, bits = struct.unpack("<HHIIHH", raw[:16])
                if tag == 0xFFFE and len(raw) >= 26:
                    tag = struct.unpack("<H", raw[24:26])[0]
                if tag != 1:
                    raise ValueError(f"compressed/non-PCM WAV is unsupported: {path}")
                fmt = channels, sr, block_align, bits
            elif chunk_id == b"data":
                data_size = size
            audio.seek(start + size + (size & 1))
    if fmt is None or data_size is None:
        raise ValueError(f"WAV is missing fmt/data: {path}")
    channels, sr, block_align, bits = fmt
    return {
        "sample_rate_hz": sr,
        "frame_count": data_size // block_align,
        "channels": channels,
        "sample_width_bytes": bits // 8,
    }


def normalize_mvp_edl(edl: dict[str, object]) -> tuple[dict[str, object], list[tuple[str, Path]]]:
    if edl.get("schema_version") != "approved-edl-draft-mvp-v1":
        raise ValueError("not a P1 MVP EDL")
    reviewer = str(edl.get("reviewer", "")).strip()
    if len(reviewer) < 2:
        raise ValueError("P1 EDL reviewer is missing")
    raw_tracks = edl.get("tracks")
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise ValueError("P1 EDL tracks are missing")
    tracks = []
    hashes = {}
    for raw in raw_tracks:
        if not isinstance(raw, dict):
            raise ValueError("invalid P1 EDL track")
        track_id = str(raw.get("track_id", ""))
        path = Path(str(raw.get("audio_path", ""))).expanduser()
        expected_hash = raw.get("audio_sha256")
        if not track_id or not path.is_absolute() or not isinstance(expected_hash, str):
            raise ValueError("invalid P1 EDL track identity/path/hash")
        tracks.append((track_id, path))
        hashes[track_id] = expected_hash
    track_ids = [label for label, _ in tracks]
    if len(track_ids) != len(set(track_ids)):
        raise ValueError("duplicate P1 EDL track_id")
    cuts = []
    for raw in edl.get("cuts") or []:
        if raw.get("applies_to_tracks") != track_ids:
            raise ValueError("P1 EDL cut does not apply to every track")
        cuts.append({
            "start_sample": raw.get("start_sample"),
            "end_sample": raw.get("end_sample"),
            "crossfade_ms": raw.get("crossfade_ms"),
        })
    normalized = {
        "schema_version": 1,
        "review_status": "approved",
        "time_base_hz": edl.get("sample_rate_hz"),
        "source_sha256": hashes,
        "cuts": cuts,
    }
    return normalized, tracks


def validate_edl(
    edl: dict[str, object],
    tracks: list[tuple[str, Path]],
    sample_rate: int,
    frame_count: int,
) -> list[dict[str, int]]:
    if edl.get("schema_version") != 1:
        raise ValueError("EDL schema_version must be 1")
    if edl.get("review_status") != "approved":
        raise ValueError("EDL review_status must be approved")
    if edl.get("time_base_hz") != sample_rate:
        raise ValueError("EDL time_base_hz must match source sample rate")

    expected_hashes = edl.get("source_sha256")
    if not isinstance(expected_hashes, dict):
        raise ValueError("EDL source_sha256 must be an object")
    for label, path in tracks:
        expected = expected_hashes.get(label)
        if not isinstance(expected, str):
            raise ValueError(f"EDL is missing source hash for {label}")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"source hash mismatch for {label}")

    raw_cuts = edl.get("cuts")
    if not isinstance(raw_cuts, list):
        raise ValueError("EDL cuts must be an array")
    cuts: list[dict[str, int]] = []
    previous_end = 0
    for index, raw_cut in enumerate(raw_cuts):
        if not isinstance(raw_cut, dict):
            raise ValueError(f"cut {index} must be an object")
        start = raw_cut.get("start_sample")
        end = raw_cut.get("end_sample")
        crossfade_ms = raw_cut.get("crossfade_ms")
        if not all(isinstance(value, int) for value in (start, end, crossfade_ms)):
            raise ValueError(f"cut {index} values must be integers")
        if not (0 <= start < end <= frame_count):
            raise ValueError(f"cut {index} is outside the source timeline")
        if start < previous_end:
            raise ValueError(f"cut {index} overlaps or is out of order")
        if not 20 <= crossfade_ms <= 80:
            raise ValueError(f"cut {index} crossfade_ms must be within 20-80")
        cuts.append(
            {
                "start_sample": start,
                "end_sample": end,
                "crossfade_ms": crossfade_ms,
                "crossfade_samples": round(crossfade_ms * sample_rate / 1000),
            }
        )
        previous_end = end

    for index, cut in enumerate(cuts):
        previous_cut_end = cuts[index - 1]["end_sample"] if index else 0
        next_cut_start = cuts[index + 1]["start_sample"] if index + 1 < len(cuts) else frame_count
        fade = cut["crossfade_samples"]
        if cut["start_sample"] - previous_cut_end < fade:
            raise ValueError(f"cut {index} lacks a left crossfade handle")
        if next_cut_start - cut["end_sample"] < fade:
            raise ValueError(f"cut {index} lacks a right crossfade handle")
    return cuts


def render_filter(cuts: list[dict[str, int]], frame_count: int) -> str:
    if not cuts:
        return "[0:a]anull[out]"
    boundaries = []
    cursor = 0
    for cut in cuts:
        boundaries.append((cursor, cut["start_sample"]))
        cursor = cut["end_sample"]
    boundaries.append((cursor, frame_count))

    parts = []
    for index, (start, end) in enumerate(boundaries):
        parts.append(
            f"[0:a]atrim=start_sample={start}:end_sample={end},"
            f"asetpts=PTS-STARTPTS[s{index}]"
        )
    current = "s0"
    for index, cut in enumerate(cuts, start=1):
        output = "out" if index == len(cuts) else f"x{index}"
        parts.append(
            f"[{current}][s{index}]acrossfade=ns={cut['crossfade_samples']}:"
            f"c1=tri:c2=tri[{output}]"
        )
        current = output
    return ";".join(parts)


def run(command: list[str]) -> None:
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", type=parse_track)
    parser.add_argument("--edl", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-automated-e2e", action="store_true")
    args = parser.parse_args()

    edl_path = args.edl.expanduser()
    output_dir = args.output_dir.expanduser()
    if not edl_path.is_absolute() or not output_dir.is_absolute():
        parser.error("all paths must be absolute")
    if not edl_path.is_file():
        parser.error("EDL does not exist")
    edl_raw = json.loads(edl_path.read_text(encoding="utf-8"))
    if edl_raw.get("schema_version") == "approved-edl-draft-mvp-v1":
        if str(edl_raw.get("reviewer", "")).startswith("AUTOMATED_") and not args.allow_automated_e2e:
            raise ValueError("automated E2E EDL cannot be rendered as a human-approved result")
        edl, tracks = normalize_mvp_edl(edl_raw)
        if args.track:
            parser.error("P1 MVP EDL already embeds tracks; do not pass --track")
    else:
        if not args.track:
            parser.error("legacy EDL requires --track LABEL=/absolute/path")
        tracks = [(label, path.expanduser()) for label, path in args.track]
        edl = edl_raw
    if any(not path.is_absolute() for _, path in tracks):
        parser.error("all track paths must be absolute")
    if len(tracks) < 1:
        parser.error("at least one aligned track is required")
    if not edl_path.is_file() or any(not path.is_file() for _, path in tracks):
        parser.error("an input file does not exist")

    infos = [wav_info(path) for _, path in tracks]
    shared = {(info["sample_rate_hz"], info["frame_count"]) for info in infos}
    if len(shared) != 1:
        raise ValueError("all tracks must have equal sample rate and frame count")
    if any(info["channels"] != 1 for info in infos):
        raise ValueError("MVP currently expects mono source tracks")
    sample_rate, frame_count = shared.pop()
    cuts = validate_edl(edl, tracks, sample_rate, frame_count)
    ffmpeg = resolve_ffmpeg(args.ffmpeg)

    output_dir.mkdir(parents=True, exist_ok=True)
    stem_paths = [output_dir / f"{label}.edited.wav" for label, _ in tracks]
    mix_path = output_dir / "speech_mix.wav"
    mix_mp3_path = output_dir / "speech_mix.mp3"
    manifest_path = output_dir / "render_manifest.json"
    targets = [*stem_paths, mix_path, mix_mp3_path, manifest_path]
    if any(path.exists() for path in targets):
        raise FileExistsError("refusing to overwrite an existing render output")

    filter_graph = render_filter(cuts, frame_count)
    if args.dry_run:
        print(json.dumps({"filter_graph": filter_graph, "targets": [str(p) for p in targets]}, indent=2))
        return 0

    for (_, source), destination in zip(tracks, stem_paths):
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(source),
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-c:a",
                "pcm_s24le",
                str(destination),
            ]
        )

    mix_inputs = []
    for path in stem_paths:
        mix_inputs.extend(["-i", str(path)])
    weight = round(1 / len(stem_paths), 9)
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            *mix_inputs,
            "-filter_complex",
            f"amix=inputs={len(stem_paths)}:normalize=0:weights='{' '.join([str(weight)] * len(stem_paths))}'[mix]",
            "-map",
            "[mix]",
            "-c:a",
            "pcm_s24le",
            str(mix_path),
        ]
    )
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(mix_path),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(mix_mp3_path),
        ]
    )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "edl_path": str(edl_path),
        "edl_sha256": sha256_file(edl_path),
        "sample_rate_hz": sample_rate,
        "source_frame_count": frame_count,
        "cut_count": len(cuts),
        "removed_source_samples": sum(c["end_sample"] - c["start_sample"] for c in cuts),
        "crossfade_overlap_samples": sum(c["crossfade_samples"] for c in cuts),
        "outputs": [
            {"path": str(path), "sha256": sha256_file(path), **wav_info(path)}
            for path in [*stem_paths, mix_path]
        ],
        "encoded_outputs": [
            {"path": str(mix_mp3_path), "sha256": sha256_file(mix_mp3_path)}
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
