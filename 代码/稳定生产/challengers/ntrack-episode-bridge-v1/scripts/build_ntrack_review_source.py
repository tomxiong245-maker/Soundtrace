#!/usr/bin/env python3
"""Bridge a P0 N-track transcription run to the existing MVP review input.

This Challenger never makes an automatic cut decision. It turns P0's generic
physical-track transcripts into the source package consumed by
``review-product-v1/scripts/build_mvp_package.py``. A conflicting transcript on
another track is fail-closed; every remaining proposal still requires human
review.

Raw ASR stays immutable. A separate canonical transcript applies only a
deterministic, glossary-backed merge of adjacent English subword fragments,
such as ``fe`` + ``ature`` -> ``feature``. Candidate boundaries always snap to
complete canonical tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    relative = (base.parent / path).resolve()
    return relative if relative.exists() else (PROJECT_ROOT / path).resolve()


def clean_token(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").casefold()


def english_fragment(value: str) -> str | None:
    compact = re.sub(r"[^0-9A-Za-z]+", "", value or "")
    return compact if compact and re.fullmatch(r"[0-9A-Za-z]+", compact) else None


def display_text(value: str) -> str:
    return str(value or "").strip()


def token_duration_overlap(word: dict[str, Any], start: float, end: float) -> float:
    return max(
        0.0,
        min(float(word["end_seconds"]), end)
        - max(float(word["start_seconds"]), start),
    )


def validate_raw_words(
    words: Iterable[dict[str, Any]], duration: float, track_id: str
) -> None:
    previous = -1.0
    for index, word in enumerate(words, 1):
        start = float(word.get("start_seconds", -1))
        end = float(word.get("end_seconds", -1))
        if start < -0.05 or end <= start or end > duration + 0.25:
            raise SystemExit(
                f"{track_id} word {index} has invalid time {start:.3f}-{end:.3f}"
            )
        if start + 0.25 < previous:
            raise SystemExit(f"{track_id} word {index} is non-monotonic")
        previous = max(previous, start)


def canonicalize_words(
    raw_words: list[dict[str, Any]],
    track_id: str,
    glossary_terms: set[str],
    max_gap_seconds: float,
) -> list[dict[str, Any]]:
    """Create display tokens while keeping a complete raw-token back-reference."""
    result: list[dict[str, Any]] = []
    index = 0
    canonical_index = 0
    while index < len(raw_words):
        first = raw_words[index]
        best_end = index
        pieces: list[str] = []
        previous_end = float(first["start_seconds"])
        for look_ahead in range(index, min(len(raw_words), index + 6)):
            word = raw_words[look_ahead]
            fragment = english_fragment(str(word.get("text", "")))
            if fragment is None:
                break
            start = float(word["start_seconds"])
            if look_ahead > index and start - previous_end > max_gap_seconds:
                break
            pieces.append(fragment)
            merged = "".join(pieces).casefold()
            if look_ahead > index and merged in glossary_terms:
                best_end = look_ahead
            previous_end = float(word["end_seconds"])

        selected = raw_words[index : best_end + 1]
        if best_end > index:
            raw_last = display_text(str(selected[-1].get("text", "")))
            suffix = "".join(ch for ch in raw_last if not ch.isalnum())
            text = "".join(
                english_fragment(str(word.get("text", ""))) or ""
                for word in selected
            ) + suffix
        else:
            text = display_text(str(first.get("text", "")))
        probabilities = [
            word.get("probability")
            for word in selected
            if word.get("probability") is not None
        ]
        canonical_index += 1
        result.append(
            {
                "word_id": f"{track_id}:n{canonical_index:06d}",
                "text": text,
                "start_seconds": float(selected[0]["start_seconds"]),
                "end_seconds": float(selected[-1]["end_seconds"]),
                "probability": (
                    min(float(value) for value in probabilities)
                    if probabilities
                    else None
                ),
                "classification": "unknown",
                "raw_word_ids": [
                    str(word.get("word_id", "")) for word in selected
                ],
                "raw_texts": [str(word.get("text", "")) for word in selected],
            }
        )
        index = best_end + 1
    return result


def contiguous(words: list[dict[str, Any]], max_gap_seconds: float) -> bool:
    return all(
        float(next_word["start_seconds"]) - float(word["end_seconds"])
        <= max_gap_seconds
        for word, next_word in zip(words, words[1:])
    )


def proposal_from_words(
    reason_key: str, source_track: str, words: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "reason_key": reason_key,
        "source_track": source_track,
        "start_seconds": float(words[0]["start_seconds"]),
        "end_seconds": float(words[-1]["end_seconds"]),
        "evidence_words": words,
        "evidence_text": "".join(clean_token(word["text"]) for word in words),
    }


def find_filler_runs(
    words: list[dict[str, Any]], track_id: str, rule: dict[str, Any]
) -> list[dict[str, Any]]:
    if not rule.get("enabled", False):
        return []
    fillers = {clean_token(value) for value in rule.get("tokens", [])}
    min_consecutive = int(rule.get("min_consecutive", 1))
    max_gap = float(rule.get("max_gap_seconds", 0.45))
    proposals: list[dict[str, Any]] = []
    index = 0
    while index < len(words):
        if clean_token(words[index]["text"]) not in fillers:
            index += 1
            continue
        end = index
        while (
            end + 1 < len(words)
            and clean_token(words[end + 1]["text"]) in fillers
            and float(words[end + 1]["start_seconds"])
            - float(words[end]["end_seconds"])
            <= max_gap
        ):
            end += 1
        selected = words[index : end + 1]
        if len(selected) >= min_consecutive:
            proposals.append(
                proposal_from_words("filler_hesitation", track_id, selected)
            )
        index = end + 1
    return proposals


def find_immediate_repetitions(
    words: list[dict[str, Any]], track_id: str, rule: dict[str, Any]
) -> list[dict[str, Any]]:
    if not rule.get("enabled", False):
        return []
    max_tokens = int(rule.get("max_phrase_tokens", 3))
    min_chars = int(rule.get("min_phrase_chars", 2))
    max_gap = float(rule.get("max_adjacent_gap_seconds", 0.45))
    proposals: list[dict[str, Any]] = []
    for index in range(len(words)):
        for width in range(1, max_tokens + 1):
            end = index + 2 * width
            if end > len(words):
                break
            first, second = words[index : index + width], words[index + width : end]
            phrase = "".join(clean_token(word["text"]) for word in first)
            if (
                len(phrase) >= min_chars
                and phrase
                == "".join(clean_token(word["text"]) for word in second)
                and contiguous(first + second, max_gap)
            ):
                proposals.append(
                    proposal_from_words("immediate_repetition", track_id, first)
                )
                break
    return proposals


def overlap_ratio(left: dict[str, Any], right: dict[str, Any]) -> float:
    overlap = max(
        0.0,
        min(float(left["end_seconds"]), float(right["end_seconds"]))
        - max(float(left["start_seconds"]), float(right["start_seconds"])),
    )
    shortest = min(
        float(left["end_seconds"]) - float(left["start_seconds"]),
        float(right["end_seconds"]) - float(right["start_seconds"]),
    )
    return overlap / shortest if shortest > 0 else 0.0


def proposal_score(proposal: dict[str, Any]) -> tuple[float, str]:
    probabilities = [word.get("probability") for word in proposal["evidence_words"]]
    usable = [float(value) for value in probabilities if value is not None]
    return (
        sum(usable) / len(usable) if usable else 0.0,
        proposal["source_track"],
    )


def deduplicate_proposals(
    proposals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse the same inferred filler/repetition heard on several microphones."""
    result: list[dict[str, Any]] = []
    for proposal in sorted(
        proposals,
        key=lambda item: (
            float(item["start_seconds"]),
            item["reason_key"],
            item["evidence_text"],
            item["source_track"],
        ),
    ):
        matches = [
            existing
            for existing in result
            if existing["reason_key"] == proposal["reason_key"]
            and existing["evidence_text"] == proposal["evidence_text"]
            and overlap_ratio(existing, proposal) >= 0.5
        ]
        if not matches:
            proposal["corroborated_track_ids"] = [proposal["source_track"]]
            result.append(proposal)
            continue
        existing = matches[0]
        corroborated = sorted(
            set(existing.get("corroborated_track_ids", []))
            | {proposal["source_track"]}
        )
        existing["corroborated_track_ids"] = corroborated
        if proposal_score(proposal) > proposal_score(existing):
            replacement = dict(proposal)
            replacement["corroborated_track_ids"] = corroborated
            result[result.index(existing)] = replacement
    return result


def consolidate_adjacent_repetitions(
    proposals: list[dict[str, Any]], max_gap_seconds: float
) -> list[dict[str, Any]]:
    """Merge a run such as A A A A into one proposal deleting A A A.

    Only proposals from the same physical track, rule and repeated phrase are
    merged.  Cross-track timing is never blended into a new cut boundary.
    """
    result: list[dict[str, Any]] = []
    for proposal in sorted(
        proposals,
        key=lambda item: (
            float(item["start_seconds"]),
            item["source_track"],
            item["reason_key"],
        ),
    ):
        previous = result[-1] if result else None
        gap = (
            float(proposal["start_seconds"])
            - float(previous["end_seconds"])
            if previous is not None
            else None
        )
        if (
            previous is not None
            and proposal["reason_key"] == "immediate_repetition"
            and previous["reason_key"] == proposal["reason_key"]
            and previous["source_track"] == proposal["source_track"]
            and previous["evidence_text"] == proposal["evidence_text"]
            and gap is not None
            and 0.0 <= gap <= max_gap_seconds
        ):
            existing_ids = {
                str(word.get("word_id", ""))
                for word in previous["evidence_words"]
            }
            previous["evidence_words"].extend(
                word
                for word in proposal["evidence_words"]
                if str(word.get("word_id", "")) not in existing_ids
            )
            previous["evidence_words"].sort(
                key=lambda word: (
                    float(word["start_seconds"]),
                    float(word["end_seconds"]),
                    str(word.get("word_id", "")),
                )
            )
            previous["end_seconds"] = max(
                float(previous["end_seconds"]),
                float(proposal["end_seconds"]),
            )
            previous["corroborated_track_ids"] = sorted(
                set(previous.get("corroborated_track_ids", []))
                | set(proposal.get("corroborated_track_ids", []))
            )
            previous["merged_repetition_proposals"] = int(
                previous.get("merged_repetition_proposals", 1)
            ) + int(proposal.get("merged_repetition_proposals", 1))
            continue
        proposal["merged_repetition_proposals"] = int(
            proposal.get("merged_repetition_proposals", 1)
        )
        result.append(proposal)
    return result


def cross_track_decision(
    proposal: dict[str, Any],
    words_by_track: dict[str, list[dict[str, Any]]],
    filler_tokens: set[str],
    conflict_overlap_seconds: float,
) -> tuple[str, list[str], dict[str, list[dict[str, Any]]]]:
    """Fail closed on text that conflicts with the deletion on another track."""
    source = proposal["source_track"]
    start, end = float(proposal["start_seconds"]), float(proposal["end_seconds"])
    source_text = proposal["evidence_text"]
    conflicts: dict[str, list[dict[str, Any]]] = {}
    for track_id, words in words_by_track.items():
        if track_id == source:
            continue
        for word in words:
            if (
                token_duration_overlap(word, start, end)
                < conflict_overlap_seconds
            ):
                continue
            token = clean_token(word["text"])
            if not token or token in filler_tokens or token in source_text:
                continue
            conflicts.setdefault(track_id, []).append(word)
    if conflicts:
        return (
            "BLOCKED",
            ["OTHER_TRACK_CONFLICTING_TRANSCRIPT", "NO_FRAME_LEVEL_ACTIVITY"],
            conflicts,
        )
    return (
        "NEEDS_HUMAN_REVIEW",
        ["NO_FRAME_LEVEL_ACTIVITY", "WHOLE_TOKEN_BOUNDARY"],
        conflicts,
    )


def read_p0_inputs(
    report_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != "p0-mvp-report-v1":
        raise SystemExit("only p0-mvp-report-v1 is accepted")
    if report.get("engineering_gate") != "PASS":
        raise SystemExit("P0 engineering_gate is not PASS; do not generate candidates")
    declared_tracks = report.get("tracks") or []
    if not declared_tracks or int(report.get("track_count", 0)) != len(
        declared_tracks
    ):
        raise SystemExit("P0 report has invalid track list")
    track_ids = [str(track.get("track_id", "")) for track in declared_tracks]
    if not all(track_ids) or len(track_ids) != len(set(track_ids)):
        raise SystemExit("P0 report track_id must be non-empty and unique")

    loaded: list[dict[str, Any]] = []
    expected_rate = int(report["sample_rate_hz"])
    expected_frames = int(report["frame_count"])
    for track in declared_tracks:
        transcript_path = resolve_path(str(track["transcript_path"]), report_path)
        if not transcript_path.is_file():
            raise SystemExit(f"P0 transcript is missing: {transcript_path}")
        doc = json.loads(transcript_path.read_text(encoding="utf-8"))
        track_id = str(track["track_id"])
        if (
            doc.get("schema_version") != "ntrack-transcript-v1"
            or doc.get("track_id") != track_id
        ):
            raise SystemExit(f"P0 transcript does not match report: {track_id}")
        if (
            int(doc.get("sample_rate_hz", -1)) != expected_rate
            or int(doc.get("frame_count", -1)) != expected_frames
        ):
            raise SystemExit(f"P0 transcript time base mismatch: {track_id}")
        audio_path = Path(str(doc.get("source_audio_path", "")))
        if not audio_path.is_file():
            raise SystemExit(f"P0 source audio is missing: {audio_path}")
        duration = expected_frames / expected_rate
        raw_words = doc.get("words") or []
        validate_raw_words(raw_words, duration, track_id)
        loaded.append(
            {
                "track_id": track_id,
                "label": str(
                    track.get("label") or doc.get("label") or track_id
                ),
                "audio_path": audio_path.resolve(),
                "audio_sha256": sha256_file(audio_path),
                "raw_transcript_path": transcript_path.resolve(),
                "raw_transcript_sha256": sha256_file(transcript_path),
                "raw_words": raw_words,
                "engine": doc.get("engine"),
                "model_ref": doc.get("model_ref"),
            }
        )
    return report, loaded


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build(
    p0_report_path: Path,
    out: Path,
    rules_path: Path,
    episode_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    p0_report_path = p0_report_path.resolve()
    rules_path = rules_path.resolve()
    report, loaded_tracks = read_p0_inputs(p0_report_path)
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    if rules.get("policy") != "review_only_no_automatic_accept":
        raise SystemExit("bridge rules must keep review_only_no_automatic_accept")
    sample_rate = int(report["sample_rate_hz"])
    frame_count = int(report["frame_count"])
    duration = frame_count / sample_rate
    p0_sha = sha256_file(p0_report_path)
    rules_sha = sha256_file(rules_path)
    glossary = {
        clean_token(term)
        for term in rules["english_normalization"].get("glossary_terms", [])
    }
    max_english_gap = float(
        rules["english_normalization"].get("max_adjacent_gap_seconds", 0.08)
    )
    canonical_dir = out / "canonical_transcripts"
    words_by_track: dict[str, list[dict[str, Any]]] = {}
    track_manifest_entries: list[dict[str, Any]] = []

    for track in loaded_tracks:
        canonical_words = canonicalize_words(
            track["raw_words"], track["track_id"], glossary, max_english_gap
        )
        words_by_track[track["track_id"]] = canonical_words
        canonical_doc = {
            "schema_version": "ntrack-canonical-transcript-v1",
            "track_id": track["track_id"],
            "label": track["label"],
            "source_audio_path": str(track["audio_path"]),
            "source_audio_sha256": track["audio_sha256"],
            "sample_rate_hz": sample_rate,
            "frame_count": frame_count,
            "raw_transcript_path": str(track["raw_transcript_path"]),
            "raw_transcript_sha256": track["raw_transcript_sha256"],
            "normalization": {
                "method": "glossary_backed_adjacent_english_subword_merge_only",
                "rules_sha256": rules_sha,
                "raw_asr_immutable": True,
            },
            "words": canonical_words,
        }
        canonical_path = canonical_dir / f"{track['track_id']}.canonical.json"
        write_json(canonical_path, canonical_doc)
        track_manifest_entries.append(
            {
                "track_id": track["track_id"],
                "label": track["label"],
                "source_key": track["track_id"],
                "audio_path": str(track["audio_path"]),
                "transcript_path": str(canonical_path.resolve()),
            }
        )

    raw_proposals: list[dict[str, Any]] = []
    for track_id, words in words_by_track.items():
        raw_proposals.extend(
            find_filler_runs(words, track_id, rules["filler_hesitation"])
        )
        raw_proposals.extend(
            find_immediate_repetitions(
                words, track_id, rules["immediate_repetition"]
            )
        )
    proposals = deduplicate_proposals(raw_proposals)
    proposals = consolidate_adjacent_repetitions(
        proposals,
        float(rules.get("adjacent_candidate_merge_gap_seconds", 0.12)),
    )
    filler_tokens = {
        clean_token(token)
        for token in rules["filler_hesitation"].get("tokens", [])
    }
    conflict_overlap = float(
        rules["cross_track_guard"].get(
            "conflicting_word_overlap_seconds", 0.05
        )
    )
    reviewable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for proposal in proposals:
        start = max(0.0, float(proposal["start_seconds"]))
        end = min(duration, float(proposal["end_seconds"]))
        if end <= start:
            continue
        if end - start < float(rules.get("min_candidate_seconds", 0.12)):
            safety_status = "BLOCKED"
            reason_codes = ["CANDIDATE_TOO_SHORT", "WHOLE_TOKEN_BOUNDARY"]
            conflicts = {}
        else:
            safety_status, reason_codes, conflicts = cross_track_decision(
                proposal, words_by_track, filler_tokens, conflict_overlap
            )
        source_track = next(
            track
            for track in loaded_tracks
            if track["track_id"] == proposal["source_track"]
        )
        item = {
            "reason_key": proposal["reason_key"],
            "source_track": proposal["source_track"],
            "start_sample": round(start * sample_rate),
            "end_sample": round(end * sample_rate),
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": round(end - start, 6),
            "context_start_seconds": max(
                0.0, start - float(rules.get("context_seconds", 5.0))
            ),
            "context_end_seconds": min(
                duration, end + float(rules.get("context_seconds", 5.0))
            ),
            "safety_status": safety_status,
            "reason_codes": reason_codes,
            "evidence_words": proposal["evidence_words"],
            "evidence_text": proposal["evidence_text"],
            "corroborated_track_ids": proposal.get(
                "corroborated_track_ids", []
            ),
            "merged_repetition_proposals": proposal.get(
                "merged_repetition_proposals", 1
            ),
            "conflicting_words_by_track": conflicts,
            "boundary_policy": "whole_canonical_token_no_padding",
            "provenance": {
                "p0_report_sha256": p0_sha,
                "source_track_transcript_sha256": source_track[
                    "raw_transcript_sha256"
                ],
                "rules_sha256": rules_sha,
                "candidate_policy": rules["policy"],
            },
        }
        (reviewable if safety_status == "NEEDS_HUMAN_REVIEW" else blocked).append(
            item
        )

    all_items = sorted(
        reviewable + blocked,
        key=lambda item: (
            item["start_seconds"],
            item["source_track"],
            item["reason_key"],
            item["evidence_text"],
        ),
    )
    for index, item in enumerate(all_items, 1):
        item["candidate_id"] = f"C{index:03d}"
    reviewable = [
        item for item in all_items if item["safety_status"] == "NEEDS_HUMAN_REVIEW"
    ]
    blocked = [item for item in all_items if item["safety_status"] == "BLOCKED"]

    effective_episode_id = episode_id or str(report.get("episode_id") or "EP04")
    created = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "schema_version": "ntrack-input-v1",
        "episode_id": effective_episode_id,
        "purpose": "P0_TO_REVIEW_PRODUCT_CHALLENGER_BRIDGE",
        "note": "轨道是物理麦编号；不猜测性别或人物身份。",
        "tracks": track_manifest_entries,
    }
    source_package = {
        "schema_version": "ntrack-review-source-v1",
        "episode_id": effective_episode_id,
        "generated_at": created,
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "candidate_policy": rules["policy"],
        "safety_note": (
            "没有帧级主讲/串音 gold。所有未阻断候选都需要真人审核，"
            "绝不自动生成批准 EDL。"
        ),
        "input_provenance": {
            "p0_report_path": str(p0_report_path),
            "p0_report_sha256": p0_sha,
            "rules_path": str(rules_path),
            "rules_sha256": rules_sha,
            "track_manifest_path": str(
                (out / "tracks.manifest.json").resolve()
            ),
        },
        "counts": {
            "raw_proposals": len(raw_proposals),
            "reviewable": len(reviewable),
            "blocked": len(blocked),
        },
        "candidates": reviewable,
    }
    blocked_package = {
        "schema_version": "ntrack-blocked-candidates-v1",
        "episode_id": effective_episode_id,
        "generated_at": created,
        "source_package_sha256": sha256_bytes(source_package),
        "candidates": blocked,
    }
    report_doc = {
        "schema_version": "ntrack-episode-bridge-report-v1",
        "status": (
            "READY_FOR_REVIEW_PACKAGE"
            if reviewable
            else "READY_WITH_ZERO_REVIEWABLE_CANDIDATES"
        ),
        "episode_id": effective_episode_id,
        "track_count": len(loaded_tracks),
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "candidate_counts": source_package["counts"],
        "automatic_cutting": "DISABLED",
        "next_step": (
            "Use review-product-v1 build_mvp_package.py with tracks.manifest.json "
            "and candidate_source.json; pass --ffmpeg to regenerate A/B from every "
            "input track."
        ),
    }
    write_json(out / "tracks.manifest.json", manifest)
    write_json(out / "candidate_source.json", source_package)
    write_json(out / "blocked_candidates.json", blocked_package)
    write_json(out / "bridge_report.json", report_doc)
    return report_doc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build generic N-track review source from a P0 run"
    )
    parser.add_argument("--p0-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "rules/candidate-generation.ntrack-safe-v1.json",
    )
    parser.add_argument("--episode-id")
    parser.add_argument(
        "--created-at", help="UTC ISO timestamp for reproducible test fixtures"
    )
    args = parser.parse_args()
    result = build(
        args.p0_report,
        args.out.resolve(),
        args.rules,
        args.episode_id,
        args.created_at,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
