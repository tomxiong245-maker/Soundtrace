#!/usr/bin/env python3
"""EP04-v11：在 v10 基础上加三条保护规则解决"剪多"：
 1) 弱口癖时长 > 0.4s → reject（长的多半承担语气）
 2) 密集抑制：同一 track 上 5s 滑窗内至多剪 2 个单发弱口癖
 3) 相邻同字合并：紧邻同一弱口癖（gap < 0.3s）保留最后一个（承接语气）
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ep04_v4 as v4
from render_ep04_v5 import long_pause_cuts_tiered
from render_ep04_v7 import (
    STRONG_FILLERS_ONLY_LONG_OR_DENSE,
    NG_LONG_THRESHOLD_SECONDS, NG_DENSE_WINDOW_SECONDS, NG_DENSE_MIN_COUNT,
    _load_activity, _other_primary_overlap, _ng_is_dense,
)
from render_ep04_v8 import apply_sync_cuts_adaptive
from render_ep04_v10 import STRONG_FILLERS_ALWAYS_V10, WEAK_FILLERS_V10


WEAK_MAX_DURATION_SECONDS = 0.4  # v11 新增：长于此的弱口癖视为语气承担
WEAK_DENSE_WINDOW_SECONDS = 5.0
WEAK_DENSE_MAX_PER_WINDOW = 2
WEAK_ADJACENT_MERGE_GAP_SECONDS = 0.3


def detect_solo_fillers_v11(activity_dir: Path, sr: int) -> list[dict]:
    by_track = _load_activity(activity_dir)
    # "嗯"密集判定所用集合
    all_ng = []
    for tr, ws in by_track.items():
        for w in ws:
            if str(w.get("text", "")).strip() != "嗯": continue
            if (w.get("activity", {}) or {}).get("classification") == "bleed": continue
            all_ng.append({"track": tr, "start_s": float(w["start_seconds"])})

    # 先收集候选，再做密集抑制与相邻合并
    raw = []
    for tr, ws in by_track.items():
        for i, w in enumerate(ws):
            t = str(w.get("text", "")).strip()
            cls = (w.get("activity", {}) or {}).get("classification", "unknown")
            if cls == "bleed": continue
            is_ng = t in STRONG_FILLERS_ONLY_LONG_OR_DENSE
            is_other_strong = t in STRONG_FILLERS_ALWAYS_V10
            is_weak = t in WEAK_FILLERS_V10
            if not (is_ng or is_other_strong or is_weak): continue
            s = float(w["start_seconds"]); e = float(w["end_seconds"])
            dur = e - s
            if is_ng:
                dense = _ng_is_dense(all_ng, {"track": tr, "start_s": s})
                if dur < NG_LONG_THRESHOLD_SECONDS and not dense: continue
                kind = "ng"
            elif is_weak:
                # v11 新规则 A：时长 > 0.4s → reject（可能承担语气）
                if dur > WEAK_MAX_DURATION_SECONDS: continue
                kind = "weak"
            else:
                kind = "strong"
            if _other_primary_overlap(by_track, tr, s, e): continue
            raw.append({
                "track_id": tr, "text": t, "kind": kind, "duration": dur,
                "start_seconds": s, "end_seconds": e,
                "start_sample": int(round(s * sr)),
                "end_sample": int(round(e * sr)),
            })

    # v11 新规则 B: 相邻同字合并（同 track、同文字、gap < 0.3s → 只保留最后一个）
    raw.sort(key=lambda c: (c["track_id"], c["start_seconds"]))
    kept_after_adjacent = []
    i = 0
    while i < len(raw):
        j = i
        # 找出与 raw[i] 连着的同字块
        while (j + 1 < len(raw)
               and raw[j + 1]["track_id"] == raw[i]["track_id"]
               and raw[j + 1]["text"] == raw[i]["text"]
               and raw[j + 1]["start_seconds"] - raw[j]["end_seconds"] < WEAK_ADJACENT_MERGE_GAP_SECONDS):
            j += 1
        if j > i and raw[i]["kind"] == "weak":
            # 相邻 >=2 个同字弱口癖：保留最后一个（第 j 个）作为承接，剪掉 i..j-1
            for k in range(i, j):
                kept_after_adjacent.append(raw[k])
            # 最后一个（raw[j]）不剪
        else:
            for k in range(i, j + 1):
                kept_after_adjacent.append(raw[k])
        i = j + 1

    # v11 新规则 C: 密集抑制（同 track 上 5s 滑窗内最多 2 个弱口癖，超过 reject）
    kept_after_adjacent.sort(key=lambda c: (c["track_id"], c["start_seconds"]))
    result = []
    per_track_kept: dict[str, list[float]] = {}
    for c in kept_after_adjacent:
        if c["kind"] != "weak":
            result.append(c); continue
        tr = c["track_id"]; s = c["start_seconds"]
        recent = per_track_kept.setdefault(tr, [])
        # 保留 5s 内
        recent[:] = [t for t in recent if s - t <= WEAK_DENSE_WINDOW_SECONDS]
        if len(recent) >= WEAK_DENSE_MAX_PER_WINDOW:
            continue  # 密集抑制
        recent.append(s)
        result.append(c)

    # 转成 output 格式
    out = []
    for c in result:
        out.append({
            "track_id": c["track_id"], "text": c["text"],
            "kind": c["kind"], "reason": f"solo_{c['kind']}:{c['text']}",
            "start_sample": c["start_sample"],
            "end_sample": c["end_sample"],
            "start_seconds": c["start_seconds"],
            "end_seconds": c["end_seconds"],
        })
    return out


_ACTIVITY_DIR = None


def _lp_and_solo_v11(mix_source, sr, noise_db, min_silence_seconds,
                     trigger_seconds, safety_ms, keep_head_ms, keep_tail_ms):
    lps = long_pause_cuts_tiered(mix_source, sr, noise_db, min_silence_seconds,
                                  trigger_seconds, safety_ms)
    if _ACTIVITY_DIR is None: return lps
    for s in detect_solo_fillers_v11(Path(_ACTIVITY_DIR), sr):
        lps.append({
            "silence_start_seconds": s["start_seconds"],
            "silence_end_seconds": s["end_seconds"],
            "silence_duration_seconds": round(s["end_seconds"] - s["start_seconds"], 3),
            "cut_start_sample": s["start_sample"],
            "cut_end_sample": s["end_sample"],
            "cut_duration_seconds": round(s["end_seconds"] - s["start_seconds"], 3),
            "keep_head_ms": 0, "keep_tail_ms": 0,
            "solo_filler": True, "solo_text": s["text"],
            "solo_kind": s["kind"], "track_id": s["track_id"],
            "reason": s["reason"],
        })
    return lps


v4.apply_sync_cuts_ep = apply_sync_cuts_adaptive
v4.long_pause_cuts = _lp_and_solo_v11


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--activity-dir", required=True)
    args, remaining = ap.parse_known_args()
    _ACTIVITY_DIR = args.activity_dir
    sys.argv = [sys.argv[0]] + remaining
    sys.exit(v4.main())
