#!/usr/bin/env python3
"""Finish an approved project: render cuts, assemble program, and run post-encode QC."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


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


def run(command: list[str], env: dict[str, str]) -> None:
    process = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip())


def load_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON manifest: {path}") from error
    if not isinstance(data, dict):
        raise ValueError(f"manifest root must be an object: {path}")
    return data


def validate_file(path: Path, expected_sha256: str, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"{label} hash mismatch: {path}")


def validate_edl_sources(edl: Path, tracks: list[tuple[str, Path]]) -> None:
    data = load_json(edl)
    if data.get("review_status") != "approved":
        raise ValueError("EDL is not approved")
    expected = data.get("source_sha256")
    if not isinstance(expected, dict):
        raise ValueError("EDL has no source hash map")
    for label, path in tracks:
        digest = expected.get(label)
        if not isinstance(digest, str):
            raise ValueError(f"EDL has no source hash for {label}")
        validate_file(path, digest, f"source track {label}")


def validate_output_entries(
    entries: object, expected_paths: list[Path], stage: str
) -> None:
    if not isinstance(entries, list):
        raise ValueError(f"{stage} manifest has no output list")
    by_path = {
        item.get("path"): item
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    if set(by_path) != {str(path) for path in expected_paths}:
        raise ValueError(f"{stage} manifest output paths do not match this run")
    for path in expected_paths:
        item = by_path[str(path)]
        digest = item.get("sha256")
        if not isinstance(digest, str):
            raise ValueError(f"{stage} output has no SHA-256: {path}")
        validate_file(path, digest, f"{stage} output")


def validate_render_stage(
    render_dir: Path,
    edl: Path,
    tracks: list[tuple[str, Path]],
) -> Path:
    manifest_path = render_dir / "render_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            f"render stage is partial or unverified: {render_dir}; use a new output directory"
        )
    data = load_json(manifest_path)
    if data.get("edl_sha256") != sha256_file(edl):
        raise ValueError("render stage EDL hash does not match this run")
    expected = [render_dir / f"{label}.edited.wav" for label, _ in tracks]
    expected.append(render_dir / "speech_mix.wav")
    validate_output_entries(data.get("outputs"), expected, "render")
    return manifest_path


def same_parameters(actual: object, expected: dict[str, object]) -> bool:
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    for key, value in expected.items():
        observed = actual[key]
        if isinstance(value, float):
            if not isinstance(observed, (int, float)) or abs(float(observed) - value) > 1e-9:
                return False
        elif observed != value:
            return False
    return True


def validate_assembly_stage(
    program_dir: Path,
    speech: Path,
    music: Path,
    parameters: dict[str, object],
) -> Path:
    manifest_path = program_dir / "assembly_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            f"assembly stage is partial or unverified: {program_dir}; use a new output directory"
        )
    data = load_json(manifest_path)
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("assembly manifest has no input map")
    for label, path in (("speech", speech), ("music", music)):
        entry = inputs.get(label)
        if not isinstance(entry, dict) or entry.get("path") != str(path):
            raise ValueError(f"assembly {label} path does not match this run")
        digest = entry.get("sha256")
        if not isinstance(digest, str):
            raise ValueError(f"assembly {label} has no SHA-256")
        validate_file(path, digest, f"assembly input {label}")
    if not same_parameters(data.get("parameters"), parameters):
        raise ValueError("assembly parameters do not match this run")
    validate_output_entries(
        data.get("outputs"),
        [program_dir / "master.approved.wav", program_dir / "master.approved.mp3"],
        "assembly",
    )
    return manifest_path


def validate_inspection(path: Path, expected: dict[str, Path]) -> None:
    data = load_json(path)
    entries = data.get("inputs")
    if not isinstance(entries, list):
        raise ValueError("inspection report has no inputs")
    by_label = {
        item.get("label"): item for item in entries if isinstance(item, dict)
    }
    if set(by_label) != set(expected):
        raise ValueError("inspection labels do not match this run")
    for label, audio_path in expected.items():
        entry = by_label[label]
        if entry.get("path") != str(audio_path):
            raise ValueError(f"inspection path mismatch for {label}")
        digest = entry.get("sha256")
        if not isinstance(digest, str):
            raise ValueError(f"inspection report has no SHA-256 for {label}")
        validate_file(audio_path, digest, f"inspection input {label}")


def validate_loudness(path: Path, expected: dict[str, Path]) -> None:
    data = load_json(path)
    entries = data.get("measurements")
    if not isinstance(entries, list):
        raise ValueError("loudness report has no measurements")
    by_label = {
        item.get("label"): item for item in entries if isinstance(item, dict)
    }
    if set(by_label) != set(expected):
        raise ValueError("loudness labels do not match this run")
    for label, audio_path in expected.items():
        entry = by_label[label]
        if entry.get("path") != str(audio_path):
            raise ValueError(f"loudness path mismatch for {label}")
        digest = entry.get("sha256")
        if not isinstance(digest, str):
            raise ValueError("legacy loudness report lacks input SHA-256 and cannot be resumed safely")
        validate_file(audio_path, digest, f"loudness input {label}")


def validate_final_manifest(
    path: Path,
    edl: Path,
    expected_outputs: list[Path],
    stage_manifests: dict[str, Path],
) -> None:
    data = load_json(path)
    if data.get("schema_version") != 2:
        raise ValueError("legacy final manifest cannot be resumed safely")
    edl_entry = data.get("edl")
    if not isinstance(edl_entry, dict) or edl_entry.get("sha256") != sha256_file(edl):
        raise ValueError("final manifest EDL does not match this run")
    validate_output_entries(data.get("outputs"), expected_outputs, "final")
    recorded = data.get("stage_manifests")
    if not isinstance(recorded, dict):
        raise ValueError("final manifest has no stage manifest hashes")
    for label, manifest_path in stage_manifests.items():
        entry = recorded.get(label)
        if not isinstance(entry, dict) or entry.get("path") != str(manifest_path):
            raise ValueError(f"final manifest stage path mismatch: {label}")
        digest = entry.get("sha256")
        if not isinstance(digest, str):
            raise ValueError(f"final manifest stage has no SHA-256: {label}")
        validate_file(manifest_path, digest, f"final stage manifest {label}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=parse_track)
    parser.add_argument("--edl", required=True, type=Path)
    parser.add_argument("--music", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--speech-delay-seconds", type=float, default=2.015)
    parser.add_argument("--speech-gain-db", type=float, default=3.0)
    parser.add_argument("--intro-music-gain-db", type=float, default=-2.85)
    parser.add_argument("--outro-music-gain-db", type=float, default=0.0)
    parser.add_argument("--outro-overlap-seconds", type=float, default=23.171)
    parser.add_argument("--music-crossfade-ms", type=int, default=750)
    parser.add_argument("--peak-limit-dbfs", type=float, default=-1.0)
    parser.add_argument("--mp3-bitrate", default="192k")
    parser.add_argument("--ducking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tracks = [(label, path.expanduser()) for label, path in args.track]
    edl = args.edl.expanduser()
    music = args.music.expanduser()
    output_dir = args.output_dir.expanduser()
    ffmpeg = args.ffmpeg.expanduser()
    paths = [*(path for _, path in tracks), edl, music, ffmpeg]
    if not output_dir.is_absolute() or any(not path.is_absolute() for path in paths):
        parser.error("all paths must be absolute")
    if len(tracks) < 2 or any(not path.is_file() for path in paths):
        parser.error("at least two tracks and all input files are required")
    if output_dir.exists() and not args.resume:
        raise FileExistsError("refusing to overwrite an existing final output directory")
    validate_edl_sources(edl, tracks)

    render_dir = output_dir / "edited"
    program_dir = output_dir / "program"
    qc_dir = output_dir / "qc"
    render_manifest = render_dir / "render_manifest.json"
    assembly_manifest = program_dir / "assembly_manifest.json"
    inspection_path = qc_dir / "inspection.json"
    loudness_path = qc_dir / "loudness.json"
    track_args = []
    for label, path in tracks:
        track_args.extend(["--track", f"{label}={path}"])
    render_command = [
        sys.executable,
        str(SCRIPT_DIR / "render_approved_edl.py"),
        *track_args,
        "--edl",
        str(edl),
        "--output-dir",
        str(render_dir),
        "--ffmpeg",
        str(ffmpeg),
    ]
    assemble_command = [
        sys.executable,
        str(SCRIPT_DIR / "assemble_program.py"),
        "--speech",
        str(render_dir / "speech_mix.wav"),
        "--music",
        str(music),
        "--output-dir",
        str(program_dir),
        "--ffmpeg",
        str(ffmpeg),
        "--speech-delay-seconds",
        str(args.speech_delay_seconds),
        "--speech-gain-db",
        str(args.speech_gain_db),
        "--intro-music-gain-db",
        str(args.intro_music_gain_db),
        "--outro-music-gain-db",
        str(args.outro_music_gain_db),
        "--outro-overlap-seconds",
        str(args.outro_overlap_seconds),
        "--music-crossfade-ms",
        str(args.music_crossfade_ms),
        "--peak-limit-dbfs",
        str(args.peak_limit_dbfs),
        "--mp3-bitrate",
        args.mp3_bitrate,
        "--output-name",
        "master.approved",
        "--status",
        "approved_edl_master_target_not_frozen",
        "--ducking" if args.ducking else "--no-ducking",
    ]
    wav_path = program_dir / "master.approved.wav"
    mp3_path = program_dir / "master.approved.mp3"
    inspect_command = [
        sys.executable,
        str(SCRIPT_DIR / "inspect_audio.py"),
        "--input",
        f"wav={wav_path}",
        "--input",
        f"mp3={mp3_path}",
        "--output",
        str(inspection_path),
    ]
    loudness_command = [
        sys.executable,
        str(SCRIPT_DIR / "measure_loudness.py"),
        "--input",
        f"wav={wav_path}",
        "--input",
        f"mp3={mp3_path}",
        "--output",
        str(loudness_path),
        "--ffmpeg",
        str(ffmpeg),
    ]
    assembly_parameters = {
        "speech_delay_seconds": args.speech_delay_seconds,
        "speech_gain_db": args.speech_gain_db,
        "intro_music_gain_db": args.intro_music_gain_db,
        "outro_music_gain_db": args.outro_music_gain_db,
        "outro_overlap_seconds": args.outro_overlap_seconds,
        "music_crossfade_ms": args.music_crossfade_ms,
        "peak_limit_dbfs": args.peak_limit_dbfs,
        "mp3_bitrate": args.mp3_bitrate,
        "ducking": args.ducking,
        "ducking_threshold": 0.04,
        "ducking_ratio": 8.0,
        "ducking_attack_ms": 20.0,
        "ducking_release_ms": 300.0,
    }
    commands = [render_command, assemble_command, inspect_command, loudness_command]
    if args.dry_run:
        print(json.dumps({"commands": commands}, ensure_ascii=False, indent=2))
        return 0

    output_dir.mkdir(parents=True, exist_ok=args.resume)
    qc_dir.mkdir(exist_ok=args.resume)
    env = os.environ.copy()
    env["FFMPEG_BIN"] = str(ffmpeg)
    stage_events = []
    if render_dir.exists():
        validate_render_stage(render_dir, edl, tracks)
        stage_events.append({"stage": "render", "action": "reused_verified"})
    else:
        run(render_command, env)
        validate_render_stage(render_dir, edl, tracks)
        stage_events.append({"stage": "render", "action": "executed"})

    speech_mix = render_dir / "speech_mix.wav"
    if program_dir.exists():
        validate_assembly_stage(program_dir, speech_mix, music, assembly_parameters)
        stage_events.append({"stage": "assembly", "action": "reused_verified"})
    else:
        run(assemble_command, env)
        validate_assembly_stage(program_dir, speech_mix, music, assembly_parameters)
        stage_events.append({"stage": "assembly", "action": "executed"})

    expected_audio = {"wav": wav_path, "mp3": mp3_path}
    if inspection_path.exists():
        validate_inspection(inspection_path, expected_audio)
        stage_events.append({"stage": "inspection", "action": "reused_verified"})
    else:
        run(inspect_command, env)
        validate_inspection(inspection_path, expected_audio)
        stage_events.append({"stage": "inspection", "action": "executed"})

    if loudness_path.exists():
        validate_loudness(loudness_path, expected_audio)
        stage_events.append({"stage": "loudness", "action": "reused_verified"})
    else:
        run(loudness_command, env)
        validate_loudness(loudness_path, expected_audio)
        stage_events.append({"stage": "loudness", "action": "executed"})

    manifest = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "approved_edl_render_complete_target_not_frozen",
        "edl": {"path": str(edl), "sha256": sha256_file(edl)},
        "outputs": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in (wav_path, mp3_path)
        ],
        "qc": {
            "inspection": str(inspection_path),
            "loudness": str(loudness_path),
        },
        "stage_manifests": {
            "render": {"path": str(render_manifest), "sha256": sha256_file(render_manifest)},
            "assembly": {
                "path": str(assembly_manifest),
                "sha256": sha256_file(assembly_manifest),
            },
            "inspection": {
                "path": str(inspection_path),
                "sha256": sha256_file(inspection_path),
            },
            "loudness": {
                "path": str(loudness_path),
                "sha256": sha256_file(loudness_path),
            },
        },
        "stage_events": stage_events,
    }
    manifest_path = output_dir / "final_manifest.json"
    if manifest_path.exists():
        validate_final_manifest(
            manifest_path,
            edl,
            [wav_path, mp3_path],
            {
                "render": render_manifest,
                "assembly": assembly_manifest,
                "inspection": inspection_path,
                "loudness": loudness_path,
            },
        )
        print(manifest_path)
        return 0
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
