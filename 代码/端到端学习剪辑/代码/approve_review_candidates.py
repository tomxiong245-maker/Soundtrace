#!/usr/bin/env python3
"""Export an explicitly authorized bulk-approved EDL from review candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--approval-note", required=True)
    parser.add_argument("--accept-all-for-mvp", action="store_true")
    args = parser.parse_args()

    candidates_path = args.candidates.expanduser()
    output_path = args.output.expanduser()
    if not candidates_path.is_absolute() or not output_path.is_absolute():
        parser.error("all paths must be absolute")
    if not candidates_path.is_file():
        parser.error("candidate file does not exist")
    if output_path.exists():
        parser.error("refusing to overwrite an existing approved EDL")
    if not args.accept_all_for_mvp:
        parser.error("bulk approval requires the explicit --accept-all-for-mvp flag")
    if not args.reviewer.strip() or not args.approval_note.strip():
        parser.error("reviewer and approval note cannot be empty")

    package = json.loads(candidates_path.read_text(encoding="utf-8"))
    if package.get("status") != "pending_human_review":
        raise ValueError("candidate package is not pending human review")
    source_hashes = package.get("source_sha256")
    if not isinstance(source_hashes, dict) or len(source_hashes) < 2:
        raise ValueError("candidate package has invalid source hashes")
    raw_candidates = package.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("candidate package contains no candidates")

    cuts = []
    previous_end = 0
    for candidate in raw_candidates:
        start = candidate.get("start_sample")
        end = candidate.get("end_sample")
        crossfade = candidate.get("crossfade_ms")
        if not all(isinstance(value, int) for value in (start, end, crossfade)):
            raise ValueError(f"invalid sample interval: {candidate.get('candidate_id')}")
        if start < previous_end or start >= end:
            raise ValueError(f"overlapping or invalid candidate: {candidate.get('candidate_id')}")
        if not 20 <= crossfade <= 80:
            raise ValueError(f"invalid crossfade: {candidate.get('candidate_id')}")
        cuts.append(
            {
                "candidate_id": candidate["candidate_id"],
                "start_sample": start,
                "end_sample": end,
                "crossfade_ms": crossfade,
                "category": candidate.get("category"),
                "reason": candidate.get("reason"),
                "deleted_text": candidate.get("deleted_text"),
                "bulk_review_decision": "accept",
            }
        )
        previous_end = end

    edl = {
        "schema_version": 1,
        "review_status": "approved",
        "approval_mode": "bulk_accept_all_explicit_user_authorization_for_mvp",
        "reviewer": args.reviewer.strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "approval_note": args.approval_note.strip(),
        "candidate_package": {
            "path": str(candidates_path),
            "sha256": sha256_file(candidates_path),
            "package_id": package.get("package_id"),
        },
        "time_base_hz": package["time_base_hz"],
        "source_sha256": source_hashes,
        "accepted_candidate_count": len(cuts),
        "cuts": cuts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(edl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
