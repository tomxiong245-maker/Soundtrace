#!/usr/bin/env python3
"""check_silence_location · 用 pydub.silence.detect_silence 判 cut 是否落静音段内.

**开源工具**：pydub.silence.detect_silence (装在 miniforge3 · 已 audit)
**规则**：
  - 抽取候选 ±1s 窗口的 wav 段（避免加载 55min 整片）
  - 用 pydub 检测静音区间
  - 判 cut_start / cut_end 是否都落在同一静音区间内
  - 若是 → butt splice 可行 · 若否 → 需要 crossfade

**输入**：candidate (start_seconds/end_seconds/source_track_id)
       + raw WAV 路径 (3 轨 · 按 source_track_id 选)
**输出**：{silence_intervals_nearby, cut_fully_in_silence, cut_spans_boundary, silence_thresh_db, min_silence_len_ms}

**默认参数**（EP04 实测调过）:
  - silence_thresh_db = -40 (环境底噪一般 -45 以下 · 阈值 -40 保守)
  - min_silence_len_ms = 100 (100ms 以上视为静音段 · 不管 breath 类)
  - context_window_s = 1.5 (抽 cut 前后 1.5s 分析)

不改 EDL / 不改 audio / 只输出 verdict.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_SILENCE_THRESH_DB = -40.0
DEFAULT_MIN_SILENCE_LEN_MS = 100
DEFAULT_CONTEXT_S = 1.5


def extract_wav_window(raw_wav: Path, start_s: float, end_s: float,
                       ffmpeg: str = "/opt/homebrew/bin/ffmpeg") -> Path:
    """用 ffmpeg 抽 [start_s, end_s] 段 · 16kHz mono · 供 pydub 消费."""
    tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run([
        ffmpeg, "-v", "error", "-ss", f"{start_s}", "-to", f"{end_s}",
        "-i", str(raw_wav), "-ar", "16000", "-ac", "1", "-f", "wav", str(tmp), "-y"
    ], check=True)
    return tmp


def check(candidate: dict, raw_wav: Path,
          silence_thresh_db: float = DEFAULT_SILENCE_THRESH_DB,
          min_silence_len_ms: int = DEFAULT_MIN_SILENCE_LEN_MS,
          context_s: float = DEFAULT_CONTEXT_S,
          ffmpeg: str = "/opt/homebrew/bin/ffmpeg") -> dict:
    from pydub import AudioSegment
    from pydub.silence import detect_silence

    cut_start_s = float(candidate.get("start_seconds") or 0)
    cut_end_s = float(candidate.get("end_seconds") or cut_start_s)
    window_start = max(0.0, cut_start_s - context_s)
    window_end = cut_end_s + context_s

    wav_path = extract_wav_window(raw_wav, window_start, window_end, ffmpeg=ffmpeg)
    try:
        seg = AudioSegment.from_wav(str(wav_path))
        # detect_silence returns [(start_ms, end_ms), ...] relative to seg start
        silences_rel = detect_silence(
            seg,
            min_silence_len=min_silence_len_ms,
            silence_thresh=silence_thresh_db,
        )
    finally:
        try:
            wav_path.unlink()
        except Exception:
            pass

    # 相对时间 → 绝对时间
    silences_abs = [
        (window_start + s / 1000, window_start + e / 1000)
        for s, e in silences_rel
    ]

    cut_fully_in_silence = any(
        s <= cut_start_s + 0.005 and cut_end_s - 0.005 <= e
        for s, e in silences_abs
    )
    # cut 部分与静音段重叠但不完全在内
    cut_partial_overlap = any(
        max(s, cut_start_s) < min(e, cut_end_s)
        for s, e in silences_abs
    ) and not cut_fully_in_silence

    if cut_fully_in_silence:
        verdict = "CUT_IN_SILENCE_BUTT_SPLICE_OK"
        reason = "剪口完全落在静音段内 · butt splice (crossfade=0) 可用 · 无 ghost 风险"
    elif cut_partial_overlap:
        verdict = "CUT_SPANS_BOUNDARY_NEEDS_CROSSFADE"
        reason = "剪口跨越静音-内容边界 · 需要 crossfade 平滑过渡 · 但避免 200ms 以上（内容尾巴会糊过来）"
    else:
        verdict = "CUT_IN_CONTENT_ZONE"
        reason = "剪口完全在内容段（无静音支撑）· 必须 crossfade + 有 ghost 风险 · 建议 human_review"

    return {
        "verdict": verdict,
        "reason": reason,
        "cut_start_s": cut_start_s,
        "cut_end_s": cut_end_s,
        "cut_duration_ms": (cut_end_s - cut_start_s) * 1000,
        "cut_fully_in_silence": cut_fully_in_silence,
        "cut_spans_boundary": cut_partial_overlap,
        "silence_intervals_nearby": [
            {"start_s": round(s, 4), "end_s": round(e, 4), "duration_ms": round((e - s) * 1000, 1)}
            for s, e in silences_abs
        ],
        "parameters": {
            "silence_thresh_db": silence_thresh_db,
            "min_silence_len_ms": min_silence_len_ms,
            "context_s": context_s,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate-json", required=True, type=Path)
    ap.add_argument("--raw-wav", required=True, type=Path,
                    help="raw WAV of candidate.source_track_id")
    ap.add_argument("--silence-thresh-db", type=float, default=DEFAULT_SILENCE_THRESH_DB)
    ap.add_argument("--min-silence-len-ms", type=int, default=DEFAULT_MIN_SILENCE_LEN_MS)
    ap.add_argument("--context-s", type=float, default=DEFAULT_CONTEXT_S)
    ap.add_argument("--ffmpeg", default="/opt/homebrew/bin/ffmpeg")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    cd = json.loads(args.candidate_json.read_text(encoding="utf-8"))
    candidates = cd.get("candidates") if isinstance(cd, dict) and "candidates" in cd else [cd]
    results = []
    for c in candidates:
        r = check(c, args.raw_wav,
                  silence_thresh_db=args.silence_thresh_db,
                  min_silence_len_ms=args.min_silence_len_ms,
                  context_s=args.context_s,
                  ffmpeg=args.ffmpeg)
        r["candidate_id"] = c.get("candidate_id")
        results.append(r)

    out = {
        "schema_version": "check-silence-location-v1",
        "raw_wav": str(args.raw_wav),
        "candidate_count": len(candidates),
        "results": results,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
