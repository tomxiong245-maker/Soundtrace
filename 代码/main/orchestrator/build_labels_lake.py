#!/usr/bin/env python3
"""build_labels_lake — 扫全项目 human_decisions.json 汇总真人标签数据。

单期 run 里的 experience_signal 只回填了**本 run 上游同事件**的 ha/hr。这让
autocut_gate 只看到冰山一角（EP04 v20 → 5 项 / 总 33 项，其余 85% 丢弃）。

本工具产出跨 run/跨 episode 的**标签数据湖**：

    {
      "by_reason_key": {
         "filler_hesitation": {
             "strong_hesitation_sound": {
                "accept": 8, "reject": 2,
                "case_ids": ["EP03::C005", "EP04-v20::C007", ...]
             },
             "repeated_weak_filler": {...},
         },
         "immediate_repetition": {...},
         ...
      },
      "by_run": {run_id → {accept: N, reject: M}},
      "by_reviewer": {name → {accept, reject, latest_at}},
      "summary": {total, distinct_runs, distinct_reviewers, ...}
    }

autocut_gate v2 会消费本文件的 by_reason_key 段做 G5 判决，允许少量反例
（accept_rate ≥ threshold）而不是一票否决。

Usage:
    python3 build_labels_lake.py \\
        --project-root /path/to/剪辑项目 \\
        --exclude-reviewers AUTOMATED_TEXT_FIRST \\
        --out main/knowledge/labels_lake.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _load(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _stratum_subtype(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return "unknown"
    stratum = str(candidate.get("stratum") or "")
    parts = stratum.split(":")
    return parts[1] if len(parts) >= 2 else "unknown"


def _reason_key(candidate: dict[str, Any] | None, decision_entry: dict[str, Any]) -> str:
    if candidate:
        r = candidate.get("reason_key") or candidate.get("candidate_reason_key")
        if r:
            return str(r)
    return str(decision_entry.get("candidate_reason_key") or decision_entry.get("reason_key") or "unknown")


def _find_candidate(run_dir: Path, candidate_id: str) -> dict[str, Any] | None:
    """Look up candidate details for a decision. Prefer newer canonical
    sources, fall back through older run layouts."""
    lookup_paths = [
        run_dir / "all_candidates.json",
        run_dir / "calibration_source.json",
        run_dir / "candidates" / "candidate_source.json",
        run_dir / "candidate_source.json",
        run_dir / "review_bundle" / "review_package.json",
    ]
    for p in lookup_paths:
        if not p.is_file():
            continue
        doc = _load(p)
        if not doc:
            continue
        cands = doc.get("candidates") if isinstance(doc, dict) else doc
        for c in cands or []:
            if str(c.get("candidate_id")) == str(candidate_id):
                return c
    return None


def build(project_root: Path, *, exclude_reviewers: set[str]) -> dict[str, Any]:
    # 3-level nesting: reason_key -> subtype -> filler_token -> counts
    by_reason: dict[str, dict[str, dict[str, dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: {"accept": 0, "reject": 0, "case_ids": []}))
    )
    by_run: dict[str, dict[str, int]] = defaultdict(lambda: {"accept": 0, "reject": 0})
    by_reviewer: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"accept": 0, "reject": 0, "latest_at": None}
    )
    seen_case_keys: set[str] = set()

    hd_paths = sorted(project_root.glob("main/runs/**/human_decisions.json"))
    for hd in hd_paths:
        rel = str(hd.relative_to(project_root))
        if "AUDIO-CLEANUP" in rel:
            continue
        doc = _load(hd)
        if not doc:
            continue
        reviewer = str(doc.get("reviewer") or "").strip()
        if reviewer in exclude_reviewers:
            continue
        if not reviewer:
            continue
        run_id = str(doc.get("run_id") or hd.parent.name)
        run_dir = hd.parent

        for entry in doc.get("decisions", []) or []:
            decision = str(entry.get("decision") or "")
            if "accept" not in decision and "reject" not in decision:
                continue
            side = "accept" if "accept" in decision else "reject"
            cid = str(entry.get("candidate_id") or "")
            candidate = _find_candidate(run_dir, cid) if cid else None
            reason = _reason_key(candidate, entry)
            subtype = _stratum_subtype(candidate)
            # filler_token: from candidate.filler_token, else from proposed_delete_text
            filler_token = "unknown"
            if candidate:
                tok = candidate.get("filler_token") or candidate.get("proposed_delete_text")
                if tok:
                    filler_token = str(tok).strip()[:20] or "unknown"
            case_key = f"{run_id}::{cid}"
            if case_key in seen_case_keys:
                continue
            seen_case_keys.add(case_key)
            by_reason[reason][subtype][filler_token][side] += 1
            by_reason[reason][subtype][filler_token]["case_ids"].append(case_key)
            by_run[run_id][side] += 1
            by_reviewer[reviewer][side] += 1
            at = entry.get("decided_at") or entry.get("timestamp") or doc.get("saved_at")
            if at:
                cur = by_reviewer[reviewer]["latest_at"]
                if cur is None or str(at) > str(cur):
                    by_reviewer[reviewer]["latest_at"] = str(at)

    # Compute rollups at three levels: token, subtype, reason
    reason_out: dict[str, Any] = {}
    for reason, subs in by_reason.items():
        reason_out[reason] = {"_subtypes": {}}
        r_acc = r_rej = 0
        for sub, tokens in subs.items():
            reason_out[reason]["_subtypes"][sub] = {"_tokens": {}}
            s_acc = s_rej = 0
            for tok, data in tokens.items():
                a, rj = data["accept"], data["reject"]
                tot = a + rj
                reason_out[reason]["_subtypes"][sub]["_tokens"][tok] = {
                    "accept": a, "reject": rj, "total": tot,
                    "accept_rate": round(a / tot, 3) if tot else 0.0,
                    "case_ids": data["case_ids"],
                }
                s_acc += a; s_rej += rj
            s_tot = s_acc + s_rej
            reason_out[reason]["_subtypes"][sub]["accept"] = s_acc
            reason_out[reason]["_subtypes"][sub]["reject"] = s_rej
            reason_out[reason]["_subtypes"][sub]["total"] = s_tot
            reason_out[reason]["_subtypes"][sub]["accept_rate"] = round(s_acc / s_tot, 3) if s_tot else 0.0
            r_acc += s_acc; r_rej += s_rej
        r_tot = r_acc + r_rej
        reason_out[reason]["accept"] = r_acc
        reason_out[reason]["reject"] = r_rej
        reason_out[reason]["total"] = r_tot
        reason_out[reason]["accept_rate"] = round(r_acc / r_tot, 3) if r_tot else 0.0

    total_accept = sum(v["accept"] for v in by_run.values())
    total_reject = sum(v["reject"] for v in by_run.values())

    return {
        "schema_version": "labels-lake-v2",
        "note": "v2: three-level nesting reason_key → subtype → filler_token. Each level carries its own rollup accept/reject/total/rate; gate consumers can look up at any level.",
        "project_root": str(project_root),
        "excluded_reviewers": sorted(exclude_reviewers),
        "summary": {
            "total_decisions": total_accept + total_reject,
            "total_accept": total_accept,
            "total_reject": total_reject,
            "distinct_runs": len(by_run),
            "distinct_reviewers": len(by_reviewer),
            "hd_files_scanned": len(hd_paths),
        },
        "by_reason_key": reason_out,
        "by_run": dict(by_run),
        "by_reviewer": {k: dict(v) for k, v in by_reviewer.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", type=Path, default=Path("."))
    ap.add_argument(
        "--exclude-reviewers",
        default="AUTOMATED_TEXT_FIRST,LEARNED_FROM_HUMAN_v4",
        help="逗号分隔的 reviewer 排除名单（机器生成的伪 reviewer）",
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    exclude = {r.strip() for r in args.exclude_reviewers.split(",") if r.strip()}
    lake = build(args.project_root.resolve(), exclude_reviewers=exclude)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(lake, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"summary": lake["summary"], "out": str(args.out)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
