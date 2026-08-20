#!/usr/bin/env python3
"""EP04-v3 渲染：融合所有已 accept 剪切，改用 equal-power 200ms crossfade。

剪切来源：
- 6 段口癖真人 EDL  (sync cut)
- 29 段瞬态 CALIBRATED_v2  (sync cut)
- 4 段长停顿 v2 silencedetect（-30dBFS × 0.5s, trigger 1.2s, safety 100ms,
  keep 400/400 呼吸）  (sync cut，只裁死寂中段)
- 36 段串音 CALIBRATED_v2  (源轨 gate)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np


def _decode(raw: bytes, sw: int) -> np.ndarray:
    if sw == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8)
        n = b.size // 3
        b = b[: n * 3].reshape(n, 3)
        v = (b[:, 0].astype(np.int32)
             | (b[:, 1].astype(np.int32) << 8)
             | (b[:, 2].astype(np.int32) << 16))
        v[v >= (1 << 23)] -= (1 << 24)
        return v.astype(np.float32) / (1 << 23)
    if sw == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float32) / (1 << 31)
    raise ValueError(sw)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as wf:
            n_ch = wf.getnchannels(); sw = wf.getsampwidth(); sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        data = _decode(raw, sw)
    except wave.Error:
        return _read_ext(path)
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data, sr


def _read_ext(path: Path) -> tuple[np.ndarray, int]:
    with path.open("rb") as f:
        f.read(12)
        fmt = None; data_bytes = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, sz = struct.unpack("<4sI", hdr)
            payload = f.read(sz)
            if sz % 2:
                f.read(1)
            if cid == b"fmt ":
                tag = struct.unpack("<H", payload[0:2])[0]
                ch = struct.unpack("<H", payload[2:4])[0]
                sr = struct.unpack("<I", payload[4:8])[0]
                bits = struct.unpack("<H", payload[14:16])[0]
                fmt = (tag, ch, sr, bits)
            elif cid == b"data":
                data_bytes = payload
    _, ch, sr, bits = fmt
    d = _decode(data_bytes, bits // 8)
    if ch > 1:
        d = d.reshape(-1, ch).mean(axis=1)
    return d, sr


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def _append_int16(w: wave.Wave_write, x: np.ndarray) -> None:
    xi = np.clip(x, -1.0, 1.0)
    xi = (xi * 32767.0).astype("<i2")
    w.writeframes(xi.tobytes())


def merge_intervals(items: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not items:
        return []
    items = sorted(items)
    out = [items[0]]
    for s, e in items[1:]:
        if s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def silencedetect(source: Path, noise_db: float, min_seconds: float
                  ) -> list[tuple[float, float]]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(source),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}",
         "-f", "null", "-"], capture_output=True, text=True)
    log = r.stderr
    ss = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", log)]
    ee = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", log)]
    n = min(len(ss), len(ee))
    return list(zip(ss[:n], ee[:n]))


def apply_gate_inplace(x: np.ndarray, gates: list[tuple[int, int]],
                       fade_samples: int) -> None:
    for s, e in gates:
        s = max(0, min(x.size, s)); e = max(s, min(x.size, e))
        if e <= s:
            continue
        f = min(fade_samples, (e - s) // 2)
        if f > 0:
            # equal-power fade：cos^2 / sin^2 之和 = 1
            t = np.linspace(0, np.pi / 2, f, dtype=np.float32)
            ramp_out = np.cos(t)
            ramp_in = np.sin(t)
            x[s:s + f] *= ramp_out
            x[s + f:e - f] = 0.0
            x[e - f:e] *= ramp_in
        else:
            x[s:e] = 0.0


def apply_sync_cuts_equal_power(x: np.ndarray, sr: int,
                                cuts: list[tuple[int, int]],
                                out_path: Path, crossfade_ms: int) -> int:
    """在同一条 mono 音轨上按 sync cuts 剪切；每个剪口做 equal-power crossfade。"""
    fade = int(sr * crossfade_ms / 1000)
    keeps: list[tuple[int, int]] = []
    cur = 0
    for s, e in cuts:
        s = max(0, min(x.size, s)); e = max(s, min(x.size, e))
        if s > cur:
            keeps.append((cur, s))
        cur = e
    if cur < x.size:
        keeps.append((cur, x.size))
    total = 0
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        tail: np.ndarray | None = None
        first = True
        # equal-power ramps
        t = np.linspace(0, np.pi / 2, fade, dtype=np.float32) if fade > 0 else np.zeros(0, dtype=np.float32)
        r_out = np.cos(t) if fade > 0 else t
        r_in = np.sin(t) if fade > 0 else t
        for a, b in keeps:
            seg = x[a:b].astype(np.float32, copy=False)
            if first:
                if seg.size <= fade:
                    tail = seg.copy(); first = False; continue
                _append_int16(w, seg[:-fade] if fade > 0 else seg)
                total += (seg.size - fade) if fade > 0 else seg.size
                tail = seg[-fade:].copy() if fade > 0 else np.zeros(0, dtype=np.float32)
                first = False; continue
            f = min(fade, tail.size if tail is not None else 0, seg.size)
            if f > 0:
                head = tail[-f:] * r_out[:f] + seg[:f] * r_in[:f]
                pre = tail[:-f] if tail.size > f else np.zeros(0, dtype=np.float32)
                if pre.size:
                    _append_int16(w, pre); total += pre.size
                _append_int16(w, head); total += head.size
                mid = seg[f:]
            else:
                if tail is not None and tail.size:
                    _append_int16(w, tail); total += tail.size
                mid = seg
            if mid.size <= fade:
                tail = mid.copy()
            else:
                _append_int16(w, mid[:-fade] if fade > 0 else mid)
                total += (mid.size - fade) if fade > 0 else mid.size
                tail = mid[-fade:].copy() if fade > 0 else np.zeros(0, dtype=np.float32)
        if tail is not None and tail.size:
            _append_int16(w, tail); total += tail.size
    return total


def long_pause_cut_intervals(mix_source: Path, sr: int,
                             noise_db: float, min_silence_seconds: float,
                             trigger_seconds: float,
                             safety_ms: int, keep_head_ms: int,
                             keep_tail_ms: int) -> list[dict]:
    """在 mix_source 上找长停顿；返回 [{silence, cut_start_sample, cut_end_sample, ...}]。"""
    silences = silencedetect(mix_source, noise_db, min_silence_seconds)
    safety = safety_ms / 1000.0
    kh = keep_head_ms / 1000.0
    kt = keep_tail_ms / 1000.0
    out = []
    for s, e in silences:
        dur = e - s
        if dur < trigger_seconds:
            continue
        safe_start = s + safety
        safe_end = e - safety
        cut_start = safe_start + kh
        cut_end = safe_end - kt
        if cut_end - cut_start < 0.05:  # 不值得剪
            continue
        out.append({
            "silence_start_seconds": round(s, 3),
            "silence_end_seconds": round(e, 3),
            "silence_duration_seconds": round(dur, 3),
            "cut_start_seconds": round(cut_start, 3),
            "cut_end_seconds": round(cut_end, 3),
            "cut_duration_seconds": round(cut_end - cut_start, 3),
            "cut_start_sample": int(round(cut_start * sr)),
            "cut_end_sample": int(round(cut_end * sr)),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", action="append", required=True)
    ap.add_argument("--filler-edl", required=True)
    ap.add_argument("--calibrated-decisions-v2b", required=True,
                    help="含 transient + crosstalk 决定")
    ap.add_argument("--merged-candidates", required=True)
    ap.add_argument("--mix-for-silencedetect", required=True,
                    help="用于扫真实长停顿的三轨平均混音 WAV")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sync-crossfade-ms", type=int, default=200)
    ap.add_argument("--gate-fade-ms", type=int, default=30)
    ap.add_argument("--long-pause-noise-db", type=float, default=-30.0)
    ap.add_argument("--long-pause-trigger-seconds", type=float, default=1.2)
    ap.add_argument("--long-pause-safety-ms", type=int, default=100)
    ap.add_argument("--long-pause-keep-head-ms", type=int, default=400)
    ap.add_argument("--long-pause-keep-tail-ms", type=int, default=400)
    args = ap.parse_args()

    tracks: dict[str, Path] = {}
    for spec in args.track:
        label, p = spec.split("=", 1)
        tracks[label] = Path(p)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    filler_edl = json.loads(Path(args.filler_edl).read_text(encoding="utf-8"))
    calibrated = json.loads(Path(args.calibrated_decisions_v2b).read_text(encoding="utf-8"))
    merged = json.loads(Path(args.merged_candidates).read_text(encoding="utf-8"))
    by_id = {c["candidate_id"]: c for c in merged["candidates"]}

    sr = 48000
    sync_items: list[tuple[int, int]] = []
    gates_by_track: dict[str, list[tuple[int, int]]] = {t: [] for t in tracks}
    records: list[dict] = []

    # 1) filler 6 段
    for c in filler_edl["cuts"]:
        sync_items.append((c["start_sample"], c["end_sample"]))
        records.append({"source": "EP04-真人-filler",
                         "candidate_id": c["candidate_id"],
                         "start_sample": c["start_sample"],
                         "end_sample": c["end_sample"],
                         "action": "sync_cut"})
    # 2) transient + crosstalk gates
    for d in calibrated["decisions"]:
        if d["decision"] != "accept":
            continue
        cid = d["candidate_id"]; c = by_id.get(cid)
        if not c:
            continue
        action = d.get("action")
        s = int(c["start_sample"]); e = int(c["end_sample"])
        if action == "sync_cut_all_tracks":
            sync_items.append((s, e))
            records.append({"source": "CALIBRATED_v2_transient",
                             "candidate_id": cid,
                             "start_sample": s, "end_sample": e,
                             "action": "sync_cut",
                             "reason_key": c.get("reason_key")})
        elif action == "gate_source_track":
            src = c["track_id"]
            if src in gates_by_track:
                gates_by_track[src].append((s, e))
                records.append({"source": "CALIBRATED_v2_crosstalk",
                                 "candidate_id": cid,
                                 "start_sample": s, "end_sample": e,
                                 "action": "gate", "track_id": src})
    # 3) v2 长停顿
    lp = long_pause_cut_intervals(
        Path(args.mix_for_silencedetect), sr,
        args.long_pause_noise_db, 0.5,
        args.long_pause_trigger_seconds, args.long_pause_safety_ms,
        args.long_pause_keep_head_ms, args.long_pause_keep_tail_ms,
    )
    for i, item in enumerate(lp, 1):
        sync_items.append((item["cut_start_sample"], item["cut_end_sample"]))
        records.append({"source": "LONG_PAUSE_v2",
                         "candidate_id": f"LPv2-{i:03d}",
                         "start_sample": item["cut_start_sample"],
                         "end_sample": item["cut_end_sample"],
                         "action": "sync_cut",
                         "silence_start_seconds": item["silence_start_seconds"],
                         "silence_end_seconds": item["silence_end_seconds"],
                         "silence_duration_seconds": item["silence_duration_seconds"]})

    sync_merged = merge_intervals(sync_items)
    gates_merged = {t: merge_intervals(v) for t, v in gates_by_track.items()}

    # EDL 输出
    edl_out = {
        "schema_version": "approved-edl-v3",
        "episode_id": "EP04-v3",
        "sample_rate_hz": sr,
        "sync_crossfade_ms": args.sync_crossfade_ms,
        "crossfade_curve": "equal_power (sin/cos)",
        "gate_fade_ms": args.gate_fade_ms,
        "cuts": records,
        "sync_cuts_merged": [{"start_sample": s, "end_sample": e,
                              "duration_seconds": (e - s) / sr}
                             for s, e in sync_merged],
        "gates_by_track": {t: [{"start_sample": s, "end_sample": e,
                                 "duration_seconds": (e - s) / sr}
                                for s, e in v]
                           for t, v in gates_merged.items()},
        "total_sync_cut_seconds": round(sum(e - s for s, e in sync_merged) / sr, 3),
    }
    (out_dir / "EP04-v3.edl.json").write_text(
        json.dumps(edl_out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 渲染三条 stem
    stem_paths = []
    edited_seconds = 0.0
    original_seconds = 0.0
    for label, wp in tracks.items():
        x, sr_probe = _read_wav(wp)
        sr = sr_probe
        original_seconds = x.size / sr
        apply_gate_inplace(x, gates_merged.get(label, []),
                            int(sr * args.gate_fade_ms / 1000))
        out = out_dir / f"{label}.edited.wav"
        n = apply_sync_cuts_equal_power(x, sr, sync_merged, out,
                                         args.sync_crossfade_ms)
        edited_seconds = n / sr
        del x
        stem_paths.append(out)

    # amix
    mix_wav = out_dir / "EP04-v3.speech-mix.wav"
    inputs = []
    for p in stem_paths:
        inputs += ["-i", str(p)]
    subprocess.run(
        ["ffmpeg", "-y", *inputs,
         "-filter_complex", f"amix=inputs={len(stem_paths)}:normalize=0",
         "-c:a", "pcm_s16le", str(mix_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    mix_mp3 = out_dir / "EP04-v3.speech-mix.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mix_wav),
         "-c:a", "libmp3lame", "-b:a", "192k", str(mix_mp3)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    manifest = {
        "schema_version": "render-manifest-v3",
        "episode_id": "EP04-v3",
        "sample_rate_hz": sr,
        "sync_crossfade_ms": args.sync_crossfade_ms,
        "crossfade_curve": "equal_power (sin/cos)",
        "gate_fade_ms": args.gate_fade_ms,
        "sync_cuts_count": len(sync_merged),
        "gate_count_by_track": {t: len(v) for t, v in gates_merged.items()},
        "total_sync_cut_seconds": edl_out["total_sync_cut_seconds"],
        "original_duration_seconds": round(original_seconds, 3),
        "edited_duration_seconds": round(edited_seconds, 3),
        "long_pause_params": {
            "noise_db": args.long_pause_noise_db,
            "trigger_seconds": args.long_pause_trigger_seconds,
            "safety_ms": args.long_pause_safety_ms,
            "keep_head_ms": args.long_pause_keep_head_ms,
            "keep_tail_ms": args.long_pause_keep_tail_ms,
            "long_pause_segments": lp,
        },
        "outputs": {
            "edl": str((out_dir / "EP04-v3.edl.json").resolve()),
            "stems": [str(p.resolve()) for p in stem_paths],
            "stem_shas": {p.name: _sha(p) for p in stem_paths},
            "speech_mix_wav": str(mix_wav.resolve()),
            "speech_mix_wav_sha256": _sha(mix_wav),
            "speech_mix_mp3": str(mix_mp3.resolve()),
            "speech_mix_mp3_sha256": _sha(mix_mp3),
        },
    }
    (out_dir / "render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "sync_cuts": len(sync_merged),
        "gates": {t: len(v) for t, v in gates_merged.items()},
        "sync_cut_seconds": edl_out["total_sync_cut_seconds"],
        "long_pause_segments": len(lp),
        "edited_seconds": manifest["edited_duration_seconds"],
        "mix_mp3": str(mix_mp3),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
