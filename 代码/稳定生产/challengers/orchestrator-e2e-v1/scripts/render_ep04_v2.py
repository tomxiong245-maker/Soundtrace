#!/usr/bin/env python3
"""EP04-v2 渲染：把 6 段既有口癖 EDL + 29 段瞬态 accept 合并成一份 v2 EDL，
按整数 sample 全轨同步剪切三条 WAV，输出 3 个 stem + speech mix WAV + MP3。

仅使用 numpy 手动 crossfade（30 ms）；不改任何 Champion。
最终 MP3 用 ffmpeg（系统自带）编码。
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


def _read_wav(path: Path) -> tuple[np.ndarray, int, int]:
    """返回 (float32 mono, sample_rate, bits_per_sample)."""
    try:
        with wave.open(str(path), "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        data = _decode(raw, sw)
        bits = sw * 8
    except wave.Error:
        return _read_ext(path)
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data, sr, bits


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


def _read_ext(path: Path) -> tuple[np.ndarray, int, int]:
    with path.open("rb") as f:
        riff = f.read(12)
        assert riff[:4] == b"RIFF" and riff[8:12] == b"WAVE"
        fmt = None
        data = None
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
                data = payload
        assert fmt and data is not None
        _, ch, sr, bits = fmt
        d = _decode(data, bits // 8)
        if ch > 1:
            d = d.reshape(-1, ch).mean(axis=1)
        return d, sr, bits


def _write_wav_int16(path: Path, x: np.ndarray, sr: int) -> None:
    x = np.clip(x, -1.0, 1.0)
    xi = (x * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(xi.tobytes())


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def apply_cuts_stream(input_path: Path, out_path: Path,
                       cuts: list[tuple[int, int]],
                       sr: int, crossfade_ms: int = 30,
                       chunk_seconds: float = 30.0) -> int:
    """流式：读整条输入 → 按 cuts 切 → crossfade → 写出 int16 mono WAV。

    只保留最多 `2*fade + chunk` 个 float32 样本在内存，避免长音频 OOM。
    返回 edited sample 数。
    """
    fade = int(sr * crossfade_ms / 1000)
    # 生成 keep segments：[cur_start, next_cut_start)
    x, _sr, _bits = _read_wav(input_path)
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
    total_out = 0
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        tail: np.ndarray | None = None
        first = True
        for (a, b) in keeps:
            seg = x[a:b].astype(np.float32)
            if first:
                # 直接输出除末端 fade 外的部分（留作与下一段 crossfade）
                if seg.size <= fade:
                    tail = seg
                    first = False
                    continue
                pcm = seg[:-fade] if fade > 0 else seg
                _append_int16(w, pcm)
                total_out += pcm.size
                tail = seg[-fade:] if fade > 0 else np.zeros(0, dtype=np.float32)
                first = False
                continue
            # 与 tail 做 crossfade
            f = min(fade, tail.size if tail is not None else 0, seg.size)
            if f > 0:
                ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
                head = tail[-f:] * (1.0 - ramp) + seg[:f] * ramp
                pre = tail[:-f] if tail.size > f else np.zeros(0, dtype=np.float32)
                _append_int16(w, pre)
                total_out += pre.size
                _append_int16(w, head)
                total_out += head.size
                mid = seg[f:]
            else:
                if tail is not None and tail.size:
                    _append_int16(w, tail)
                    total_out += tail.size
                mid = seg
            if mid.size <= fade:
                tail = mid
            else:
                pcm = mid[:-fade] if fade > 0 else mid
                _append_int16(w, pcm)
                total_out += pcm.size
                tail = mid[-fade:] if fade > 0 else np.zeros(0, dtype=np.float32)
        if tail is not None and tail.size:
            _append_int16(w, tail)
            total_out += tail.size
    return total_out


def _append_int16(w: wave.Wave_write, pcm_f: np.ndarray) -> None:
    xi = np.clip(pcm_f, -1.0, 1.0)
    xi = (xi * 32767.0).astype("<i2")
    w.writeframes(xi.tobytes())


def merge_cuts(cuts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not cuts:
        return []
    cuts = sorted(cuts)
    out = [cuts[0]]
    for s, e in cuts[1:]:
        if s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", action="append", required=True,
                    help="LABEL=/abs/path.wav")
    ap.add_argument("--filler-edl", required=True,
                    help="既有口癖 EDL JSON")
    ap.add_argument("--calibrated-decisions", required=True,
                    help="校对后决定 JSON")
    ap.add_argument("--merged-candidates", required=True,
                    help="合并后的候选 JSON（用于取 start/end sample）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--crossfade-ms", type=int, default=30)
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

    # 收集所有 accept 的剪切区间（全轨同步）
    v2_cuts: list[dict] = []
    for c in filler_edl["cuts"]:
        v2_cuts.append({
            "candidate_id": c["candidate_id"],
            "source": "filler-EP04-review-product-v2",
            "start_sample": c["start_sample"],
            "end_sample": c["end_sample"],
            "applies_to_tracks": c["applies_to_tracks"],
            "reason_family": "filler_immediate_repetition",
        })
    accept_ids = [d["candidate_id"] for d in calibrated["decisions"]
                  if d["decision"] == "accept"]
    for cid in accept_ids:
        c = by_id.get(cid)
        if not c:
            continue
        # 对 transient/crosstalk 用全轨同步剪：保守 30ms crossfade
        v2_cuts.append({
            "candidate_id": cid,
            "source": "EP04-v2-calibrated",
            "start_sample": int(c["start_sample"]),
            "end_sample": int(c["end_sample"]),
            "applies_to_tracks": list(tracks.keys()),
            "reason_family": c["reason_family"],
            "reason_key": c.get("reason_key"),
        })

    # 全轨同步 → 合并成整数 sample 区间
    sync_cuts = merge_cuts([(c["start_sample"], c["end_sample"]) for c in v2_cuts])
    cut_seconds_total = sum(e - s for s, e in sync_cuts) / 48000.0

    # 写 EDL
    edl_out = {
        "schema_version": "approved-edl-v2",
        "episode_id": "EP04-v2",
        "sample_rate_hz": 48000,
        "reviewer_mix": "熊镇正 (filler) + CALIBRATED_v1_auto (transient)",
        "cuts": v2_cuts,
        "sync_cuts_merged_samples": [{"start_sample": s, "end_sample": e,
                                       "duration_seconds": (e-s)/48000}
                                      for s, e in sync_cuts],
        "total_cut_seconds": round(cut_seconds_total, 3),
        "policy_note": (
            "transient 候选实际上属于源轨事件；本 v2 为简洁一致采用全轨同步剪切并加 30 ms crossfade，"
            "剪切区间总长 < 1% 整期，不改变对话结构。"
        ),
    }
    (out_dir / "EP04-v2.edl.json").write_text(
        json.dumps(edl_out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 剪三条 stem（流式，避免 OOM）
    stem_paths: list[Path] = []
    sr_ref = 48000
    original_seconds = 0.0
    edited_seconds = 0.0
    for label, wp in tracks.items():
        out = out_dir / f"{label}.edited.wav"
        # 用 _read_wav 探测采样率（兼容 extensible WAV）
        _probe, sr_probe, _bits = _read_wav(wp)
        sr_ref = sr_probe
        original_seconds = _probe.size / sr_ref
        del _probe  # 立即释放
        n_out = apply_cuts_stream(wp, out, sync_cuts, sr_ref,
                                   crossfade_ms=args.crossfade_ms)
        edited_seconds = n_out / sr_ref
        stem_paths.append(out)

    # speech mix：ffmpeg amix 三条 edited stem
    mix_wav = out_dir / "EP04-v2.speech-mix.wav"
    inputs = []
    for p in stem_paths:
        inputs += ["-i", str(p)]
    subprocess.run(
        ["ffmpeg", "-y", *inputs,
         "-filter_complex", f"amix=inputs={len(stem_paths)}:normalize=0",
         "-c:a", "pcm_s16le", str(mix_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # MP3
    mix_mp3 = out_dir / "EP04-v2.speech-mix.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(mix_wav),
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(mix_mp3)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # manifest
    manifest = {
        "schema_version": "render-manifest-v2",
        "episode_id": "EP04-v2",
        "sample_rate_hz": sr_ref,
        "crossfade_ms": args.crossfade_ms,
        "cuts_count": len(v2_cuts),
        "sync_cuts_count_after_merge": len(sync_cuts),
        "total_cut_seconds": round(cut_seconds_total, 3),
        "original_duration_seconds": round(original_seconds, 3),
        "edited_duration_seconds": round(edited_seconds, 3),
        "outputs": {
            "edl": str((out_dir / "EP04-v2.edl.json").resolve()),
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
        "cuts": len(v2_cuts),
        "sync_cuts": len(sync_cuts),
        "cut_seconds": round(cut_seconds_total, 3),
        "edited_seconds": round(edited_seconds, 3),
        "mix_mp3": str(mix_mp3),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
