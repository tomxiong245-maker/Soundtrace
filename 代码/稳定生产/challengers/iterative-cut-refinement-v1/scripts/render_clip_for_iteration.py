#!/usr/bin/env python3
"""render_clip_for_iteration · 单候选按参数剪一段短 clip · 供 Optuna iteration 用.

**用途**：iteration 每次要用不同 crossfade / pause / boundary_offset 参数剪一次同一个候选 ·
需要一个"per-candidate render" · 与 Champion 主 render (整片) 分离。用 pydub 做。

**输入**：
  --raw-wav      raw source wav 路径
  --start        cut start seconds
  --end          cut end seconds
  --crossfade-ms crossfade 长度
  --pause-ms     剪后停顿（room tone or silence）
  --head-pad-ms  剪前 pad
  --boundary-offset-ms  边界微调 (±30)
  --room-tone-pad-ms  room tone 长度
  --out          输出 wav 路径

**逻辑**：
  1. 从 raw wav 切 [start - context_pre, end + context_post] 拿"剪前后各 1.5s 上下文"
  2. 应用 boundary_offset · 微调 cut_start / cut_end
  3. 分成 pre 段（0 到 cut_start）+ post 段（cut_end 到 end）
  4. pre 尾 crossfade_ms 与 post 头 crossfade_ms overlap · 用 pydub crossfade
  5. 若 room_tone_pad_ms > 0 · 在中间插一段 room tone（从静音段采样）
  6. 落 wav 输出

**依赖**：pydub（已装）· 无 ffmpeg 直接编辑（pydub 会调 ffmpeg for wav io · 用户系统已有）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

CONTEXT_MS = 1500  # 剪前后各留 1.5s 上下文


def render_clip(
    raw_wav: Path,
    start_seconds: float,
    end_seconds: float,
    crossfade_ms: int,
    pause_ms: int = 0,
    head_pad_ms: int = 0,
    boundary_offset_ms: int = 0,
    room_tone_pad_ms: int = 0,
    out_path: Optional[Path] = None,
) -> Path:
    """按参数剪一段 clip · 返回输出 path."""
    from pydub import AudioSegment  # lazy import

    audio = AudioSegment.from_wav(str(raw_wav))
    dur_ms = len(audio)

    # 转 ms · apply boundary_offset
    cut_start_ms = int(start_seconds * 1000) + boundary_offset_ms
    cut_end_ms = int(end_seconds * 1000) + boundary_offset_ms

    # 上下文窗口
    context_start_ms = max(0, cut_start_ms - CONTEXT_MS)
    context_end_ms = min(dur_ms, cut_end_ms + CONTEXT_MS)

    # pre = [context_start, cut_start] · post = [cut_end, context_end]
    pre = audio[context_start_ms:cut_start_ms]
    post = audio[cut_end_ms:context_end_ms]

    # head_pad_ms · pre 头部往前扩展 (从原音频往前取额外 head_pad_ms)
    if head_pad_ms > 0:
        extra_head_start = max(0, context_start_ms - head_pad_ms)
        extra_head = audio[extra_head_start:context_start_ms]
        pre = extra_head + pre

    # crossfade · 必须 crossfade_ms <= min(len(pre), len(post))
    safe_xf = min(int(crossfade_ms), len(pre) - 10, len(post) - 10)
    safe_xf = max(0, safe_xf)

    # pause_ms · 在 pre 和 post 之间插入 pause_ms 静音段
    # 优先用 pre 尾静音重复 (更自然) · fallback 用 pydub silent
    pause_segment = None
    if pause_ms > 0:
        try:
            from pydub.silence import detect_silence
            silences_in_pre = detect_silence(pre, min_silence_len=50, silence_thresh=-40)
            if silences_in_pre:
                pause_source = pre[silences_in_pre[-1][0]:silences_in_pre[-1][1]]
                if len(pause_source) > 0:
                    n_repeat = int(pause_ms / len(pause_source)) + 1
                    pause_segment = (pause_source * n_repeat)[:pause_ms]
        except Exception:
            pass
        if pause_segment is None or len(pause_segment) < pause_ms // 2:
            pause_segment = AudioSegment.silent(duration=pause_ms, frame_rate=pre.frame_rate)

    # room tone (若 pad > 0) · 采样 pre 尾 room_tone_pad_ms 作 room tone
    if room_tone_pad_ms > 0 and len(pre) > room_tone_pad_ms:
        room_tone = pre[-room_tone_pad_ms:]  # 用 pre 尾静音替代 · 简单近似
        if pause_segment is not None:
            # 结构: pre + pause + room_tone + post (crossfade 只在 room_tone 尾 · post 头)
            combined = pre.append(pause_segment, crossfade=0)
            combined = combined.append(room_tone, crossfade=0)
            combined = combined.append(post, crossfade=safe_xf)
        else:
            # 拼: pre + room_tone + post · pre 尾与 room_tone 无 crossfade（同源）
            # room_tone 尾与 post 头 crossfade
            combined = pre.append(room_tone, crossfade=0)
            combined = combined.append(post, crossfade=safe_xf)
    else:
        if pause_segment is not None:
            # 结构: pre + pause + post
            combined = pre.append(pause_segment, crossfade=0)
            combined = combined.append(post, crossfade=safe_xf)
        else:
            combined = pre.append(post, crossfade=safe_xf)

    if out_path is None:
        out_path = Path(f"/tmp/iter_clip_{start_seconds:.2f}_{end_seconds:.2f}_{crossfade_ms}xf.wav")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(out_path), format="wav")
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw-wav", required=True, type=Path)
    ap.add_argument("--start", required=True, type=float)
    ap.add_argument("--end", required=True, type=float)
    ap.add_argument("--crossfade-ms", type=int, default=50)
    ap.add_argument("--pause-ms", type=int, default=0)
    ap.add_argument("--head-pad-ms", type=int, default=0)
    ap.add_argument("--boundary-offset-ms", type=int, default=0)
    ap.add_argument("--room-tone-pad-ms", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    out = render_clip(
        raw_wav=args.raw_wav,
        start_seconds=args.start,
        end_seconds=args.end,
        crossfade_ms=args.crossfade_ms,
        pause_ms=args.pause_ms,
        head_pad_ms=args.head_pad_ms,
        boundary_offset_ms=args.boundary_offset_ms,
        room_tone_pad_ms=args.room_tone_pad_ms,
        out_path=args.out,
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
