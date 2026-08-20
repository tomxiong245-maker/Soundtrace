#!/usr/bin/env python3
"""
annotate_shortlist.py

从 56 个候选中挑 P1/P2/P3 三层精审集，给每条打上 tag_priority。
不改现有候选内容，只新增字段。
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def disagreement_long_pause(c: dict, cand_rules: dict) -> float:
    dur = c["duration_seconds"]
    lo = cand_rules["long_pause"]["min_seconds"]
    hi = cand_rules["long_pause"]["max_seconds"]
    mid = (lo + hi) / 2
    dist_to_mid = abs(dur - mid)
    max_dist = mid - lo
    return min(1.0, dist_to_mid / max_dist)


def disagreement_immediate_repetition(c: dict) -> float:
    phrase = c.get("asr_repeated_phrase", "") or ""
    L = len(phrase)
    if L == 0:
        return 0.5
    return max(0.0, (4 - L) / 2)


def disagreement_filler(c: dict, cand_rules: dict) -> float:
    n = len(c.get("asr_evidence_words", []))
    thr = cand_rules["filler_hesitation"]["min_consecutive"]
    if n <= thr:
        return 1.0
    return max(0.0, 1.0 - (n - thr) / 4)


def confidence_long_pause(c: dict, cand_rules: dict) -> float:
    return 1.0 - disagreement_long_pause(c, cand_rules)


def confidence_immediate_repetition(c: dict) -> float:
    phrase = c.get("asr_repeated_phrase", "") or ""
    L = len(phrase)
    return min(1.0, L / 4)


def confidence_filler(c: dict, cand_rules: dict) -> float:
    n = len(c.get("asr_evidence_words", []))
    thr = cand_rules["filler_hesitation"]["min_consecutive"]
    return min(1.0, (n - thr) / 4) if n > thr else 0.0


DISAGREEMENT_FN = {
    "long_pause": disagreement_long_pause,
    "immediate_repetition": lambda c, r: disagreement_immediate_repetition(c),
    "filler_hesitation": disagreement_filler,
}
CONFIDENCE_FN = {
    "long_pause": confidence_long_pause,
    "immediate_repetition": lambda c, r: confidence_immediate_repetition(c),
    "filler_hesitation": confidence_filler,
}


def build_shortlist(candidates, shortlist_rules, cand_rules):
    layer_split = shortlist_rules["layer_split"]
    control_prefer = shortlist_rules["control_group_policy"]["prefer_risk"]

    for c in candidates:
        cat = c["category"]
        if cat in DISAGREEMENT_FN:
            c["_disagreement"] = DISAGREEMENT_FN[cat](c, cand_rules)
            c["_confidence_score"] = CONFIDENCE_FN[cat](c, cand_rules)
        else:
            c["_disagreement"] = 0.5
            c["_confidence_score"] = 0.5
        c["tag_priority"] = None

    for typ, splits in layer_split.items():
        n_edge = splits["edge"]
        n_anchor = splits["anchor"]
        n_ctrl = splits["control"]

        pool_high = [c for c in candidates if c["category"] == typ and c["risk"] == "high"]
        pool_other = [c for c in candidates if c["category"] == typ and c["risk"] != "high"]

        edges = sorted(pool_high, key=lambda c: -c["_disagreement"])[:n_edge]
        used = set(id(c) for c in edges)

        remaining_high = [c for c in pool_high if id(c) not in used]
        anchors = sorted(remaining_high, key=lambda c: -c["_confidence_score"])[:n_anchor]

        ctrls_all = sorted(
            pool_other,
            key=lambda c: (0 if c["risk"] == control_prefer else 1, -c["_disagreement"]),
        )
        ctrls = ctrls_all[:n_ctrl]

        for c in edges:
            c["tag_priority"] = "P1"
        for c in anchors:
            c["tag_priority"] = "P2"
        for c in ctrls:
            c["tag_priority"] = "P3"

    for c in candidates:
        if c["tag_priority"] is None:
            if c["risk"] == "high":
                c["tag_priority"] = "BACKLOG_HIGH"
            else:
                c["tag_priority"] = "BACKLOG_LOW"

    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--shortlist-rules", type=Path, required=True)
    ap.add_argument("--candidate-rules", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    if not args.output.is_absolute():
        ap.error("--output must be absolute")

    data = json.loads(args.candidates.read_text(encoding="utf-8"))
    shortlist_rules = json.loads(args.shortlist_rules.read_text(encoding="utf-8"))
    cand_rules = json.loads(args.candidate_rules.read_text(encoding="utf-8"))

    cands = data.get("candidates", data) if isinstance(data, dict) else data
    build_shortlist(cands, shortlist_rules, cand_rules)

    from collections import Counter
    tag_dist = Counter(c["tag_priority"] for c in cands)
    print(f"分层结果：{dict(tag_dist)}")
    print(f"\nP1 精选 {tag_dist['P1']} 条：")
    for c in cands:
        if c["tag_priority"] == "P1":
            phrase = c.get("asr_repeated_phrase", "") or ""
            print(f"  {c['candidate_id']} [{c['category']:22}] risk={c['risk']:6} "
                  f"dur={c['duration_seconds']:.2f}s "
                  f"disagree={c.get('_disagreement', 0):.2f}"
                  + (f" repeat='{phrase}'" if phrase else ""))

    output = {
        "episode": data.get("episode", "unknown"),
        "count": len(cands),
        "candidates": cands,
        "_shortlist_meta": {
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "shortlist_rules_version": shortlist_rules["rules_version"],
            "shortlist_rules_sha256": sha256_file(args.shortlist_rules),
            "candidate_rules_version": cand_rules.get("rules_version"),
            "candidate_rules_sha256": sha256_file(args.candidate_rules),
            "target_p1_size": shortlist_rules["target_p1_size"],
            "actual_distribution": dict(tag_dist),
        },
        "_meta": data.get("_meta", {}),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
