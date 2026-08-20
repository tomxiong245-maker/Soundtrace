#!/usr/bin/env python3
"""Create a reproducible, media-free audit sample from a run's no-candidate regions.

This tool deliberately reads only ``run_identity.json``, ``input_manifest.json``
and ``all_candidates.json``.  It never opens, decodes, copies, hashes, or
otherwise accesses referenced media.  The output is an audit *plan* for a
human to listen to later in the original local review environment; it cannot
prove that no missed edit exists outside the sampled windows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "no-candidate-window-audit-v1"
TOOL_VERSION = "no-candidate-window-sampler-v1"
HUMAN_LISTENING_FIELDS = frozenset(
    {
        "human_review_status",
        "human_finding",
        "human_notes",
    }
)
MAX_CHECK_DIFFERENCES = 20


class AuditError(ValueError):
    """A validation error that must prevent creation of an audit bundle."""


@dataclass(frozen=True)
class Parameters:
    seed: str
    count: int
    window_seconds: Decimal
    handle_seconds: Decimal
    window_samples: int
    handle_samples: int


def fail(message: str) -> None:
    raise AuditError(message)


def is_int(value: Any) -> bool:
    return type(value) is int


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        fail(f"required JSON file is missing or is not a file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read JSON {path}: {error}")
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    """Hash one of the three JSON evidence files; never call this on media."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        fail(f"cannot hash JSON evidence {path}: {error}")
    return digest.hexdigest()


def canonical_decimal(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"", "-0"} else rendered


def seconds_to_samples(raw_value: str, sample_rate_hz: int, label: str) -> tuple[Decimal, int]:
    try:
        seconds = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        fail(f"{label} must be a finite decimal number")
    if not seconds.is_finite() or seconds < 0:
        fail(f"{label} must be a non-negative finite decimal number")
    exact_samples = seconds * Decimal(sample_rate_hz)
    if exact_samples != exact_samples.to_integral_value():
        fail(
            f"{label}={raw_value!r} does not align to an integer sample at "
            f"sample_rate_hz={sample_rate_hz}"
        )
    return seconds, int(exact_samples)


def require_string(document: dict[str, Any], key: str, label: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        fail(f"{label}.{key} must be a non-empty string")
    return value


def parse_parameters(
    *,
    seed: str,
    count: int,
    window_seconds_raw: str,
    handle_seconds_raw: str,
    sample_rate_hz: int,
) -> Parameters:
    if not isinstance(seed, str) or not seed:
        fail("seed must be a non-empty string")
    if not is_int(count) or not 8 <= count <= 20:
        fail("count must be an integer from 8 through 20")

    window_seconds, window_samples = seconds_to_samples(
        window_seconds_raw, sample_rate_hz, "window_seconds"
    )
    if not Decimal("20") <= window_seconds <= Decimal("30"):
        fail("window_seconds must be between 20 and 30 seconds inclusive")
    if window_samples <= 0:
        fail("window_seconds must be greater than zero")

    handle_seconds, handle_samples = seconds_to_samples(
        handle_seconds_raw, sample_rate_hz, "handle_seconds"
    )
    return Parameters(
        seed=seed,
        count=count,
        window_seconds=window_seconds,
        handle_seconds=handle_seconds,
        window_samples=window_samples,
        handle_samples=handle_samples,
    )


def load_sources(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    if not run_dir.is_dir():
        fail(f"run_dir is not a directory: {run_dir}")
    identity_path = run_dir / "run_identity.json"
    input_path = run_dir / "input_manifest.json"
    candidates_path = run_dir / "all_candidates.json"
    identity = read_json(identity_path)
    input_manifest = read_json(input_path)
    all_candidates = read_json(candidates_path)
    hashes = {
        "run_identity_sha256": sha256_file(identity_path),
        "input_manifest_sha256": sha256_file(input_path),
        "all_candidates_sha256": sha256_file(candidates_path),
    }
    return identity, input_manifest, all_candidates, hashes


def validate_sources(
    identity: dict[str, Any],
    input_manifest: dict[str, Any],
    all_candidates: dict[str, Any],
    hashes: dict[str, str],
) -> tuple[str, str, int, int, list[str], list[dict[str, Any]]]:
    run_id = require_string(identity, "run_id", "run_identity")
    episode_id = require_string(identity, "episode_id", "run_identity")

    for label, document in (("input_manifest", input_manifest), ("all_candidates", all_candidates)):
        if document.get("run_id") != run_id:
            fail(f"{label}.run_id does not match run_identity.run_id")
        if document.get("episode_id") != episode_id:
            fail(f"{label}.episode_id does not match run_identity.episode_id")
        if document.get("run_identity_sha256") != hashes["run_identity_sha256"]:
            fail(f"{label}.run_identity_sha256 does not match the actual run_identity.json SHA-256")

    sample_rate_hz = input_manifest.get("sample_rate_hz")
    frame_count = input_manifest.get("frame_count")
    if not is_int(sample_rate_hz) or sample_rate_hz <= 0:
        fail("input_manifest.sample_rate_hz must be a positive integer")
    if not is_int(frame_count) or frame_count <= 0:
        fail("input_manifest.frame_count must be a positive integer")

    tracks = input_manifest.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        fail("input_manifest.tracks must be a non-empty list")
    track_ids: list[str] = []
    for index, track in enumerate(tracks):
        if not isinstance(track, dict):
            fail(f"input_manifest.tracks[{index}] must be an object")
        track_id = track.get("track_id")
        if not isinstance(track_id, str) or not track_id:
            fail(f"input_manifest.tracks[{index}].track_id must be a non-empty string")
        if track.get("sample_rate_hz") != sample_rate_hz:
            fail(f"input_manifest.tracks[{index}].sample_rate_hz disagrees with root sample_rate_hz")
        if track.get("frame_count") != frame_count:
            fail(f"input_manifest.tracks[{index}].frame_count disagrees with root frame_count")
        track_ids.append(track_id)
    if len(track_ids) != len(set(track_ids)):
        fail("input_manifest.tracks has duplicate track_id values")

    candidates = all_candidates.get("candidates")
    if not isinstance(candidates, list):
        fail("all_candidates.candidates must be a list")
    candidate_ids: set[str] = set()
    checked_candidates: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            fail(f"all_candidates.candidates[{index}] must be an object")
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            fail(f"all_candidates.candidates[{index}].candidate_id must be a non-empty string")
        if candidate_id in candidate_ids:
            fail(f"all_candidates has duplicate candidate_id={candidate_id!r}")
        candidate_ids.add(candidate_id)
        start = candidate.get("start_sample")
        end = candidate.get("end_sample")
        if not is_int(start) or not is_int(end):
            fail(f"candidate {candidate_id} must use integer start_sample and end_sample")
        if not 0 <= start < end <= frame_count:
            fail(
                f"candidate {candidate_id} has invalid sample interval "
                f"[{start}, {end}) for frame_count={frame_count}"
            )
        checked_candidates.append(
            {
                "candidate_id": candidate_id,
                "start_sample": start,
                "end_sample": end,
            }
        )
    return run_id, episode_id, sample_rate_hz, frame_count, track_ids, checked_candidates


def merge_protected_intervals(
    candidates: list[dict[str, Any]], handle_samples: int, frame_count: int
) -> list[dict[str, Any]]:
    raw_intervals = [
        {
            "start_sample": max(0, candidate["start_sample"] - handle_samples),
            "end_sample": min(frame_count, candidate["end_sample"] + handle_samples),
            "candidate_ids": [candidate["candidate_id"]],
        }
        for candidate in candidates
    ]
    raw_intervals.sort(
        key=lambda interval: (
            interval["start_sample"],
            interval["end_sample"],
            interval["candidate_ids"][0],
        )
    )
    merged: list[dict[str, Any]] = []
    for interval in raw_intervals:
        if not merged or interval["start_sample"] > merged[-1]["end_sample"]:
            merged.append(interval.copy())
            continue
        previous = merged[-1]
        previous["end_sample"] = max(previous["end_sample"], interval["end_sample"])
        previous["candidate_ids"] = sorted(
            set(previous["candidate_ids"]) | set(interval["candidate_ids"])
        )
    return merged


def complement_intervals(protected: list[dict[str, Any]], frame_count: int) -> list[tuple[int, int]]:
    free: list[tuple[int, int]] = []
    cursor = 0
    for interval in protected:
        start = interval["start_sample"]
        end = interval["end_sample"]
        if cursor < start:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < frame_count:
        free.append((cursor, frame_count))
    return free


def rng_for_seed(seed: str) -> random.Random:
    # Python's built-in string hash is intentionally process-randomized.  A
    # SHA-derived integer makes the sample stable across machines and versions.
    seed_value = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest(), "big")
    return random.Random(seed_value)


def choose_windows(
    free_segments: list[tuple[int, int]], parameters: Parameters
) -> tuple[list[tuple[int, int]], int]:
    """Choose capacity-preserving random slots from free timeline segments.

    Each free segment gets a deterministic random phase within its unused
    remainder.  It is then divided into exact window-sized slots.  Sampling
    from those slots cannot make an otherwise feasible request fail because
    of an unlucky early placement, and selected windows can never overlap.
    """
    rng = rng_for_seed(parameters.seed)
    slots: list[tuple[int, int]] = []
    for start, end in free_segments:
        segment_length = end - start
        capacity = segment_length // parameters.window_samples
        if capacity == 0:
            continue
        remainder = segment_length - capacity * parameters.window_samples
        offset = rng.randint(0, remainder)
        slots.extend(
            (
                start + offset + slot_index * parameters.window_samples,
                start + offset + (slot_index + 1) * parameters.window_samples,
            )
            for slot_index in range(capacity)
        )
    capacity = len(slots)
    if capacity < parameters.count:
        fail(
            "cannot draw the requested number of non-overlapping no-candidate windows: "
            f"requested={parameters.count}, capacity={capacity}. No output was written."
        )
    rng.shuffle(slots)
    return sorted(slots[: parameters.count]), capacity


def seconds_from_samples(samples: int, sample_rate_hz: int) -> str:
    return canonical_decimal(Decimal(samples) / Decimal(sample_rate_hz))


def audit_request_sha(
    *,
    run_id: str,
    hashes: dict[str, str],
    parameters: Parameters,
) -> str:
    request = {
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "run_identity_sha256": hashes["run_identity_sha256"],
        "input_manifest_sha256": hashes["input_manifest_sha256"],
        "all_candidates_sha256": hashes["all_candidates_sha256"],
        "seed": parameters.seed,
        "count": parameters.count,
        "window_seconds": canonical_decimal(parameters.window_seconds),
        "handle_seconds": canonical_decimal(parameters.handle_seconds),
    }
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_audit_document(
    *,
    run_dir: Path,
    seed: str,
    count: int,
    window_seconds_raw: str,
    handle_seconds_raw: str,
) -> dict[str, Any]:
    """Build an audit document in memory. This function writes nothing."""
    identity, input_manifest, all_candidates, hashes = load_sources(run_dir)
    run_id, episode_id, sample_rate_hz, frame_count, track_ids, candidates = validate_sources(
        identity, input_manifest, all_candidates, hashes
    )
    parameters = parse_parameters(
        seed=seed,
        count=count,
        window_seconds_raw=window_seconds_raw,
        handle_seconds_raw=handle_seconds_raw,
        sample_rate_hz=sample_rate_hz,
    )
    protected = merge_protected_intervals(candidates, parameters.handle_samples, frame_count)
    free_segments = complement_intervals(protected, frame_count)
    selected, slot_capacity = choose_windows(free_segments, parameters)
    request_sha = audit_request_sha(run_id=run_id, hashes=hashes, parameters=parameters)
    windows = [
        {
            "window_id": f"NC{index:03d}",
            "start_sample": start,
            "end_sample": end,
            "start_seconds": seconds_from_samples(start, sample_rate_hz),
            "end_seconds": seconds_from_samples(end, sample_rate_hz),
            "duration_seconds": canonical_decimal(parameters.window_seconds),
            "review_scope": "listen to all aligned input tracks over the same timeline window",
            "human_review_status": "PENDING_HUMAN_LISTENING",
            "human_finding": None,
            "human_notes": None,
        }
        for index, (start, end) in enumerate(selected, start=1)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "audit_id": f"{run_id}-no-candidate-audit-{request_sha[:12]}",
        "provenance": {
            "run_id": run_id,
            "episode_id": episode_id,
            "run_dir": str(run_dir.resolve()),
            "run_identity_sha256": hashes["run_identity_sha256"],
            "input_manifest_sha256": hashes["input_manifest_sha256"],
            "all_candidates_sha256": hashes["all_candidates_sha256"],
            "input_manifest_schema_version": input_manifest.get("schema_version"),
            "all_candidates_schema_version": all_candidates.get("schema_version"),
            "source_files_read": ["run_identity.json", "input_manifest.json", "all_candidates.json"],
            "media_access": "none; no WAV/MP3/media path was opened, decoded, copied, or hashed",
        },
        "parameters": {
            "seed": parameters.seed,
            "count": parameters.count,
            "window_seconds": canonical_decimal(parameters.window_seconds),
            "window_samples": parameters.window_samples,
            "candidate_handle_seconds": canonical_decimal(parameters.handle_seconds),
            "candidate_handle_samples": parameters.handle_samples,
            "sample_rate_hz": sample_rate_hz,
            "frame_count": frame_count,
            "track_ids": track_ids,
        },
        "sampling": {
            "selection_strategy": "capacity_preserving_random_phase_and_slot_shuffle_v1",
            "source_candidate_count": len(candidates),
            "protected_interval_count": len(protected),
            "protected_intervals": protected,
            "free_segment_count": len(free_segments),
            "free_samples": sum(end - start for start, end in free_segments),
            "non_overlapping_window_capacity": slot_capacity,
            "selection_is_without_replacement": True,
        },
        "windows": windows,
        "human_listening_instruction": (
            "For every window, listen to the synchronized original local tracks and record whether a "
            "clear missed edit, a possible missed edit, or no clear issue is found. This plan creates "
            "no edit decision, EDL, or automatic deletion."
        ),
        "limits": [
            "抽到的窗口均无问题，不证明节目其余区域没有漏剪。",
            "这只是 all_candidates.json 中无候选区域的随机抽查，不能衡量候选 precision、语义正确性或最终音频质量。",
            "在主张漏剪率下降或减少人工审核量前，仍必须有真人试听和明确的审核记录。",
        ],
    }


def render_markdown(document: dict[str, Any]) -> str:
    provenance = document["provenance"]
    parameters = document["parameters"]
    sampling = document["sampling"]
    lines = [
        "# 无候选区域随机抽查",
        "",
        f"- audit_id: `{document['audit_id']}`",
        f"- run_id: `{provenance['run_id']}`",
        f"- 固定随机种子: `{parameters['seed']}`",
        f"- 输入身份 SHA-256: `{provenance['run_identity_sha256']}`",
        f"- input_manifest SHA-256: `{provenance['input_manifest_sha256']}`",
        f"- all_candidates SHA-256: `{provenance['all_candidates_sha256']}`",
        f"- 音频访问: {provenance['media_access']}",
        "",
        "这是一份**人工试听计划**，不是剪辑决定。它从 `all_candidates.json` 中没有候选的位置抽取时间窗，"
        "并在每个候选前后排除配置的保护区。请在原 run 的本地试听环境中，对同一时间线的所有轨道同步试听。",
        "",
        "## 抽样参数",
        "",
        f"- 抽取窗口数：{parameters['count']}",
        f"- 单窗口长度：{parameters['window_seconds']} 秒 / {parameters['window_samples']} samples",
        f"- 候选保护区：前后各 {parameters['candidate_handle_seconds']} 秒 / {parameters['candidate_handle_samples']} samples",
        f"- 源候选数：{sampling['source_candidate_count']}；合并后的保护区：{sampling['protected_interval_count']} 个",
        f"- 可容纳的不重叠窗口数：{sampling['non_overlapping_window_capacity']}",
        "",
        "## 待人工试听窗口",
        "",
        "| ID | 开始（秒） | 结束（秒） | Sample 区间 | 结论 | 备注 |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for window in document["windows"]:
        lines.append(
            f"| {window['window_id']} | {window['start_seconds']} | {window['end_seconds']} | "
            f"[{window['start_sample']}, {window['end_sample']}) | 待试听 | |"
        )
    lines.extend(
        [
            "",
            "## 如何记录",
            "",
            "对每个窗口至少记为：`无明确问题`、`可能漏剪` 或 `明确漏剪/需要新候选`，并写出时间点与原因。"
            "发现问题时，应新建受审核候选；不能直接据此修改 EDL 或自动删剪。",
            "",
            "## 重要限制",
            "",
        ]
    )
    lines.extend(f"- {limit}" for limit in document["limits"])
    lines.append("")
    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def write_audit_bundle(output_dir: Path, document: dict[str, Any]) -> None:
    """Write JSON+Markdown as one atomically published directory.

    The final output directory must not exist.  All files are first written to
    a sibling staging directory, then that completed directory is atomically
    renamed into place.  Thus a validation or write failure cannot leave a
    half-published audit bundle at the requested output path.
    """
    if output_dir.exists():
        fail(f"output_dir already exists; refusing to overwrite evidence: {output_dir}")
    parent = output_dir.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        fail(f"output_dir parent is not a directory: {parent}")
    staging = parent / f".{output_dir.name}.staging-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        write_text(
            staging / "no_candidate_windows.json",
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        write_text(staging / "no_candidate_windows.md", render_markdown(document))
        os.replace(staging, output_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def run_audit(
    *,
    run_dir: Path,
    output_dir: Path,
    seed: str,
    count: int,
    window_seconds_raw: str,
    handle_seconds_raw: str,
) -> dict[str, Any]:
    document = build_audit_document(
        run_dir=run_dir,
        seed=seed,
        count=count,
        window_seconds_raw=window_seconds_raw,
        handle_seconds_raw=handle_seconds_raw,
    )
    write_audit_bundle(output_dir, document)
    return document


def document_without_human_listening_results(document: dict[str, Any]) -> dict[str, Any]:
    """Return the invariant part of an audit document for a strict rebuild check.

    A human may fill in exactly three result fields for each sampled window.
    They are deliberately excluded from reproducibility checks; every other
    field, including the window IDs and boundaries, remains evidence and must
    match the deterministic rebuild exactly.
    """
    windows = document.get("windows")
    if not isinstance(windows, list):
        fail("existing no_candidate_windows.json.windows must be a list")
    normalized = dict(document)
    normalized_windows: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            fail(f"existing no_candidate_windows.json.windows[{index}] must be an object")
        normalized_windows.append(
            {
                key: value
                for key, value in window.items()
                if key not in HUMAN_LISTENING_FIELDS
            }
        )
    normalized["windows"] = normalized_windows
    return normalized


def value_for_error(value: Any) -> str:
    rendered = repr(value)
    if len(rendered) <= 180:
        return rendered
    return f"{rendered[:177]}..."


def collect_document_differences(
    expected: Any,
    actual: Any,
    *,
    path: str = "$",
    max_differences: int = MAX_CHECK_DIFFERENCES,
) -> list[str]:
    """Collect compact, path-specific differences between JSON-compatible values."""
    differences: list[str] = []

    def add(message: str) -> None:
        if len(differences) < max_differences:
            differences.append(message)

    def child_path(parent: str, key: str) -> str:
        return f"{parent}[{key!r}]"

    def visit(expected_value: Any, actual_value: Any, current_path: str) -> None:
        if len(differences) >= max_differences:
            return
        if type(expected_value) is not type(actual_value):
            add(
                f"{current_path}: expected {type(expected_value).__name__} "
                f"{value_for_error(expected_value)}, found {type(actual_value).__name__} "
                f"{value_for_error(actual_value)}"
            )
            return
        if isinstance(expected_value, dict):
            expected_keys = set(expected_value)
            actual_keys = set(actual_value)
            for key in sorted(expected_keys - actual_keys):
                if len(differences) >= max_differences:
                    return
                add(f"{child_path(current_path, key)} is missing; expected {value_for_error(expected_value[key])}")
            for key in sorted(actual_keys - expected_keys):
                if len(differences) >= max_differences:
                    return
                add(f"{child_path(current_path, key)} is unexpected; found {value_for_error(actual_value[key])}")
            for key in sorted(expected_keys & actual_keys):
                visit(expected_value[key], actual_value[key], child_path(current_path, key))
            return
        if isinstance(expected_value, list):
            if len(expected_value) != len(actual_value):
                add(
                    f"{current_path}: expected list length {len(expected_value)}, "
                    f"found {len(actual_value)}"
                )
            for index, (expected_item, actual_item) in enumerate(zip(expected_value, actual_value)):
                visit(expected_item, actual_item, f"{current_path}[{index}]")
            return
        if expected_value != actual_value:
            add(
                f"{current_path}: expected {value_for_error(expected_value)}, "
                f"found {value_for_error(actual_value)}"
            )

    visit(expected, actual, path)
    return differences


def check_audit_bundle(output_dir: Path, expected_document: dict[str, Any]) -> None:
    """Strictly validate an existing audit JSON without writing any artifact."""
    if not output_dir.is_dir():
        fail(f"--check requires an existing audit directory: {output_dir}")
    audit_path = output_dir / "no_candidate_windows.json"
    actual_document = read_json(audit_path)
    expected_invariant = document_without_human_listening_results(expected_document)
    actual_invariant = document_without_human_listening_results(actual_document)
    differences = collect_document_differences(expected_invariant, actual_invariant)
    if differences:
        detail = "\n".join(f"- {difference}" for difference in differences)
        if len(differences) >= MAX_CHECK_DIFFERENCES:
            detail += f"\n- additional differences may be present; showing the first {MAX_CHECK_DIFFERENCES}"
        fail(
            "existing no_candidate_windows.json does not match the deterministic rebuild from "
            "the frozen run JSON and supplied sampling parameters. Only per-window "
            "human_review_status, human_finding, and human_notes are ignored:\n"
            f"{detail}"
        )


def check_audit(
    *,
    run_dir: Path,
    output_dir: Path,
    seed: str,
    count: int,
    window_seconds_raw: str,
    handle_seconds_raw: str,
) -> dict[str, Any]:
    """Rebuild expected evidence in memory and check an existing audit bundle."""
    document = build_audit_document(
        run_dir=run_dir,
        seed=seed,
        count=count,
        window_seconds_raw=window_seconds_raw,
        handle_seconds_raw=handle_seconds_raw,
    )
    check_audit_bundle(output_dir, document)
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="frozen run directory containing the three source JSON files")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new audit directory when building, or existing audit directory with --check",
    )
    parser.add_argument("--seed", required=True, help="required stable random seed; stored verbatim in provenance")
    parser.add_argument("--count", type=int, default=8, help="number of windows to sample, inclusive range: 8..20 (default: 8)")
    parser.add_argument("--window-seconds", default="25", help="one window duration in seconds, inclusive range: 20..30 (default: 25)")
    parser.add_argument("--handle-seconds", default="5", help="candidate exclusion handle before and after each candidate (default: 5)")
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "rebuild expected audit in memory and validate existing no_candidate_windows.json; "
            "never writes or changes audit files"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.check:
            document = check_audit(
                run_dir=args.run_dir,
                output_dir=args.output_dir,
                seed=args.seed,
                count=args.count,
                window_seconds_raw=args.window_seconds,
                handle_seconds_raw=args.handle_seconds,
            )
        else:
            document = run_audit(
                run_dir=args.run_dir,
                output_dir=args.output_dir,
                seed=args.seed,
                count=args.count,
                window_seconds_raw=args.window_seconds,
                handle_seconds_raw=args.handle_seconds,
            )
    except AuditError as error:
        scope = "audit files unchanged" if args.check else "no audit bundle written"
        print(f"FAIL ({scope}): {error}", file=sys.stderr)
        return 2
    except OSError as error:
        scope = "audit files unchanged" if args.check else "no audit bundle written"
        print(f"FAIL ({scope}): {error}", file=sys.stderr)
        return 2
    if args.check:
        print(
            "PASS: existing media-free audit exactly matches the deterministic rebuild "
            "apart from per-window human listening results "
            f"({args.output_dir}; {len(document['windows'])} windows; audit_id={document['audit_id']})"
        )
        return 0
    print(
        "PASS: wrote media-free audit bundle "
        f"{args.output_dir} ({len(document['windows'])} non-overlapping windows; "
        f"audit_id={document['audit_id']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
