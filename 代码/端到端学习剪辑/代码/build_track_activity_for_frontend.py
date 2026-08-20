#!/usr/bin/env python3
"""从两条 *.classified.json 抽出前端所需的**词级**活动分类。

输入：转写与轨道标注/activity-v2-aligned/{female,male}.classified.json
输出：审核前端/track_activity.json

前端在渲染候选卡时，会把该候选窗口内的所有词按分类逐词染色：
  primary   → 正常显示（这轨这时候是主讲）
  bleed     → 灰化/小字（这轨这时候是串音，另一说话人漏进来的）
  ambiguous → 中间态

这样，即便两轨的转写在同一秒都出现"对,然后如果"，
UI 也能明确告诉审核人：一轨是主讲、一轨是串音——
而不是像旧版那样把一整轨藏掉，导致真在讲话的人也消失。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 主要源头：main run 里 06_activity 与 candidates.json 时间线一致，保证词级 s/e 对齐
SRC = REPO / "main/runs/EP03-freshrun-20260810-1730/06_activity"
# fallback：如果没有 main run（学习剪辑的独立跑），退回 activity-v2-aligned
SRC_FALLBACK = REPO / "端到端学习剪辑/转写与轨道标注/activity-v2-aligned"
OUT = REPO / "审核前端/track_activity.json"


def slim_segments(path: Path) -> list[dict]:
    data = json.load(path.open())
    out = []
    for seg in data["segments"]:
        act = seg.get("activity") or {}
        out.append(
            {
                "start": round(seg["start_seconds"], 3),
                "end": round(seg["end_seconds"], 3),
                "cls": act.get("classification", "unknown"),
            }
        )
    return out


def slim_words(path: Path) -> list[dict]:
    data = json.load(path.open())
    out = []
    for w in data["words"]:
        act = w.get("activity") or {}
        out.append(
            {
                "start": round(w["start_seconds"], 3),
                "end": round(w["end_seconds"], 3),
                "cls": act.get("classification", "unknown"),
                "t": w["text"],
            }
        )
    return out


def main() -> None:
    src = SRC if (SRC / "female.classified.json").exists() else SRC_FALLBACK
    payload = {
        "schema_version": 2,
        "source_dir": str(src),
        "tracks": {
            "female": {
                "segments": slim_segments(src / "female.classified.json"),
                "words": slim_words(src / "female.classified.json"),
            },
            "male": {
                "segments": slim_segments(src / "male.classified.json"),
                "words": slim_words(src / "male.classified.json"),
            },
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {}
    for t, side in payload["tracks"].items():
        seg_c = {c: 0 for c in ("primary", "bleed", "ambiguous", "unknown")}
        wrd_c = {c: 0 for c in ("primary", "bleed", "ambiguous", "unknown")}
        for s in side["segments"]:
            seg_c[s["cls"]] = seg_c.get(s["cls"], 0) + 1
        for w in side["words"]:
            wrd_c[w["cls"]] = wrd_c.get(w["cls"], 0) + 1
        counts[t] = {"segments": seg_c, "words": wrd_c}
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
