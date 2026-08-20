#!/usr/bin/env python3
"""Rank rendered global-cut boundaries for targeted human re-listening.

This is deliberately an *after-render* diagnostic.  It reads a normal
``delivery-edl-v1`` plus its matching ``delivery-render-manifest-v1`` and
creates one immutable JSON report per variant.  It never changes an EDL,
audio file, review decision, delivery state, or automatic-cut policy.

The numerical signals are adapted from the earlier end-to-end experiment
``端到端学习剪辑/代码/analyze_cut_transitions.py``.  They can find unusually
large level, spectral, or waveform changes relative to the other cuts in the
same render.  They cannot determine whether an edit sounds natural, preserves
meaning, or may be approved.  A high score means "listen here first"; a low
score never means "approved".

Typical use after both variants have rendered::

    python3 main/orchestrator/transition_qc.py \
      --run-dir /absolute/path/to/main/runs/EP04/EP04-v20-... \
      --variant human_approved --variant machine_assisted_draft

By default this writes the following *new* files and refuses to overwrite an
existing report::

    render_human_approved/transition_qc.json
    render_machine_assisted_draft/transition_qc.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA_VERSION = "rendered-transition-qc-v1"
STATUS = "OBJECTIVE_ANOMALY_RANKING_SUBJECTIVE_LISTENING_REQUIRED"
DEFAULT_CONTEXT_MS = 150.0
DEFAULT_PRIORITY_COUNT = 5
VARIANTS = ("human_approved", "machine_assisted_draft")


class TransitionQCError(RuntimeError):
    """A render/EDL identity or timeline condition is unsafe to analyze."""


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_legacy_metric_module() -> Any:
    """Load the established experiment's spectral-distance helper.

    Keeping this narrow import makes the production adapter use the same
    spectral definition as the documented EP03 experiment without importing
    that script's CLI or its old, single-schema assumptions.
    """

    source = project_root() / "端到端学习剪辑/代码/analyze_cut_transitions.py"
    if not source.is_file():
        raise TransitionQCError(f"existing transition metric source is missing: {source}")
    spec = importlib.util.spec_from_file_location("legacy_transition_metrics", source)
    if spec is None or spec.loader is None:
        raise TransitionQCError("cannot load the existing transition metric source")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "spectral_cosine_distance", None)):
        raise TransitionQCError("existing transition metric source lacks spectral_cosine_distance")
    return module


LEGACY_METRICS = load_legacy_metric_module()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransitionQCError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TransitionQCError(f"JSON object required: {path}")
    return payload


def integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise TransitionQCError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TransitionQCError(f"{field} must be an integer") from exc
    if result != value:
        raise TransitionQCError(f"{field} must be an integer")
    return result


def rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def dbfs(value: float) -> float:
    return 20.0 * math.log10(max(abs(value), 1e-12))


def rms(samples: np.ndarray) -> float:
    if not samples.size:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))


def safe_relative_path(root: Path, relpath: Any, field: str) -> Path:
    if not isinstance(relpath, str) or not relpath.strip():
        raise TransitionQCError(f"{field} must be a non-empty relative path")
    relative = Path(relpath)
    if relative.is_absolute():
        raise TransitionQCError(f"{field} must be relative to the current run")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise TransitionQCError(f"{field} escapes the current run") from exc
    return resolved


class RenderedWavReader:
    """PCM WAV reader that mixes a rendered mono/stereo file to one analysis lane."""

    def __init__(self, path: Path) -> None:
        try:
            self._handle = wave.open(str(path), "rb")
        except (OSError, wave.Error) as exc:
            raise TransitionQCError(f"cannot decode rendered WAV {path}: {exc}") from exc
        self.path = path
        if self._handle.getcomptype() != "NONE":
            self._handle.close()
            raise TransitionQCError(f"rendered WAV must be PCM: {path}")
        self.channels = self._handle.getnchannels()
        self.sample_rate = self._handle.getframerate()
        self.frame_count = self._handle.getnframes()
        self.sample_width = self._handle.getsampwidth()
        if self.channels < 1:
            self._handle.close()
            raise TransitionQCError(f"rendered WAV has no audio channels: {path}")
        if self.sample_width not in {2, 3, 4}:
            self._handle.close()
            raise TransitionQCError(f"unsupported PCM width {self.sample_width}: {path}")

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "RenderedWavReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _decode_to_mono(self, data: bytes) -> np.ndarray:
        if not data:
            return np.empty(0, dtype=np.float64)
        if self.sample_width == 2:
            values = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
        elif self.sample_width == 3:
            raw = np.frombuffer(data, dtype=np.uint8)
            if raw.size % 3:
                raise TransitionQCError(f"truncated 24-bit PCM frame in {self.path}")
            triples = raw.reshape(-1, 3)
            values_i32 = (
                triples[:, 0].astype(np.int32)
                | (triples[:, 1].astype(np.int32) << 8)
                | (triples[:, 2].astype(np.int32) << 16)
            )
            values_i32 = np.where(values_i32 & 0x800000, values_i32 - 0x1000000, values_i32)
            values = values_i32.astype(np.float64) / 8388608.0
        else:
            values = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
        if values.size % self.channels:
            raise TransitionQCError(f"truncated multi-channel PCM frame in {self.path}")
        return values.reshape(-1, self.channels).mean(axis=1, dtype=np.float64)

    def read(self, start_sample: int, end_sample: int) -> np.ndarray:
        start = max(0, min(self.frame_count, start_sample))
        end = max(start, min(self.frame_count, end_sample))
        self._handle.setpos(start)
        return self._decode_to_mono(self._handle.readframes(end - start))


def transition_metrics(
    reader: RenderedWavReader,
    transition_start: int,
    transition_end: int,
    context_samples: int,
) -> tuple[dict[str, float | None], dict[str, Any]]:
    """Measure one rendered edit boundary, including zero-length butt splices."""

    pre = reader.read(transition_start - context_samples, transition_start)
    fade = reader.read(transition_start, transition_end)
    post = reader.read(transition_end, transition_end + context_samples)
    pre_rms = rms(pre)
    post_rms = rms(post)
    fade_rms = rms(fade) if fade.size else None
    context_rms = math.sqrt((pre_rms**2 + post_rms**2) * 0.5) if pre.size and post.size else None

    if fade.size:
        boundary_values = []
        if pre.size:
            boundary_values.append(abs(float(fade[0]) - float(pre[-1])))
        if post.size:
            boundary_values.append(abs(float(post[0]) - float(fade[-1])))
        combined = np.concatenate((pre, fade, post))
    else:
        boundary_values = [abs(float(post[0]) - float(pre[-1]))] if pre.size and post.size else []
        combined = np.concatenate((pre, post))
    differences = np.abs(np.diff(combined)) if combined.size > 1 else np.empty(0)

    spectral = None
    if pre.size and post.size:
        spectral = LEGACY_METRICS.spectral_cosine_distance(pre, post, reader.sample_rate)

    metrics = {
        "pre_rms_dbfs": rounded(dbfs(pre_rms)) if pre.size else None,
        "crossfade_rms_dbfs": rounded(dbfs(fade_rms)) if fade_rms is not None else None,
        "post_rms_dbfs": rounded(dbfs(post_rms)) if post.size else None,
        "post_minus_pre_rms_db": rounded(dbfs(post_rms) - dbfs(pre_rms)) if pre.size and post.size else None,
        "crossfade_minus_context_rms_db": rounded(dbfs(fade_rms) - dbfs(context_rms))
        if fade_rms is not None and context_rms is not None
        else None,
        "boundary_jump_max_dbfs": rounded(dbfs(max(boundary_values))) if boundary_values else None,
        "max_sample_delta_dbfs": rounded(dbfs(float(np.max(differences)))) if differences.size else None,
        "pre_post_spectral_distance": rounded(spectral),
    }
    observation = {
        "available_pre_context_samples": int(pre.size),
        "available_crossfade_samples": int(fade.size),
        "available_post_context_samples": int(post.size),
        "requested_context_samples": context_samples,
        "context_complete": pre.size == context_samples and post.size == context_samples,
        "transition_kind": "crossfade" if fade.size else "butt_splice",
    }
    return metrics, observation


def percentile_rank(values: list[float]) -> list[float]:
    """Tie-aware relative ranks; values are comparable only inside one variant."""

    if not values:
        return []
    size = len(values)
    ranks = []
    for value in values:
        less = sum(candidate < value for candidate in values)
        equal = sum(candidate == value for candidate in values)
        ranks.append((less + 0.5 * equal) / size)
    return ranks


def verify_identity(
    run_dir: Path, variant: str, identity: dict[str, Any], edl: dict[str, Any], render: dict[str, Any]
) -> None:
    expected_identity_sha = sha256_file(run_dir / "run_identity.json")
    for label, payload in (("EDL", edl), ("render manifest", render)):
        if payload.get("episode_id") != identity.get("episode_id"):
            raise TransitionQCError(f"{label} episode_id does not match run identity")
        if payload.get("run_id") != identity.get("run_id"):
            raise TransitionQCError(f"{label} run_id does not match run identity")
        if payload.get("run_identity_sha256") != expected_identity_sha:
            raise TransitionQCError(f"{label} run identity SHA does not match")
        if payload.get("variant") != variant:
            raise TransitionQCError(f"{label} variant does not match {variant}")
    expected_edl_relpath = f"{variant}.edl.json"
    if render.get("source_edl_relpath") != expected_edl_relpath:
        raise TransitionQCError("render manifest source EDL path does not match the selected variant")
    actual_edl_sha = sha256_file(run_dir / expected_edl_relpath)
    if render.get("source_edl_sha256") != actual_edl_sha:
        raise TransitionQCError("render manifest source EDL SHA does not match the current EDL")


def resolve_voice_start_sample(run_dir: Path, variant: str, render: dict[str, Any], sample_rate: int) -> int:
    music = render.get("music")
    if isinstance(music, dict) and music.get("voice_start_sample") is not None:
        return integer(music["voice_start_sample"], "render music voice_start_sample")
    root_manifest = run_dir / "music_manifest.json"
    if root_manifest.is_file():
        root_music = read_json(root_manifest)
        variants = root_music.get("variants")
        if isinstance(variants, dict):
            variant_record = variants.get(variant)
            if isinstance(variant_record, dict) and variant_record.get("voice_start_sample") is not None:
                return integer(variant_record["voice_start_sample"], "music manifest voice_start_sample")
        observed = root_music.get("observed_frozen_timing")
        if isinstance(observed, dict) and observed.get("speech_start_seconds") is not None:
            return round(float(observed["speech_start_seconds"]) * sample_rate)
    raise TransitionQCError(
        "render has no speech_mix and no trustworthy voice-start mapping for master-WAV fallback"
    )


def resolve_analysis_audio(
    run_dir: Path, variant: str, render_dir: Path, render: dict[str, Any], sample_rate: int
) -> tuple[Path, str, int]:
    outputs = render.get("outputs")
    if not isinstance(outputs, dict):
        raise TransitionQCError("render manifest outputs must be an object")
    master = safe_relative_path(run_dir, outputs.get("master_wav"), "render outputs.master_wav")
    if not master.is_file():
        raise TransitionQCError("rendered master WAV is missing")
    expected_master_sha = outputs.get("master_wav_sha256")
    if not isinstance(expected_master_sha, str) or sha256_file(master) != expected_master_sha:
        raise TransitionQCError("rendered master WAV SHA does not match its manifest")
    speech_relpath = outputs.get("speech_mix")
    if isinstance(speech_relpath, str) and speech_relpath:
        speech_mix = safe_relative_path(run_dir, speech_relpath, "render outputs.speech_mix")
        if not speech_mix.is_file():
            raise TransitionQCError("render manifest declares speech_mix but the file is missing")
        try:
            speech_mix.relative_to(render_dir.resolve())
        except ValueError as exc:
            raise TransitionQCError("speech_mix is not located in the selected render directory") from exc
        return speech_mix, "speech_mix_before_music_and_loudness", 0
    voice_start = resolve_voice_start_sample(run_dir, variant, render, sample_rate)
    return master, "master_wav_fallback_music_and_loudness_may_influence_metrics", voice_start


def parse_render_cuts(edl: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    raw_cuts = edl.get("render_sync_cuts")
    if not isinstance(raw_cuts, list):
        raise TransitionQCError("EDL render_sync_cuts must be an array")
    frame_count = integer(edl.get("frame_count"), "EDL frame_count")
    ordered: list[dict[str, Any]] = []
    previous_end = 0
    total_removed = 0
    total_fade = 0
    for index, raw in enumerate(raw_cuts, start=1):
        if not isinstance(raw, dict):
            raise TransitionQCError("EDL render_sync_cuts contains a non-object")
        start = integer(raw.get("start_sample"), f"render_sync_cuts[{index}].start_sample")
        end = integer(raw.get("end_sample"), f"render_sync_cuts[{index}].end_sample")
        fade = integer(raw.get("crossfade_samples", 0), f"render_sync_cuts[{index}].crossfade_samples")
        if not (0 <= start < end <= frame_count):
            raise TransitionQCError(f"render_sync_cuts[{index}] is out of source timeline range")
        if start < previous_end:
            raise TransitionQCError("render_sync_cuts overlap; rendered transition mapping is ambiguous")
        if fade < 0 or fade > end - start:
            raise TransitionQCError(f"render_sync_cuts[{index}] has an invalid crossfade length")
        source_action_ids = raw.get("source_action_ids")
        if not isinstance(source_action_ids, list) or not source_action_ids or not all(
            isinstance(action_id, str) and action_id for action_id in source_action_ids
        ):
            raise TransitionQCError(f"render_sync_cuts[{index}] has no valid source_action_ids")
        transition_start = start - total_removed - total_fade - fade
        if transition_start < 0:
            raise TransitionQCError(f"render_sync_cuts[{index}] maps before the rendered timeline")
        ordered.append(
            {
                "transition_id": f"render-cut-{index:04d}",
                "source_start_sample": start,
                "source_end_sample": end,
                "crossfade_samples": fade,
                "crossfade_ms": rounded(fade * 1000.0 / integer(edl.get("sample_rate_hz"), "EDL sample_rate_hz"), 3),
                "source_action_ids": source_action_ids,
                "rendered_speech_transition_start_sample": transition_start,
                "rendered_speech_transition_end_sample": transition_start + fade,
            }
        )
        previous_end = end
        total_removed += end - start
        total_fade += fade
    expected_frame_count = frame_count - total_removed - total_fade
    if expected_frame_count < 0:
        raise TransitionQCError("EDL cut/fade lengths exceed the input timeline")
    return ordered, expected_frame_count


def attach_action_provenance(cuts: list[dict[str, Any]], edl: dict[str, Any]) -> None:
    actions = edl.get("global_sync_actions")
    if not isinstance(actions, list):
        raise TransitionQCError("EDL global_sync_actions must be an array")
    action_by_id: dict[str, dict[str, Any]] = {}
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("action_id"), str):
            raise TransitionQCError("EDL global_sync_actions contains an invalid action")
        action_id = action["action_id"]
        if action_id in action_by_id:
            raise TransitionQCError("EDL global_sync_actions has duplicate action_id")
        action_by_id[action_id] = action
    for cut in cuts:
        linked = []
        for action_id in cut["source_action_ids"]:
            action = action_by_id.get(action_id)
            if action is None:
                raise TransitionQCError(
                    f"render cut {cut['transition_id']} references unknown action {action_id}"
                )
            linked.append(action)
        cut["candidate_ids"] = sorted(
            {str(action["candidate_id"]) for action in linked if action.get("candidate_id") is not None}
        )
        cut["decision_provenance"] = sorted(
            {str(action["decision_provenance"]) for action in linked if action.get("decision_provenance") is not None}
        )
        cut["risk_levels"] = sorted(
            {str(action["risk_level"]) for action in linked if action.get("risk_level") is not None}
        )


def annotate_relative_scores(records: list[dict[str, Any]]) -> None:
    feature_names = (
        "absolute_level_step_db",
        "absolute_crossfade_level_change_db",
        "spectral_distance",
        "boundary_jump_dbfs",
    )
    for record in records:
        metrics = record["metrics"]
        record["feature_values"] = {
            "absolute_level_step_db": rounded(abs(metrics["post_minus_pre_rms_db"]))
            if metrics["post_minus_pre_rms_db"] is not None
            else None,
            "absolute_crossfade_level_change_db": rounded(abs(metrics["crossfade_minus_context_rms_db"]))
            if metrics["crossfade_minus_context_rms_db"] is not None
            else None,
            "spectral_distance": metrics["pre_post_spectral_distance"],
            # Values closer to 0 dBFS are larger waveform discontinuities.
            "boundary_jump_dbfs": metrics["boundary_jump_max_dbfs"],
        }
    for feature in feature_names:
        available = [(index, record["feature_values"][feature]) for index, record in enumerate(records)]
        numeric = [(index, float(value)) for index, value in available if value is not None]
        ranks = percentile_rank([value for _, value in numeric])
        values_by_index = {index: rank for (index, _), rank in zip(numeric, ranks)}
        for index, record in enumerate(records):
            record.setdefault("feature_percentiles", {})[feature] = rounded(values_by_index.get(index))
    for record in records:
        percentiles = record["feature_percentiles"]
        available = [(feature, value) for feature, value in percentiles.items() if value is not None]
        strongest = sorted(available, key=lambda item: (-float(item[1]), item[0]))[:2]
        record["strongest_objective_features"] = [feature for feature, _ in strongest]
        record["objective_anomaly_priority_score"] = rounded(
            100.0 * sum(float(value) for _, value in strongest) / len(strongest)
        ) if strongest else None


def generate_transition_qc(
    run_dir: Path,
    variant: str,
    *,
    context_ms: float = DEFAULT_CONTEXT_MS,
    priority_count: int = DEFAULT_PRIORITY_COUNT,
    output_name: str = "transition_qc.json",
) -> dict[str, Any]:
    """Write a one-variant rendered-transition report without changing delivery state."""

    if variant not in VARIANTS:
        raise TransitionQCError(f"unsupported variant: {variant}")
    if not 20.0 <= context_ms <= 1000.0:
        raise TransitionQCError("context_ms must be within 20-1000")
    if priority_count < 1:
        raise TransitionQCError("priority_count must be at least 1")
    if Path(output_name).name != output_name or not output_name.endswith(".json"):
        raise TransitionQCError("output_name must be a simple .json filename")

    run_dir = run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        raise TransitionQCError(f"run directory does not exist: {run_dir}")
    identity_path = run_dir / "run_identity.json"
    state_path = run_dir / "state.json"
    input_manifest_path = run_dir / "input_manifest.json"
    edl_path = run_dir / f"{variant}.edl.json"
    render_dir = run_dir / f"render_{variant}"
    render_path = render_dir / "render_manifest.json"
    for required in (identity_path, state_path, input_manifest_path, edl_path, render_path):
        if not required.is_file():
            raise TransitionQCError(f"required rendered-delivery evidence is missing: {required}")
    output_path = render_dir / output_name
    if output_path.exists():
        raise TransitionQCError(f"refusing to overwrite existing transition QC report: {output_path}")

    identity = read_json(identity_path)
    state = read_json(state_path)
    if state.get("state") not in {"MACHINE_ASSISTED_DRAFT_RENDERED", "FINAL_QC_REQUIRED"}:
        raise TransitionQCError(
            "transition QC may only write during post-render pre-final-QC states; "
            "completed or review-pending runs are read-only"
        )
    input_manifest = read_json(input_manifest_path)
    edl = read_json(edl_path)
    render = read_json(render_path)
    verify_identity(run_dir, variant, identity, edl, render)
    sample_rate = integer(edl.get("sample_rate_hz"), "EDL sample_rate_hz")
    if sample_rate <= 0:
        raise TransitionQCError("EDL sample_rate_hz must be positive")
    if input_manifest.get("episode_id") != identity.get("episode_id") or input_manifest.get("run_id") != identity.get("run_id"):
        raise TransitionQCError("input manifest does not match run identity")
    if input_manifest.get("run_identity_sha256") != sha256_file(identity_path):
        raise TransitionQCError("input manifest run identity SHA does not match")
    if integer(input_manifest.get("sample_rate_hz"), "input manifest sample_rate_hz") != sample_rate:
        raise TransitionQCError("input manifest sample rate does not match EDL")
    if integer(input_manifest.get("frame_count"), "input manifest frame_count") != integer(
        edl.get("frame_count"), "EDL frame_count"
    ):
        raise TransitionQCError("input manifest frame count does not match EDL")
    cuts, expected_speech_frames = parse_render_cuts(edl)
    attach_action_provenance(cuts, edl)
    analysis_audio, audio_role, timeline_offset = resolve_analysis_audio(
        run_dir, variant, render_dir, render, sample_rate
    )
    context_samples = round(sample_rate * context_ms / 1000.0)

    with RenderedWavReader(analysis_audio) as reader:
        if reader.sample_rate != sample_rate:
            raise TransitionQCError(
                f"analysis WAV sample rate {reader.sample_rate} does not match EDL {sample_rate}"
            )
        if audio_role == "speech_mix_before_music_and_loudness" and reader.frame_count != expected_speech_frames:
            raise TransitionQCError(
                "speech_mix frame count does not match EDL cut/fade mapping; refusing ambiguous transition locations"
            )
        records: list[dict[str, Any]] = []
        for cut in cuts:
            analysis_start = int(cut["rendered_speech_transition_start_sample"]) + timeline_offset
            analysis_end = int(cut["rendered_speech_transition_end_sample"]) + timeline_offset
            if not (0 <= analysis_start <= analysis_end <= reader.frame_count):
                raise TransitionQCError(
                    f"{cut['transition_id']} maps outside the selected rendered audio timeline"
                )
            metrics, context = transition_metrics(reader, analysis_start, analysis_end, context_samples)
            records.append(
                {
                    **cut,
                    "analysis_audio_transition_start_sample": analysis_start,
                    "analysis_audio_transition_end_sample": analysis_end,
                    "analysis_audio_transition_seconds": rounded(analysis_start / sample_rate),
                    "metrics": metrics,
                    "context_observation": context,
                    "decision_authority": "none_objective_metrics_do_not_approve_or_reject_edits",
                }
            )

        annotate_relative_scores(records)
        ranked = sorted(
            records,
            key=lambda item: (
                -(float(item["objective_anomaly_priority_score"]) if item["objective_anomaly_priority_score"] is not None else -1.0),
                int(item["source_start_sample"]),
                str(item["transition_id"]),
            ),
        )
        for rank, record in enumerate(ranked, start=1):
            record["priority_rank"] = rank
            record["recommended_human_action"] = (
                "priority_relisten" if rank <= priority_count else "routine_relisten_not_auto_pass"
            )

        source_gates = edl.get("source_track_gates")
        source_gate_count = len(source_gates) if isinstance(source_gates, list) else 0
        report = {
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now(),
            "status": STATUS,
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "variant": variant,
            "run_identity_sha256": sha256_file(identity_path),
            "run_state_when_created": state["state"],
            "source_evidence": {
                "edl_relpath": edl_path.name,
                "edl_sha256": sha256_file(edl_path),
                "render_manifest_relpath": f"render_{variant}/render_manifest.json",
                "render_manifest_sha256": sha256_file(render_path),
                "analysis_audio_relpath": str(analysis_audio.relative_to(run_dir)),
                "analysis_audio_sha256": sha256_file(analysis_audio),
                "analysis_audio_role": audio_role,
            },
            "timeline_validation": {
                "sample_rate_hz": sample_rate,
                "analysis_audio_frame_count": reader.frame_count,
                "expected_speech_mix_frame_count": expected_speech_frames,
                "speech_timeline_to_analysis_audio_offset_samples": timeline_offset,
                "mapping_status": "PASS",
            },
            "scope": {
                "checked": "render_sync_cuts/global_sync_cut boundaries only",
                "source_track_gates_excluded_count": source_gate_count,
                "not_checked": [
                    "semantic correctness or deleted meaning",
                    "whether a cut sounds natural to a listener",
                    "source_track_gate artifacts",
                    "music entry/exit quality",
                    "unlisted or unrendered candidate regions",
                ],
            },
            "method": {
                "adapted_from": "端到端学习剪辑/代码/analyze_cut_transitions.py",
                "context_ms": context_ms,
                "context_samples": context_samples,
                "feature_definitions": {
                    "absolute_level_step_db": "absolute RMS level difference before versus after the rendered transition",
                    "absolute_crossfade_level_change_db": "absolute crossfade RMS deviation versus neighbouring context",
                    "spectral_distance": "pre/post spectral cosine distance using the established EP03 experiment helper",
                    "boundary_jump_dbfs": "largest sample discontinuity at a crossfade edge or direct butt splice",
                },
                "scoring": "mean of the two largest available tie-aware percentile ranks; relative within this variant only",
            },
            "human_review_rule": (
                "This report only orders points for focused listening. Neither a low score nor a high score "
                "creates an accept/reject decision, changes an EDL, or authorizes automatic editing."
            ),
            "transition_count": len(records),
            "priority_relisten_count": min(priority_count, len(records)),
            "ranked_transition_ids": [record["transition_id"] for record in ranked],
            "transitions": ranked,
        }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="delivery run with rendered dual variants")
    parser.add_argument(
        "--variant",
        action="append",
        choices=VARIANTS,
        help="variant to analyze; repeat it, or omit it to analyze both variants",
    )
    parser.add_argument("--context-ms", type=float, default=DEFAULT_CONTEXT_MS)
    parser.add_argument("--priority-count", type=int, default=DEFAULT_PRIORITY_COUNT)
    parser.add_argument(
        "--output-name",
        default="transition_qc.json",
        help="new JSON filename inside each selected render directory; existing files are never overwritten",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    variants = args.variant or list(VARIANTS)
    try:
        reports = [
            generate_transition_qc(
                args.run_dir,
                variant,
                context_ms=args.context_ms,
                priority_count=args.priority_count,
                output_name=args.output_name,
            )
            for variant in variants
        ]
    except TransitionQCError as exc:
        print(f"transition QC blocked: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": STATUS,
                "run_dir": str(args.run_dir.expanduser().resolve()),
                "variants": [report["variant"] for report in reports],
                "reports": [
                    f"render_{report['variant']}/{args.output_name}" for report in reports
                ],
                "warning": "priority order only; human listening remains required",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
