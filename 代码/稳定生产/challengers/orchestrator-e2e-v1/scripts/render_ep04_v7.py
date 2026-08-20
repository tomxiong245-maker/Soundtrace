#!/usr/bin/env python3
"""EP04-v7：在 v6 基础上，对'嗯'保留活人感——只剪长/密集，不剪单发短嗯。

反馈来源（艳馨）：楠哥的"嗯"保留一部分作为活人感；只有嗯得比较长或密集时才删。

规则改动：
- '嗯' 单发短（< 0.8s）且非密集（同轨 5s 窗口内 < 3 个）→ 保留
- '嗯' 长（>= 0.8s）或密集（同轨 5s 窗口内 >= 3 个）→ 剪
- 其它强口癖 呃/额/唔/唉/哎/哦/uh/um/er/erm → 单发即剪（不变）
- 弱口癖 → 同 v6
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ep04_v4 as v4
from render_ep04_v5 import long_pause_cuts_tiered


STRONG_FILLERS_ALWAYS = {"呃", "额", "唔", "唉", "哎", "哦",
                          "uh", "um", "er", "erm"}
# "嗯"要保留活人感，只在长/密集时才剪
STRONG_FILLERS_ONLY_LONG_OR_DENSE = {"嗯"}
NG_LONG_THRESHOLD_SECONDS = 0.8
NG_DENSE_WINDOW_SECONDS = 5.0
NG_DENSE_MIN_COUNT = 3

WEAK_FILLERS = {"啊", "那个", "这个", "就是", "然后", "对"}
WEAK_MIN_GAP_SECONDS = 0.1


def _load_activity(activity_dir: Path) -> dict[str, list[dict]]:
    out = {}
    for p in sorted(activity_dir.glob("track_*.classified.json")):
        label = p.stem.replace(".classified", "")
        d = json.loads(p.read_text(encoding="utf-8"))
        out[label] = d.get("words", [])
    return out


def _other_primary_overlap(by_track, exclude, t0, t1):
    for tr, ws in by_track.items():
        if tr == exclude:
            continue
        for w in ws:
            cls = (w.get("activity", {}) or {}).get("classification")
            if cls != "primary":
                continue
            if float(w["end_seconds"]) <= t0 or float(w["start_seconds"]) >= t1:
                continue
            return True
    return False


def _ng_is_dense(all_ng: list[dict], item: dict) -> bool:
    """判断该'嗯'是否在同轨滑窗内密集。"""
    tr = item["track"]
    t = item["start_s"]
    half = NG_DENSE_WINDOW_SECONDS / 2
    cnt = sum(1 for x in all_ng
              if x["track"] == tr and abs(x["start_s"] - t) <= half)
    return cnt >= NG_DENSE_MIN_COUNT


def detect_solo_fillers(activity_dir: Path, sr: int) -> list[dict]:
    by_track = _load_activity(activity_dir)
    # 收集所有非 bleed 的"嗯"，用于密集判定
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
                # 只在长或密集时剪
                dense = _ng_is_dense(
                    all_ng, {"track": tr, "start_s": s})
                if dur < NG_LONG_THRESHOLD_SECONDS and not dense:
                    continue  # 保留（活人感）
                reason = "ng_long" if dur >= NG_LONG_THRESHOLD_SECONDS else "ng_dense"
                kind = "strong_ng_kept_activity"
            elif is_weak:
                prev_gap = w["start_seconds"] - ws[i - 1]["end_seconds"] if i > 0 else 999
                next_gap = ws[i + 1]["start_seconds"] - w["end_seconds"] if i + 1 < len(ws) else 999
                consec = (i + 1 < len(ws)
                          and str(ws[i + 1].get("text", "")).strip() == t)
                if not (prev_gap >= WEAK_MIN_GAP_SECONDS
                        or next_gap >= WEAK_MIN_GAP_SECONDS or consec):
                    continue
                reason = "weak_filler_with_pause"
                kind = "weak"
            else:  # is_other_strong
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


def _lp_and_solo_filler_v7(mix_source, sr, noise_db, min_silence_seconds,
                            trigger_seconds, safety_ms,
                            keep_head_ms, keep_tail_ms):
    lps = long_pause_cuts_tiered(mix_source, sr, noise_db, min_silence_seconds,
                                  trigger_seconds, safety_ms)
    global _ACTIVITY_DIR
    if _ACTIVITY_DIR is None:
        return lps
    solo = detect_solo_fillers(Path(_ACTIVITY_DIR), sr)
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


v4.long_pause_cuts = _lp_and_solo_filler_v7


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--activity-dir", required=True)
    args, remaining = ap.parse_known_args()
    _ACTIVITY_DIR = args.activity_dir
    sys.argv = [sys.argv[0]] + remaining
    sys.exit(v4.main())
