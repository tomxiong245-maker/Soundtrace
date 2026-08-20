#!/usr/bin/env python3
"""Run the owner-approved N-track automix as a hash-bound run-local stage.

The adapter consumes already-rendered mono stems.  It never decides semantic
cuts and never changes the EDL; source-track gates and global cuts must already
have been applied by the renderer before this stage.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from array import array
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTOMIX_SCRIPT = PROJECT_ROOT / "稳定生产/challengers/automix-v1/scripts/automix_v1.py"


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_automix_module():
    spec = importlib.util.spec_from_file_location("minglue_automix_v1", AUTOMIX_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("automix script cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_automix_speech_mix(
    *,
    track_paths: list[Path],
    output_path: Path,
    tmp_dir: Path,
    run_id: str,
    run_identity_sha256: str,
    variant: str,
    edl_path: Path,
    source_track_gate_count: int,
    min_gap_db: float = 3.0,
    secondary_atten_db: float = -12.0,
    crossfade_ms: int = 30,
    frame_ms: int = 20,
) -> dict[str, Any]:
    if not track_paths:
        raise ValueError("automix requires at least one rendered mono stem")
    if not edl_path.is_file():
        raise FileNotFoundError(edl_path)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    module = _load_automix_module()
    samples: list[array] = []
    rate: int | None = None
    input_rows: list[dict[str, Any]] = []
    for path in track_paths:
        buf, current_rate = module.read_mono_wav_int16(path)
        if rate is None:
            rate = current_rate
        elif rate != current_rate:
            raise ValueError("automix stems have different sample rates")
        samples.append(buf)
        input_rows.append({"path": str(path), "sha256": sha256_file(path), "sample_count": len(buf)})
    if rate != 48000:
        raise ValueError(f"automix requires 48 kHz stems, got {rate}")
    target_samples = min(len(buf) for buf in samples)
    frame_size = int(rate * frame_ms / 1000)
    rms_per_track = [module.rms_frames(buf, frame_size) for buf in samples]
    primary = module.decide_primary(rms_per_track, min_gap_db)
    usable_samples = len(primary) * frame_size
    working = [buf[:usable_samples] for buf in samples]
    outputs: list[array] = []
    for index, buf in enumerate(working):
        envelope = module.gain_envelope_for_track(
            primary,
            index,
            len(working),
            frame_size,
            usable_samples,
            secondary_atten_db,
            crossfade_ms,
            rate,
        )
        outputs.append(module.apply_gain(buf, envelope))
    mixed = module.mix_mono(outputs)
    if len(mixed) < target_samples:
        mixed.extend(array("h", [0] * (target_samples - len(mixed))))
    elif len(mixed) > target_samples:
        del mixed[target_samples:]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    module.write_mono_wav_int16(output_path, mixed, rate)
    primary_counts = [0] * len(samples)
    ambiguous = 0
    for item in primary:
        if item == -1:
            ambiguous += 1
        else:
            primary_counts[item] += 1
    manifest = {
        "schema_version": "automix-run-manifest-v1",
        "adapter_id": "automix-render-v1",
        "adapter_source": str(AUTOMIX_SCRIPT),
        "adapter_source_sha256": sha256_file(AUTOMIX_SCRIPT),
        "run_id": run_id,
        "run_identity_sha256": run_identity_sha256,
        "variant": variant,
        "source_edl_relpath": str(edl_path.name),
        "source_edl_sha256": sha256_file(edl_path),
        "source_track_gate_count": source_track_gate_count,
        "inputs": input_rows,
        "output": {
            "relpath": str(output_path),
            "sha256": sha256_file(output_path),
            "sample_rate_hz": rate,
            "sample_count": len(mixed),
        },
        "parameters": {
            "frame_ms": frame_ms,
            "min_gap_db": min_gap_db,
            "secondary_atten_db": secondary_atten_db,
            "crossfade_ms": crossfade_ms,
        },
        "stats": {
            "frame_count": len(primary),
            "primary_frame_counts": primary_counts,
            "ambiguous_frame_count": ambiguous,
            "ambiguous_percent": round(100 * ambiguous / max(1, len(primary)), 3),
        },
        "safety": {
            "semantic_decision": False,
            "edl_mutation": False,
            "source_track_gates_applied_before_mix": True,
            "fallback": "direct_mix_is_allowed only as an explicitly recorded run-level fallback",
        },
    }
    manifest_path = output_path.with_name("automix_manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    manifest["manifest_sha256"] = sha256_file(manifest_path)
    return manifest


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tmp-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-identity-sha256", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--edl", type=Path, required=True)
    parser.add_argument("--source-track-gate-count", type=int, default=0)
    args = parser.parse_args()
    manifest = run_automix_speech_mix(
        track_paths=[path.expanduser().resolve() for path in args.tracks],
        output_path=args.output.expanduser().resolve(),
        tmp_dir=args.tmp_dir.expanduser().resolve(),
        run_id=args.run_id,
        run_identity_sha256=args.run_identity_sha256,
        variant=args.variant,
        edl_path=args.edl.expanduser().resolve(),
        source_track_gate_count=args.source_track_gate_count,
    )
    print(json.dumps({"status": "PASS", "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
