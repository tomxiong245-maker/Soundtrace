#!/usr/bin/env python3
"""Normalize approved candidate-family detectors into the canonical review source.

The adapter intentionally integrates only two owner-approved families:
``self_correction`` and ``cough_like``.  It never creates a decision or EDL.
Self-correction is a high-risk global semantic candidate.  Cough is a
high-risk source-track gate candidate and must never become a global cut.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SELF_CORRECTION_SCRIPT = PROJECT_ROOT / "稳定生产/challengers/self-correction-v1/scripts/detect_self_correction_wordlevel.py"
SELF_CORRECTION_RULES = PROJECT_ROOT / "稳定生产/challengers/self-correction-v1/rules/self-correction-wordlevel.v1.json"
TRANSIENT_SCRIPT = PROJECT_ROOT / "稳定生产/challengers/transient-events-v1/scripts/detect_transient_events.py"
TRANSIENT_RULES = PROJECT_ROOT / "稳定生产/challengers/transient-events-v1/rules/transient-events.v1.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_pairs(values: Iterable[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected LABEL=/absolute/path, got {value!r}")
        label, raw_path = value.split("=", 1)
        path = Path(raw_path).expanduser().resolve()
        if not label or not path.is_file():
            raise ValueError(f"missing detector input: {value!r}")
        if label in result:
            raise ValueError(f"duplicate detector label: {label}")
        result[label] = path
    return result


def _candidate_id(family: str, track_id: str, start_sample: int, end_sample: int, text: str = "") -> str:
    payload = f"{family}|{track_id}|{start_sample}|{end_sample}|{text}".encode("utf-8")
    return f"FAM-{family}-{track_id}-" + hashlib.sha256(payload).hexdigest()[:12]


def _seconds(start_sample: int, end_sample: int, sample_rate_hz: int) -> tuple[float, float]:
    return start_sample / sample_rate_hz, end_sample / sample_rate_hz


def normalize_self_correction_rows(
    rows: Iterable[Mapping[str, Any]], *, track_id: str, sample_rate_hz: int, detector_path: Path, rules_path: Path
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        abandoned = row.get("abandoned_span") or {}
        start_sample = int(row.get("start_sample"))
        end_sample = int(row.get("end_sample"))
        if end_sample <= start_sample:
            continue
        text = str(abandoned.get("text") or "").strip()
        start_seconds, end_seconds = _seconds(start_sample, end_sample, sample_rate_hz)
        candidate_id = _candidate_id("self_correction", track_id, start_sample, end_sample, text)
        normalized.append(
            {
                "candidate_id": candidate_id,
                "candidate_kind": "self_correction",
                "reason_key": "self_correction",
                "source_track": track_id,
                "source_track_id": track_id,
                "start_sample": start_sample,
                "end_sample": end_sample,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "duration_seconds": end_seconds - start_seconds,
                "proposed_delete_text": text,
                "evidence_text": text,
                "abandoned_span": dict(abandoned),
                "retry_span": dict(row.get("retry_span") or {}),
                "cut_scope": "abandoned_span_only",
                "boundary_lock": True,
                "boundary_lock_reason": "self-correction abandoned span is already anchored to ASR word bounds",
                "safety_status": "NEEDS_HUMAN_REVIEW",
                "default_action": "human_review_required",
                "review_display": {
                    "mode": "global_sync_cut",
                    "requires_audio_review": True,
                    "summary": "说错重来候选：只拟删弃用段，重说段保留；必须真人听 A/B。",
                },
                "rendering": {"crossfade_ms": 100.0, "curve": "qsin", "scope": "review_preview_only"},
                "family_provenance": {
                    "adapter": "candidate_family_adapter-v1",
                    "detector": "self_correction_wordlevel",
                    "detector_path": str(detector_path),
                    "detector_sha256": sha256_file(detector_path),
                    "rules_path": str(rules_path),
                    "rules_sha256": sha256_file(rules_path),
                    "raw_detector_candidate": dict(row),
                },
                "policy": "review_only_no_automatic_accept",
            }
        )
    return normalized


def normalize_transient_rows(
    rows: Iterable[Mapping[str, Any]], *, track_id: str, sample_rate_hz: int, detector_path: Path, rules_path: Path
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        # The owner-approved near-term scope is cough only.  Do not silently
        # reintroduce the historically false-positive mic-bump/thump families.
        if str(row.get("reason_key") or "") != "cough_like":
            continue
        start_sample = int(row.get("start_sample"))
        end_sample = int(row.get("end_sample"))
        if end_sample <= start_sample:
            continue
        start_seconds, end_seconds = _seconds(start_sample, end_sample, sample_rate_hz)
        candidate_id = _candidate_id("cough_like", track_id, start_sample, end_sample)
        normalized.append(
            {
                "candidate_id": candidate_id,
                "candidate_kind": "transient_events",
                "reason_key": "cough_like",
                "source_track": track_id,
                "source_track_id": track_id,
                "start_sample": start_sample,
                "end_sample": end_sample,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "duration_seconds": end_seconds - start_seconds,
                "proposed_delete_text": "",
                "evidence_text": "（咳嗽/瞬态声学事件）",
                "cut_scope": "source_track_gate_only",
                "action_type": "source_track_gate",
                "safety_status": "NEEDS_HUMAN_REVIEW",
                "default_action": "human_review_required",
                "review_display": {
                    "mode": "source_track_gate",
                    "requires_audio_review": True,
                    "summary": "咳嗽候选：只允许静音当前源轨，不能全轨删剪；必须真人听原版/A-B。",
                },
                "rendering": {"crossfade_ms": 0.0, "scope": "source_track_gate_preview_only"},
                "family_provenance": {
                    "adapter": "candidate_family_adapter-v1",
                    "detector": "transient_events",
                    "detector_path": str(detector_path),
                    "detector_sha256": sha256_file(detector_path),
                    "rules_path": str(rules_path),
                    "rules_sha256": sha256_file(rules_path),
                    "raw_detector_candidate": dict(row),
                },
                "policy": "review_only_no_automatic_accept",
            }
        )
    return normalized


def _run_detector(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"detector failed ({completed.returncode}): {(completed.stderr or completed.stdout)[-1200:]}")


def integrate_candidate_families(
    *,
    base_source: Path,
    self_transcripts: Mapping[str, Path],
    transient_wavs: Mapping[str, Path],
    transient_transcripts: Mapping[str, Path],
    out_path: Path,
    sample_rate_hz: int,
    detector_out_dir: Path,
    python: str | None = None,
) -> dict[str, Any]:
    base = read_json(base_source)
    candidates = [dict(row) for row in base.get("candidates") or [] if isinstance(row, Mapping)]
    python = python or sys.executable
    detector_out_dir.mkdir(parents=True, exist_ok=True)
    added: list[dict[str, Any]] = []
    self_raw_paths: dict[str, str] = {}
    for track_id, transcript in sorted(self_transcripts.items()):
        raw_path = detector_out_dir / f"self_correction_wordlevel.{track_id}.json"
        _run_detector(
            [
                python,
                str(SELF_CORRECTION_SCRIPT),
                "--transcript",
                f"{track_id}={transcript}",
                "--rules",
                str(SELF_CORRECTION_RULES),
                "--sample-rate-hz",
                str(sample_rate_hz),
                "--out",
                str(raw_path),
            ]
        )
        self_raw_paths[track_id] = str(raw_path)
        raw = read_json(raw_path)
        for track in raw.get("tracks") or []:
            if isinstance(track, Mapping):
                added.extend(
                    normalize_self_correction_rows(
                        track.get("candidates") or [],
                        track_id=track_id,
                        sample_rate_hz=sample_rate_hz,
                        detector_path=SELF_CORRECTION_SCRIPT,
                        rules_path=SELF_CORRECTION_RULES,
                    )
                )
    transient_raw_paths: dict[str, str] = {}
    for track_id, wav in sorted(transient_wavs.items()):
        raw_path = detector_out_dir / f"transient_events.{track_id}.json"
        command = [
            python,
            str(TRANSIENT_SCRIPT),
            "--wav",
            f"{track_id}={wav}",
            "--rules",
            str(TRANSIENT_RULES),
            "--out",
            str(raw_path),
        ]
        transcript = transient_transcripts.get(track_id)
        if transcript is not None:
            command.extend(["--transcript", f"{track_id}={transcript}"])
        _run_detector(command)
        transient_raw_paths[track_id] = str(raw_path)
        raw = read_json(raw_path)
        for track in raw.get("tracks") or []:
            if isinstance(track, Mapping):
                added.extend(
                    normalize_transient_rows(
                        track.get("candidates") or [],
                        track_id=track_id,
                        sample_rate_hz=sample_rate_hz,
                        detector_path=TRANSIENT_SCRIPT,
                        rules_path=TRANSIENT_RULES,
                    )
                )
    existing_keys = {
        (str(row.get("reason_key")), str(row.get("source_track")), int(row.get("start_sample", -1)), int(row.get("end_sample", -1)))
        for row in candidates
        if isinstance(row, Mapping)
    }
    unique_added: list[dict[str, Any]] = []
    for row in sorted(added, key=lambda item: str(item["candidate_id"])):
        key = (str(row["reason_key"]), str(row["source_track"]), int(row["start_sample"]), int(row["end_sample"]))
        if key in existing_keys:
            continue
        existing_keys.add(key)
        unique_added.append(row)
    candidates.extend(unique_added)
    base["candidates"] = candidates
    base["candidate_family_integration"] = {
        "schema_version": "candidate-family-integration-v1",
        "adapter": "candidate_family_adapter-v1",
        "base_candidate_source_sha256": sha256_file(base_source),
        "sample_rate_hz": sample_rate_hz,
        "enabled_families": ["self_correction", "cough_like"],
        "excluded_transient_families": ["mic_bump_like", "thump_like"],
        "self_correction_raw_outputs": self_raw_paths,
        "transient_raw_outputs": transient_raw_paths,
        "added_candidate_ids": [row["candidate_id"] for row in unique_added],
        "added_counts": {
            "self_correction": sum(row.get("reason_key") == "self_correction" for row in unique_added),
            "cough_like": sum(row.get("reason_key") == "cough_like" for row in unique_added),
        },
        "safety": {
            "all_added_candidates_require_human_review": True,
            "self_correction_global_cut_only_after_human_accept": True,
            "cough_source_track_gate_only_after_human_accept": True,
            "never_creates_human_decision": True,
            "never_creates_edl": True,
        },
    }
    write_json(out_path, base)
    return base["candidate_family_integration"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-source", type=Path, required=True)
    parser.add_argument("--self-transcript", action="append", default=[])
    parser.add_argument("--transient-wav", action="append", default=[])
    parser.add_argument("--transient-transcript", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--detector-out-dir", type=Path, required=True)
    parser.add_argument("--sample-rate-hz", type=int, required=True)
    args = parser.parse_args()
    integration = integrate_candidate_families(
        base_source=args.base_source.expanduser().resolve(),
        self_transcripts=parse_pairs(args.self_transcript),
        transient_wavs=parse_pairs(args.transient_wav),
        transient_transcripts=parse_pairs(args.transient_transcript),
        out_path=args.out.expanduser().resolve(),
        sample_rate_hz=args.sample_rate_hz,
        detector_out_dir=args.detector_out_dir.expanduser().resolve(),
    )
    print(json.dumps({"status": "PASS", "integration": integration}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
