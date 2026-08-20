#!/usr/bin/env python3
"""Infer a coarse human-edit baseline from low-rate mono analysis proxies."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal


def load_proxy(path: Path) -> np.ndarray:
    return np.fromfile(path, dtype="<f4").astype(np.float64)


def normalized_match(template: np.ndarray, search: np.ndarray) -> tuple[int, float]:
    centered = template - template.mean()
    correlation = signal.fftconvolve(search, centered[::-1], mode="valid")
    cumulative = np.concatenate(([0.0], np.cumsum(np.square(search))))
    energy = cumulative[centered.size :] - cumulative[: -centered.size]
    denominator = np.sqrt(np.maximum(energy * np.square(centered).sum(), 1e-18))
    scores = correlation / denominator
    index = int(np.argmax(np.abs(scores)))
    return index, float(scores[index])


def top_music_matches(
    reference: np.ndarray,
    template: np.ndarray,
    sample_rate: int,
    count: int = 2,
) -> list[dict[str, float]]:
    centered = template - template.mean()
    correlation = signal.fftconvolve(reference, centered[::-1], mode="valid")
    cumulative = np.concatenate(([0.0], np.cumsum(np.square(reference))))
    energy = cumulative[centered.size :] - cumulative[: -centered.size]
    denominator = np.sqrt(np.maximum(energy * np.square(centered).sum(), 1e-18))
    scores = correlation / denominator
    work = np.abs(scores).copy()
    radius = max(sample_rate, centered.size // 2)
    matches = []
    for _ in range(count):
        index = int(np.argmax(work))
        matches.append(
            {
                "reference_start_seconds": round(index / sample_rate, 3),
                "normalized_correlation": round(float(scores[index]), 6),
            }
        )
        work[max(0, index - radius) : min(work.size, index + radius)] = 0
    return matches


def timeline_anchors(
    raw_mix: np.ndarray,
    reference: np.ndarray,
    sample_rate: int,
    start_seconds: float,
    end_seconds: float,
    step_seconds: float,
    template_seconds: float,
    search_radius_seconds: float,
) -> list[dict[str, float]]:
    template_frames = round(template_seconds * sample_rate)
    step_frames = round(step_seconds * sample_rate)
    radius_frames = round(search_radius_seconds * sample_rate)
    start_frame = round(start_seconds * sample_rate)
    end_frame = min(round(end_seconds * sample_rate), reference.size - template_frames)
    previous_raw: int | None = None
    anchors = []

    for reference_frame in range(start_frame, end_frame + 1, step_frames):
        template = reference[reference_frame : reference_frame + template_frames]
        if previous_raw is None:
            search_start, search_end = 0, raw_mix.size
        else:
            expected = previous_raw + step_frames
            search_start = max(0, expected - radius_frames)
            search_end = min(
                raw_mix.size, expected + radius_frames + template_frames
            )
        local_index, score = normalized_match(
            template, raw_mix[search_start:search_end]
        )
        raw_frame = search_start + local_index
        if abs(score) < 0.45:
            raw_frame, score = normalized_match(template, raw_mix)
        anchors.append(
            {
                "reference_seconds": round(reference_frame / sample_rate, 3),
                "raw_seconds": round(raw_frame / sample_rate, 3),
                "raw_minus_reference_seconds": round(
                    (raw_frame - reference_frame) / sample_rate, 3
                ),
                "normalized_correlation": round(score, 6),
            }
        )
        previous_raw = raw_frame
    return anchors


def build_plateaus(
    anchors: list[dict[str, float]],
    minimum_correlation: float = 0.55,
    offset_tolerance_seconds: float = 0.08,
    minimum_anchor_count: int = 3,
) -> list[dict[str, float | int]]:
    strong = [a for a in anchors if abs(a["normalized_correlation"]) >= minimum_correlation]
    raw_runs: list[list[dict[str, float]]] = []
    current: list[dict[str, float]] = []
    for anchor in strong:
        median = (
            float(np.median([a["raw_minus_reference_seconds"] for a in current]))
            if current
            else anchor["raw_minus_reference_seconds"]
        )
        if not current or abs(anchor["raw_minus_reference_seconds"] - median) <= offset_tolerance_seconds:
            current.append(anchor)
        else:
            raw_runs.append(current)
            current = [anchor]
    if current:
        raw_runs.append(current)

    runs = [run for run in raw_runs if len(run) >= minimum_anchor_count]
    merged: list[list[dict[str, float]]] = []
    for run in runs:
        run_offset = float(np.median([a["raw_minus_reference_seconds"] for a in run]))
        if merged:
            previous_offset = float(
                np.median([a["raw_minus_reference_seconds"] for a in merged[-1]])
            )
            if abs(run_offset - previous_offset) <= offset_tolerance_seconds:
                merged[-1].extend(run)
                continue
        merged.append(run)

    plateaus = []
    for run in merged:
        plateaus.append(
            {
                "reference_start_seconds": run[0]["reference_seconds"],
                "reference_end_seconds": run[-1]["reference_seconds"],
                "raw_minus_reference_seconds": round(
                    float(np.median([a["raw_minus_reference_seconds"] for a in run])), 3
                ),
                "anchor_count": len(run),
                "median_absolute_correlation": round(
                    float(np.median([abs(a["normalized_correlation"]) for a in run])), 6
                ),
            }
        )
    return plateaus


def derive_cuts(plateaus: list[dict[str, float | int]]) -> list[dict[str, object]]:
    cuts = []
    for previous, following in zip(plateaus, plateaus[1:]):
        old_offset = float(previous["raw_minus_reference_seconds"])
        new_offset = float(following["raw_minus_reference_seconds"])
        removed = new_offset - old_offset
        if removed <= 0.08:
            continue
        boundary_start = float(previous["reference_end_seconds"])
        boundary_end = float(following["reference_start_seconds"])
        boundary_midpoint = (boundary_start + boundary_end) / 2
        cuts.append(
            {
                "status": "inferred_not_approved",
                "reference_boundary_range_seconds": [boundary_start, boundary_end],
                "approximate_raw_interval_seconds": [
                    round(boundary_midpoint + old_offset, 3),
                    round(boundary_midpoint + new_offset, 3),
                ],
                "estimated_removed_seconds": round(removed, 3),
                "confidence": (
                    "medium"
                    if min(
                        float(previous["median_absolute_correlation"]),
                        float(following["median_absolute_correlation"]),
                    )
                    >= 0.7
                    else "low"
                ),
                "note": "Derived from reference/raw timeline mapping; inspect audio before approval",
            }
        )
    return cuts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--music", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-rate", type=int, default=1000)
    args = parser.parse_args()

    paths = [*args.track, args.reference, args.music, args.output]
    if any(not path.expanduser().is_absolute() for path in paths):
        parser.error("all paths must be absolute")
    output = args.output.expanduser()
    if output.exists():
        parser.error(f"refusing to overwrite existing report: {output}")

    tracks = [load_proxy(path.expanduser()) for path in args.track]
    if len({track.size for track in tracks}) != 1:
        raise ValueError("track proxies must have equal lengths")
    raw_mix = np.mean(tracks, axis=0)
    reference = load_proxy(args.reference.expanduser())
    music = load_proxy(args.music.expanduser())
    sample_rate = args.sample_rate

    segment_specs = {
        "full": music,
        "first_30_seconds": music[: 30 * sample_rate],
        "last_30_seconds": music[-30 * sample_rate :],
        "first_15_seconds": music[: 15 * sample_rate],
        "last_15_seconds": music[-15 * sample_rate :],
    }
    music_matches = {
        name: top_music_matches(reference, segment, sample_rate)
        for name, segment in segment_specs.items()
    }
    anchors = timeline_anchors(
        raw_mix,
        reference,
        sample_rate,
        start_seconds=65,
        end_seconds=max(65, reference.size / sample_rate - 72),
        step_seconds=1,
        template_seconds=0.8,
        search_radius_seconds=15,
    )
    plateaus = build_plateaus(anchors)
    cuts = derive_cuts(plateaus)
    first_offset = float(plateaus[0]["raw_minus_reference_seconds"])
    last_offset = float(plateaus[-1]["raw_minus_reference_seconds"])

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "inferred_baseline_not_an_approved_edl",
        "analysis_sample_rate_hz": sample_rate,
        "durations_seconds": {
            "raw": round(raw_mix.size / sample_rate, 3),
            "reference": round(reference.size / sample_rate, 3),
            "music": round(music.size / sample_rate, 3),
        },
        "music_matches": music_matches,
        "timeline_summary": {
            "estimated_lead_added_before_raw_seconds": round(max(0, -first_offset), 3),
            "estimated_total_removed_from_raw_seconds": round(last_offset - first_offset, 3),
            "last_observed_raw_minus_reference_seconds": round(last_offset, 3),
            "strong_anchor_count": sum(
                abs(a["normalized_correlation"]) >= 0.55 for a in anchors
            ),
            "total_anchor_count": len(anchors),
        },
        "plateaus": plateaus,
        "inferred_cut_candidates": cuts,
        "anchors": anchors,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
