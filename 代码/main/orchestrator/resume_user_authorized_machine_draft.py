#!/usr/bin/env python3
"""Finish a bounded EP04 machine-draft render after an interrupted export.

This recovery path is intentionally narrow.  It only consumes a complete
``master_pre_loudnorm.wav`` already produced by
``render_user_authorized_machine_draft.py``; it never re-runs ASR, denoising,
candidate generation, or the source-track render.  It also never creates a
human decision, a human-approved EDL, or an automatic-cut policy.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = Path(__file__).resolve().parent
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import delivery_orchestrator as delivery  # noqa: E402
import transition_qc  # noqa: E402
import render_user_authorized_machine_draft as machine_draft  # noqa: E402


def fail(message: str) -> None:
    raise delivery.DeliveryError(message)


def read_object(path: Path) -> dict[str, Any]:
    value = delivery.read_json(path)
    if not isinstance(value, dict):
        fail(f"JSON object required: {path}")
    return value


def build_render_manifest(run_dir: Path, ffmpeg: str, pre_loudnorm: Path, master: Path, mp3: Path) -> dict[str, Any]:
    identity = delivery.require_identity(run_dir)
    plan = read_object(run_dir / "plan.json")
    edl = read_object(run_dir / "machine_assisted_draft.edl.json")
    input_manifest = read_object(run_dir / "input_manifest.json")
    timing = delivery.resolve_run_music_timing(run_dir, plan)
    template = plan.get("music", {}).get("music_template_id")
    if template != "reference-linear-v1":
        fail(f"resume path only supports reference-linear-v1, got {template!r}")
    sample_rate = int(input_manifest["sample_rate_hz"])
    pre_info = delivery.wav_info(pre_loudnorm)
    pre_duration = float(pre_info["duration_seconds"])
    voice_start = float(timing["voice_start_seconds"])
    outro_tail = float(timing["outro_music_tail_seconds"])
    speech_duration = pre_duration - voice_start - outro_tail
    if speech_duration <= 0:
        fail("pre-loudnorm master duration cannot infer a positive speech duration")
    outro_start = voice_start + speech_duration - float(timing["outro_fade_in_lead_seconds"])
    intro_duration = float(timing["intro_fade_out_end_seconds"])
    outro_duration = float(timing["outro_fade_in_lead_seconds"]) + outro_tail
    loudness = read_object(run_dir / "resume_loudness.json") if (run_dir / "resume_loudness.json").is_file() else None
    if loudness is None:
        loudness = {}
    return {
        "schema_version": "delivery-render-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": delivery.sha256_file(run_dir / "run_identity.json"),
        "variant": "machine_assisted_draft",
        "source_edl_relpath": "machine_assisted_draft.edl.json",
        "source_edl_sha256": delivery.sha256_file(run_dir / "machine_assisted_draft.edl.json"),
        "render_method": "resumed_local_ffmpeg_from_existing_pre_loudnorm_master",
        "outputs": {
            "stems": [],
            "speech_mix": None,
            "master_pre_loudnorm": delivery.relative_to_run(run_dir, pre_loudnorm),
            "master_pre_loudnorm_sha256": delivery.sha256_file(pre_loudnorm),
            "master_wav": delivery.relative_to_run(run_dir, master),
            "master_mp3": delivery.relative_to_run(run_dir, mp3),
            "master_wav_sha256": delivery.sha256_file(master),
            "master_mp3_sha256": delivery.sha256_file(mp3),
        },
        "intermediate_cleanup": {
            "status": "CLEANED_TO_FIT_LOCAL_DISK",
            "note": "source stems and speech_mix were validated before this resume and removed after pre-loudnorm assembly; raw inputs remain untouched",
        },
        "loudness": loudness,
        "music": {
            "music_template_id": template,
            "voice_start_sample": round(voice_start * sample_rate),
            "intro_music_start_sample": 0,
            "intro_music_end_sample": round(intro_duration * sample_rate),
            "outro_music_start_sample": round(outro_start * sample_rate),
            "outro_music_end_sample": round((outro_start + outro_duration) * sample_rate),
            "voice_start_seconds": voice_start,
            "intro_music_only_end_seconds": float(timing["intro_music_only_end_seconds"]),
            "intro_fade_out_start_seconds": float(timing["intro_fade_out_start_seconds"]),
            "intro_fade_out_end_seconds": float(timing["intro_fade_out_end_seconds"]),
            "outro_fade_in_lead_seconds": float(timing["outro_fade_in_lead_seconds"]),
            "outro_music_tail_seconds": outro_tail,
            "speech_duration_seconds_inferred": speech_duration,
            "parameters": {
                "music_gain_db": float(timing["music_gain_db"]),
                "ducking": timing["ducking"],
                "intro_fade_out": f"{float(timing['intro_fade_out_start_seconds']):g}s-{float(timing['intro_fade_out_end_seconds']):g}s",
                "outro_fade_in": f"{float(timing['outro_fade_in_lead_seconds']):g}s",
            },
            "timing_authority": timing.get("timing_authority"),
            "timing_sha256": delivery.sha256_bytes(timing),
            "source_asset_relpath": "assets/fixed_intro_outro_music.mp3",
            "source_sha256": machine_draft.delivery.MUSIC_SHA256,
            "parameter_status": "AUDITION_DEFAULTS_NOT_RELEASE_SPEC",
        },
    }


def run(run_dir: Path, ffmpeg: str) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    identity = delivery.require_identity(run_dir)
    state = read_object(run_dir / "state.json")
    if state.get("state") != "CALIBRATED":
        fail(f"resume requires CALIBRATED state, got {state.get('state')!r}")
    if not (run_dir / "machine_assisted_draft.edl.json").is_file():
        fail("machine-assisted EDL is missing")
    render_dir = run_dir / "render_machine_assisted_draft"
    pre_loudnorm = render_dir / "master_pre_loudnorm.wav"
    if not pre_loudnorm.is_file():
        fail("complete pre-loudnorm master is missing")
    master = render_dir / f"{identity['run_id']}.machine_assisted_draft.master.wav"
    mp3 = render_dir / f"{identity['run_id']}.machine_assisted_draft.master.mp3"
    if master.exists() or mp3.exists():
        fail("resume refuses to overwrite an existing final output")

    loudness = delivery.loudnorm_two_pass(pre_loudnorm, master, ffmpeg)
    delivery.write_json(run_dir / "resume_loudness.json", loudness)
    encoded = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(master), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)],
        capture_output=True,
        text=True,
        check=False,
    )
    if encoded.returncode:
        fail(f"MP3 encoding failed: {encoded.stderr[-1000:]}")
    render = build_render_manifest(run_dir, ffmpeg, pre_loudnorm, master, mp3)
    delivery.write_json(render_dir / "render_manifest.json", render)
    delivery.transition(run_dir, "MACHINE_ASSISTED_DRAFT_RENDERED", "resumed loudness/MP3 export from complete pre-loudnorm master")
    transition = transition_qc.generate_transition_qc(run_dir, "machine_assisted_draft")
    qc = machine_draft.write_machine_qc(run_dir, ffmpeg, render, transition)
    actions, _, _ = machine_draft.build_actions(
        run_dir=run_dir,
        authorization=read_object(run_dir / "machine_draft_authorization.json"),
        authorization_sha=delivery.sha256_file(run_dir / "machine_draft_authorization.json"),
    )
    machine_draft.write_report(run_dir, actions, qc)
    return {
        "status": "MACHINE_ASSISTED_DRAFT_RENDERED__NOT_HUMAN_APPROVED",
        "run_dir": str(run_dir),
        "machine_cut_count": len(actions),
        "master_wav": str(master),
        "master_mp3": str(mp3),
        "qc": qc.get("automatic_qc"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg")
    args = parser.parse_args()
    try:
        result = run(args.run_dir, delivery.resolve_ffmpeg(args.ffmpeg))
    except delivery.DeliveryError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
