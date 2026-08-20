#!/usr/bin/env python3
"""Fail-closed validator for the small N-track review MVP."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha_obj(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_package(path: Path, *, verify_track_hashes: bool = True) -> list[str]:
    pkg = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    if pkg.get("schema_version") != "review-product-mvp-v2":
        errors.append("wrong schema_version")
    tracks = pkg.get("tracks") or []
    ids = [t.get("track_id") for t in tracks]
    if len(ids) < 1 or len(ids) != len(set(ids)) or pkg.get("track_count") != len(ids):
        errors.append("invalid tracks")
    for track in tracks:
        audio = Path(track.get("audio_path", ""))
        if not audio.is_file() or (
            verify_track_hashes and sha_file(audio) != track.get("audio_sha256")
        ):
            errors.append(f"track audio mismatch: {track.get('track_id')}")
        transcript_value = track.get("transcript_path")
        if transcript_value:
            transcript = Path(transcript_value)
            if not transcript.is_file() or sha_file(transcript) != track.get("transcript_sha256"):
                errors.append(f"track transcript mismatch: {track.get('track_id')}")
        semantic_value = track.get("semantic_transcript_path")
        if semantic_value:
            semantic = Path(semantic_value)
            if not semantic.is_file() or sha_file(semantic) != track.get("semantic_transcript_sha256"):
                errors.append(f"track semantic transcript mismatch: {track.get('track_id')}")
    ui = path.parent / "index.html"
    if not ui.is_file() or sha_file(ui) != pkg.get("ui_sha256"):
        errors.append("ui hash mismatch")
    seen = set()
    for cand in pkg.get("candidates", []):
        cid = cand.get("candidate_id")
        if cid in seen:
            errors.append(f"duplicate candidate: {cid}")
        seen.add(cid)
        if cand.get("global_cut", {}).get("applies_to_tracks") != ids:
            errors.append(f"{cid}: global cut is not N-track")
        if set(cand.get("text_tracks", {})) != set(ids):
            errors.append(f"{cid}: text tracks mismatch")
        if cand.get("source_track_id") not in ids:
            errors.append(f"{cid}: unknown source track")
        declared = cand.get("semantic_sha256")
        computed = sha_obj({k: v for k, v in cand.items() if k != "semantic_sha256"})
        if declared != computed:
            errors.append(f"{cid}: semantic hash mismatch")
        for key in ("original", "proposed_cut"):
            asset = path.parent / cand.get("previews", {}).get(f"{key}_path", "")
            if not asset.is_file() or sha_file(asset) != cand.get("previews", {}).get(f"{key}_sha256"):
                errors.append(f"{cid}: {key} preview mismatch")
    computed_manifest = sha_obj({k: v for k, v in pkg.items() if k != "review_manifest_sha256"})
    if computed_manifest != pkg.get("review_manifest_sha256"):
        errors.append("review manifest mismatch")
    return errors


def validate_decisions(pkg: dict, doc: dict) -> list[str]:
    errors = []
    reviewer = str(doc.get("reviewer", "")).strip()
    if len(reviewer) < 2:
        errors.append("reviewer required")
    if doc.get("schema_version") != "human-decisions-mvp-v1":
        errors.append("wrong decision schema")
    if doc.get("package_id") != pkg.get("package_id"):
        errors.append("package mismatch")
    if doc.get("review_manifest_sha256") != pkg.get("review_manifest_sha256"):
        errors.append("manifest mismatch")
    by_id = {c["candidate_id"]: c for c in pkg.get("candidates", [])}
    decisions = doc.get("decisions") or []
    if len(decisions) != len(by_id):
        errors.append("pending or missing decisions")
    seen = set()
    for decision in decisions:
        cid = decision.get("candidate_id")
        if cid in seen or cid not in by_id:
            errors.append(f"duplicate or unknown candidate: {cid}")
            continue
        seen.add(cid)
        if decision.get("decision") not in ("accept", "reject"):
            errors.append(f"{cid}: adjust is disabled in this MVP")
        if decision.get("reviewer") != reviewer or not decision.get("decided_at"):
            errors.append(f"{cid}: reviewer/time missing")
        feedback = decision.get("feedback", "")
        if not isinstance(feedback, str):
            errors.append(f"{cid}: feedback must be a string")
        elif len(feedback) > 500:
            errors.append(f"{cid}: feedback exceeds 500 characters")
        cand = by_id[cid]
        if decision.get("candidate_semantic_sha256") != cand.get("semantic_sha256"):
            errors.append(f"{cid}: candidate hash mismatch")
        listened = decision.get("listened_previews") or {}
        if not isinstance(listened, dict):
            errors.append(f"{cid}: invalid listened_previews")
            continue
        listened_kinds = []
        for kind in ("original", "proposed_cut"):
            preview_sha = listened.get(f"{kind}_sha256")
            listened_at = listened.get(f"{kind}_listened_at")
            if preview_sha is None and listened_at is None:
                continue
            if preview_sha != cand["previews"][f"{kind}_sha256"]:
                errors.append(f"{cid}: {kind} preview mismatch")
            if not listened_at:
                errors.append(f"{cid}: {kind} listened time missing")
            listened_kinds.append(kind)
        inferred_basis = (
            "text_and_audio" if len(listened_kinds) == 2 else
            "text_with_audio" if listened_kinds else
            "text_only"
        )
        declared_basis = decision.get("review_basis")
        if declared_basis is not None and declared_basis != inferred_basis:
            errors.append(f"{cid}: review_basis mismatch")
        for kind in (cand.get("review_requirements") or {}).get("must_listen_to", []):
            if kind not in listened_kinds:
                errors.append(f"{cid}: {kind} preview is required for this candidate")
    return errors


def approved_edl(pkg: dict, doc: dict) -> dict:
    accepted = {d["candidate_id"] for d in doc["decisions"] if d["decision"] == "accept"}
    cuts = []
    for cand in pkg["candidates"]:
        if cand["candidate_id"] in accepted:
            cuts.append({
                "candidate_id": cand["candidate_id"],
                "start_sample": cand["start_sample"],
                "end_sample": cand["end_sample"],
                "applies_to_tracks": [t["track_id"] for t in pkg["tracks"]],
                "crossfade_ms": 50,
            })
    return {
        "schema_version": "approved-edl-draft-mvp-v1",
        "package_id": pkg["package_id"],
        "review_manifest_sha256": pkg["review_manifest_sha256"],
        "sample_rate_hz": pkg["sample_rate_hz"],
        "tracks": pkg["tracks"],
        "reviewer": doc["reviewer"],
        "cuts": cuts,
    }
