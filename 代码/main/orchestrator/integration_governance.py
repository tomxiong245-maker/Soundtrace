#!/usr/bin/env python3
"""Validate and freeze the owner-attested mainline integration registry.

This is a governance gate, not a semantic-decision gate.  It lets an owner-
approved capability enter a future run while preserving the distinction between
owner attestation, independent verification, human semantic approval and
Champion/publish promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "integration-governance-v1"
ALLOWED_STATUS = {
    "OWNER_ATTESTED_INTEGRATE",
    "EVIDENCE_VERIFIED_INTEGRATE",
    "INTEGRATED_PENDING_REAL_RUN",
    "REOPENED_ON_ISSUE",
    "ISOLATED_NOT_MAINLINE",
    "DEFERRED",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]+$")


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


def validate_registry(registry: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if registry.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version")
    if not registry.get("registry_id"):
        errors.append("registry_id")
    policy = registry.get("policy")
    if not isinstance(policy, Mapping):
        errors.append("policy")
    else:
        if "component_adoption_gate" not in policy:
            errors.append("policy.component_adoption_gate")
        if "semantic_edit_gate" not in policy:
            errors.append("policy.semantic_edit_gate")
        if "verification_order" not in policy:
            errors.append("policy.verification_order")
    mainline = registry.get("mainline")
    if not isinstance(mainline, list) or not mainline:
        errors.append("mainline")
    else:
        seen: set[str] = set()
        for index, item in enumerate(mainline):
            if not isinstance(item, Mapping):
                errors.append(f"mainline[{index}] not object")
                continue
            capability_id = str(item.get("capability_id") or "")
            if not ID_RE.fullmatch(capability_id):
                errors.append(f"mainline[{index}].capability_id")
            if capability_id in seen:
                errors.append(f"duplicate capability_id: {capability_id}")
            seen.add(capability_id)
            if item.get("status") not in ALLOWED_STATUS:
                errors.append(f"mainline[{index}].status")
            if not item.get("mainline_scope"):
                errors.append(f"mainline[{index}].mainline_scope")
            if not item.get("source"):
                errors.append(f"mainline[{index}].source")
            if not item.get("safety"):
                errors.append(f"mainline[{index}].safety")
            if item.get("status") == "OWNER_ATTESTED_INTEGRATE" and item.get("independent_verification") == "PASS":
                errors.append(f"owner attestation cannot claim independent PASS: {capability_id}")
    exclusions = registry.get("mainline_exclusions")
    if not isinstance(exclusions, list):
        errors.append("mainline_exclusions")
    return errors


def load_registry(path: str | Path) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    registry = read_json(resolved)
    errors = validate_registry(registry)
    if errors:
        raise ValueError("integration registry invalid: " + ", ".join(errors))
    return resolved, registry


def mainline_capabilities(registry: Mapping[str, Any]) -> list[str]:
    return [
        str(item["capability_id"])
        for item in registry.get("mainline") or []
        if isinstance(item, Mapping)
        and item.get("status") in {"OWNER_ATTESTED_INTEGRATE", "EVIDENCE_VERIFIED_INTEGRATE", "INTEGRATED_PENDING_REAL_RUN"}
    ]


def freeze_registry(source: str | Path, destination: str | Path) -> dict[str, Any]:
    source_path, registry = load_registry(source)
    destination_path = Path(destination).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    shutil.copy2(source_path, destination_path)
    return {
        "schema_version": "integration-governance-freeze-v1",
        "registry_id": registry["registry_id"],
        "source_relpath": str(source_path),
        "source_sha256": sha256_file(source_path),
        "frozen_relpath": str(destination_path),
        "frozen_sha256": sha256_file(destination_path),
        "mainline_capabilities": mainline_capabilities(registry),
        "owner_attested_count": sum(
            1 for item in registry.get("mainline") or []
            if isinstance(item, Mapping) and item.get("status") == "OWNER_ATTESTED_INTEGRATE"
        ),
        "independent_verification_required": True,
        "semantic_edit_gate_unchanged": True,
    }


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--freeze-into", type=Path)
    args = parser.parse_args()
    source, registry = load_registry(args.registry)
    if args.freeze_into:
        result = freeze_registry(source, args.freeze_into)
    else:
        result = {
            "status": "PASS",
            "registry_id": registry["registry_id"],
            "sha256": sha256_file(source),
            "mainline_capabilities": mainline_capabilities(registry),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
