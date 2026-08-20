#!/usr/bin/env python3
"""denoise-audit-v1: 生成 6 段 A/B/C 试听 + 客观测量。

设计：
  A = raw 3 轨 amix + loudnorm 两阶段到 -16 LUFS  （当前 v12 处理链等价，无降噪）
  B = raw 3 轨 amix，不做 loudnorm                （原始参考基线，保留真实底噪电平）
  C = afftdn 逐轨降噪三轨 amix + loudnorm 两阶段到 -16 LUFS（唯一变量：降噪）

响度匹配：A 与 C 同到 -16 LUFS 后可直接盲比；B 单独保留原电平作真实底噪参考。
时间轴：raw 时间轴（因为要横向对齐三种版本的相同内容）。
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
import hashlib
from pathlib import Path
from typing import Optional

import os
# 从脚本位置回溯到 project root：scripts/gen_ab_c.py → challengers/denoise-audit-v1/scripts → 稳定生产/challengers/... → 项目根
_here = Path(__file__).resolve()
_root_candidates = [_here.parents[i] for i in range(3, 6)]
PROJECT_ROOT = next(
    (p for p in _root_candidates if (p / "音频参考库/raw material").exists()),
    None,
)
if PROJECT_ROOT is None:
    # 支持通过 env 覆盖
    env = os.environ.get("PROJECT_ROOT")
    if env:
        PROJECT_ROOT = Path(env)
if PROJECT_ROOT is None:
    raise RuntimeError("cannot locate PROJECT_ROOT; set env PROJECT_ROOT=...")
RAW = {
    "track_01": PROJECT_ROOT / "音频参考库/raw material/第四集/ZOOM0009_Tr1.WAV",
    "track_02": PROJECT_ROOT / "音频参考库/raw material/第四集/ZOOM0009_Tr2.WAV",
    "track_03": PROJECT_ROOT / "音频参考库/raw material/第四集/ZOOM0009_Tr3.WAV",
}
DENOISED = {
    "track_01": PROJECT_ROOT / "main/runs/EP04-p0-20260811/04_denoise/track_01.denoised.wav",
    "track_02": PROJECT_ROOT / "main/runs/EP04-p0-20260811/04_denoise/track_02.denoised.wav",
    "track_03": PROJECT_ROOT / "main/runs/EP04-p0-20260811/04_denoise/track_03.denoised.wav",
}
SEGMENTS = [
    # (id, label, raw_start_s, duration_s, note)
    ("SEG1", "quiet_speech",    60.0, 30.0, "开场附近安静说话"),
    ("SEG2", "moderate_speech", 300.0, 30.0, "常态说话，附近有 solo_filler '对' 271s"),
    ("SEG3", "midshow_speech",  900.0, 30.0, "中段说话（denoise preview 覆盖 900s）"),
    ("SEG4", "later_speech",    1500.0, 30.0, "后段说话（denoise preview 覆盖 1500s）"),
    ("SEG5", "long_pause",      2418.0, 30.0, "覆盖 2425-2432s 的 7.08s 长死寂"),
    ("SEG6", "closing",         3240.0, 30.0, "节目结尾最后 30s"),
]
TARGET_LUFS = -16.0
TARGET_TP = -1.0
TARGET_LRA = 11.0


def run(cmd, timeout=90, check=True, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd fail rc={r.returncode}: {' '.join(map(str,cmd))[:200]}\n{r.stderr[-500:]}")
    return r


def cut_track(src: Path, start: float, dur: float, out: Path) -> None:
    """从 src WAV 截取 [start, start+dur] 到 out，保持 sample-accurate。"""
    run([
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(src),
        "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
        str(out),
    ], timeout=60)


def amix_three(t1: Path, t2: Path, t3: Path, out: Path) -> None:
    """三轨等权 mix → mono 24-bit WAV。"""
    run([
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-i", str(t1), "-i", str(t2), "-i", str(t3),
        "-filter_complex", "[0][1][2]amix=inputs=3:normalize=0[a]",
        "-map", "[a]", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1",
        str(out),
    ], timeout=60)


def loudnorm_two_pass(src: Path, dst: Path,
                       I: float = TARGET_LUFS, TP: float = TARGET_TP, LRA: float = TARGET_LRA) -> dict:
    """两阶段 loudnorm。返回 measured + target。"""
    # pass 1: measure
    r = run([
        "ffmpeg", "-hide_banner", "-i", str(src),
        "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json",
        "-f", "null", "-"
    ], timeout=120, check=False)
    log = r.stderr
    m = re.search(r"\{[^{}]*?\"input_i\".*?\}", log, re.DOTALL)
    if not m:
        raise RuntimeError(f"loudnorm pass1 fail on {src}: {log[-300:]}")
    measured = json.loads(m.group(0))
    # pass 2
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
    ], timeout=120)
    return {"target": {"I": I, "TP": TP, "LRA": LRA}, "measured_pass1": measured}


def measure_only(src: Path) -> dict:
    """只做 loudness measure，不写入。返回原始 measure（含 input_i, input_tp, input_lra, input_thresh, target_offset）。"""
    r = run([
        "ffmpeg", "-hide_banner", "-i", str(src),
        "-af", f"loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
        "-f", "null", "-"
    ], timeout=90, check=False)
    log = r.stderr
    m = re.search(r"\{[^{}]*?\"input_i\".*?\}", log, re.DOTALL)
    if not m:
        return {}
    return json.loads(m.group(0))


def astats_measure(src: Path) -> dict:
    """用 astats 得 RMS_level / peak_level / crest / DC / dynamic_range / noise_floor 估计。"""
    r = run([
        "ffmpeg", "-hide_banner", "-i", str(src),
        "-af", "astats=metadata=1:reset=0",
        "-f", "null", "-"
    ], timeout=90, check=False)
    log = r.stderr
    stats = {}
    for k in ["RMS level dB", "Peak level dB", "RMS peak dB", "RMS trough dB",
              "Flat factor", "Peak count", "Bit depth", "Dynamic range",
              "Zero crossings", "Number of samples"]:
        m = re.search(rf"Overall\s.*?{re.escape(k)}:\s*([-\d.]+)", log, re.DOTALL)
        if not m:
            m = re.search(rf"{re.escape(k)}:\s*([-\d.]+)", log)
        if m:
            stats[k] = float(m.group(1))
    return stats


def encode_mp3(wav: Path, mp3: Path, bitrate: str = "192k") -> None:
    run([
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-i", str(wav), "-c:a", "libmp3lame", "-b:a", bitrate,
        str(mp3)
    ], timeout=60)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def process_segment(seg_id: str, label: str, start: float, dur: float, out_dir: Path,
                     work_dir: Path) -> dict:
    """处理一段，返回该段 metrics dict。"""
    seg_metrics = {
        "seg_id": seg_id, "label": label,
        "raw_start_seconds": start, "duration_seconds": dur,
        "versions": {},
    }
    # 1) 从 raw 三轨截取 30s（work_dir 用于中间品）
    raw_cuts = []
    for tid, src in RAW.items():
        p = work_dir / f"{seg_id}.raw.{tid}.wav"
        cut_track(src, start, dur, p)
        raw_cuts.append(p)
    # 从 denoised 三轨截取 30s
    den_cuts = []
    for tid, src in DENOISED.items():
        p = work_dir / f"{seg_id}.denoised.{tid}.wav"
        cut_track(src, start, dur, p)
        den_cuts.append(p)

    # 2) amix
    raw_mix = work_dir / f"{seg_id}.raw_amix.wav"
    den_mix = work_dir / f"{seg_id}.denoised_amix.wav"
    amix_three(*raw_cuts, raw_mix)
    amix_three(*den_cuts, den_mix)

    # 3) 三个版本
    # A: raw amix + loudnorm two-pass (模拟当前 v12 处理链)
    A_wav = out_dir / f"{seg_id}.A_v12_current.wav"
    A_info = loudnorm_two_pass(raw_mix, A_wav)
    seg_metrics["versions"]["A_v12_current"] = {
        "wav_path": str(A_wav.relative_to(out_dir.parent)),
        "processing": "raw_3ch_amix -> loudnorm two-pass (-16 LUFS / -1 dBTP)",
        "no_denoise": True,
        "loudnorm": A_info,
        "astats": astats_measure(A_wav),
    }

    # B: raw amix, no loudnorm (原始基线)
    B_wav = out_dir / f"{seg_id}.B_raw_baseline.wav"
    # 转 16-bit mono 保持一致
    run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
         "-i", str(raw_mix), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
         str(B_wav)], timeout=60)
    seg_metrics["versions"]["B_raw_baseline"] = {
        "wav_path": str(B_wav.relative_to(out_dir.parent)),
        "processing": "raw_3ch_amix (no loudnorm) — 原始基线，保留真实底噪电平",
        "no_denoise": True,
        "loudness_measure": measure_only(B_wav),
        "astats": astats_measure(B_wav),
    }

    # C: denoised amix + loudnorm two-pass
    C_wav = out_dir / f"{seg_id}.C_denoised_test.wav"
    C_info = loudnorm_two_pass(den_mix, C_wav)
    seg_metrics["versions"]["C_denoised_test"] = {
        "wav_path": str(C_wav.relative_to(out_dir.parent)),
        "processing": "afftdn_3ch_amix -> loudnorm two-pass (-16 LUFS / -1 dBTP)",
        "denoise_filter": "afftdn=nr=8.0:nf=-55.0:tn=1:gs=5 (from EP04-p0-20260811/04_denoise)",
        "loudnorm": C_info,
        "astats": astats_measure(C_wav),
    }

    # 4) 各转成 mp3
    for k in ["A_v12_current", "B_raw_baseline", "C_denoised_test"]:
        wav = out_dir / f"{seg_id}.{k}.wav"
        mp3 = out_dir / f"{seg_id}.{k}.mp3"
        encode_mp3(wav, mp3, "192k")
        seg_metrics["versions"][k]["mp3_path"] = str(mp3.relative_to(out_dir.parent))
        seg_metrics["versions"][k]["mp3_sha256"] = sha256(mp3)
        seg_metrics["versions"][k]["wav_sha256"] = sha256(wav)

    # 5) 清中间品（work_dir 的三轨切片和 amix 中间品可删；保留 out_dir 里的 A/B/C）
    for p in raw_cuts + den_cuts + [raw_mix, den_mix]:
        try:
            p.unlink()
        except Exception:
            pass

    return seg_metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="e.g. main/runs/DENOISE-AUDIT-v1-<ts>")
    ap.add_argument("--segments", default="all", help="comma-separated seg ids or 'all'")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    prev_dir = run_dir / "previews"
    work_dir = run_dir / "logs" / "work"
    prev_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    if args.segments == "all":
        selected = SEGMENTS
    else:
        want = set(args.segments.split(","))
        selected = [s for s in SEGMENTS if s[0] in want]

    # 检查输入存在
    for label, path in list(RAW.items()) + list(DENOISED.items()):
        if not path.exists():
            print(f"MISSING INPUT: {label} → {path}")
            sys.exit(2)

    metrics = {
        "schema_version": "denoise-audit-v1",
        "target_lufs": TARGET_LUFS,
        "target_true_peak_dbtp": TARGET_TP,
        "target_lra": TARGET_LRA,
        "raw_source_shas": {tid: sha256(p) for tid, p in RAW.items()},
        "denoised_source_shas": {tid: sha256(p) for tid, p in DENOISED.items()},
        "denoise_filter_reference": "afftdn=nr=8.0:nf=-55.0:tn=1:gs=5 + 25ms latency compensation (from EP04-p0-20260811/04_denoise/denoise_manifest.json)",
        "segments": [],
    }
    for seg in selected:
        seg_id, label, start, dur, note = seg
        print(f"\n=== {seg_id} ({label}) raw {start:.1f}..{start+dur:.1f}s ===")
        sm = process_segment(seg_id, label, start, dur, prev_dir, work_dir)
        sm["note"] = note
        metrics["segments"].append(sm)
        # 增量保存
        (run_dir / "metrics" / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  A_v12_current   loudness={sm['versions']['A_v12_current']['loudnorm']['measured_pass1'].get('input_i','?')} LUFS (raw before pass1)")
        print(f"  C_denoised_test loudness={sm['versions']['C_denoised_test']['loudnorm']['measured_pass1'].get('input_i','?')} LUFS (raw before pass1)")

    print(f"\nDONE. metrics: {run_dir/'metrics/metrics.json'}")


if __name__ == "__main__":
    main()
