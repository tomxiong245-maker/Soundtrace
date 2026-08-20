#!/usr/bin/env python3
"""Build a hash-bound review bundle with stricter global-pause requirements.

The existing ``review-product-v1`` builder remains responsible for generating
the N-track A/B previews.  This wrapper copies only the review-policy metadata
from this Challenger's source package into the resulting, hash-bound package.
In particular, all-track long-pause candidates require both previews to be
heard before a human decision can be saved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[4]
BASE_BUILDER = (
    PROJECT_ROOT
    / "稳定生产/challengers/review-product-v1/scripts/build_mvp_package.py"
)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha_bytes(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def policy_for(source_candidate: dict[str, Any]) -> dict[str, Any]:
    display = source_candidate.get("review_display") or {}
    required = ["original", "proposed_cut"] if display.get(
        "requires_audio_review", False
    ) else []
    return {
        "must_listen_to": required,
        "reason": (
            "全轨共同长停顿必须完整试听原音频与压缩后音频，"
            "再决定是否采用剪切。"
            if required
            else "文字可先判；需要确认语气或边界时请试听。"
        ),
    }


def enrich(package_path: Path, source_path: Path) -> dict[str, Any]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source_by_id = {
        str(candidate["candidate_id"]): candidate
        for candidate in source.get("candidates", [])
    }
    package_ids = {str(candidate["candidate_id"]) for candidate in package["candidates"]}
    if package_ids != set(source_by_id):
        raise SystemExit("review builder candidate IDs do not exactly match source package")

    copied_fields = (
        "candidate_kind",
        "filler_subtype",
        "source_track_note",
        "global_silence",
        "proposed_delete_words",
        "retained_evidence_words",
        "evidence_text",
        "proposed_delete_text",
        "filler_token",
        "cross_mic_variants",
        "review_display",
    )
    for candidate in package["candidates"]:
        source_candidate = source_by_id[str(candidate["candidate_id"])]
        for field in copied_fields:
            if field in source_candidate:
                candidate[field] = source_candidate[field]
        candidate["review_requirements"] = policy_for(source_candidate)
        candidate["semantic_sha256"] = sha_bytes(
            {key: value for key, value in candidate.items() if key != "semantic_sha256"}
        )

    package["review_policy"] = {
        "name": "filler-global-pause-v1",
        "source_package_sha256": sha_file(source_path),
        "long_pause_requires_both_previews": True,
        "automatic_cutting": "DISABLED",
    }
    package["review_manifest_sha256"] = sha_bytes(
        {
            key: value
            for key, value in package.items()
            if key != "review_manifest_sha256"
        }
    )
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, required=True)
    parser.add_argument("--previews-dir", type=Path, required=True)
    parser.add_argument("--tracks-manifest", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.source_package.resolve()
    out = args.out.resolve()
    if not source_path.is_file() or not BASE_BUILDER.is_file():
        raise SystemExit("source package or base review builder is missing")
    command = [
        sys.executable,
        str(BASE_BUILDER),
        "--source-package",
        str(source_path),
        "--previews-dir",
        str(args.previews_dir.resolve()),
        "--tracks-manifest",
        str(args.tracks_manifest.resolve()),
        "--frontend",
        str(args.frontend.resolve()),
        "--out",
        str(out),
        "--ffmpeg",
        str(args.ffmpeg.resolve()),
    ]
    subprocess.run(command, check=True)
    package = enrich(out / "review_package.json", source_path)
    print(
        json.dumps(
            {
                "status": "BUILT",
                "package": str(out / "review_package.json"),
                "candidate_count": len(package["candidates"]),
                "required_audio_candidates": sum(
                    bool(candidate["review_requirements"]["must_listen_to"])
                    for candidate in package["candidates"]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
