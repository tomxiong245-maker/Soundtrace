#!/usr/bin/env python3
"""EP04-v2 专用：给 canonical 词级转写补 primary/bleed/ambiguous 分类。

- 只在 EP04-v2 目录写；不改任何 Champion 与既有 Challenger。
- 与 端到端学习剪辑/代码/classify_track_activity.py 语义等价（能量启发式），
  但支持 canonical `words[]` 结构与 24-bit extensible WAV。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
import wave
from pathlib import Path

import numpy as np


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        data = _decode_pcm(raw, sw)
    except wave.Error:
        return _read_wav_extensible(path)
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data, sr


def _decode_pcm(raw: bytes, sw: int) -> np.ndarray:
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
    raise ValueError(f"unsupported sample width: {sw}")


def _read_wav_extensible(path: Path) -> tuple[np.ndarray, int]:
    with path.open("rb") as f:
        riff = f.read(12)
        if riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise ValueError(f"not RIFF WAVE: {path}")
        fmt = None
        data_bytes = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, sz = struct.unpack("<4sI", hdr)
            payload = f.read(sz)
            if sz % 2 == 1:
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
        data = _decode_pcm(data_bytes, bits // 8)
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)
        return data, sr


def build_envelope(x: np.ndarray, sr: int, window_ms: int) -> tuple[np.ndarray, int]:
    win = max(1, round(sr * window_ms / 1000))
    n = x.size // win
    e = x[: n * win].reshape(n, win)
    p = np.mean(e.astype(np.float64) ** 2, axis=1)
    return p, win


def interval_db(env: np.ndarray, start: int, end: int, win: int) -> float:
    a = max(0, start // win)
    b = min(env.size, max(a + 1, math.ceil(end / win)))
    p = float(np.median(env[a:b])) if b > a else 0.0
    return 10.0 * math.log10(max(p, 1e-12))


def classify_word(own_db: float, other_dbs: list[float], dominance_db: float) -> str:
    other_max = max(other_dbs) if other_dbs else -120.0
    dom = own_db - other_max
    if dom >= dominance_db:
        return "primary"
    if dom <= -dominance_db:
        return "bleed"
    return "ambiguous"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", action="append", required=True,
                    help="LABEL=/abs/path.wav")
    ap.add_argument("--transcript", action="append", required=True,
                    help="LABEL=/abs/path.canonical.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--window-ms", type=int, default=20)
    ap.add_argument("--dominance-db", type=float, default=3.0)
    args = ap.parse_args(argv)

    tracks: dict[str, Path] = dict(spec.split("=", 1) for spec in args.track)
    transcripts: dict[str, Path] = dict(
        spec.split("=", 1) for spec in args.transcript)
    for k in tracks:
        tracks[k] = Path(tracks[k])
    for k in transcripts:
        transcripts[k] = Path(transcripts[k])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    envelopes: dict[str, tuple[np.ndarray, int, int]] = {}
    for label, p in tracks.items():
        x, sr = _read_wav(p)
        env, win = build_envelope(x, sr, args.window_ms)
        envelopes[label] = (env, win, sr)

    summaries = []
    for label, tp in transcripts.items():
        transcript = json.loads(tp.read_text(encoding="utf-8"))
        words = transcript.get("words", [])
        others = [l for l in tracks if l != label]
        env_own, win_own, sr = envelopes[label]
        counts = {"primary": 0, "bleed": 0, "ambiguous": 0}
        for w in words:
            s = int(w.get("start_seconds", 0.0) * sr)
            e = int(w.get("end_seconds", 0.0) * sr)
            own_db = interval_db(env_own, s, e, win_own)
            other_dbs = []
            for ol in others:
                env_o, win_o, _ = envelopes[ol]
                other_dbs.append(interval_db(env_o, s, e, win_o))
            cls = classify_word(own_db, other_dbs, args.dominance_db)
            counts[cls] += 1
            w["activity"] = {
                "classification": cls,
                "own_rms_dbfs": round(own_db, 3),
                "strongest_other_rms_dbfs": round(max(other_dbs), 3) if other_dbs else None,
                "dominance_db": round(own_db - (max(other_dbs) if other_dbs else -120.0), 3),
            }
        out_path = out_dir / f"{label}.classified.json"
        transcript["activity_classification"] = {
            "algorithm": "energy_heuristic_v1",
            "window_ms": args.window_ms,
            "dominance_db": args.dominance_db,
        }
        out_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        summaries.append({"label": label, "path": str(out_path), "counts": counts})
    manifest = {
        "schema_version": "activity-manifest-v1",
        "algorithm": "energy_heuristic_v1",
        "window_ms": args.window_ms,
        "dominance_db": args.dominance_db,
        "tracks": summaries,
    }
    (out_dir / "activity_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"tracks": len(summaries), "out_dir": str(out_dir),
                      "summaries": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
