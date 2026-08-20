"""Fixture tests for automix-v1.

Uses synthetic tiny mono WAVs (2 tracks, 3 seconds) so ffmpeg + Python logic
can be validated without waiting on EP03's 30-minute audio.
"""
from __future__ import annotations

import array
import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "稳定生产/challengers/automix-v1/scripts"))

import automix_v1 as amx  # noqa: E402


def _write_sine_wav(path: Path, freq_hz: float, seconds: float, rate: int = 48000, amp: float = 0.4):
    n = int(seconds * rate)
    samples = array.array("h")
    for i in range(n):
        v = int(amp * 32767 * math.sin(2 * math.pi * freq_hz * i / rate))
        samples.append(v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def _write_silence_wav(path: Path, seconds: float, rate: int = 48000):
    _write_sine_wav(path, freq_hz=0, seconds=seconds, rate=rate, amp=0)


def _write_music_mp3(path: Path, seconds: float = 60):
    tmp_wav = path.with_suffix(".tmp.wav")
    _write_sine_wav(tmp_wav, freq_hz=220, seconds=seconds, amp=0.3)
    subprocess.run(
        [amx.FFMPEG, "-y", "-i", str(tmp_wav), "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(path)],
        check=True, capture_output=True,
    )
    tmp_wav.unlink()


class AutomixCoreTests(unittest.TestCase):
    def test_rms_frames_zero_for_silence(self):
        s = array.array("h", [0] * 4800)
        rms = amx.rms_frames(s, 480)
        self.assertEqual(len(rms), 10)
        for v in rms:
            self.assertEqual(v, 0.0)

    def test_rms_frames_positive_for_sine(self):
        s = array.array("h", [int(10000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(4800)])
        rms = amx.rms_frames(s, 480)
        for v in rms:
            self.assertGreater(v, 1000)

    def test_decide_primary_picks_louder_track(self):
        # Track 0 loud, track 1 silent
        loud = [10000.0] * 10
        silent = [0.0] * 10
        seq = amx.decide_primary([loud, silent], min_gap_db=3.0)
        self.assertEqual(seq, [0] * 10)

    def test_decide_primary_ambiguous_when_below_gap(self):
        a = [1000.0] * 5
        b = [1000.0] * 5
        seq = amx.decide_primary([a, b], min_gap_db=3.0)
        # Both equal, top-second gap = 0 dB < 3 dB → all ambiguous
        self.assertEqual(seq, [-1] * 5)

    def test_gain_envelope_ramps_at_frame_boundary(self):
        primary = [0, 1, 0, 1]
        n_samples = 4 * 100
        env = amx.gain_envelope_for_track(
            primary_seq=primary,
            track_idx=0,
            n_tracks=2,
            frame_size=100,
            n_samples=n_samples,
            secondary_atten_db=-12.0,
            crossfade_ms=1,   # 1ms at 48000 = 48 samples; use small rate here
            rate=48000,
        )
        # Track 0 primary in frame 0, secondary in frame 1
        self.assertAlmostEqual(env[0], 1.0, places=2)
        self.assertAlmostEqual(env[-1], 10 ** (-12.0 / 20), places=2)


class AutomixEndToEndFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # 2 tracks: track_0 alternates loud/silent 0.5s each; track_1 opposite
        self.t0 = self.root / "t0.wav"
        self.t1 = self.root / "t1.wav"
        rate = 48000
        n_total = int(rate * 3.0)
        s0 = array.array("h")
        s1 = array.array("h")
        for i in range(n_total):
            phase = int((i / rate) * 2) % 2  # every 0.5s block
            if phase == 0:
                s0.append(int(0.4 * 32767 * math.sin(2 * math.pi * 500 * i / rate)))
                s1.append(0)
            else:
                s0.append(0)
                s1.append(int(0.4 * 32767 * math.sin(2 * math.pi * 500 * i / rate)))
        for path, samples in [(self.t0, s0), (self.t1, s1)]:
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                w.writeframes(samples.tobytes())

        self.music = self.root / "music.mp3"
        _write_music_mp3(self.music, seconds=60)

        self.release_spec_path = self.root / "release_specs.json"
        self.release_spec_path.write_text(json.dumps({
            "specs": {
                "test-template-v1": {
                    "target_integrated_lufs": -22.2,
                    "target_true_peak_dbfs": -0.1,
                    "target_true_peak_dbfs_safety_floor": -1.0,
                    "target_lra_lu": 7.9,
                    "bit_rate_bps": 192000,
                }
            }
        }))

        self.music_template_path = self.root / "music_templates.json"
        self.music_template_path.write_text(json.dumps({
            "templates": {
                "test-template-v1": {
                    "voice_start_seconds": 0.5,
                    "intro_music_only_end_seconds": 0.5,
                    "intro_fade_out_start_seconds": 0.5,
                    "intro_fade_out_end_seconds": 1.5,
                    "outro_fade_in_lead_seconds": 1.0,
                    "outro_music_tail_seconds": 2.0,
                    "music_gain_db": -12.0,
                    "ducking": "none",
                }
            }
        }))

        self.output = self.root / "out.mp3"
        self.tmp_dir = self.root / "tmp"

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_to_end_produces_mp3(self):
        rc = amx.main([
            "--tracks", str(self.t0), str(self.t1),
            "--music", str(self.music),
            "--release-spec", str(self.release_spec_path),
            "--music-template", str(self.music_template_path),
            "--template-id", "test-template-v1",
            "--output", str(self.output),
            "--tmp-dir", str(self.tmp_dir),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue(self.output.exists())
        self.assertGreater(self.output.stat().st_size, 5000)
        stats = json.loads((self.tmp_dir / "automix_stats.json").read_text())
        self.assertGreater(stats["n_frames"], 100)
        # Both tracks should be primary for some frames since they alternate
        self.assertGreater(stats["primary_frame_counts"][0], 0)
        self.assertGreater(stats["primary_frame_counts"][1], 0)


if __name__ == "__main__":
    unittest.main()
