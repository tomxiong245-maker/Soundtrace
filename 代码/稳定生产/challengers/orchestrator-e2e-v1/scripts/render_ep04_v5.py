#!/usr/bin/env python3
"""EP04-v5：分档 keep，每段长停顿都听得出剪切。"""

from __future__ import annotations

import sys
from pathlib import Path

# 复用 v4 的所有函数
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ep04_v4 as v4


def long_pause_cuts_tiered(mix_source, sr, noise_db, min_silence_seconds,
                            trigger_seconds, safety_ms):
    """分档 keep：短停顿保留少、长停顿保留多。"""
    sils = v4.silencedetect(mix_source, noise_db, min_silence_seconds)
    safety = safety_ms / 1000.0
    out = []
    for s, e in sils:
        dur = e - s
        if dur < trigger_seconds:
            continue
        # 分档 keep
        if dur >= 3.0:
            kh = kt = 0.400
        elif dur >= 2.0:
            kh = kt = 0.300
        elif dur >= 1.0:
            kh = kt = 0.200
        else:
            kh = kt = 0.100
        cs = s + safety + kh
        ce = e - safety - kt
        if ce - cs < 0.15:
            continue
        out.append({
            "silence_start_seconds": round(s, 3),
            "silence_end_seconds": round(e, 3),
            "silence_duration_seconds": round(dur, 3),
            "cut_start_sample": int(round(cs * sr)),
            "cut_end_sample": int(round(ce * sr)),
            "cut_duration_seconds": round(ce - cs, 3),
            "keep_head_ms": int(kh * 1000),
            "keep_tail_ms": int(kt * 1000),
        })
    return out


# monkeypatch v4 的长停顿函数
v4.long_pause_cuts = lambda mix_source, sr, noise_db, min_silence_seconds, \
    trigger_seconds, safety_ms, keep_head_ms, keep_tail_ms: \
    long_pause_cuts_tiered(mix_source, sr, noise_db, min_silence_seconds,
                            trigger_seconds, safety_ms)


if __name__ == "__main__":
    sys.exit(v4.main())
