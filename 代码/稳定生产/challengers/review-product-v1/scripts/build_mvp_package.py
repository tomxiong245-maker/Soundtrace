#!/usr/bin/env python3
"""Build a deterministic, hash-bound N-track review bundle for the MVP."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha_bytes(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(value: str, base: Path) -> Path:
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    from_base = (base.parent / p).resolve()
    return from_base if from_base.exists() else (PROJECT_ROOT / p).resolve()


def wav_info(path: Path) -> dict:
    """Support PCM and PCM-extensible WAV written by field recorders."""
    fmt = None
    data_size = None
    with path.open("rb") as f:
        if f.read(4) != b"RIFF":
            raise SystemExit(f"not RIFF WAV: {path}")
        f.seek(4, 1)
        if f.read(4) != b"WAVE":
            raise SystemExit(f"not WAVE: {path}")
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", header)
            start = f.tell()
            if chunk_id == b"fmt ":
                raw = f.read(size)
                tag, channels, sr, _, block_align, bits = struct.unpack("<HHIIHH", raw[:16])
                if tag == 0xFFFE and len(raw) >= 26:
                    tag = struct.unpack("<H", raw[24:26])[0]
                if tag != 1:
                    raise SystemExit(f"only PCM WAV is supported, format={tag}: {path}")
                fmt = channels, sr, block_align, bits
            elif chunk_id == b"data":
                data_size = size
            f.seek(start + size + (size & 1))
    if fmt is None or data_size is None:
        raise SystemExit(f"missing WAV fmt/data: {path}")
    channels, sr, block_align, bits = fmt
    return {
        "channels": channels,
        "sample_rate_hz": sr,
        "frame_count": data_size // block_align,
        "bits_per_sample": bits,
    }


def render_ntrack_preview(
    ffmpeg: Path, audio_paths: list[Path], old: dict, output: Path, proposed: bool
) -> None:
    context_start = max(0.0, float(old.get("context_start_seconds", old["start_seconds"] - 5)))
    context_end = float(old.get("context_end_seconds", old["end_seconds"] + 5))
    duration = max(0.1, context_end - context_start)
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    for path in audio_paths:
        cmd += ["-ss", f"{context_start:.6f}", "-t", f"{duration:.6f}", "-i", str(path)]
    labels = []
    filters = []
    for idx in range(len(audio_paths)):
        labels.append(f"[a{idx}]")
        filters.append(f"[{idx}:a]aresample=48000,asetpts=PTS-STARTPTS[a{idx}]")
    filters.append(
        "".join(labels) + f"amix=inputs={len(audio_paths)}:duration=longest:normalize=1[mix]"
    )
    if proposed:
        rel_start = max(0.0, float(old["start_seconds"]) - context_start)
        rel_end = min(duration, float(old["end_seconds"]) - context_start)
        rendering = old.get("rendering") or {}
        crossfade_seconds = max(
            0.02,
            float(rendering.get("crossfade_ms", 100.0)) / 1000.0,
        )
        curve = str(rendering.get("curve", "tri"))
        if curve not in {
            "tri", "qsin", "hsin", "esin", "log", "par", "qua", "cub", "squ",
            "cbr", "exp", "iqsin", "ihsin", "dese", "desi", "losi", "sinc",
            "isinc", "quat", "iquat", "qsin2", "hsin2", "tset", "nofade",
        }:
            curve = str(rendering.get("fallback_curve", "tri"))
        filters += [
            "[mix]asplit=2[mixa][mixb]",
            f"[mixa]atrim=end={rel_start:.6f},asetpts=PTS-STARTPTS[before]",
            f"[mixb]atrim=start={rel_end:.6f},asetpts=PTS-STARTPTS[after]",
            f"[before][after]acrossfade=d={crossfade_seconds:.6f}:c1={curve}:c2={curve}[out]",
        ]
        output_label = "[out]"
    else:
        output_label = "[mix]"
    cmd += [
        "-filter_complex", ";".join(filters), "-map", output_label,
        "-ar", "48000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k", str(output),
    ]
    subprocess.run(cmd, check=True)


def context_from_transcript(path: Path | None, start: float, end: float) -> list[dict]:
    if path is None:
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for word in doc.get("words", []):
        s = float(word.get("start_seconds", 0))
        e = float(word.get("end_seconds", s))
        if e < start or s > end:
            continue
        result.append({
            "text": str(word.get("text", "")),
            "start_seconds": s,
            "end_seconds": e,
            "classification": word.get("activity", {}).get(
                "classification", word.get("classification", "unknown")
            ),
        })
    return result


def load_semantic_transcript(path: Path | None) -> dict | None:
    """Load the optional sentence/clause layer used only for reviewer context.

    The word-level ASR remains the immutable timing evidence.  This layer is
    copied into the review package as a small, hash-bound display aid; it is
    never used to change a candidate boundary or approve a cut.
    """

    if path is None:
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != "semantic-transcript-v1":
        raise SystemExit(f"invalid semantic transcript schema: {path}")
    if not isinstance(document.get("sentences"), list):
        raise SystemExit(f"semantic transcript has no sentences: {path}")
    return document


def _overlaps(start: float, end: float, item: dict) -> bool:
    item_start = float(item.get("start_seconds", 0.0))
    item_end = float(item.get("end_seconds", item_start))
    return item_end > start and item_start < end


def semantic_context_for_candidate(
    document: dict | None, start: float, end: float
) -> dict:
    """Return compact sentence/clause context for one candidate interval."""

    if document is None:
        return {
            "available": False,
            "display_note": "没有句子层数据；以下词级转写仍是原始 ASR 证据。",
            "sentences": [],
            "clauses": [],
        }
    sentences: list[dict] = []
    clauses: list[dict] = []
    for sentence in document.get("sentences") or []:
        if not isinstance(sentence, dict) or not _overlaps(start, end, sentence):
            continue
        sentences.append(
            {
                "sentence_id": sentence.get("sentence_id"),
                "start_seconds": sentence.get("start_seconds"),
                "end_seconds": sentence.get("end_seconds"),
                "raw_text_joined": sentence.get("raw_text_joined", ""),
                "text_punctuated": sentence.get("text_punctuated", ""),
                "boundary_method": sentence.get("boundary_method"),
                "clauses": [
                    {
                        "clause_id": clause.get("clause_id"),
                        "start_seconds": clause.get("start_seconds"),
                        "end_seconds": clause.get("end_seconds"),
                        "raw_text_joined": clause.get("raw_text_joined", ""),
                        "text_punctuated": clause.get("text_punctuated", ""),
                        "boundary_after": clause.get("boundary_after") or {},
                        "boundary_method": clause.get("boundary_method"),
                    }
                    for clause in sentence.get("clauses") or []
                    if isinstance(clause, dict) and _overlaps(start, end, clause)
                ],
            }
        )
        clauses.extend(sentences[-1]["clauses"])
    source_transcript = document.get("source_transcript") or {}
    return {
        "available": bool(sentences),
        "layer_kind": document.get("layer_kind"),
        "policy_version": (document.get("policy") or {}).get("version", "timing_text_heuristic_v1"),
        "source_transcript_sha256": source_transcript.get("sha256"),
        "display_note": "仅用于阅读上下文；标点和分句是启发式假设，不改写 ASR、时间戳或删剪决定。",
        "sentences": sentences,
        "clauses": clauses,
    }


def review_scope_from_source(source: dict, candidate_ids: list[str]) -> dict:
    """Expose frozen review routing metadata without changing candidates.

    The review page must distinguish the *run* from the candidate-rule version,
    and must not make a high candidate-confidence label look like a high-risk
    routing decision.  The delivery orchestrator already freezes this routing
    result in ``delivery_calibration_selection``.  Reuse that evidence here
    instead of reimplementing risk classification inside the frontend.

    This is package-level display metadata only: candidate boundaries and each
    candidate's semantic hash remain unchanged.
    """

    selection = source.get("delivery_calibration_selection") or {}
    report = selection.get("selection_report") or {}
    high_risk = report.get("high_risk")
    if not isinstance(high_risk, list):
        return {
            "available": False,
            "note": "本审核源未声明风险分层；请以候选说明和审核要求为准。",
        }

    candidate_id_set = set(candidate_ids)
    high_ids = [str(candidate_id) for candidate_id in high_risk]
    unknown = sorted(set(high_ids) - candidate_id_set)
    if unknown:
        raise SystemExit(
            "delivery_calibration_selection references high-risk candidates "
            "outside this review package: "
            + ", ".join(unknown)
        )
    high_id_set = set(high_ids)
    low_ids = [candidate_id for candidate_id in candidate_ids if candidate_id not in high_id_set]
    return {
        "available": True,
        "selection_policy": selection.get("high_risk_policy"),
        "high_risk_candidate_ids": high_ids,
        "high_risk_count": len(high_ids),
        "low_risk_candidate_ids": low_ids,
        "low_risk_count": len(low_ids),
        "note": "风险级别来自本次冻结的审核分层；候选置信度不等于风险级别。",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-package", type=Path, required=True)
    ap.add_argument("--previews-dir", type=Path, required=True)
    ap.add_argument("--tracks-manifest", type=Path, required=True)
    ap.add_argument("--frontend", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ffmpeg", type=Path, help="If set, regenerate A/B previews from every input track")
    args = ap.parse_args()

    source_path = args.source_package.resolve()
    tracks_manifest_path = args.tracks_manifest.resolve()
    frontend_path = args.frontend.resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    manifest = json.loads(tracks_manifest_path.read_text(encoding="utf-8"))
    tracks_cfg = manifest.get("tracks") or []
    if not tracks_cfg:
        raise SystemExit("tracks manifest is empty")

    out = args.out.resolve()
    previews_out = out / "previews"
    out.mkdir(parents=True, exist_ok=True)
    previews_out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(frontend_path, out / "index.html")

    tracks = []
    source_key_to_id = {}
    transcript_docs = {}
    semantic_docs = {}
    sample_rate = frame_count = None
    for cfg in tracks_cfg:
        track_id = str(cfg["track_id"])
        source_key = str(cfg.get("source_key", track_id))
        audio = resolve(cfg["audio_path"], tracks_manifest_path)
        if not audio.is_file():
            raise SystemExit(f"missing track audio: {audio}")
        info = wav_info(audio)
        if info["channels"] != 1:
            raise SystemExit(f"track must be mono: {track_id}")
        sample_rate = info["sample_rate_hz"] if sample_rate is None else sample_rate
        frame_count = info["frame_count"] if frame_count is None else frame_count
        if info["sample_rate_hz"] != sample_rate or info["frame_count"] != frame_count:
            raise SystemExit(f"track timeline mismatch: {track_id}")
        transcript_value = cfg.get("transcript_path")
        transcript = resolve(transcript_value, tracks_manifest_path) if transcript_value else None
        if transcript is not None and not transcript.is_file():
            raise SystemExit(f"missing transcript: {transcript}")
        semantic_value = cfg.get("semantic_transcript_path") or cfg.get("semantic_path")
        semantic = resolve(semantic_value, tracks_manifest_path) if semantic_value else None
        if semantic is not None and not semantic.is_file():
            raise SystemExit(f"missing semantic transcript: {semantic}")
        tracks.append({
            "track_id": track_id,
            "label": cfg.get("label", track_id),
            "source_key": source_key,
            "audio_path": str(audio),
            "audio_sha256": sha_file(audio),
            "transcript_path": str(transcript) if transcript else None,
            "transcript_sha256": sha_file(transcript) if transcript else None,
            "semantic_transcript_path": str(semantic) if semantic else None,
            "semantic_transcript_sha256": sha_file(semantic) if semantic else None,
        })
        source_key_to_id[source_key] = track_id
        transcript_docs[track_id] = transcript
        semantic_docs[track_id] = load_semantic_transcript(semantic)

    track_ids = [t["track_id"] for t in tracks]
    if len(track_ids) != len(set(track_ids)) or len(source_key_to_id) != len(tracks):
        raise SystemExit("track_id and source_key must both be unique")
    ffmpeg = args.ffmpeg.resolve() if args.ffmpeg else None
    if ffmpeg is not None and not ffmpeg.is_file():
        raise SystemExit(f"missing ffmpeg: {ffmpeg}")
    preview_audio_paths = [Path(t["audio_path"]) for t in tracks]
    candidates = []
    for old in source.get("candidates", []):
        cid = old["candidate_id"]
        if ffmpeg is not None:
            render_ntrack_preview(ffmpeg, preview_audio_paths, old, previews_out / f"{cid}.original.mp3", False)
            render_ntrack_preview(ffmpeg, preview_audio_paths, old, previews_out / f"{cid}.proposed-cut.mp3", True)
        else:
            for suffix in ("original", "proposed-cut"):
                src = args.previews_dir.resolve() / f"{cid}.{suffix}.mp3"
                if not src.is_file():
                    raise SystemExit(f"missing preview: {src}")
                shutil.copy2(src, previews_out / src.name)
        start_context = float(old.get("context_start_seconds", old["start_seconds"] - 5))
        end_context = float(old.get("context_end_seconds", old["end_seconds"] + 5))
        text_tracks = {}
        old_context = old.get("context_words", {})
        for track in tracks:
            tid, source_key = track["track_id"], track["source_key"]
            if source_key in old_context:
                words = [{
                    "text": str(w.get("text", "")),
                    "start_seconds": float(w.get("start_seconds", 0)),
                    "end_seconds": float(w.get("end_seconds", 0)),
                    "classification": w.get("classification", "unknown"),
                } for w in old_context[source_key]]
            else:
                words = context_from_transcript(
                    transcript_docs[tid], start_context, end_context
                )
            text_tracks[tid] = {"label": track["label"], "words": words}

        if old.get("source_track") not in source_key_to_id:
            raise SystemExit(f"candidate {cid} has unknown source_track: {old.get('source_track')}")
        source_track_id = source_key_to_id[old.get("source_track")]
        cand = {
            "candidate_id": cid,
            "reason_key": old.get("reason_key", "unknown"),
            "candidate_kind": old.get("candidate_kind", old.get("reason_key", "unknown")),
            "source_track_id": source_track_id,
            "start_sample": int(old["start_sample"]),
            "end_sample": int(old["end_sample"]),
            "start_seconds": float(old["start_seconds"]),
            "end_seconds": float(old["end_seconds"]),
            "global_cut": {
                "start_sample": int(old["start_sample"]),
                "end_sample": int(old["end_sample"]),
                "applies_to_tracks": track_ids,
            },
            "text_tracks": text_tracks,
            "semantic_context": semantic_context_for_candidate(
                semantic_docs.get(source_track_id),
                float(old["start_seconds"]),
                float(old["end_seconds"]),
            ),
            "previews": {
                "original_path": f"previews/{cid}.original.mp3",
                "original_sha256": sha_file(previews_out / f"{cid}.original.mp3"),
                "proposed_cut_path": f"previews/{cid}.proposed-cut.mp3",
                "proposed_cut_sha256": sha_file(previews_out / f"{cid}.proposed-cut.mp3"),
            },
            "risk_notes": old.get("reason_codes", []),
            "provenance": old.get("provenance", {}),
        }
        # Preserve the candidate generator's safety and review semantics.  The
        # package owns its semantic hash, so this cannot silently change an
        # existing review: any source rule/requirement change makes a new package.
        for key in (
            "filler_subtype",
            "filler_token",
            "proposed_delete_text",
            "global_silence",
            "review_display",
            "boundary_policy",
            "confidence_tier",
            "default_action",
            "clause_position",
            "rendering",
            "repetition_signature",
        ):
            if key in old:
                cand[key] = old[key]
        requires_audio = bool((old.get("review_display") or {}).get("requires_audio_review"))
        cand["review_requirements"] = {
            "must_listen_to": ["original", "proposed_cut"] if requires_audio else [],
            "reason": (
                "高风险听感候选必须听原版与拟压缩版 A/B。"
                if requires_audio
                else "文字可先判；需要确认语气或边界时请试听。"
            ),
        }
        cand["semantic_sha256"] = sha_bytes(cand)
        candidates.append(cand)

    review_scope = review_scope_from_source(
        source, [str(candidate["candidate_id"]) for candidate in candidates]
    )
    pkg = {
        "schema_version": "review-product-mvp-v2",
        "episode_id": manifest.get("episode_id", "EP03"),
        "run_id": source.get("run_id"),
        "package_id": f"{manifest.get('episode_id', 'EP03')}-review-{sha_file(source_path)[:12]}",
        "created_at": source.get("generated_at"),
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "tracks": tracks,
        "track_count": len(tracks),
        "source_package_path": str(source_path),
        "source_package_sha256": sha_file(source_path),
        "tracks_manifest_path": str(tracks_manifest_path),
        "tracks_manifest_sha256": sha_file(tracks_manifest_path),
        "ui_sha256": sha_file(out / "index.html"),
        "candidate_policy": source.get("candidate_policy"),
        "review_scope": review_scope,
        "mvp_limits": {
            "adjust_enabled": False,
            "adjust_reason": "六点前 MVP 只开放安全的删除/保留。边界不合适请选择保留。",
        },
        "preview_generation": {
            "mode": "all_input_tracks_regenerated" if ffmpeg else "legacy_previews_copied",
            "included_track_ids": track_ids if ffmpeg else None,
        },
        "candidates": candidates,
        "review_manifest_sha256": "PENDING",
    }
    pkg["review_manifest_sha256"] = sha_bytes({
        k: v for k, v in pkg.items() if k != "review_manifest_sha256"
    })
    (out / "review_package.json").write_text(
        json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "BUILT",
        "track_count": len(tracks),
        "candidate_count": len(candidates),
        "package": str(out / "review_package.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
