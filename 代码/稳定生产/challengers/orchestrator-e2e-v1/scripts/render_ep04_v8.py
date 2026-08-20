#!/usr/bin/env python3
"""EP04-v8：修复短剪切的 crossfade 过长导致的 glitch。

关键改动：crossfade 长度按每段剪切时长自适应，crossfade = min(base_ms, cut_ms // 2)
- 短碰麦 (0.14s) → 50-70ms crossfade（v7 是 200ms 导致 glitch）
- 长停顿 (>0.5s) → 仍用 200ms
- 中间的按 cut_ms/2 分配
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ep04_v4 as v4
from render_ep04_v5 import long_pause_cuts_tiered
from render_ep04_v7 import (
    detect_solo_fillers, _lp_and_solo_filler_v7  # noqa
)
import render_ep04_v7 as v7_mod


def apply_sync_cuts_adaptive(x, sr, cuts, out_path, base_crossfade_ms):
    """每段 crossfade 长度按剪切时长自适应，避免短剪切的 glitch。

    crossfade_ms(i) = min(base, cut_i_duration_ms // 2)
    """
    # 计算 keep 段
    keeps = []
    cur = 0
    cut_durations = []
    for s, e in cuts:
        s = max(0, min(x.size, s)); e = max(s, min(x.size, e))
        if s > cur:
            keeps.append((cur, s))
        cut_durations.append((e - s) / sr)
        cur = e
    if cur < x.size:
        keeps.append((cur, x.size))

    # crossfade 长度：每个"接合处"对应一个 cut
    # keeps 数 = len(cut_durations) + 1（首尾各一）；接合处数 = len(keeps) - 1 = len(cut_durations)
    # keep[i] 与 keep[i+1] 之间对应 cut[i]，其 cut_dur = cut_durations[i]
    cx_list = []
    for cd in cut_durations:
        cx_ms = min(base_crossfade_ms, (cd * 1000) / 2)
        cx_ms = max(10, cx_ms)  # 至少 10ms 避免咔嗒
        cx_list.append(int(sr * cx_ms / 1000))

    total = 0
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        tail = None
        for i, (a, b) in enumerate(keeps):
            seg = x[a:b].astype(np.float32, copy=False)
            if i == 0:
                # 首段：末尾留出下一 crossfade 的 fade 大小
                fade = cx_list[0] if len(cx_list) > 0 else 0
                if seg.size <= fade:
                    tail = seg.copy()
                    continue
                _append(w, seg[:-fade] if fade > 0 else seg)
                total += (seg.size - fade) if fade > 0 else seg.size
                tail = seg[-fade:].copy() if fade > 0 else np.zeros(0, dtype=np.float32)
                continue

            # 与 tail crossfade：本次接合处用 cx_list[i-1]
            fade = cx_list[i - 1] if i - 1 < len(cx_list) else 0
            f = min(fade, tail.size if tail is not None else 0, seg.size)
            if f > 0:
                t = np.linspace(0, np.pi / 2, f, dtype=np.float32)
                r_out = np.cos(t); r_in = np.sin(t)
                head = tail[-f:] * r_out + seg[:f] * r_in
                pre = tail[:-f] if tail.size > f else np.zeros(0, dtype=np.float32)
                if pre.size:
                    _append(w, pre); total += pre.size
                _append(w, head); total += head.size
                mid = seg[f:]
            else:
                if tail is not None and tail.size:
                    _append(w, tail); total += tail.size
                mid = seg

            # 为下一 crossfade 留 tail
            next_fade = cx_list[i] if i < len(cx_list) else 0
            if mid.size <= next_fade:
                tail = mid.copy()
            else:
                _append(w, mid[:-next_fade] if next_fade > 0 else mid)
                total += (mid.size - next_fade) if next_fade > 0 else mid.size
                tail = mid[-next_fade:].copy() if next_fade > 0 else np.zeros(0, dtype=np.float32)

        if tail is not None and tail.size:
            _append(w, tail); total += tail.size
    return total


def _append(w, x):
    xi = np.clip(x, -1.0, 1.0)
    xi = (xi * 32767.0).astype("<i2")
    w.writeframes(xi.tobytes())


# monkey-patch v4.apply_sync_cuts_ep
v4.apply_sync_cuts_ep = apply_sync_cuts_adaptive
# 沿用 v7 的 long_pause_cuts 定制（含 solo filler）
v4.long_pause_cuts = v7_mod._lp_and_solo_filler_v7


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--activity-dir", required=True)
    args, remaining = ap.parse_known_args()
    v7_mod._ACTIVITY_DIR = args.activity_dir
    sys.argv = [sys.argv[0]] + remaining
    sys.exit(v4.main())
