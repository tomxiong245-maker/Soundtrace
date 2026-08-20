"""Central paths & config for asr-speaker-v1 pipeline."""

from __future__ import annotations

from pathlib import Path

# Repo root — the parent of `稳定生产` etc.
REPO = Path(__file__).resolve().parents[3]
CHAL = REPO / "稳定生产" / "challengers" / "asr-speaker-v1"
RUN = REPO / "main" / "runs" / "EP03-asr-speaker-v1"
BENCH = REPO / "benchmark" / "EP03-ASR-mini-gold-v1"
SEGMENTS_DIR = BENCH / "segments"
GOLD_JSON = BENCH / "gold.json"
FRESHRUN_ASR = REPO / "main" / "runs" / "EP03-freshrun-20260810-1730" / "05_asr"

ENGINES_ASR = ["faster_whisper_small", "sensevoice_small", "mlx_whisper_turbo"]
ENGINES_DIAR = ["pyannote_3_1", "sherpa_onnx", "dual_track_energy"]

# 10 ms frame grid for all frame-level metrics
FRAME_MS = 10
SAMPLE_RATE_HZ = 48000  # ep03 timeline; segments are 48k mono wav

# Silver truth thresholds (physical facts)
SILENCE_DBFS = -50.0
DOMINANCE_DB = 3.0  # own vs other; below this = uncertain; above = primary

MODEL_CACHE = CHAL / "environment" / "models"
