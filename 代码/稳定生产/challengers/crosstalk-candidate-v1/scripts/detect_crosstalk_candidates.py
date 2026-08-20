#!/usr/bin/env python3
"""crosstalk-candidate-v1: 串音候选检测。

只读 `06_activity/*.classified.json` 之类的词级 activity 数据。
只把源轨在此段主要是 bleed、且另一条轨真正在说话（primary）时段
标记为 review-only 候选，建议 gate/duck 源轨，绝不产生全轨同步删除。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _count_in_window(words: list[dict[str, Any]], t0: float, t1: float
                     ) -> dict[str, int]:
    c = {"primary": 0, "bleed": 0, "ambiguous": 0, "unknown": 0, "total": 0}
    for w in words:
        ws = float(w.get("start_seconds", 0.0))
        we = float(w.get("end_seconds", 0.0))
        # 有交集则计入
        if we <= t0 or ws >= t1:
            continue
        c["total"] += 1
        cls = ((w.get("activity") or {}).get("classification") or "unknown")
        if cls not in c:
            c["unknown"] += 1
        else:
            c[cls] += 1
    return c


def _tracks_span(tracks: dict[str, list[dict[str, Any]]]) -> tuple[float, float]:
    lo = float("inf"); hi = 0.0
    for ws in tracks.values():
        for w in ws:
            lo = min(lo, float(w.get("start_seconds", 0.0)))
            hi = max(hi, float(w.get("end_seconds", 0.0)))
    if lo == float("inf"):
        lo = 0.0
    return lo, hi


def detect_crosstalk(tracks: dict[str, list[dict[str, Any]]],
                     rules: dict[str, Any],
                     sample_rate_hz: int = 48000
                     ) -> list[dict[str, Any]]:
    win = float(rules["window_seconds"])
    step = float(rules["step_seconds"])
    min_bleed = int(rules["min_bleed_words_per_window"])
    min_bleed_ratio = float(rules["min_bleed_ratio_per_window"])
    require_other_prim = bool(rules["require_other_primary_in_window"])
    min_other_prim = int(rules["min_other_primary_words_per_window"])
    merge_gap = float(rules["merge_gap_seconds"])
    downgrade = bool(rules["downgrade_when_source_has_primary"])

    lo, hi = _tracks_span(tracks)
    if hi <= lo:
        return []

    raw: dict[str, list[tuple[float, float, dict[str, Any]]]] = {t: [] for t in tracks}
    tids = list(tracks.keys())
    t = lo
    while t < hi:
        t1 = min(hi, t + win)
        for src in tids:
            src_ct = _count_in_window(tracks[src], t, t1)
            if src_ct["bleed"] < min_bleed:
                continue
            b = src_ct["bleed"]
            p = src_ct["primary"]
            denom = max(1, b + p + src_ct["ambiguous"])
            ratio = b / denom
            if ratio < min_bleed_ratio:
                continue
            # 另一轨是否有 primary
            other_prim_total = 0
            dominant_other: str | None = None
            best_prim = 0
            for oth in tids:
                if oth == src:
                    continue
                oc = _count_in_window(tracks[oth], t, t1)
                other_prim_total += oc["primary"]
                if oc["primary"] > best_prim:
                    best_prim = oc["primary"]
                    dominant_other = oth
            if require_other_prim and other_prim_total < min_other_prim:
                continue
            level = "high" if p == 0 else ("medium" if downgrade else "high")
            raw[src].append((t, t1, {
                "bleed_words": b,
                "primary_words_on_source": p,
                "ambiguous_words_on_source": src_ct["ambiguous"],
                "total_words_on_source": src_ct["total"],
                "bleed_ratio": round(ratio, 4),
                "other_primary_words_total": other_prim_total,
                "dominant_other_track_id": dominant_other,
                "confidence": level,
            }))
        t += step

    out: list[dict[str, Any]] = []
    for src, wins in raw.items():
        if not wins:
            continue
        wins.sort(key=lambda x: x[0])
        cur_start, cur_end, cur_meta = wins[0]
        acc = [cur_meta]
        for s, e, m in wins[1:]:
            if s - cur_end <= merge_gap:
                cur_end = max(cur_end, e)
                acc.append(m)
            else:
                out.append(_pack(src, cur_start, cur_end, acc, sample_rate_hz))
                cur_start, cur_end = s, e
                acc = [m]
        out.append(_pack(src, cur_start, cur_end, acc, sample_rate_hz))
    return out


def _pack(src: str, s: float, e: float, acc: list[dict[str, Any]],
          sr: int) -> dict[str, Any]:
    bleed_words = sum(m["bleed_words"] for m in acc)
    other_prim = sum(m["other_primary_words_total"] for m in acc)
    prim_on_src = sum(m["primary_words_on_source"] for m in acc)
    dominant_counts: dict[str, int] = {}
    for m in acc:
        if m["dominant_other_track_id"]:
            dominant_counts[m["dominant_other_track_id"]] = dominant_counts.get(
                m["dominant_other_track_id"], 0) + m["other_primary_words_total"]
    dominant = max(dominant_counts, key=dominant_counts.get) if dominant_counts else None
    confidence = "high" if prim_on_src == 0 else "medium"
    action = "gate_source_track" if prim_on_src == 0 else "duck_source_track"
    return {
        "reason_key": "crosstalk_on_source",
        "track_id": src,
        "source_track_id": src,
        "applies_to_tracks": [src],
        "other_dominant_track_id": dominant,
        "start_seconds": s,
        "end_seconds": e,
        "start_sample": int(round(s * sr)),
        "end_sample": int(round(e * sr)),
        "bleed_words": bleed_words,
        "primary_words_on_source": prim_on_src,
        "other_primary_words_total": other_prim,
        "confidence": confidence,
        "suggested_action": action,
        "windows": acc,
        "policy": "review_only_no_automatic_accept",
    }


def load_classified(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("words", [])


def sha256_of_rules(rules: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", action="append", required=True,
                    help="LABEL=/abs/path.classified.json；可多次")
    ap.add_argument("--rules", required=True)
    ap.add_argument("--sample-rate-hz", type=int, default=48000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    tracks: dict[str, list[dict[str, Any]]] = {}
    for spec in args.transcript:
        label, p = spec.split("=", 1)
        tracks[label] = load_classified(Path(p))
    cands = detect_crosstalk(tracks, rules, args.sample_rate_hz)
    result = {
        "schema_version": "crosstalk-candidate-run-v1",
        "rules_path": args.rules,
        "rules_sha256": sha256_of_rules(rules),
        "sample_rate_hz": args.sample_rate_hz,
        "track_ids": list(tracks.keys()),
        "candidates": cands,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidates": len(cands), "out": args.out},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
