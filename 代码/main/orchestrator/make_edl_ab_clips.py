#!/usr/bin/env python3
"""Create short original/edited A/B mixes for every global-sync cut in an EDL.

The utility is deliberately review-only: it reads one run's frozen EDL and
raw input links, creates small local MP3 clips with context on both sides of
each edit, and writes a manifest.  It never changes the EDL, labels, policy,
or source audio.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def run_command(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr[-2000:])


def render_original(*, ffmpeg: str, sources: list[Path], start: int, end: int, sample_rate: int, out: Path) -> None:
    filters: list[str] = []
    labels: list[str] = []
    for index in range(len(sources)):
        label = f"a{index}"
        labels.append(f"[{label}]")
        filters.append(f"[{index}:a]atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS[{label}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(sources)}:duration=longest:normalize=1,aresample={sample_rate},aformat=channel_layouts=stereo[out]"
    )
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for source in sources:
        command += ["-i", str(source)]
    command += ["-filter_complex", ";".join(filters), "-map", "[out]", "-ar", str(sample_rate), "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(out)]
    run_command(command)


def render_edited(*, ffmpeg: str, sources: list[Path], window_start: int, cut_start: int, cut_end: int, window_end: int, crossfade: int, sample_rate: int, out: Path) -> None:
    filters: list[str] = []
    labels: list[str] = []
    for index in range(len(sources)):
        pre, post, edited = f"pre{index}", f"post{index}", f"e{index}"
        filters.append(f"[{index}:a]atrim=start_sample={window_start}:end_sample={cut_start},asetpts=PTS-STARTPTS[{pre}]")
        filters.append(f"[{index}:a]atrim=start_sample={cut_end}:end_sample={window_end},asetpts=PTS-STARTPTS[{post}]")
        filters.append(f"[{pre}][{post}]acrossfade=ns={crossfade}:c1=qsin:c2=qsin[{edited}]")
        labels.append(f"[{edited}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(sources)}:duration=longest:normalize=1,aresample={sample_rate},aformat=channel_layouts=stereo[out]"
    )
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for source in sources:
        command += ["-i", str(source)]
    command += ["-filter_complex", ";".join(filters), "-map", "[out]", "-ar", str(sample_rate), "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(out)]
    run_command(command)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--context-seconds", type=float, default=2.0)
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    if not 0.5 <= args.context_seconds <= 10:
        raise SystemExit("--context-seconds must be between 0.5 and 10")
    run_dir = args.run_dir.expanduser().resolve()
    edl = read_object(run_dir / "machine_assisted_draft.edl.json")
    manifest = read_object(run_dir / "input_manifest.json")
    sample_rate = int(edl["sample_rate_hz"])
    frame_count = int(edl["frame_count"])
    inputs = {str(row["track_id"]): row for row in manifest.get("tracks") or [] if isinstance(row, dict)}
    sources: list[Path] = []
    for track in edl.get("tracks") or []:
        source_meta = inputs.get(str(track.get("track_id")))
        if not source_meta:
            raise SystemExit(f"input manifest lacks {track.get('track_id')!r}")
        source = (run_dir / str(source_meta["input_relpath"])).resolve()
        if not source.is_file():
            raise SystemExit(f"raw review source is unavailable: {source}")
        sources.append(source)
    output = (args.out_dir or run_dir / "review_ab_clips").expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=False)
    context = round(args.context_seconds * sample_rate)
    records: list[dict[str, Any]] = []
    render_cuts = {tuple(row.get("source_action_ids") or []): row for row in edl.get("render_sync_cuts") or []}
    for action in edl.get("global_sync_actions") or []:
        if not isinstance(action, dict) or action.get("action_type") != "global_sync_cut":
            continue
        action_id = str(action["action_id"])
        cut = render_cuts.get((action_id,))
        if not cut:
            raise SystemExit(f"render_sync_cuts lacks {action_id}")
        cut_start, cut_end = int(action["start_sample"]), int(action["end_sample"])
        start, end = max(0, cut_start - context), min(frame_count, cut_end + context)
        crossfade = int(cut.get("crossfade_samples", 0))
        if crossfade <= 0 or cut_start - start <= crossfade or end - cut_end <= crossfade:
            raise SystemExit(f"insufficient context for safe A/B preview of {action_id}")
        candidate_id = str(action["candidate_id"])
        before = output / f"{candidate_id}.before.mp3"
        after = output / f"{candidate_id}.after.mp3"
        render_original(ffmpeg=args.ffmpeg, sources=sources, start=start, end=end, sample_rate=sample_rate, out=before)
        render_edited(ffmpeg=args.ffmpeg, sources=sources, window_start=start, cut_start=cut_start, cut_end=cut_end, window_end=end, crossfade=crossfade, sample_rate=sample_rate, out=after)
        records.append({
            "candidate_id": candidate_id,
            "action_id": action_id,
            "source_window_samples": [start, end],
            "cut_samples": [cut_start, cut_end],
            "crossfade_samples": crossfade,
            "before_relpath": before.name,
            "after_relpath": after.name,
        })
    (output / "manifest.json").write_text(json.dumps({
        "schema_version": "review-ab-clips-v1",
        "episode_id": edl["episode_id"],
        "run_id": edl["run_id"],
        "source": "raw_three_track_mix_for_cut_naturalness_review",
        "note": "The frozen denoised media files were not locally retained; these clips isolate cut timing/crossfade using the same raw three-track inputs and EDL.",
        "context_seconds_each_side": args.context_seconds,
        "records": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "out_dir": str(output), "count": len(records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
