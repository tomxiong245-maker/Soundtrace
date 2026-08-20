"""Synthesize deterministic WAV fixtures and gold for adapter/scorer tests.

Runs anywhere (no ffmpeg / mlx / torch needed). Writes into tests/fixtures/.
Uses only the stdlib `wave` module + `struct`.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import wave
from pathlib import Path


SR = 16000


def write_wav(path: Path, samples: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def sine(freq: float, dur_s: float, amp: float = 0.3) -> list[int]:
    n = int(dur_s * SR)
    return [int(math.sin(2 * math.pi * freq * i / SR) * amp * 32760) for i in range(n)]


def silence(dur_s: float) -> list[int]:
    return [0] * int(dur_s * SR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    write_wav(out / "silence_10s.wav", silence(10.0))

    write_wav(out / "single_speaker.wav",
              silence(0.5) + sine(220, 2.0) + silence(0.3) + sine(220, 1.5) + silence(0.7))

    write_wav(out / "two_speaker_alternating.wav",
              silence(0.2) + sine(180, 1.0) + silence(0.1) + sine(320, 1.0) +
              silence(0.1) + sine(180, 1.0) + silence(0.1) + sine(320, 1.0) + silence(0.2))

    # overlap: mix two sines simultaneously in the middle
    def _mix(a, b):
        return [max(-32768, min(32767, x + y)) for x, y in zip(a, b)]
    seg_a = silence(0.5) + sine(180, 3.0) + silence(0.5)
    seg_b = silence(1.5) + sine(320, 3.0) + silence(0.5)
    n = min(len(seg_a), len(seg_b))
    write_wav(out / "two_speaker_overlap.wav", _mix(seg_a[:n], seg_b[:n]))

    # loudness delta: same content, different amp
    ld = out / "loudness_delta_two_tracks"
    ld.mkdir(parents=True, exist_ok=True)
    write_wav(ld / "female.wav", silence(0.1) + sine(220, 2.0, amp=0.4) + silence(0.1))
    write_wav(ld / "male.wav", silence(0.1) + sine(220, 2.0, amp=0.05) + silence(0.1))

    # Synthetic gold — 2 tiny "segments"
    gold = {
        "schema_version": 2,
        "status": "SYNTHETIC",
        "segments": [
            {
                "id": "T01",
                "kind": "synthetic",
                "start_seconds_in_ep03": 0.0,
                "duration_seconds": 10.0,
                "files": {"speech_mix_wav": "silence_10s.wav"},
                "silence_intervals": [[0.0, 10.0]],
                "gold": {
                    "transcript": "",
                    "speaker_attribution": [],
                    "missed_sentences": [],
                    "reviewer": "synthetic",
                    "reviewed_at": "1970-01-01T00:00:00Z",
                    "reviewed": True,
                },
            },
            {
                "id": "T02",
                "kind": "synthetic",
                "start_seconds_in_ep03": 0.0,
                "duration_seconds": 5.0,
                "files": {"speech_mix_wav": "single_speaker.wav"},
                "silence_intervals": [[0.0, 0.5], [2.5, 2.8], [4.3, 5.0]],
                "gold": {
                    "transcript": "喂喂",
                    "speaker_attribution": [
                        {"start": 0.5, "end": 2.5, "speaker_id": "s1"},
                        {"start": 2.8, "end": 4.3, "speaker_id": "s1"},
                    ],
                    "missed_sentences": [],
                    "reviewer": "synthetic",
                    "reviewed_at": "1970-01-01T00:00:00Z",
                    "reviewed": True,
                },
            },
        ],
    }
    (out / "synthetic_gold.json").write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("fixtures ready at", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
