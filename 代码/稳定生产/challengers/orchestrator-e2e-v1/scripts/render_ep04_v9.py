#!/usr/bin/env python3
"""EP04-v9：弱口癖不再要求前后停顿，全部单发即剪；沿用 v8 自适应 crossfade + v7 "嗯"活人感。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ep04_v4 as v4
from render_ep04_v5 import long_pause_cuts_tiered
from render_ep04_v7 import (
    STRONG_FILLERS_ALWAYS,
    STRONG_FILLERS_ONLY_LONG_OR_DENSE,
    WEAK_FILLERS,
    NG_LONG_THRESHOLD_SECONDS,
    NG_DENSE_WINDOW_SECONDS,
    NG_DENSE_MIN_COUNT,
    _load_activity,
    _other_primary_overlap,
    _ng_is_dense,
)
import render_ep04_v7 as v7_mod
from render_ep04_v8 import apply_sync_cuts_adaptive


def detect_solo_fillers_v9(activity_dir: Path, sr: int) -> list[dict]:
    """v9：弱口癖不看前后停顿，只做跨轨安全 + 分类过滤。"""
    by_track = _load_activity(activity_dir)
    all_ng = []
    for tr, ws in by_track.items():
        for w in ws:
            if str(w.get("text", "")).strip() != "嗯":
                continue
            if (w.get("activity", {}) or {}).get("classification") == "bleed":
                continue
            all_ng.append({"track": tr, "start_s": float(w["start_seconds"])})

    out = []
    for tr, ws in by_track.items():
        for i, w in enumerate(ws):
            t = str(w.get("text", "")).strip()
            cls = (w.get("activity", {}) or {}).get("classification", "unknown")
            if cls == "bleed":
                continue
            is_ng = t in STRONG_FILLERS_ONLY_LONG_OR_DENSE
            is_other_strong = t in STRONG_FILLERS_ALWAYS
            is_weak = t in WEAK_FILLERS
            if not (is_ng or is_other_strong or is_weak):
                continue
            s = float(w["start_seconds"]); e = float(w["end_seconds"])
            dur = e - s
            if is_ng:
                dense = _ng_is_dense(all_ng, {"track": tr, "start_s": s})
                if dur < NG_LONG_THRESHOLD_SECONDS and not dense:
                    continue
                reason = "ng_long" if dur >= NG_LONG_THRESHOLD_SECONDS else "ng_dense"
                kind = "strong_ng_kept_activity"
            elif is_weak:
                # v9：不再要求前后有停顿
                reason = "weak_filler_v9"
                kind = "weak"
            else:
                reason = f"strong_filler:{t}"
                kind = "strong"
            if _other_primary_overlap(by_track, tr, s, e):
                continue
            out.append({
                "track_id": tr, "text": t, "kind": kind, "reason": reason,
                "start_sample": int(round(s * sr)),
                "end_sample": int(round(e * sr)),
                "start_seconds": s, "end_seconds": e,
            })
    return out


_ACTIVITY_DIR = None


def _lp_and_solo_filler_v9(mix_source, sr, noise_db, min_silence_seconds,
                            trigger_seconds, safety_ms,
                            keep_head_ms, keep_tail_ms):
    lps = long_pause_cuts_tiered(mix_source, sr, noise_db, min_silence_seconds,
                                  trigger_seconds, safety_ms)
    global _ACTIVITY_DIR
    if _ACTIVITY_DIR is None:
        return lps
    solo = detect_solo_fillers_v9(Path(_ACTIVITY_DIR), sr)
    for s in solo:
        lps.append({
            "silence_start_seconds": s["start_seconds"],
            "silence_end_seconds": s["end_seconds"],
            "silence_duration_seconds": round(s["end_seconds"] - s["start_seconds"], 3),
            "cut_start_sample": s["start_sample"],
            "cut_end_sample": s["end_sample"],
            "cut_duration_seconds": round(s["end_seconds"] - s["start_seconds"], 3),
            "keep_head_ms": 0, "keep_tail_ms": 0,
            "solo_filler": True,
            "solo_text": s["text"],
            "solo_kind": s["kind"],
            "track_id": s["track_id"],
            "reason": s["reason"],
        })
    return lps


v4.apply_sync_cuts_ep = apply_sync_cuts_adaptive
v4.long_pause_cuts = _lp_and_solo_filler_v9


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--activity-dir", required=True)
    args, remaining = ap.parse_known_args()
    _ACTIVITY_DIR = args.activity_dir
    sys.argv = [sys.argv[0]] + remaining
    sys.exit(v4.main())
