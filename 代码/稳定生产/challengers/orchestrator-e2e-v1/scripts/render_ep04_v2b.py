#!/usr/bin/env python3
"""EP04-v2b 渲染：

- transient accept → 全轨同步剪切（30 ms crossfade）
- crosstalk high accept → 源轨 gate（把源轨该段样本 fade-out→静音→fade-in，其他轨不动）
- filler 6 段真人 EDL → 全轨同步剪切

用流式方式处理长音频，避免 OOM。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np


# --- WAV I/O (与 render_ep04_v2.py 一致的实现，独立文件避免耦合)

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
    raise ValueError(f"sw={sw}")


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        data = _decode(raw, sw)
    except wave.Error:
        return _read_ext(path)
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data, sr


def _read_ext(path: Path) -> tuple[np.ndarray, int]:
    with path.open("rb") as f:
        riff = f.read(12)
        assert riff[:4] == b"RIFF" and riff[8:12] == b"WAVE"
        fmt = None
        data_bytes = None
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
        assert fmt and data_bytes is not None
        tag, ch, sr, bits = fmt
        d = _decode(data_bytes, bits // 8)
        if ch > 1:
            d = d.reshape(-1, ch).mean(axis=1)
        return d, sr


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def _append_int16(w: wave.Wave_write, pcm_f: np.ndarray) -> None:
    xi = np.clip(pcm_f, -1.0, 1.0)
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


def apply_gate_inplace(x: np.ndarray, gate_intervals: list[tuple[int, int]],
                       fade_samples: int) -> None:
    """把源轨在 gate_intervals 里的段变静音（含 fade-out/fade-in）；in-place。"""
    n = x.size
    for s, e in gate_intervals:
        s = max(0, min(n, s))
        e = max(s, min(n, e))
        if e <= s:
            continue
        f = min(fade_samples, (e - s) // 2)
        if f > 0:
            ramp_out = np.linspace(1.0, 0.0, f, dtype=np.float32)
            x[s:s + f] *= ramp_out
            x[s + f:e - f] = 0.0
            ramp_in = np.linspace(0.0, 1.0, f, dtype=np.float32)
            x[e - f:e] *= ramp_in
        else:
            x[s:e] = 0.0


def apply_sync_cuts_stream(x: np.ndarray, sr: int,
                            cuts: list[tuple[int, int]],
                            out_path: Path, crossfade_ms: int) -> int:
    """已加载 float32 数组上做 sync cuts + crossfade，流式写 int16。返回样本数。"""
    fade = int(sr * crossfade_ms / 1000)
    keeps: list[tuple[int, int]] = []
    cur = 0
    for s, e in cuts:
        s = max(0, min(x.size, s))
        e = max(s, min(x.size, e))
        if s > cur:
            keeps.append((cur, s))
        cur = e
    if cur < x.size:
        keeps.append((cur, x.size))
    total = 0
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        tail: np.ndarray | None = None
        first = True
        for a, b in keeps:
            seg = x[a:b].astype(np.float32, copy=False)
            if first:
                if seg.size <= fade:
                    tail = seg.copy()
                    first = False
                    continue
                _append_int16(w, seg[:-fade] if fade > 0 else seg)
                total += (seg.size - fade) if fade > 0 else seg.size
                tail = seg[-fade:].copy() if fade > 0 else np.zeros(0, dtype=np.float32)
                first = False
                continue
            f = min(fade, tail.size if tail is not None else 0, seg.size)
            if f > 0:
                ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
                head = tail[-f:] * (1.0 - ramp) + seg[:f] * ramp
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", action="append", required=True,
                    help="LABEL=/abs/path.wav")
    ap.add_argument("--filler-edl", required=True)
    ap.add_argument("--calibrated-decisions", required=True)
    ap.add_argument("--merged-candidates", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--crossfade-ms", type=int, default=30)
    ap.add_argument("--gate-fade-ms", type=int, default=15)
    args = ap.parse_args()

    tracks: dict[str, Path] = {}
    for spec in args.track:
        label, p = spec.split("=", 1)
        tracks[label] = Path(p)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    filler_edl = json.loads(Path(args.filler_edl).read_text(encoding="utf-8"))
    calibrated = json.loads(Path(args.calibrated_decisions).read_text(encoding="utf-8"))
    merged = json.loads(Path(args.merged_candidates).read_text(encoding="utf-8"))
    by_id = {c["candidate_id"]: c for c in merged["candidates"]}

    # 收集 sync cuts（全轨同步剪）与 per-track gates
    sync_cut_items: list[tuple[int, int]] = []
    gates_by_track: dict[str, list[tuple[int, int]]] = {t: [] for t in tracks}
    edl_records: list[dict] = []

    # filler EDL：全轨同步剪
    for c in filler_edl["cuts"]:
        sync_cut_items.append((c["start_sample"], c["end_sample"]))
        edl_records.append({
            "candidate_id": c["candidate_id"],
            "source": "filler-EP04-review-product-v2",
            "start_sample": c["start_sample"],
            "end_sample": c["end_sample"],
            "action": "sync_cut_all_tracks",
        })

    for d in calibrated["decisions"]:
        if d["decision"] != "accept":
            continue
        cid = d["candidate_id"]
        c = by_id.get(cid)
        if not c:
            continue
        action = d.get("action")
        s = int(c["start_sample"]); e = int(c["end_sample"])
        if action == "sync_cut_all_tracks":
            sync_cut_items.append((s, e))
            edl_records.append({
                "candidate_id": cid, "source": "CALIBRATED_v2_auto",
                "start_sample": s, "end_sample": e,
                "action": "sync_cut_all_tracks",
                "reason_family": c["reason_family"],
                "reason_key": c.get("reason_key"),
            })
        elif action == "gate_source_track":
            src = c["track_id"]
            if src in gates_by_track:
                gates_by_track[src].append((s, e))
                edl_records.append({
                    "candidate_id": cid, "source": "CALIBRATED_v2_auto",
                    "start_sample": s, "end_sample": e,
                    "action": "gate_source_track",
                    "track_id": src,
                    "reason_family": c["reason_family"],
                    "reason_key": c.get("reason_key"),
                })

    sync_cuts_merged = merge_intervals(sync_cut_items)
    gates_merged = {t: merge_intervals(v) for t, v in gates_by_track.items()}

    total_cut_seconds = sum(e - s for s, e in sync_cuts_merged) / 48000.0
    total_gate_seconds = {t: sum(e - s for s, e in v) / 48000.0
                          for t, v in gates_merged.items()}

    # 写 EDL
    edl_out = {
        "schema_version": "approved-edl-v2b",
        "episode_id": "EP04-v2b",
        "sample_rate_hz": 48000,
        "reviewer_mix": "熊镇正 (filler) + CALIBRATED_v2_auto (transient + crosstalk_gate)",
        "cuts": edl_records,
        "sync_cuts_merged": [{"start_sample": s, "end_sample": e,
                              "duration_seconds": (e - s) / 48000}
                             for s, e in sync_cuts_merged],
        "gates_by_track": {t: [{"start_sample": s, "end_sample": e,
                                 "duration_seconds": (e - s) / 48000}
                                for s, e in v]
                           for t, v in gates_merged.items()},
        "total_sync_cut_seconds": round(total_cut_seconds, 3),
        "total_gate_seconds_by_track": {t: round(v, 3)
                                         for t, v in total_gate_seconds.items()},
    }
    (out_dir / "EP04-v2b.edl.json").write_text(
        json.dumps(edl_out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 渲染三条 stem：先读 float → 应用源轨 gate → 应用 sync cuts + crossfade
    sr_ref = 48000
    stem_paths: list[Path] = []
    original_seconds = 0.0
    edited_seconds = 0.0
    gate_fade = int(sr_ref * args.gate_fade_ms / 1000)
    for label, wp in tracks.items():
        x, sr = _read_wav(wp)
        sr_ref = sr
        original_seconds = x.size / sr
        apply_gate_inplace(x, gates_merged.get(label, []), gate_fade)
        out = out_dir / f"{label}.edited.wav"
        n_out = apply_sync_cuts_stream(x, sr, sync_cuts_merged, out,
                                        args.crossfade_ms)
        edited_seconds = n_out / sr
        del x
        stem_paths.append(out)

    # speech mix via ffmpeg
    mix_wav = out_dir / "EP04-v2b.speech-mix.wav"
    inputs = []
    for p in stem_paths:
        inputs += ["-i", str(p)]
    subprocess.run(
        ["ffmpeg", "-y", *inputs,
         "-filter_complex", f"amix=inputs={len(stem_paths)}:normalize=0",
         "-c:a", "pcm_s16le", str(mix_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    mix_mp3 = out_dir / "EP04-v2b.speech-mix.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mix_wav),
         "-c:a", "libmp3lame", "-b:a", "192k", str(mix_mp3)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    manifest = {
        "schema_version": "render-manifest-v2b",
        "episode_id": "EP04-v2b",
        "sample_rate_hz": sr_ref,
        "crossfade_ms": args.crossfade_ms,
        "gate_fade_ms": args.gate_fade_ms,
        "sync_cuts_count": len(sync_cuts_merged),
        "gate_count_by_track": {t: len(v) for t, v in gates_merged.items()},
        "total_sync_cut_seconds": round(total_cut_seconds, 3),
        "total_gate_seconds_by_track": {t: round(v, 3)
                                         for t, v in total_gate_seconds.items()},
        "original_duration_seconds": round(original_seconds, 3),
        "edited_duration_seconds": round(edited_seconds, 3),
        "outputs": {
            "edl": str((out_dir / "EP04-v2b.edl.json").resolve()),
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
        "sync_cuts": len(sync_cuts_merged),
        "gates": {t: len(v) for t, v in gates_merged.items()},
        "sync_cut_seconds": round(total_cut_seconds, 3),
        "gate_seconds_by_track": {t: round(v, 3) for t, v in total_gate_seconds.items()},
        "edited_seconds": round(edited_seconds, 3),
        "mix_mp3": str(mix_mp3),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
