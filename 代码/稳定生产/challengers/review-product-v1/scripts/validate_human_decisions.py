#!/usr/bin/env python3
"""
validate_human_decisions.py · review-product-v1

用法：
    python3 validate_human_decisions.py <review_package.json> <human_decisions.json>

fail-closed 拒绝类别（≥13 类）：
    R01 缺 required 字段
    R02 decision 为 pending / 未知值
    R03 reviewer 为空 / 硬编码列表
    R04 review_manifest_sha256 不匹配审核包
    R05 decisions 里 candidate 未知
    R06 decisions 里 candidate 重复
    R07 candidate_semantic_sha256 与审核包内候选不一致（篡改/换包）
    R08 listened_preview_sha256 不属于该候选的 original/proposed；adjust 时不是 adjustment.reprocessed_preview_sha256
    R09 adjust 缺 adjustment；或 crossfade_ms 越界；或 new_start_sample >= new_end_sample
    R10 每个候选必须有决定；决定数 != 候选数 → 拒绝
    R11 adjust 后未生成新 reprocessed_preview_sha256（等于旧 preview）
    R12 sample 边界越界（超过 sample_rate*24h）
    R13 package_id 与审核包不匹配（旧 package）

Exit code 0 = pass；非 0 = 拒绝原因写 stderr。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

HARD_CODED_REVIEWERS = {
    "", "null", "None", "system", "agent", "Agent", "AI",
    "claude", "Claude", "renting"  # 防"默认 renting"
}


def _load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate(pkg_path: str, dec_path: str) -> Tuple[bool, List[str]]:
    r: List[str] = []
    try:
        pkg = _load(pkg_path)
        dec = _load(dec_path)
    except Exception as e:
        return False, [f"R00 JSON parse failed: {e}"]

    # R13 package_id
    if pkg.get("package_id") != dec.get("package_id"):
        r.append(f"R13 package_id mismatch: pkg={pkg.get('package_id')} dec={dec.get('package_id')}")

    # R04 review_manifest_sha256
    if pkg.get("review_manifest_sha256") != dec.get("review_manifest_sha256"):
        r.append("R04 review_manifest_sha256 mismatch")

    # R03 reviewer
    top_reviewer = dec.get("reviewer", "")
    if not isinstance(top_reviewer, str) or top_reviewer.strip() in HARD_CODED_REVIEWERS:
        r.append(f"R03 reviewer invalid or hard-coded: {top_reviewer!r}")

    # R01 required
    for k in ("schema_version", "package_id", "review_manifest_sha256",
              "reviewer", "session_started_at", "session_ended_at", "decisions"):
        if k not in dec:
            r.append(f"R01 missing top-level field {k}")

    pkg_candidates = {c["candidate_id"]: c for c in pkg.get("candidates", [])}
    seen = set()
    decisions = dec.get("decisions", [])

    # R10 决定数 == 候选数
    if len(decisions) != len(pkg_candidates):
        r.append(
            f"R10 decisions count ({len(decisions)}) != candidates count "
            f"({len(pkg_candidates)})"
        )

    for i, d in enumerate(decisions):
        cid = d.get("candidate_id", f"<idx-{i}>")
        # R01 每条 required
        for k in ("candidate_id", "candidate_semantic_sha256", "decision",
                  "reviewer", "decided_at", "listened_at",
                  "listened_preview_sha256"):
            if k not in d:
                r.append(f"R01 decision[{cid}] missing {k}")

        # R02 decision 枚举
        if d.get("decision") not in ("accept", "reject", "adjust"):
            r.append(f"R02 decision[{cid}] value {d.get('decision')!r} not in enum")

        # R03 每条 reviewer
        rv = d.get("reviewer", "")
        if not isinstance(rv, str) or rv.strip() in HARD_CODED_REVIEWERS:
            r.append(f"R03 decision[{cid}] reviewer invalid or hard-coded: {rv!r}")

        # R05 unknown candidate
        if cid not in pkg_candidates:
            r.append(f"R05 decision[{cid}] not in package candidates")
            continue

        # R06 duplicate
        if cid in seen:
            r.append(f"R06 duplicate decision for {cid}")
        seen.add(cid)

        # R07 semantic 不一致
        pkg_c = pkg_candidates[cid]
        if d.get("candidate_semantic_sha256") != pkg_c.get("semantic_sha256"):
            r.append(
                f"R07 decision[{cid}] semantic_sha256 mismatch: "
                f"decision={d.get('candidate_semantic_sha256')} pkg={pkg_c.get('semantic_sha256')}"
            )

        # R08/R11 listened_preview 必须匹配 previews 或 adjustment 里的新 preview
        orig = pkg_c.get("previews", {}).get("original_sha256")
        prop = pkg_c.get("previews", {}).get("proposed_sha256")
        lp = d.get("listened_preview_sha256")
        adj = d.get("adjustment")
        if d.get("decision") == "adjust":
            # R09 adjust 必须有 adjustment
            if not adj:
                r.append(f"R09 decision[{cid}] adjust without adjustment block")
            else:
                new_start = adj.get("new_start_sample")
                new_end = adj.get("new_end_sample")
                cf = adj.get("crossfade_ms")
                new_prev = adj.get("reprocessed_preview_sha256")
                if not isinstance(new_start, int) or not isinstance(new_end, int) or new_start >= new_end:
                    r.append(f"R09 decision[{cid}] adjustment sample bounds invalid")
                if not isinstance(cf, int) or cf < 20 or cf > 80:
                    r.append(f"R09 decision[{cid}] crossfade_ms out of [20,80]")
                sr = pkg.get("sample_rate", 48000)
                if isinstance(new_end, int) and new_end > sr * 24 * 3600:
                    r.append(f"R12 decision[{cid}] adjustment new_end_sample out of 24h bound")
                # R11 new preview 不能等于旧 preview
                if new_prev == orig or new_prev == prop:
                    r.append(f"R11 decision[{cid}] reprocessed_preview_sha256 equals stale preview")
                # R08 listened_preview 必须等于新 preview
                if lp != new_prev:
                    r.append(
                        f"R08 decision[{cid}] listened_preview_sha256 must equal "
                        f"adjustment.reprocessed_preview_sha256 for adjust"
                    )
        else:
            # accept/reject：listened_preview 必须属于 previews.original/proposed
            if lp not in (orig, prop):
                r.append(
                    f"R08 decision[{cid}] listened_preview_sha256 {lp} "
                    f"not in candidate previews {{original={orig}, proposed={prop}}}"
                )

    return len(r) == 0, r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("decisions")
    args = ap.parse_args()
    ok, reasons = validate(args.package, args.decisions)
    if ok:
        print("PASS")
        return 0
    print("FAIL", file=sys.stderr)
    for r in reasons:
        print(f"  - {r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
