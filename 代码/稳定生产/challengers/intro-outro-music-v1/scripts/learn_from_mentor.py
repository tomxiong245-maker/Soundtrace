#!/usr/bin/env python3
"""intro-outro-music-v1 · 从 Mentor 参考音频自动测出片头片尾参数。

- 输入：intro.mentor.mp3、outro.mentor.mp3（Mentor 已经做好的 30 s / 60 s 参考片段）、片头片尾音乐 raw
- 方法：短窗 RMS 包络 + 简单事件识别
  * intro：从纯音乐开始 → 检测语音起点（RMS 忽降忽升+基线抬升）→ 记 music 与 voice 重叠长度、fade-out 时长
  * outro：从语音结束 → 检测音乐 fade-in 点 → 记 fade-in 长度、结尾静音
- 输出：intro_outro.learned.json（供 Skill/渲染读取）
- 只读，不改任何 Champion；不训练模型。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np


def decode_mp3_to_wav(mp3: Path, wav: Path, sr: int = 48000) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3), "-ac", "1", "-ar", str(sr),
         "-c:a", "pcm_s16le", str(wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise ValueError(f"expect int16 PCM: {path}")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if n_ch > 1:
        x = x.reshape(-1, n_ch).mean(axis=1)
    return x, sr


def envelope_dbfs(x: np.ndarray, sr: int, hop_ms: int = 20,
                  win_ms: int = 50) -> tuple[np.ndarray, int]:
    hop = int(sr * hop_ms / 1000)
    win = int(sr * win_ms / 1000)
    if win < hop:
        win = hop
    n = max(0, (x.size - win) // hop + 1)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, win),
        strides=(x.strides[0] * hop, x.strides[0]),
        writeable=False,
    )
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    dbfs = 20.0 * np.log10(np.maximum(rms, 1e-9))
    return dbfs.astype(np.float32), hop


def analyze_intro(intro: Path, music_raw: Path, work: Path) -> dict:
    """intro：先纯音乐→逐渐淡出+语音进入。检测 voice_start 与 music_end。"""
    wav_intro = work / "intro.mono.wav"
    wav_music = work / "music.mono.wav"
    decode_mp3_to_wav(intro, wav_intro)
    decode_mp3_to_wav(music_raw, wav_music)
    x, sr = read_wav(wav_intro)
    env, hop = envelope_dbfs(x, sr)
    # 找出 first "语音起点"：能量升 + 高频占比升。这里最简：用 env 相对开头基线上升 > 3 dB 且持续 > 0.5 s
    frame_s = hop / sr
    baseline = float(np.median(env[: int(2.0 / frame_s)]))  # 前 2 秒的基线（音乐段）
    voice_start_frame = None
    win_persist = int(0.5 / frame_s)
    for i in range(int(2.0 / frame_s), env.size - win_persist):
        if np.median(env[i:i + win_persist]) - baseline > 3.0:
            voice_start_frame = i
            break
    voice_start_s = voice_start_frame * frame_s if voice_start_frame else None
    # 音乐淡出：voice_start 之后，音乐(可用 raw music 能量)与实际 intro 能量的差异，
    # 估算方式：voice_start 后 1 秒，能量已回落到语音正常电平；观察 voice_start 之前 1 秒内的下降斜率
    fade_out_ms = None
    if voice_start_s is not None:
        pre = env[max(0, voice_start_frame - int(2.0 / frame_s)):voice_start_frame]
        if pre.size > 10:
            # 从最高点到 voice_start_frame 的时间
            top_idx = int(np.argmax(pre))
            fade_out_ms = int((pre.size - top_idx) * frame_s * 1000)
    return {
        "duration_seconds": round(x.size / sr, 3),
        "music_baseline_dbfs": round(baseline, 2),
        "voice_start_seconds": round(voice_start_s, 3) if voice_start_s else None,
        "music_pre_voice_seconds": round(voice_start_s, 3) if voice_start_s else None,
        "music_fade_out_ms_before_voice": fade_out_ms,
    }


def analyze_outro(outro: Path, work: Path) -> dict:
    """outro：语音渐停 → 音乐渐进 → 收尾静音。"""
    wav_out = work / "outro.mono.wav"
    decode_mp3_to_wav(outro, wav_out)
    x, sr = read_wav(wav_out)
    env, hop = envelope_dbfs(x, sr)
    frame_s = hop / sr
    # 末尾静音区（最后 3s 找 rms 最低点作为结尾）
    tail = env[-int(3.0 / frame_s):]
    end_silence_dbfs = float(np.median(tail))
    # music fade-in 起点：从结尾往前找"高于结尾静音 12 dB"的第一个连续 0.5 s
    persist = int(0.5 / frame_s)
    fade_in_start_frame = None
    for i in range(env.size - persist, 0, -1):
        block = env[i:i + persist]
        if np.median(block) - end_silence_dbfs > 12.0:
            fade_in_start_frame = i
        else:
            if fade_in_start_frame is not None:
                break
    fade_in_start_s = None
    if fade_in_start_frame is not None:
        fade_in_start_s = fade_in_start_frame * frame_s
    # voice_end：从结尾往前，找到高于 end_silence + 6 dB 且相对稳定的"语音段"末端
    voice_end_frame = None
    voice_baseline = float(np.median(env[env > (end_silence_dbfs + 6)])) if (env > (end_silence_dbfs + 6)).any() else -30.0
    for i in range(env.size - 1, 0, -1):
        if env[i] > voice_baseline - 3.0:
            voice_end_frame = i
            break
    voice_end_s = voice_end_frame * frame_s if voice_end_frame else None
    return {
        "duration_seconds": round(x.size / sr, 3),
        "voice_baseline_dbfs": round(voice_baseline, 2),
        "voice_end_seconds": round(voice_end_s, 3) if voice_end_s else None,
        "music_fade_in_start_seconds": (
            round(fade_in_start_s, 3) if fade_in_start_s is not None else None),
        "end_silence_dbfs": round(end_silence_dbfs, 2),
        "music_tail_seconds": (
            round(x.size / sr - fade_in_start_s, 3)
            if fade_in_start_s is not None else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intro-mentor", required=True)
    ap.add_argument("--outro-mentor", required=True)
    ap.add_argument("--music-raw", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(exist_ok=True)

    intro = analyze_intro(Path(args.intro_mentor), Path(args.music_raw), work)
    outro = analyze_outro(Path(args.outro_mentor), work)

    doc = {
        "schema_version": "intro-outro-learned-v1",
        "source": {
            "intro_mentor": args.intro_mentor,
            "outro_mentor": args.outro_mentor,
            "music_raw": args.music_raw,
        },
        "intro": intro,
        "outro": outro,
        "derived_rules": {
            "intro_music_before_voice_seconds": intro.get("music_pre_voice_seconds"),
            "intro_music_fade_out_ms": intro.get("music_fade_out_ms_before_voice"),
            "outro_music_fade_in_offset_before_end_seconds": (
                (outro["duration_seconds"] - outro["music_fade_in_start_seconds"])
                if outro.get("music_fade_in_start_seconds") is not None else None),
            "outro_music_tail_seconds": outro.get("music_tail_seconds"),
        },
        "notes": [
            "参数是从 Mentor 30s intro / 60s outro 参考片段用能量包络自动测得，作为 Skill 起点值；",
            "非模型训练；如需精调请人工听感复核后写入 rules。",
        ],
    }
    out_path = out_dir / "intro_outro.learned.json"
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
