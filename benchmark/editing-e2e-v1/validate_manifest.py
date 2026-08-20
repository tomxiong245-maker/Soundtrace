#!/usr/bin/env python3
"""Validate the single EP03 development-v1 editing-e2e manifest.

This is intentionally *not* a generic benchmark validator: it rejects other
episodes, frozen splits, gold labels, or anything other than the two-track
EP03 development manifest. It deliberately reads only the JSON manifest and
filesystem metadata. It never opens, decodes, or hashes referenced WAV/MP3.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCHEMA = "editing-e2e-episode-v1"
EPISODE_ID = "EP03"
MANIFEST_ID = "EP03-development-v1"


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("manifest root must be a JSON object")
    return value


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def check_path(errors: list[str], label: str, value: Any) -> None:
    require(errors, isinstance(value, str) and value, f"{label}: missing path")
    if not isinstance(value, str) or not value:
        return
    path = Path(value)
    require(errors, path.is_absolute(), f"{label}: path must be absolute")
    # Path.exists() obtains metadata only. Do not call read_bytes(), hash, or a
    # media decoder here: this validator must remain safe for real local media.
    require(errors, path.exists(), f"{label}: path does not exist: {value}")


def check_sha_fields(errors: list[str], value: Any, label: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_label = f"{label}.{key}"
            if key == "sha256" or key.endswith("_sha256"):
                if child is not None:
                    require(
                        errors,
                        isinstance(child, str) and bool(SHA256_RE.fullmatch(child)),
                        f"{child_label}: expected lowercase 64-hex SHA-256 or null",
                    )
            check_sha_fields(errors, child, child_label)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_sha_fields(errors, child, f"{label}[{index}]")


def check_evidence_path(errors: list[str], parent: dict[str, Any], key: str) -> None:
    item = parent.get(key)
    require(errors, isinstance(item, dict), f"historical_evidence.{key}: missing object")
    if isinstance(item, dict):
        check_path(errors, f"historical_evidence.{key}.path", item.get("path"))


def validate(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    require(errors, doc.get("schema_version") == SCHEMA, "schema_version is wrong")
    require(errors, doc.get("episode_id") == EPISODE_ID, "this validator only accepts episode_id=EP03")
    require(errors, doc.get("manifest_id") == MANIFEST_ID, "this validator only accepts manifest_id=EP03-development-v1")
    require(errors, doc.get("split") == "development", "this validator only accepts split=development")
    status = doc.get("benchmark_status")
    require(errors, isinstance(status, dict), "benchmark_status must be an object")
    if isinstance(status, dict):
        require(errors, status.get("frozen") is False, "this development manifest must set benchmark_status.frozen=false")
        require(errors, status.get("gold") is False, "this development manifest must set benchmark_status.gold=false")
        require(
            errors,
            status.get("eligible_for_champion_comparison") is False,
            "this development manifest must not be champion-comparison eligible",
        )

    privacy = doc.get("privacy")
    require(errors, isinstance(privacy, dict) and privacy.get("local_only") is True, "privacy.local_only must be true")

    tracks = doc.get("raw_tracks")
    require(errors, isinstance(tracks, list) and len(tracks) == 2, "EP03 development manifest requires exactly two raw tracks")
    if isinstance(tracks, list):
        ids: list[Any] = []
        for index, track in enumerate(tracks):
            require(errors, isinstance(track, dict), f"raw_tracks[{index}] must be an object")
            if not isinstance(track, dict):
                continue
            ids.append(track.get("track_id"))
            check_path(errors, f"raw_tracks[{index}].path", track.get("path"))
            require(errors, track.get("sample_rate") == 48000, f"raw_tracks[{index}].sample_rate must be 48000")
            require(errors, track.get("channels") == 1, f"raw_tracks[{index}].channels must be 1")
            require(errors, isinstance(track.get("sample_count"), int) and track["sample_count"] > 0, f"raw_tracks[{index}].sample_count must be positive")
        require(errors, len(set(ids)) == len(ids), "raw_tracks track_id values must be unique")

    reference = doc.get("human_reference")
    require(errors, isinstance(reference, dict), "human_reference must be an object")
    if isinstance(reference, dict):
        check_path(errors, "human_reference.final_audio_path", reference.get("final_audio_path"))
        require(errors, reference.get("reference_edl_path") is None, "EP03 must record missing reference_edl_path as null")
        require(errors, reference.get("human_edit_map_path") is None, "EP03 must record missing human_edit_map_path as null")
        require(errors, reference.get("human_edit_map_provenance") == "none", "EP03 must mark human_edit_map_provenance=none")

    contract = doc.get("evaluation_contract")
    require(errors, isinstance(contract, dict), "evaluation_contract must be an object")
    if isinstance(contract, dict):
        missing = contract.get("cannot_compute_yet")
        require(errors, isinstance(missing, list) and len(missing) >= 6, "cannot_compute_yet must preserve the known EP03 gaps")

    evidence = doc.get("historical_evidence")
    require(errors, isinstance(evidence, dict), "historical_evidence must be an object")
    if isinstance(evidence, dict):
        check_evidence_path(errors, evidence, "source_inspection")
        check_evidence_path(errors, evidence, "mentor_reference_context")
        human_review = evidence.get("human_review")
        require(errors, isinstance(human_review, dict), "historical_evidence.human_review must be an object")
        if isinstance(human_review, dict):
            check_path(errors, "human_review.run_dir", human_review.get("run_dir"))
            for key in ("candidate_package", "human_decisions", "approved_edl_draft", "review_session_metrics", "render_fixture"):
                item = human_review.get(key)
                require(errors, isinstance(item, dict), f"human_review.{key}: missing object")
                if isinstance(item, dict):
                    check_path(errors, f"human_review.{key}.path", item.get("path"))
        system_qc = evidence.get("historical_system_run_qc_not_human_gold")
        require(errors, isinstance(system_qc, dict), "historical_system_run_qc_not_human_gold must be an object")
        if isinstance(system_qc, dict):
            check_path(errors, "historical_system_run_qc_not_human_gold.run_dir", system_qc.get("run_dir"))
            for key in ("final_manifest", "qc_inspection", "qc_loudness"):
                item = system_qc.get(key)
                require(errors, isinstance(item, dict), f"historical_system_run_qc_not_human_gold.{key}: missing object")
                if isinstance(item, dict):
                    check_path(errors, f"historical_system_run_qc_not_human_gold.{key}.path", item.get("path"))

    check_sha_fields(errors, doc)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="EP03-development-v1 episode manifest JSON to validate")
    args = parser.parse_args()
    try:
        document = read_json(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: cannot read manifest: {error}", file=sys.stderr)
        return 2

    errors = validate(document)
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: structural/path/SHA-format checks only; no referenced media was opened or hashed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
