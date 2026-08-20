#!/usr/bin/env python3
"""
build_review_product_package.py · review-product-v1

只读输入：
    - main/runs/EP03-cross-track-safety-v1/safe_candidates.json
    - main/runs/EP03-cross-track-safety-v1/review_package/review_package.json
    - main/runs/EP03-cross-track-safety-v1/review_package/previews/*.mp3
    - main/runs/EP03-freshrun-20260810-1730/... (transcript/classified)  ← 仅只读

只写输出：
    - main/runs/EP03-review-product-v1/review_package/review_package.json
    - main/runs/EP03-review-product-v1/review_package/previews/*.mp3   (硬链接或拷贝)

用法：
    python3 build_review_product_package.py \
        --safe   .../safe_candidates.json \
        --oldpkg .../review_package.json \
        --previews-dir .../previews/ \
        --out .../EP03-review-product-v1/review_package/ \
        --package-id EP03-review-product-v1-YYYYMMDD-HHMM
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def convert_context_word(w: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "text": w.get("text", ""),
        "s": w.get("start_seconds", 0.0),
        "e": w.get("end_seconds", 0.0),
        "cls": w.get("classification", "unknown"),
        "in_cut": bool(w.get("in_cut", False)),
    }


def build_candidate(c_safe: Dict[str, Any], c_old: Dict[str, Any]) -> Dict[str, Any]:
    cid = c_safe["candidate_id"]
    ctx_secs = c_old.get("context_seconds", 5.0)
    ctx_start = c_old.get("context_start_seconds", c_safe["start_seconds"] - ctx_secs)
    ctx_end = c_old.get("context_end_seconds", c_safe["end_seconds"] + ctx_secs)
    context_words = c_old.get("context_words", {"female": [], "male": []})
    text_tracks = {
        "female": {
            "track": "female",
            "window_start_seconds": ctx_start,
            "window_end_seconds": ctx_end,
            "words": [convert_context_word(w) for w in context_words.get("female", [])],
        },
        "male": {
            "track": "male",
            "window_start_seconds": ctx_start,
            "window_end_seconds": ctx_end,
            "words": [convert_context_word(w) for w in context_words.get("male", [])],
        },
    }

    # risk notes
    risk_notes = []
    if c_safe.get("other_activity_stats", {}).get("primary", 0) > 0:
        risk_notes.append("另一轨在候选窗内有 primary 词——请听 A/B 后判断")
    if c_safe["duration_seconds"] < 0.35:
        risk_notes.append("cut 窗口极短（<0.35s），边界易误伤")
    if c_safe.get("source_activity_stats", {}).get("bleed", 0) > 0:
        risk_notes.append("source track 内含 bleed 词——启发式标签可能失真")

    cand = {
        "candidate_id": cid,
        "reason_key": c_safe["reason_key"],
        "source_track": c_safe["source_track"],
        "start_sample": c_safe["start_sample"],
        "end_sample": c_safe["end_sample"],
        "start_seconds": c_safe["start_seconds"],
        "end_seconds": c_safe["end_seconds"],
        "duration_seconds": c_safe["duration_seconds"],
        "safety_status": c_safe["safety_status"],
        "reason_codes": c_safe.get("reason_codes", []),
        "evidence_words": c_safe["evidence_words"],
        "text_tracks": text_tracks,
        "global_cut": {
            "start_sample": c_safe["start_sample"],
            "end_sample": c_safe["end_sample"],
            "applies_to_tracks": ["female", "male"],
        },
        "previews": {
            "original_sha256": "PLACEHOLDER",
            "proposed_sha256": "PLACEHOLDER",
        },
        "risk_notes": risk_notes,
        "provenance": c_safe["provenance"],
    }
    return cand


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--safe", required=True)
    ap.add_argument("--oldpkg", required=True)
    ap.add_argument("--previews-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--package-id", required=True)
    ap.add_argument("--sample-rate", type=int, default=48000)
    ap.add_argument("--context-seconds", type=float, default=5.0)
    ap.add_argument("--crossfade-ms", type=int, default=40)
    args = ap.parse_args()

    with open(args.safe, "r", encoding="utf-8") as f:
        safe_data = json.load(f)
    with open(args.oldpkg, "r", encoding="utf-8") as f:
        old_pkg = json.load(f)

    # index old candidates by cid
    old_by_cid = {c["candidate_id"]: c for c in old_pkg.get("candidates", [])}

    out_pkg_dir = args.out
    out_prev_dir = os.path.join(out_pkg_dir, "previews")
    os.makedirs(out_prev_dir, exist_ok=True)

    # 拷贝 preview（不改内容）
    preview_assets: Dict[str, Any] = {}
    candidates = []
    for c_safe in safe_data.get("candidates", []):
        cid = c_safe["candidate_id"]
        c_old = old_by_cid.get(cid, {})
        cand = build_candidate(c_safe, c_old)
        # preview
        for kind, suffix in (("original_sha256", "original"), ("proposed_sha256", "proposed-cut")):
            src = os.path.join(args.previews_dir, f"{cid}.{suffix}.mp3")
            if os.path.isfile(src):
                dst = os.path.join(out_prev_dir, f"{cid}.{suffix}.mp3")
                if not os.path.isfile(dst):
                    shutil.copy2(src, dst)
                h = sha256_file(dst)
                cand["previews"][kind] = h
                preview_assets[f"{cid}.{suffix}.mp3"] = {
                    "path": f"previews/{cid}.{suffix}.mp3",
                    "sha256": h,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                cand["previews"][kind] = "MISSING"

        # semantic sha over candidate (excl. semantic_sha256)
        cand["semantic_sha256"] = sha256_bytes(canonical_json_bytes(cand))
        candidates.append(cand)

    pkg = {
        "schema_version": "review-product-v1",
        "package_id": args.package_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sample_rate": args.sample_rate,
        "master_time_base": "EP03-freshrun-20260810-1730-master",
        "source_audio": {
            "female_path": "main/runs/EP03-freshrun-20260810-1730/04_denoise/female.aligned.wav",
            "female_sha256": "TO_BE_FILLED_BY_EXACT_COMMANDS",
            "male_path": "main/runs/EP03-freshrun-20260810-1730/04_denoise/male.aligned.wav",
            "male_sha256": "TO_BE_FILLED_BY_EXACT_COMMANDS",
        },
        "input_provenance": {
            "female_classified_path": "main/runs/EP03-freshrun-20260810-1730/06_activity/female.classified.json",
            "female_classified_sha256": safe_data["input_provenance"]["female_classified_sha256"],
            "male_classified_path": "main/runs/EP03-freshrun-20260810-1730/06_activity/male.classified.json",
            "male_classified_sha256": safe_data["input_provenance"]["male_classified_sha256"],
            "candidates_source_path": "main/runs/EP03-cross-track-safety-v1/safe_candidates.json",
            "candidates_source_sha256": sha256_file(args.safe),
        },
        "candidates": candidates,
        "preview_assets": preview_assets,
        "static_assets": {
            "files": {
                # 页面 SHA 在 exact_commands.sh 中计算后回填
            }
        },
        "review_config": {
            "context_seconds": args.context_seconds,
            "crossfade_ms_default": args.crossfade_ms,
        },
        "review_manifest_sha256": "PLACEHOLDER",
    }

    # 计算 review_manifest_sha256
    manifest_copy = {k: v for k, v in pkg.items() if k != "review_manifest_sha256"}
    pkg["review_manifest_sha256"] = sha256_bytes(canonical_json_bytes(manifest_copy))

    out_path = os.path.join(out_pkg_dir, "review_package.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)
    print(f"WROTE {out_path}")
    print(f"package_id = {pkg['package_id']}")
    print(f"review_manifest_sha256 = {pkg['review_manifest_sha256']}")
    print(f"candidates = {len(candidates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
