#!/usr/bin/env python3
"""补充测量：在 A/B/C 上加入
1) 噪声底 (silence trough via silencedetect -50dBFS)
2) 频谱质心 (spectral centroid) — 高频含量指标
3) 高频能量 (>4kHz RMS) — sibilant / air 敏感
4) SNR 粗估：peak/trough

用 numpy + scipy.signal，本地纯 python，不联网。
"""
from __future__ import annotations
import json
import re
import subprocess
import wave
import math
from pathlib import Path
import numpy as np


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        bd = w.getsampwidth()
        raw = w.readframes(n)
    if bd == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif bd == 3:
        # 24-bit → 32-bit
        buf = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        i32 = (buf[:, 0].astype(np.int32)
               | (buf[:, 1].astype(np.int32) << 8)
               | (buf[:, 2].astype(np.int32) << 16))
        i32 = np.where(i32 & 0x800000, i32 | ~0xFFFFFF, i32)
        x = i32.astype(np.float32) / (2 ** 23)
    elif bd == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / (2 ** 31)
    else:
        raise ValueError(f"unsupported bit depth: {bd}")
    return x, sr


def spectral_centroid(x: np.ndarray, sr: int) -> float:
    """整段做 FFT，返回频谱质心 (Hz)。粗略但足够对比。"""
    if len(x) < 2048:
        return float("nan")
    # windowed FFT of the full segment
    n = 1 << 15   # 32768
    x = x[: (len(x) // n) * n]
    if len(x) == 0:
        return float("nan")
    frames = x.reshape(-1, n)
    mag = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1))
    freqs = np.fft.rfftfreq(n, d=1 / sr)
    total = mag.sum(axis=1)
    total = np.where(total > 0, total, 1)
    cent = (mag * freqs).sum(axis=1) / total
    return float(cent.mean())


def high_freq_rms_db(x: np.ndarray, sr: int, cutoff: float = 4000.0) -> float:
    """FFT-域求 >cutoff Hz 的 RMS（dBFS）。"""
    n = 1 << 15
    x = x[: (len(x) // n) * n]
    if len(x) == 0:
        return float("nan")
    frames = x.reshape(-1, n) * np.hanning(n)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(n, d=1 / sr)
    mask = freqs >= cutoff
    if not mask.any():
        return float("nan")
    # 用 Parseval 近似能量
    hf_energy = (mag[:, mask] ** 2).sum() / (n * frames.shape[0])
    if hf_energy <= 0:
        return -140.0
    return 10 * math.log10(hf_energy)


def silence_percentile_db(x: np.ndarray, sr: int, win_ms: float = 50.0) -> dict:
    """按 50ms 窗计算 RMS，返回 10th/50th/90th percentile (dBFS)。10th ≈ 底噪。"""
    n = int(sr * win_ms / 1000)
    if len(x) < n:
        return {}
    m = len(x) // n
    frames = x[: m * n].reshape(m, n)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-20)
    rms_db = 20 * np.log10(rms)
    return {
        "p10_dBFS": float(np.percentile(rms_db, 10)),
        "p50_dBFS": float(np.percentile(rms_db, 50)),
        "p90_dBFS": float(np.percentile(rms_db, 90)),
        "min_dBFS": float(rms_db.min()),
    }


def crosstalk_score(x_amix: np.ndarray, sr: int) -> float:
    """从 amix 里估计"次要能量占比"作为串音代理：
    用 STFT 计算前 25% 与后 25% 能量比例；越接近 1 表示能量越平均（说话 gap 少）；
    仅作为参考，不做定论。
    """
    # 简单实现：50ms 窗 RMS，看 25th/75th 比值 (dB 差)
    n = int(sr * 0.05)
    m = len(x_amix) // n
    frames = x_amix[: m * n].reshape(m, n)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-20)
    rms_db = 20 * np.log10(rms)
    return float(np.percentile(rms_db, 75) - np.percentile(rms_db, 25))


def measure_all(wav: Path) -> dict:
    x, sr = read_wav_mono(wav)
    return {
        "duration_seconds": round(len(x) / sr, 3),
        "spectral_centroid_hz": round(spectral_centroid(x, sr), 1),
        "hf_rms_gt4k_dBFS": round(high_freq_rms_db(x, sr, 4000.0), 2),
        "hf_rms_gt8k_dBFS": round(high_freq_rms_db(x, sr, 8000.0), 2),
        "rms_percentiles_50ms": silence_percentile_db(x, sr),
        "dynamic_iqr_db": round(crosstalk_score(x, sr), 2),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    run_dir = Path(args.run_dir).resolve()
    metrics_path = run_dir / "metrics" / "metrics.json"
    m = json.loads(metrics_path.read_text(encoding="utf-8"))
    for seg in m["segments"]:
        for k in ["A_v12_current", "B_raw_baseline", "C_denoised_test"]:
            wav = run_dir.parent.parent / seg["versions"][k]["wav_path"]
            if not wav.exists():
                # try under run_dir/previews
                wav = run_dir / "previews" / wav.name
            extra = measure_all(wav)
            seg["versions"][k]["spectral"] = extra
            print(f"{seg['seg_id']} {k}: centroid={extra['spectral_centroid_hz']} Hz, "
                  f"HF>4k={extra['hf_rms_gt4k_dBFS']} dB, HF>8k={extra['hf_rms_gt8k_dBFS']} dB, "
                  f"floor(p10)={extra['rms_percentiles_50ms'].get('p10_dBFS','?')} dB, "
                  f"IQR={extra['dynamic_iqr_db']} dB")
    metrics_path.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nDONE")
