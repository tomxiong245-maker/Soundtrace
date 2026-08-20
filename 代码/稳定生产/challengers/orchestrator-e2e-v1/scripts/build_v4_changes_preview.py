#!/usr/bin/env python3
"""生成 EP04-v4 关键改变对比片段。

- 从 EDL 里挑代表性剪切；
- 每条对比：
  * original = EP04 原始混音在剪切位置 ± context 秒
  * edited   = EP04-v4 speech-only 在对应"剪后位置" ± context 秒
- 静态 HTML 双击浏览器可直接播放（file:// 相对路径）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _mp3(src: Path, out: Path, start: float, dur: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}",
         "-i", str(src), "-c:a", "libmp3lame", "-b:a", "128k",
         "-ac", "1", "-ar", "48000", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def hms(s: float) -> str:
    m, ss = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{ss:05.2f}" if h else f"{int(m):02d}:{ss:05.2f}"


def build_edited_offset_map(cuts_sorted: list[tuple[int, int]], sr: int) -> list[tuple[int, int]]:
    """返回 (original_start_sample, edited_start_sample) 累加映射点。"""
    out = [(0, 0)]
    removed = 0
    for s, e in cuts_sorted:
        # 到 s 之前 removed 不变
        # 从 s 开始 removed += (e-s)
        out.append((s, s - removed))
        removed += (e - s)
        out.append((e, e - removed))
    return out


def original_to_edited_seconds(orig_s: float, cuts_sorted: list[tuple[int, int]],
                               sr: int) -> float:
    removed = 0
    orig_sample = int(round(orig_s * sr))
    for s, e in cuts_sorted:
        if orig_sample >= e:
            removed += (e - s)
        elif orig_sample >= s:
            # 落在剪切区间内 → 映射到区间起点（剪切后是那个 crossfade 中点附近）
            removed += (orig_sample - s)
            break
        else:
            break
    return (orig_sample - removed) / sr


def pick_representatives(edl: dict, sr: int) -> list[dict]:
    """挑代表性剪切：长停顿覆盖 4 档 + 串音 high/medium 各 2 + 瞬态 2。"""
    cuts = edl["cuts"]

    # 分类
    long_pause = [c for c in cuts if c["source"] == "LONG_PAUSE_v4"]
    transient = [c for c in cuts if c["source"] == "LEARNED_v4_transient"]
    crosstalk = [c for c in cuts if c["source"] == "LEARNED_v4_crosstalk"]
    filler = [c for c in cuts if c["source"] == "真人-filler"]

    picks = []
    tags = []

    # 长停顿分档挑：>3s / 2-3 / 1-2 / 0.6-1，各 1-2 条
    for lo, hi, label, count in [(3.0, 99, "长停顿·极长(>3s)", 1),
                                    (2.0, 3.0, "长停顿·长(2-3s)", 1),
                                    (1.0, 2.0, "长停顿·中(1-2s)", 2),
                                    (0.6, 1.0, "长停顿·短(0.6-1s)", 2)]:
        band = [c for c in long_pause
                if lo <= c.get("silence_seconds", 0) < hi]
        if band:
            step = max(1, len(band) // count) if count else 1
            for i in range(0, min(count, len(band))):
                picks.append(band[i * step])
                tags.append(label)

    # 串音 high 2 条，medium 2 条
    high = [c for c in crosstalk if c.get("confidence") == "high"]
    medium = [c for c in crosstalk if c.get("confidence") == "medium"]
    for c in high[: max(1, len(high) // 8)][:2]:
        picks.append(c); tags.append("串音·高置信 gate（v3 也有）")
    for c in medium[:2]:
        picks.append(c); tags.append("串音·中置信 gate（v4 新增）")

    # 瞬态 2 条：碰麦 + 咳嗽各 1
    mic = [c for c in transient if c.get("reason_key") == "mic_bump_like"]
    cough = [c for c in transient if c.get("reason_key") == "cough_like"]
    if mic:
        picks.append(mic[0]); tags.append("瞬态·碰麦")
    if cough:
        picks.append(cough[0]); tags.append("瞬态·咳嗽")

    # 真人 filler 1 条
    if filler:
        picks.append(filler[0]); tags.append("口癖·真人 accept（EP04-v2 已有）")

    out = []
    for c, tag in zip(picks, tags):
        c2 = dict(c); c2["_tag"] = tag
        out.append(c2)
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", required=True)
    ap.add_argument("--original-mix", required=True,
                    help="EP04 原始三轨平均混音 WAV（未剪切）")
    ap.add_argument("--speech-only-wav", required=True,
                    help="EP04-v4 speech-only（剪后但没加音乐的）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--context-seconds", type=float, default=6.0)
    args = ap.parse_args()

    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))
    sr = edl["sample_rate_hz"]
    sync_cuts_sorted = sorted([(c["start_sample"], c["end_sample"])
                               for c in edl["sync_cuts_merged"]])

    picks = pick_representatives(edl, sr)
    out_dir = Path(args.out_dir)
    (out_dir / "previews").mkdir(parents=True, exist_ok=True)

    entries = []
    for i, c in enumerate(picks, 1):
        cid = f"K{i:02d}-{c['candidate_id']}"
        orig_s = c["start_sample"] / sr
        orig_e = c["end_sample"] / sr

        # original：orig_s - context ~ orig_e + context
        o_start = max(0.0, orig_s - args.context_seconds)
        o_dur = (orig_e - o_start) + args.context_seconds
        _mp3(Path(args.original_mix),
             out_dir / "previews" / f"{cid}.original.mp3",
             o_start, o_dur)

        # edited：映射到剪后时间，取 ±context
        # 剪后 anchor 位置 = original_to_edited(orig_s)（对 gate 类不算剪，用 orig_s 即可）
        is_gate = c.get("action") == "gate"
        if is_gate:
            e_anchor = orig_s
        else:
            e_anchor = original_to_edited_seconds(orig_s, sync_cuts_sorted, sr)
        e_start = max(0.0, e_anchor - args.context_seconds)
        e_dur = args.context_seconds * 2  # 前 context + 后 context
        _mp3(Path(args.speech_only_wav),
             out_dir / "previews" / f"{cid}.edited.mp3",
             e_start, e_dur)

        entries.append({
            "cid": cid,
            "tag": c["_tag"],
            "orig_start_seconds": round(orig_s, 3),
            "orig_end_seconds": round(orig_e, 3),
            "orig_duration_seconds": round(orig_e - orig_s, 3),
            "orig_position": hms(orig_s),
            "action": c.get("action"),
            "confidence": c.get("confidence"),
            "reason_key": c.get("reason_key"),
            "silence_seconds": c.get("silence_seconds"),
            "cut_seconds": c.get("cut_seconds"),
            "track_id": c.get("track_id"),
            "previews": {
                "original": f"previews/{cid}.original.mp3",
                "edited": f"previews/{cid}.edited.mp3",
            }
        })

    (out_dir / "changes.json").write_text(
        json.dumps({"schema_version": "v4-changes-v1",
                    "context_seconds": args.context_seconds,
                    "entries": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({"picks": len(entries), "out_dir": str(out_dir)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
