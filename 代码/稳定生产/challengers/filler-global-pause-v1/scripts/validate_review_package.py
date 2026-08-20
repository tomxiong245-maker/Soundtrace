#!/usr/bin/env python3
"""Fail-closed validation for the filler/global-pause review bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


BASE = (
    Path(__file__).resolve().parents[2]
    / "review-product-v1/scripts/validate_mvp.py"
)


def load_base():
    import importlib.util

    spec = importlib.util.spec_from_file_location("review_product_validate_mvp", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base validator: {BASE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha_obj(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_package(path: Path, *, verify_track_hashes: bool = True) -> list[str]:
    base = load_base()
    errors = base.validate_package(path, verify_track_hashes=verify_track_hashes)
    package = json.loads(path.read_text(encoding="utf-8"))
    policy = package.get("review_policy")
    if not isinstance(policy, dict) or policy.get("name") != "filler-global-pause-v1":
        errors.append("missing filler-global-pause review policy")
    for candidate in package.get("candidates", []):
        requirements = candidate.get("review_requirements")
        if not isinstance(requirements, dict):
            errors.append(f"{candidate.get('candidate_id')}: missing review requirements")
            continue
        required = requirements.get("must_listen_to")
        if not isinstance(required, list) or any(
            item not in ("original", "proposed_cut") for item in required
        ):
            errors.append(f"{candidate.get('candidate_id')}: invalid required previews")
        if candidate.get("candidate_kind") == "global_long_pause" and set(required) != {
            "original",
            "proposed_cut",
        }:
            errors.append(f"{candidate.get('candidate_id')}: global pause must require both previews")
        declared = candidate.get("semantic_sha256")
        computed = sha_obj({key: value for key, value in candidate.items() if key != "semantic_sha256"})
        if declared != computed:
            errors.append(f"{candidate.get('candidate_id')}: semantic hash mismatch after policy enrichment")
    computed_manifest = sha_obj(
        {key: value for key, value in package.items() if key != "review_manifest_sha256"}
    )
    if computed_manifest != package.get("review_manifest_sha256"):
        errors.append("review manifest mismatch after policy enrichment")
    return errors


def validate_decisions(package: dict, document: dict) -> list[str]:
    base = load_base()
    errors = base.validate_decisions(package, document)
    by_id = {candidate["candidate_id"]: candidate for candidate in package.get("candidates", [])}
    for decision in document.get("decisions") or []:
        candidate = by_id.get(decision.get("candidate_id"))
        if candidate is None:
            continue
        requirements = candidate.get("review_requirements") or {}
        for kind in requirements.get("must_listen_to", []):
            listened = decision.get("listened_previews") or {}
            if (
                listened.get(f"{kind}_sha256")
                != candidate.get("previews", {}).get(f"{kind}_sha256")
                or not listened.get(f"{kind}_listened_at")
            ):
                errors.append(
                    f"{candidate['candidate_id']}: {kind} preview is required for this candidate"
                )
    return errors
