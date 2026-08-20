#!/usr/bin/env python3
"""EP04 music reference-linear-v1 拼接。

设计（整数 sample，SR=48000）：
  intro:  0..240000            纯音乐（0..5.000 s）
          240000..768000       音乐线性淡出（5.000..16.000 s）
  speech: 240000..147749760    speech-only 直接置于 5.000 s（3073.120 s）
  outro:  146693760..147749760 音乐线性淡入 22 s（3056.120..3078.120 s，与 speech 重叠 22 s）
          147749760..149572608 纯音乐 37.976 s 尾段（3078.120..3116.096 s）
  total:  149572608 samples = 3116.096 s

音乐素材 59.976 s = 2878848 samples；片头用前 768000，片尾用后 22+37.976 = 59.976 s，即全长；
两处相互独立，用同一 mp3 素材。

音乐电平：v12 未记录明确 ducking/EQ，此实现使用 volume=1.0 / ducking=none；作为**试听实验参数**，不是发布规范。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
import wave
from pathlib import Path
import numpy as np

SR = 48000
INTRO_MUSIC_FULL_END = 240000       # 5.000 s
SPEECH_START = 240000
INTRO_MUSIC_FADE_END = 768000       # 16.000 s
SPEECH_SAMPLES = int(round(3073.120 * SR))   # 147509760
SPEECH_END = SPEECH_START + SPEECH_SAMPLES   # 147749760 = 3078.120 s
OUTRO_MUSIC_START = SPEECH_END - int(round(22.0 * SR))   # 146693760 = 3056.120 s
OUTRO_MUSIC_FADE_IN_END = SPEECH_END                     # 147749760 = 3078.120 s
OUTRO_MUSIC_TAIL_SAMPLES = int(round(37.976 * SR))       # 1822848
TOTAL_SAMPLES = SPEECH_END + OUTRO_MUSIC_TAIL_SAMPLES    # 149572608 = 3116.096 s

MUSIC_INTRO_SAMPLES = INTRO_MUSIC_FADE_END        # 768000 samples used from intro
MUSIC_OUTRO_SAMPLES = int(round(22.0 * SR)) + OUTRO_MUSIC_TAIL_SAMPLES  # 1056000 + 1822848 = 2878848

TARGET_LUFS = -16.0
TARGET_TP = -1.0
TARGET_LRA = 11.0


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run(cmd, timeout=180, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd fail rc={r.returncode}: {' '.join(map(str,cmd))[:200]}\n{r.stderr[-800:]}")
    return r


def decode_music_to_mono_wav(mp3: Path, wav_out: Path) -> None:
    """把立体声 mp3 降混到 mono 48kHz 16-bit WAV，样本数 = 59.976s * 48000 = 2878848."""
    run([
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-i", str(mp3),
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_out),
    ], timeout=60)


def read_wav_mono_int16(p: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(p), "rb") as w:
        sr = w.getframerate()
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise RuntimeError(f"expected mono 16-bit; got ch={w.getnchannels()} bd={w.getsampwidth()}")
        n = w.getnframes()
        raw = w.readframes(n)
    return np.frombuffer(raw, dtype="<i2"), sr


def write_wav_mono_int16(x: np.ndarray, sr: int, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.astype("<i2").tobytes())


def loudnorm_two_pass(src: Path, dst: Path,
                      I: float = TARGET_LUFS, TP: float = TARGET_TP, LRA: float = TARGET_LRA) -> dict:
    r = run([
        "ffmpeg", "-hide_banner", "-i", str(src),
        "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json",
        "-f", "null", "-"
    ], timeout=240, check=False)
    m = re.search(r"\{[^{}]*?\"input_i\".*?\}", r.stderr, re.DOTALL)
    if not m:
        raise RuntimeError(f"loudnorm pass1 fail: {r.stderr[-500:]}")
    measured = json.loads(m.group(0))
    filt = (
        f"loudnorm=I={I}:TP={TP}:LRA={LRA}"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true:print_format=summary"
    )
    run([
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-i", str(src), "-af", filt,
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
        str(dst)
    ], timeout=240)
    # pass 2 output measurement
    r3 = run([
        "ffmpeg", "-hide_banner", "-i", str(dst),
        "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json",
        "-f", "null", "-"
    ], timeout=120, check=False)
    m3 = re.search(r"\{[^{}]*?\"input_i\".*?\}", r3.stderr, re.DOTALL)
    output_measure = json.loads(m3.group(0)) if m3 else {}
    return {
        "target": {"I": I, "TP": TP, "LRA": LRA, "linear": True},
        "pass1_measured": measured,
        "pass2_output_measured": output_measure,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--speech", required=True)
    ap.add_argument("--music", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--expected-speech-sha", required=True)
    ap.add_argument("--expected-music-sha", required=True)
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    speech_src = (root / args.speech).resolve()
    music_src = (root / args.music).resolve()
    run_dir = (root / args.run_dir).resolve()
    logs = run_dir / "logs"
    inputs = run_dir / "inputs"
    logs.mkdir(parents=True, exist_ok=True)
    inputs.mkdir(parents=True, exist_ok=True)

    # --- 1. SHA 校验（BLOCKED on mismatch） ---
    speech_sha = sha256_file(speech_src)
    music_sha = sha256_file(music_src)
    if speech_sha != args.expected_speech_sha:
        (run_dir / "BLOCKED_SHA_MISMATCH.txt").write_text(
            f"speech SHA mismatch: expected={args.expected_speech_sha} got={speech_sha}\n"
            "REFUSED to proceed; do not substitute source.\n"
        )
        print(f"BLOCKED: speech SHA mismatch (got {speech_sha})")
        sys.exit(3)
    if music_sha != args.expected_music_sha:
        (run_dir / "BLOCKED_SHA_MISMATCH.txt").write_text(
            f"music SHA mismatch: expected={args.expected_music_sha} got={music_sha}\n"
            "REFUSED to substitute music.\n"
        )
        print(f"BLOCKED: music SHA mismatch (got {music_sha})")
        sys.exit(3)

    # --- 2. 解码 music 到 mono WAV（不改变原素材）---
    music_mono = logs / "music_mono_48k.wav"
    decode_music_to_mono_wav(music_src, music_mono)

    speech_x, sr_s = read_wav_mono_int16(speech_src)
    music_x, sr_m = read_wav_mono_int16(music_mono)
    assert sr_s == SR and sr_m == SR, f"sr mismatch: speech={sr_s} music={sr_m}"
    assert len(speech_x) == SPEECH_SAMPLES, (
        f"speech samples mismatch: expected={SPEECH_SAMPLES} got={len(speech_x)}")
    music_samples_total = len(music_x)
    if music_samples_total < max(MUSIC_INTRO_SAMPLES, MUSIC_OUTRO_SAMPLES):
        print(f"BLOCKED: music too short: {music_samples_total} < required")
        sys.exit(3)

    # --- 3. 拼接（float32 计算，最后 clip 到 int16）---
    total = TOTAL_SAMPLES
    out = np.zeros(total, dtype=np.float32)

    # intro music: 0..768000 (前 5s 全音量 + 5..16s 线性淡出)
    intro_seg = music_x[:MUSIC_INTRO_SAMPLES].astype(np.float32)
    intro_env = np.ones(MUSIC_INTRO_SAMPLES, dtype=np.float32)
    fade_start = INTRO_MUSIC_FULL_END         # 240000
    fade_end = INTRO_MUSIC_FADE_END           # 768000
    fade_len = fade_end - fade_start          # 528000
    fade = np.linspace(1.0, 0.0, fade_len, dtype=np.float32, endpoint=True)
    intro_env[fade_start:fade_end] = fade
    intro_seg = intro_seg * intro_env
    out[0:MUSIC_INTRO_SAMPLES] += intro_seg

    # speech at 240000..147749760
    out[SPEECH_START:SPEECH_END] += speech_x.astype(np.float32)

    # outro music: 从 speech end - 22s 开始，覆盖 22s 淡入 + 37.976s 尾段 = 59.976s = 2878848 samples
    outro_seg = music_x[:MUSIC_OUTRO_SAMPLES].astype(np.float32)
    outro_env = np.ones(MUSIC_OUTRO_SAMPLES, dtype=np.float32)
    # 22s 线性淡入部分
    fade_in_len = OUTRO_MUSIC_FADE_IN_END - OUTRO_MUSIC_START   # 1056000
    fade_in = np.linspace(0.0, 1.0, fade_in_len, dtype=np.float32, endpoint=True)
    outro_env[:fade_in_len] = fade_in
    outro_seg = outro_seg * outro_env
    out[OUTRO_MUSIC_START:OUTRO_MUSIC_START + MUSIC_OUTRO_SAMPLES] += outro_seg

    # 检查是否有溢出
    max_abs = float(np.max(np.abs(out)))
    # 转成 int16。因为源都是 int16，叠加可能超过 32767；用整数域检查，soft clip 不做（保留后期 loudnorm 的裕量）。
    # 采用 headroom：如果超过 32767，先按峰值缩放到 -0.5 dBFS 内，避免整型溢出；loudnorm 再统一响度。
    peak_int = np.max(np.abs(out))
    if peak_int > 32767.0:
        scale = 32767.0 / peak_int
        out = out * scale
        pre_gain_db = 20 * np.log10(scale)
    else:
        pre_gain_db = 0.0
    out_i16 = np.clip(np.rint(out), -32768, 32767).astype(np.int16)

    pre_loudnorm = run_dir / "logs" / "assembly_pre_loudnorm.wav"
    write_wav_mono_int16(out_i16, SR, pre_loudnorm)

    # --- 4. 两阶段 loudnorm → 最终 WAV ---
    final_wav = run_dir / f"{args.run_id}.reference-linear-v1.master.wav"
    ln_info = loudnorm_two_pass(pre_loudnorm, final_wav)

    # --- 5. MP3 192k ---
    final_mp3 = run_dir / f"{args.run_id}.reference-linear-v1.master.mp3"
    run([
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-i", str(final_wav),
        "-c:a", "libmp3lame", "-b:a", "192k", str(final_mp3)
    ], timeout=180)

    # --- 6. 输出 manifest 数据 ---
    music_manifest = {
        "schema_version": "music-manifest-reference-linear-v1",
        "episode_id": "EP04",
        "run_id": args.run_id,
        "run_dir_rel": str(run_dir.relative_to(root)),
        "music_template_id": "reference-linear-v1",
        "delivery_state": "MUSIC_AUDITION_REVIEW_REQUIRED",
        "not_publish_candidate": True,
        "not_human_approved": True,
        "sources": {
            "speech_only_wav_rel": args.speech,
            "speech_only_sha256": speech_sha,
            "speech_duration_seconds": len(speech_x) / SR,
            "speech_samples": int(len(speech_x)),
            "music_mp3_rel": args.music,
            "music_sha256": music_sha,
            "music_duration_seconds": music_samples_total / SR,
            "music_samples": int(music_samples_total),
        },
        "timing_reference_linear_v1": {
            "sample_rate_hz": SR,
            "intro_music_start_sample": 0,
            "intro_music_full_volume_end_sample": INTRO_MUSIC_FULL_END,
            "speech_start_sample": SPEECH_START,
            "intro_music_fade_out_end_sample": INTRO_MUSIC_FADE_END,
            "intro_fade_out_type": "linear",
            "intro_fade_out_duration_samples": INTRO_MUSIC_FADE_END - INTRO_MUSIC_FULL_END,
            "speech_end_sample": SPEECH_END,
            "outro_music_start_sample": OUTRO_MUSIC_START,
            "outro_music_fade_in_end_sample": OUTRO_MUSIC_FADE_IN_END,
            "outro_fade_in_type": "linear",
            "outro_fade_in_duration_samples": OUTRO_MUSIC_FADE_IN_END - OUTRO_MUSIC_START,
            "outro_pure_tail_end_sample": TOTAL_SAMPLES,
            "outro_pure_tail_samples": OUTRO_MUSIC_TAIL_SAMPLES,
            "total_samples": TOTAL_SAMPLES,
            "total_seconds": TOTAL_SAMPLES / SR,
        },
        "music_gain": {
            "policy": "volume=1.0",
            "ducking": "none",
            "eq": "none",
            "note": "reused v12 implicit stance (no automix / no ducking); recorded as experimental parameters, NOT a Mentor-frozen release spec.",
        },
        "pre_loudnorm_headroom": {
            "pre_gain_scale_db_from_int16_peak": pre_gain_db,
            "note": "if intro/outro speech overlap causes int16 overflow, a linear pre-gain is applied before loudnorm; recorded for reproducibility."
        },
        "loudnorm": ln_info,
        "loudnorm_note": "两阶段 loudnorm 是 v12 工作目标，不是 Mentor 冻结的发布规格。",
        "tools": {
            "ffmpeg": run(["ffmpeg", "-version"], timeout=10).stdout.split("\n")[0],
            "python": sys.version.split()[0],
            "numpy": np.__version__,
        },
        "outputs": {
            "wav_rel": str(final_wav.relative_to(root)),
            "wav_sha256": sha256_file(final_wav),
            "mp3_rel": str(final_mp3.relative_to(root)),
            "mp3_sha256": sha256_file(final_mp3),
        },
    }
    (run_dir / "music_manifest.json").write_text(
        json.dumps(music_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nWROTE: {final_wav}")
    print(f"WROTE: {final_mp3}")
    print(f"total samples: {TOTAL_SAMPLES} = {TOTAL_SAMPLES/SR} s")
    print(f"pass1 measured I = {ln_info['pass1_measured'].get('input_i')} LUFS")
    print(f"pass2 output I = {ln_info['pass2_output_measured'].get('input_i')} LUFS, "
          f"TP = {ln_info['pass2_output_measured'].get('input_tp')} dBTP")


if __name__ == "__main__":
    main()
