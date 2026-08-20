#!/usr/bin/env python3
"""long-pause-v2 · 用 ffmpeg silencedetect 找真实静音，再校正边界 + 保留呼吸。

流程：
1. 对源音频跑 ffmpeg silencedetect（阈值默认 -35 dBFS，最小时长 0.5s）拿到 silence_intervals；
2. 只对时长 > `trigger_seconds`（默认 1.2s）的静音生成候选；
3. 边界内缩 `safety_ms`（默认 100 ms）避免切进余韵/起音；
4. 剪切策略：**只裁死寂中间部分**，两端各保留 `keep_head_ms`/`keep_tail_ms`（默认 400/400 ms）
   自然呼吸；剪掉的部分用 equal-power crossfade（默认 200 ms）拼合。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def silencedetect(source: Path, noise_db: float = -35.0,
                  min_seconds: float = 0.5) -> list[tuple[float, float]]:
    """跑 ffmpeg silencedetect，返回 [(start, end), ...] 秒。"""
    cmd = ["ffmpeg", "-hide_banner", "-i", str(source),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    log = r.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", log)]
    n = min(len(starts), len(ends))
    return list(zip(starts[:n], ends[:n]))


def make_candidates(silences: list[tuple[float, float]],
                    trigger_seconds: float,
                    safety_ms: int) -> list[dict[str, Any]]:
    out = []
    safety = safety_ms / 1000.0
    for s, e in silences:
        dur = e - s
        if dur < trigger_seconds:
            continue
        s_safe = s + safety
        e_safe = e - safety
        if e_safe <= s_safe:
            continue
        out.append({
            "silence_start": s, "silence_end": e,
            "silence_duration": round(dur, 3),
            "safe_start": s_safe, "safe_end": e_safe,
            "safe_duration": round(e_safe - s_safe, 3),
        })
    return out


def _mp3_seg(src: Path, out: Path, start: float, dur: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}",
         "-i", str(src), "-c:a", "libmp3lame", "-b:a", "192k",
         "-ac", "1", "-ar", "48000", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def render_preview(source: Path, cand: dict[str, Any], out_dir: Path,
                   cid: str, *, context_seconds: float,
                   keep_head_ms: int, keep_tail_ms: int,
                   crossfade_ms: int) -> dict[str, str]:
    """生成 original.mp3 与 proposed-cut.mp3；proposed-cut 用 equal-power crossfade。"""
    s_silence = cand["silence_start"]
    e_silence = cand["silence_end"]
    safe_start = cand["safe_start"]
    safe_end = cand["safe_end"]

    # ---- original：加上下文
    orig_start = max(0.0, s_silence - context_seconds)
    orig_dur = (e_silence - orig_start) + context_seconds
    orig_path = out_dir / f"{cid}.original.mp3"
    _mp3_seg(source, orig_path, orig_start, orig_dur)

    # ---- proposed-cut：pre + fade(head) + fade(tail) + post
    # keep_head：silence 起点 + safety 之后再保留 keep_head_ms 的呼吸；
    # 但我们要"沿着源音频"取一段包括起音的头部，因此：
    #   head_seg = [safe_start, safe_start + keep_head_ms]
    #   tail_seg = [safe_end - keep_tail_ms, safe_end]
    # crossfade 用 acrossfade filter；不预留 keep_body。
    head_ms = keep_head_ms / 1000.0
    tail_ms = keep_tail_ms / 1000.0
    cx = crossfade_ms / 1000.0

    # 前段：orig_start ... safe_start + head_ms
    pre_start = orig_start
    pre_end = safe_start + head_ms
    # 后段：safe_end - tail_ms ... e_silence + context
    post_start = safe_end - tail_ms
    post_end = e_silence + context_seconds

    pre_path = out_dir / f"{cid}._pre.wav"
    post_path = out_dir / f"{cid}._post.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{pre_start:.4f}",
         "-i", str(source), "-t", f"{max(0.05, pre_end - pre_start):.4f}",
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(pre_path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{post_start:.4f}",
         "-i", str(source), "-t", f"{max(0.05, post_end - post_start):.4f}",
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(post_path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    cut_path = out_dir / f"{cid}.proposed-cut.mp3"
    # 用 ffmpeg acrossfade（默认 tri，改 esin/ecos 会 equal-power；这里用 acrossfade curve1=tri curve2=tri 也 OK
    # 用 c1=esin, c2=esin/econvex 等更接近 equal power。以稳妥起见用 acrossfade duration=cx c1=tri c2=tri
    # 但为满足"equal power"，改用 curve=esin
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", str(pre_path), "-i", str(post_path),
         "-filter_complex",
         f"[0:a][1:a]acrossfade=d={cx:.4f}:c1=esin:c2=esin[out]",
         "-map", "[out]",
         "-c:a", "libmp3lame", "-b:a", "192k",
         "-ac", "1", "-ar", "48000", str(cut_path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for p in (pre_path, post_path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    return {"original": orig_path.name,
            "proposed_cut": cut_path.name}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="源音频 WAV/MP3")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--noise-db", type=float, default=-35.0)
    ap.add_argument("--min-silence-seconds", type=float, default=0.5,
                    help="ffmpeg silencedetect 的最短静音时长")
    ap.add_argument("--trigger-seconds", type=float, default=1.2,
                    help="只对静音 > 该值 的段生成候选")
    ap.add_argument("--safety-ms", type=int, default=100,
                    help="边界内缩，避免切进余韵/起音")
    ap.add_argument("--context-seconds", type=float, default=2.0)
    ap.add_argument("--keep-head-ms", type=int, default=400)
    ap.add_argument("--keep-tail-ms", type=int, default=400)
    ap.add_argument("--crossfade-ms", type=int, default=200)
    ap.add_argument("--limit", type=int, default=999)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    (out_dir / "previews").mkdir(parents=True, exist_ok=True)
    source = Path(args.source)
    silences = silencedetect(source, noise_db=args.noise_db,
                             min_seconds=args.min_silence_seconds)
    candidates = make_candidates(silences, args.trigger_seconds, args.safety_ms)
    candidates = candidates[: args.limit]

    packed = []
    for i, c in enumerate(candidates, 1):
        cid = f"LPv2-{i:03d}"
        previews = render_preview(
            source, c, out_dir / "previews", cid,
            context_seconds=args.context_seconds,
            keep_head_ms=args.keep_head_ms,
            keep_tail_ms=args.keep_tail_ms,
            crossfade_ms=args.crossfade_ms,
        )
        packed.append({
            "candidate_id": cid,
            **c,
            "params": {
                "noise_db": args.noise_db,
                "trigger_seconds": args.trigger_seconds,
                "safety_ms": args.safety_ms,
                "keep_head_ms": args.keep_head_ms,
                "keep_tail_ms": args.keep_tail_ms,
                "crossfade_ms": args.crossfade_ms,
                "crossfade_curve": "esin (equal-power)",
            },
            "previews": {k: f"previews/{v}" for k, v in previews.items()},
        })

    (out_dir / "samples.json").write_text(
        json.dumps({"schema_version": "long-pause-samples-v2",
                    "source": str(source),
                    "silences_total": len(silences),
                    "candidates": packed}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({"silences": len(silences), "candidates": len(packed),
                      "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
