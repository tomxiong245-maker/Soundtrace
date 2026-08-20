#!/usr/bin/env python3
"""EP04-v6：v5 全部 + 单发弱口癖（跨轨安全后）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ep04_v4 as v4
from render_ep04_v5 import long_pause_cuts_tiered


STRONG_FILLERS = {"嗯", "呃", "额", "唔", "唉", "哎", "哦",
                   "uh", "um", "er", "erm"}
WEAK_FILLERS = {"啊", "那个", "这个", "就是", "然后", "对"}
WEAK_MIN_GAP_SECONDS = 0.1


def _load_activity(activity_dir: Path) -> dict[str, list[dict]]:
    out = {}
    for p in sorted(activity_dir.glob("track_*.classified.json")):
        label = p.stem.replace(".classified", "")
        d = json.loads(p.read_text(encoding="utf-8"))
        out[label] = d.get("words", [])
    return out


def _other_primary_overlap(by_track: dict, exclude: str,
                            t0: float, t1: float) -> bool:
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


def detect_solo_fillers(activity_dir: Path, sr: int) -> list[dict]:
    by_track = _load_activity(activity_dir)
    out = []
    for tr, ws in by_track.items():
        for i, w in enumerate(ws):
            t = str(w.get("text", "")).strip()
            cls = (w.get("activity", {}) or {}).get("classification", "unknown")
            if cls == "bleed":
                continue
            is_strong = t in STRONG_FILLERS
            is_weak = t in WEAK_FILLERS
            if not (is_strong or is_weak):
                continue
            if is_weak:
                prev_gap = w["start_seconds"] - ws[i - 1]["end_seconds"] if i > 0 else 999
                next_gap = ws[i + 1]["start_seconds"] - w["end_seconds"] if i + 1 < len(ws) else 999
                consec = (i + 1 < len(ws)
                          and str(ws[i + 1].get("text", "")).strip() == t)
                if not (prev_gap >= WEAK_MIN_GAP_SECONDS
                        or next_gap >= WEAK_MIN_GAP_SECONDS or consec):
                    continue
            s = float(w["start_seconds"]); e = float(w["end_seconds"])
            if _other_primary_overlap(by_track, tr, s, e):
                continue
            out.append({
                "track_id": tr,
                "text": t,
                "kind": "strong" if is_strong else "weak",
                "start_sample": int(round(s * sr)),
                "end_sample": int(round(e * sr)),
                "start_seconds": s,
                "end_seconds": e,
            })
    return out


# 猴补：v5 的分档 keep + v6 的单发口癖
_original_main = v4.main


def _wrap_main() -> int:
    """跑完主流程后，从 EDL 里读单发口癖候选并补进 sync cuts。

    但 v4.main 是一次性完成到写文件，不方便注入。
    改法：直接在 v6 里复制 main() 关键部分 + 加单发口癖。
    这里改为通过 monkey-patch long_pause_cuts 让它返回"长停顿 + 单发口癖"
    的合并列表——两者都是同步剪。
    """
    return _original_main()


# ---- 关键 monkey-patch ----
_ACTIVITY_DIR = None


def _lp_and_solo_filler(mix_source, sr, noise_db, min_silence_seconds,
                        trigger_seconds, safety_ms,
                        keep_head_ms, keep_tail_ms):
    lps = long_pause_cuts_tiered(mix_source, sr, noise_db, min_silence_seconds,
                                  trigger_seconds, safety_ms)
    # 追加单发口癖
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
            "keep_head_ms": 0,
            "keep_tail_ms": 0,
            "solo_filler": True,
            "solo_text": s["text"],
            "solo_kind": s["kind"],
            "track_id": s["track_id"],
        })
    return lps


v4.long_pause_cuts = _lp_and_solo_filler


if __name__ == "__main__":
    import argparse
    # 单独抽出 --activity-dir 参数
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--activity-dir", required=True)
    args, remaining = ap.parse_known_args()
    _ACTIVITY_DIR = args.activity_dir
    sys.argv = [sys.argv[0]] + remaining
    sys.exit(v4.main())
