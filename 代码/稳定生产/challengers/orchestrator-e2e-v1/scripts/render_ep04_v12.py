#!/usr/bin/env python3
"""EP04-v12：熊镇正报告三点修复

1) solo_filler dur ≥ 0.1s 过滤（避免 ASR 时间戳粘连出的伪触发）
2) 最终 master 走 loudnorm 两阶段：-16 LUFS / -1 dBTP / LRA 11
3) CUT_DETAILS 类型标签在生成脚本里正确拆分（此文件负责渲染，md 生成在另一脚本）
"""

from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ep04_v4 as v4
from render_ep04_v5 import long_pause_cuts_tiered
from render_ep04_v11 import detect_solo_fillers_v11
from render_ep04_v8 import apply_sync_cuts_adaptive


SOLO_MIN_DURATION_SECONDS = 0.10  # v12: ASR 时间戳粘连过滤


_ACTIVITY_DIR = None


def _lp_and_solo_v12(mix_source, sr, noise_db, min_silence_seconds,
                     trigger_seconds, safety_ms, keep_head_ms, keep_tail_ms):
    lps = long_pause_cuts_tiered(mix_source, sr, noise_db, min_silence_seconds,
                                  trigger_seconds, safety_ms)
    if _ACTIVITY_DIR is None:
        return lps
    for s in detect_solo_fillers_v11(Path(_ACTIVITY_DIR), sr):
        dur = s["end_seconds"] - s["start_seconds"]
        if dur < SOLO_MIN_DURATION_SECONDS:
            continue  # v12: 短于 100ms 的 word 是 ASR 粘连，不剪
        lps.append({
            "silence_start_seconds": s["start_seconds"],
            "silence_end_seconds": s["end_seconds"],
            "silence_duration_seconds": round(dur, 3),
            "cut_start_sample": s["start_sample"],
            "cut_end_sample": s["end_sample"],
            "cut_duration_seconds": round(dur, 3),
            "keep_head_ms": 0, "keep_tail_ms": 0,
            "solo_filler": True, "solo_text": s["text"],
            "solo_kind": s["kind"], "track_id": s["track_id"],
            "reason": s["reason"],
        })
    return lps


v4.apply_sync_cuts_ep = apply_sync_cuts_adaptive
v4.long_pause_cuts = _lp_and_solo_v12


def loudnorm_two_pass(src_wav: Path, out_wav: Path,
                     target_i: float = -16.0,
                     target_tp: float = -1.0,
                     target_lra: float = 11.0) -> dict:
    """ITU-R BS.1770 loudnorm 两阶段归一化，返回 measured 数据。"""
    # Pass 1: measure
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(src_wav),
         "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True)
    log = r.stderr
    # 提取 JSON（在 stderr 末尾）
    m = re.search(r"\{[^{}]*?\"input_i\".*?\}", log, re.DOTALL)
    if not m:
        raise RuntimeError(f"loudnorm pass1 failed: {log[-500:]}")
    measured = json.loads(m.group(0))
    print("loudnorm pass1 measured:", json.dumps(measured, indent=2))
    # Pass 2: apply
    filt = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true:print_format=summary"
    )
    r2 = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src_wav),
         "-af", filt,
         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
         str(out_wav)],
        capture_output=True, text=True)
    if r2.returncode != 0:
        raise RuntimeError(f"loudnorm pass2 failed: {r2.stderr[-500:]}")
    return {
        "target": {"I": target_i, "TP": target_tp, "LRA": target_lra},
        "measured_pass1": measured,
        "pass2_stderr_tail": r2.stderr[-500:],
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--activity-dir", required=True)
    ap.add_argument("--target-lufs", type=float, default=-16.0)
    ap.add_argument("--target-tp", type=float, default=-1.0)
    ap.add_argument("--target-lra", type=float, default=11.0)
    args, remaining = ap.parse_known_args()
    _ACTIVITY_DIR = args.activity_dir

    sys.argv = [sys.argv[0]] + remaining
    rc = v4.main()
    if rc != 0:
        sys.exit(rc)

    # 从 --out-dir 找到 master.wav，做 loudnorm two-pass
    # 简单方式：从 remaining 里解析
    out_dir = None
    for i, a in enumerate(remaining):
        if a == "--out-dir" and i + 1 < len(remaining):
            out_dir = Path(remaining[i + 1])
            break
    if not out_dir:
        print("no --out-dir found; skip loudnorm")
        sys.exit(0)
    # v4.main 输出 EP04-v4.master.wav
    src = out_dir / "EP04-v4.master.wav"
    if not src.exists():
        print(f"master.wav not found at {src}")
        sys.exit(0)
    dst = out_dir / "EP04-v12.master.wav"
    info = loudnorm_two_pass(src, dst, args.target_lufs, args.target_tp, args.target_lra)
    (out_dir / "loudnorm_report.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成 mp3
    mp3 = out_dir / "EP04-v12.master.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(dst),
         "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\nv12 master with LUFS normalization:")
    print(f"  wav: {dst}")
    print(f"  mp3: {mp3}")
