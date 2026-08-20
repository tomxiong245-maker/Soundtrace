#!/usr/bin/env python3
"""Assemble a speech mix with reusable intro/outro music and export WAV/MP3."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path


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


def wav_info(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError("speech mix must be an uncompressed WAV")
        return audio.getframerate(), audio.getnframes(), audio.getnchannels()


def probe_duration(ffmpeg: str, path: Path) -> float:
    process = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    import re

    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", process.stderr)
    if not match:
        raise ValueError(f"could not determine duration: {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech", required=True, type=Path)
    parser.add_argument("--music", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--speech-delay-seconds", type=float, default=2.015)
    parser.add_argument("--speech-gain-db", type=float, default=3.0)
    parser.add_argument("--intro-music-gain-db", type=float, default=-2.85)
    parser.add_argument("--outro-music-gain-db", type=float, default=0.0)
    parser.add_argument("--outro-overlap-seconds", type=float, default=23.171)
    parser.add_argument("--music-crossfade-ms", type=int, default=750)
    parser.add_argument("--peak-limit-dbfs", type=float, default=-1.0)
    parser.add_argument("--mp3-bitrate", default="192k")
    parser.add_argument("--output-name", default="master.reference-style.no-delete")
    parser.add_argument(
        "--status", default="reference_style_no_delete_baseline_not_final_mastering_target"
    )
    parser.add_argument("--ducking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--ducking-threshold", type=float, default=0.04)
    parser.add_argument("--ducking-ratio", type=float, default=8.0)
    parser.add_argument("--ducking-attack-ms", type=float, default=20.0)
    parser.add_argument("--ducking-release-ms", type=float, default=300.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    speech = args.speech.expanduser()
    music = args.music.expanduser()
    output_dir = args.output_dir.expanduser()
    if not speech.is_absolute() or not music.is_absolute() or not output_dir.is_absolute():
        parser.error("all paths must be absolute")
    if not speech.is_file() or not music.is_file():
        parser.error("an input file does not exist")
    if not 500 <= args.music_crossfade_ms <= 1000:
        parser.error("music crossfade must be within 500-1000 ms")
    if args.speech_delay_seconds < 0 or args.outro_overlap_seconds < 0:
        parser.error("delay and overlap must be non-negative")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.output_name) or ".." in args.output_name:
        parser.error("--output-name must be a safe file basename")
    if not 0.000976563 <= args.ducking_threshold <= 1:
        parser.error("--ducking-threshold is outside the sidechaincompress range")
    if not 1 <= args.ducking_ratio <= 20:
        parser.error("--ducking-ratio must be within 1-20")

    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    sample_rate, speech_frames, speech_channels = wav_info(speech)
    if speech_channels != 1:
        raise ValueError("MVP currently expects a mono speech mix")
    speech_duration = speech_frames / sample_rate
    music_duration = probe_duration(ffmpeg, music)
    if args.outro_overlap_seconds >= music_duration:
        raise ValueError("outro overlap must be shorter than the music")

    speech_end = args.speech_delay_seconds + speech_duration
    outro_start = speech_end - args.outro_overlap_seconds
    fade_seconds = args.music_crossfade_ms / 1000
    delay_ms = round(args.speech_delay_seconds * 1000)
    outro_delay_ms = round(outro_start * 1000)
    limiter_linear = 10 ** (args.peak_limit_dbfs / 20)

    output_dir.mkdir(parents=True, exist_ok=True)
    wav_output = output_dir / f"{args.output_name}.wav"
    mp3_output = output_dir / f"{args.output_name}.mp3"
    manifest_output = output_dir / "assembly_manifest.json"
    if any(path.exists() for path in (wav_output, mp3_output, manifest_output)):
        raise FileExistsError("refusing to overwrite an existing assembly output")

    speech_filter = (
        f"[0:a]volume={args.speech_gain_db}dB,"
        f"adelay={delay_ms}:all=1,pan=stereo|c0=c0|c1=c0"
    )
    music_filter = (
        f"[1:a]asplit=2[music_intro][music_outro];"
        f"[music_intro]volume={args.intro_music_gain_db}dB,"
        f"afade=t=in:st=0:d={fade_seconds},"
        f"afade=t=out:st={max(0, music_duration-fade_seconds)}:d={fade_seconds}[intro];"
        f"[music_outro]volume={args.outro_music_gain_db}dB,"
        f"afade=t=in:st=0:d={fade_seconds},adelay={outro_delay_ms}:all=1[outro]"
    )
    if args.ducking:
        expected_duration = outro_start + music_duration
        graph = (
            f"{speech_filter},asplit=2[speech][sidechain_raw];"
            f"[sidechain_raw]apad=whole_dur={expected_duration}[sidechain];"
            f"{music_filter};"
            f"[intro][outro]amix=inputs=2:duration=longest:normalize=0[music_program];"
            f"[music_program][sidechain]sidechaincompress="
            f"threshold={args.ducking_threshold}:ratio={args.ducking_ratio}:"
            f"attack={args.ducking_attack_ms}:release={args.ducking_release_ms}[music];"
            f"[speech][music]amix=inputs=2:duration=longest:normalize=0,"
            f"alimiter=limit={limiter_linear}:level=false:latency=1[limited];"
            f"[limited]asplit=2[wav][mp3]"
        )
    else:
        graph = (
            f"{speech_filter}[speech];{music_filter};"
            f"[speech][intro][outro]amix=inputs=3:duration=longest:normalize=0,"
            f"alimiter=limit={limiter_linear}:level=false:latency=1[limited];"
            f"[limited]asplit=2[wav][mp3]"
        )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(speech),
        "-i",
        str(music),
        "-filter_complex",
        graph,
        "-map",
        "[wav]",
        "-c:a",
        "pcm_s24le",
        str(wav_output),
        "-map",
        "[mp3]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        args.mp3_bitrate,
        "-id3v2_version",
        "3",
        str(mp3_output),
    ]
    if args.dry_run:
        print(json.dumps({"filter_graph": graph, "outputs": [str(wav_output), str(mp3_output)]}, indent=2))
        return 0
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr.strip())

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": args.status,
        "inputs": {
            "speech": {"path": str(speech), "sha256": sha256_file(speech)},
            "music": {"path": str(music), "sha256": sha256_file(music)},
        },
        "parameters": {
            "speech_delay_seconds": args.speech_delay_seconds,
            "speech_gain_db": args.speech_gain_db,
            "intro_music_gain_db": args.intro_music_gain_db,
            "outro_music_gain_db": args.outro_music_gain_db,
            "outro_overlap_seconds": args.outro_overlap_seconds,
            "music_crossfade_ms": args.music_crossfade_ms,
            "peak_limit_dbfs": args.peak_limit_dbfs,
            "mp3_bitrate": args.mp3_bitrate,
            "ducking": args.ducking,
            "ducking_threshold": args.ducking_threshold,
            "ducking_ratio": args.ducking_ratio,
            "ducking_attack_ms": args.ducking_attack_ms,
            "ducking_release_ms": args.ducking_release_ms,
        },
        "derived": {
            "speech_duration_seconds": round(speech_duration, 6),
            "music_duration_seconds": round(music_duration, 6),
            "outro_start_seconds": round(outro_start, 6),
            "expected_program_duration_seconds": round(outro_start + music_duration, 6),
        },
        "outputs": [
            {"path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in (wav_output, mp3_output)
        ],
        "tool": subprocess.run(
            [ffmpeg, "-version"], check=False, capture_output=True, text=True
        ).stdout.splitlines()[0],
    }
    manifest_output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
