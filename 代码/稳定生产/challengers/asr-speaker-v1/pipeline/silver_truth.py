"""silver_truth.py

Produce a per-segment "silver-truth" frame grid from the TWO physical mic tracks.

Physical facts we exploit (this is exact, not a model):
  1. If both female.wav AND male.wav are below -50 dBFS in a frame,
     nobody is speaking. -> silence frame.
  2. If ONE track is at least DOMINANCE_DB louder than the OTHER at that
     frame AND that track is above -50 dBFS, that speaker is primary.
  3. If BOTH tracks are above -50 dBFS AND their dominance is under
     DOMINANCE_DB, we call the frame OVERLAP (both speaking).
  4. Anything else = ambiguous.

This is imperfect (bleed can look like overlap for very loud plosives), but for
each 10 ms frame it's a strictly physical measurement of the microphone signals
—no model, no human. That's why it works as a silver truth.

Output per segment:
    silver/S<id>.npz  with:
        frames    (N,) int8:  0=silence 1=female 2=male 3=overlap 4=ambiguous
        rms_f_db  (N,) float
        rms_m_db  (N,) float
        frame_ms  int
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

FRAME_MS = 10
SILENCE_DBFS = -50.0
DOMINANCE_DB = 3.0


def read_wav_mono_f32(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1, f"expect mono: {path}"
        sr = w.getframerate()
        n = w.getnframes()
        sw = w.getsampwidth()
        data = w.readframes(n)
    if sw == 2:
        arr = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        v = raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        arr = v.astype(np.float32) / 8388608.0
    elif sw == 4:
        arr = np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sw={sw}")
    return sr, arr


def frame_dbfs(x: np.ndarray, sr: int, frame_ms: int = FRAME_MS) -> np.ndarray:
    win = int(sr * frame_ms / 1000)
    n = len(x) // win
    if n == 0:
        return np.array([-120.0])
    frames = x[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    return db.astype(np.float32)


def label_frames(rms_f_db: np.ndarray, rms_m_db: np.ndarray) -> np.ndarray:
    n = min(len(rms_f_db), len(rms_m_db))
    f = rms_f_db[:n]
    m = rms_m_db[:n]
    silence = (f < SILENCE_DBFS) & (m < SILENCE_DBFS)
    female_loud = f >= SILENCE_DBFS
    male_loud = m >= SILENCE_DBFS
    dom = f - m
    female = female_loud & (~male_loud | (dom >= DOMINANCE_DB))
    male = male_loud & (~female_loud | (dom <= -DOMINANCE_DB))
    overlap = female_loud & male_loud & (np.abs(dom) < DOMINANCE_DB)
    out = np.full(n, 4, dtype=np.int8)  # 4=ambiguous
    out[silence] = 0
    out[female & ~overlap] = 1
    out[male & ~overlap] = 2
    out[overlap] = 3
    return out


def build_for_segment(seg_dir: Path, out_path: Path) -> dict:
    sr_f, f = read_wav_mono_f32(seg_dir / "female.wav")
    sr_m, m = read_wav_mono_f32(seg_dir / "male.wav")
    assert sr_f == sr_m
    sr = sr_f
    rms_f = frame_dbfs(f, sr)
    rms_m = frame_dbfs(m, sr)
    n = min(len(rms_f), len(rms_m))
    rms_f, rms_m = rms_f[:n], rms_m[:n]
    frames = label_frames(rms_f, rms_m)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, frames=frames, rms_f_db=rms_f, rms_m_db=rms_m,
                        frame_ms=FRAME_MS, sample_rate_hz=sr)
    counts = {int(k): int(v) for k, v in zip(*np.unique(frames, return_counts=True))}
    return {
        "segment_dir": str(seg_dir),
        "frames": int(n),
        "frame_ms": FRAME_MS,
        "duration_s": n * FRAME_MS / 1000,
        "counts": counts,
        "labels": {0: "silence", 1: "female", 2: "male", 3: "overlap", 4: "ambiguous"},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--segments-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    summary = {"segments": []}
    for seg in gold["segments"]:
        sd = args.segments_dir / seg["id"]
        if not sd.is_dir():
            summary["segments"].append({"id": seg["id"], "status": "MISSING"})
            continue
        info = build_for_segment(sd, args.out / f"{seg['id']}.npz")
        info["id"] = seg["id"]
        info["start_seconds_in_ep03"] = seg["start_seconds_in_ep03"]
        info["duration_seconds"] = seg["duration_seconds"]
        summary["segments"].append(info)
    (args.out / "silver_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("silver truth written:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
