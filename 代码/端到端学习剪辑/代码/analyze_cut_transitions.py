#!/usr/bin/env python3
"""Prioritize rendered speech crossfades using objective transition metrics."""

from __future__ import annotations

import argparse
import json
import math
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def parse_track(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("track must use LABEL=/absolute/path")
    label, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser()
    if not label or not path.is_absolute():
        raise argparse.ArgumentTypeError("track must use LABEL=/absolute/path")
    return label, path


def decode_pcm(data: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 2:
        return np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    if sample_width == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        values = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        values = np.where(values & 0x800000, values - 0x1000000, values)
        return values.astype(np.float64) / 8388608.0
    if sample_width == 4:
        return np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    raise ValueError(f"unsupported PCM width: {sample_width}")


def rms(samples: np.ndarray) -> float:
    if not samples.size:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))


def to_db(value: float) -> float:
    return 20.0 * math.log10(max(abs(value), 1e-12))


def rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def spectral_cosine_distance(
    left: np.ndarray, right: np.ndarray, sample_rate: int
) -> float | None:
    size = min(left.size, right.size)
    if size < 32:
        return None
    left = left[-size:]
    right = right[:size]
    window = np.hanning(size)
    left_power = np.square(np.abs(np.fft.rfft(left * window)))
    right_power = np.square(np.abs(np.fft.rfft(right * window)))
    frequencies = np.fft.rfftfreq(size, 1.0 / sample_rate)
    selected = (frequencies >= 80.0) & (frequencies <= min(12000.0, sample_rate / 2))
    left_power = left_power[selected]
    right_power = right_power[selected]
    left_norm = float(np.linalg.norm(left_power))
    right_norm = float(np.linalg.norm(right_power))
    if min(left_norm, right_norm) <= 1e-18:
        return None
    similarity = float(np.dot(left_power, right_power) / (left_norm * right_norm))
    return max(0.0, min(1.0, 1.0 - similarity))


class WavReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = wave.open(str(path), "rb")
        if self.handle.getcomptype() != "NONE" or self.handle.getnchannels() != 1:
            raise ValueError(f"expected mono PCM WAV: {path}")
        self.sample_rate = self.handle.getframerate()
        self.frame_count = self.handle.getnframes()
        self.sample_width = self.handle.getsampwidth()

    def close(self) -> None:
        self.handle.close()

    def read(self, start: int, end: int) -> np.ndarray:
        start = max(0, min(self.frame_count, start))
        end = max(start, min(self.frame_count, end))
        self.handle.setpos(start)
        return decode_pcm(self.handle.readframes(end - start), self.sample_width)


def transition_metrics(
    reader: WavReader,
    transition_start: int,
    transition_end: int,
    context_samples: int,
) -> dict[str, float | None]:
    pre = reader.read(transition_start - context_samples, transition_start)
    crossfade = reader.read(transition_start, transition_end)
    post = reader.read(transition_end, transition_end + context_samples)
    pre_rms = rms(pre)
    crossfade_rms = rms(crossfade)
    post_rms = rms(post)
    context_power = (pre_rms**2 + post_rms**2) * 0.5
    context_rms = math.sqrt(context_power)
    combined = np.concatenate((pre, crossfade, post))
    differences = np.abs(np.diff(combined)) if combined.size > 1 else np.empty(0)
    jump_before = abs(crossfade[0] - pre[-1]) if pre.size and crossfade.size else 0.0
    jump_after = abs(post[0] - crossfade[-1]) if post.size and crossfade.size else 0.0
    return {
        "pre_rms_dbfs": rounded(to_db(pre_rms)),
        "crossfade_rms_dbfs": rounded(to_db(crossfade_rms)),
        "post_rms_dbfs": rounded(to_db(post_rms)),
        "context_rms_dbfs": rounded(to_db(context_rms)),
        "post_minus_pre_rms_db": rounded(to_db(post_rms) - to_db(pre_rms)),
        "crossfade_minus_context_rms_db": rounded(
            to_db(crossfade_rms) - to_db(context_rms)
        ),
        "boundary_jump_max_dbfs": rounded(to_db(max(jump_before, jump_after))),
        "max_sample_delta_dbfs": rounded(
            to_db(float(np.max(differences))) if differences.size else -240.0
        ),
        "pre_post_spectral_distance": rounded(
            spectral_cosine_distance(pre, post, reader.sample_rate), 6
        ),
    }


def percentile_ranks(values: list[float]) -> list[float]:
    if not values:
        return []
    sorted_values = sorted(values)
    size = len(sorted_values)
    return [sum(candidate <= value for candidate in sorted_values) / size for value in values]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=parse_track)
    parser.add_argument("--edl", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--context-ms", type=float, default=150.0)
    args = parser.parse_args()

    tracks = [(label, path.expanduser()) for label, path in args.track]
    edl_path = args.edl.expanduser()
    candidates_path = args.candidates.expanduser()
    output_path = args.output.expanduser()
    inputs = [*(path for _, path in tracks), edl_path, candidates_path]
    if not output_path.is_absolute() or any(not path.is_absolute() for path in inputs):
        parser.error("all paths must be absolute")
    if any(not path.is_file() for path in inputs):
        parser.error("an input file does not exist")
    if output_path.exists():
        parser.error("refusing to overwrite an existing report")
    if not 20.0 <= args.context_ms <= 1000.0:
        parser.error("--context-ms must be within 20-1000")

    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    candidate_package = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates = {
        item["candidate_id"]: item for item in candidate_package.get("candidates", [])
    }
    readers = {label: WavReader(path) for label, path in tracks}
    try:
        shared = {(reader.sample_rate, reader.frame_count) for reader in readers.values()}
        if len(shared) != 1:
            raise ValueError("rendered tracks must have equal sample rate and frame count")
        sample_rate, rendered_frame_count = shared.pop()
        context_samples = round(sample_rate * args.context_ms / 1000.0)

        records = []
        removed_before = 0
        overlap_before = 0
        total_removed = 0
        total_overlap = 0
        for cut in edl.get("cuts", []):
            candidate_id = cut["candidate_id"]
            fade = round(cut["crossfade_ms"] * sample_rate / 1000.0)
            transition_start = (
                cut["start_sample"] - removed_before - overlap_before - fade
            )
            transition_end = transition_start + fade
            metrics = {
                label: transition_metrics(
                    reader, transition_start, transition_end, context_samples
                )
                for label, reader in readers.items()
            }
            stem_labels = [label for label in readers if label != "mix"]
            dominant_track = max(
                stem_labels,
                key=lambda label: float(metrics[label]["context_rms_dbfs"] or -240.0),
            ) if stem_labels else None
            candidate = candidates.get(candidate_id, {})
            semantic_risk = str(candidate.get("risk", "unknown"))
            records.append(
                {
                    "candidate_id": candidate_id,
                    "source_start_sample": cut["start_sample"],
                    "source_end_sample": cut["end_sample"],
                    "rendered_transition_start_sample": transition_start,
                    "rendered_transition_end_sample": transition_end,
                    "rendered_transition_seconds": round(
                        transition_start / sample_rate, 6
                    ),
                    "crossfade_ms": cut["crossfade_ms"],
                    "category": candidate.get("category", cut.get("category")),
                    "semantic_risk": semantic_risk,
                    "deleted_text": candidate.get("deleted_text", cut.get("deleted_text")),
                    "dominant_track_near_transition": dominant_track,
                    "metrics": metrics,
                }
            )
            removed = cut["end_sample"] - cut["start_sample"]
            removed_before += removed
            overlap_before += fade
            total_removed += removed
            total_overlap += fade

        expected_frames = (
            int(candidate_package["frame_count"]) - total_removed - total_overlap
        )
        if expected_frames != rendered_frame_count:
            raise ValueError(
                f"rendered frame count mismatch: expected {expected_frames}, got {rendered_frame_count}"
            )
        feature_extractors = {
            "absolute_level_step_db": lambda item: abs(
                float(item["metrics"]["mix"]["post_minus_pre_rms_db"] or 0.0)
            ),
            "absolute_crossfade_level_change_db": lambda item: abs(
                float(item["metrics"]["mix"]["crossfade_minus_context_rms_db"] or 0.0)
            ),
            "spectral_distance": lambda item: float(
                item["metrics"]["mix"]["pre_post_spectral_distance"] or 0.0
            ),
            "boundary_jump_dbfs": lambda item: float(
                item["metrics"]["mix"]["boundary_jump_max_dbfs"] or -240.0
            ),
        }
        feature_values = {
            name: [extractor(item) for item in records]
            for name, extractor in feature_extractors.items()
        }
        feature_percentiles = {
            name: percentile_ranks(values) for name, values in feature_values.items()
        }
        for index, item in enumerate(records):
            percentiles = {
                name: round(feature_percentiles[name][index], 6)
                for name in feature_extractors
            }
            strongest = sorted(percentiles.items(), key=lambda pair: pair[1], reverse=True)
            item["acoustic_feature_values"] = {
                name: rounded(feature_values[name][index], 6) for name in feature_extractors
            }
            item["acoustic_feature_percentiles"] = percentiles
            item["acoustic_outlier_score"] = round(
                100.0 * sum(value for _, value in strongest[:2]) / 2.0, 3
            )
            item["strongest_acoustic_features"] = [name for name, _ in strongest[:2]]

        non_mandatory = [item for item in records if item["semantic_risk"] != "high"]
        extra_review_ids = {
            item["candidate_id"]
            for item in sorted(
                non_mandatory,
                key=lambda item: (-item["acoustic_outlier_score"], item["candidate_id"]),
            )[:5]
        }
        for item in records:
            if item["semantic_risk"] == "high":
                tier = "mandatory_semantic_review"
                reasons = ["candidate_was_already_marked_high_semantic_risk"]
            elif item["candidate_id"] in extra_review_ids:
                tier = "additional_acoustic_spot_check"
                reasons = [
                    "top_five_acoustic_outlier_among_non_high_semantic_candidates",
                    *item["strongest_acoustic_features"],
                ]
            else:
                tier = "routine_spot_check"
                reasons = ["not_high_semantic_risk_and_not_top_five_acoustic_outlier"]
            item["review_tier"] = tier
            item["review_reasons"] = reasons

        tier_order = {
            "mandatory_semantic_review": 0,
            "additional_acoustic_spot_check": 1,
            "routine_spot_check": 2,
        }
        ranked = sorted(
            records,
            key=lambda item: (
                tier_order[item["review_tier"]],
                -item["acoustic_outlier_score"],
                item["rendered_transition_start_sample"],
            ),
        )
        priority_counts = Counter(item["review_tier"] for item in records)
        report = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "objective_transition_priority_subjective_review_required",
            "sample_rate_hz": sample_rate,
            "rendered_frame_count": rendered_frame_count,
            "context_ms": args.context_ms,
            "cut_count": len(records),
            "priority_counts": dict(sorted(priority_counts.items())),
            "method_warning": (
                "Nine existing high-semantic-risk candidates are mandatory reviews. Acoustic metrics "
                "only select five extra spot checks from the remaining candidates. Loudness or spectral "
                "change can be intentional, and routine tier does not prove a natural edit."
            ),
            "ranked_candidate_ids": [item["candidate_id"] for item in ranked],
            "transitions": records,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(output_path)
    finally:
        for reader in readers.values():
            reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
