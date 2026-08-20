#!/usr/bin/env python3
"""Build a non-destructive sentence-boundary / punctuation hypothesis layer.

This module deliberately does not decide whether any text should be deleted.
It converts an immutable word-level ASR transcript into sentence spans that
keep an exact reference to every original word_id and its timeline position.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_VERSION = "semantic-transcript-v1"
LAYER_KIND = "sentence_boundary_and_punctuation_hypotheses"
POLICY_VERSION = "timing_text_heuristic_v1"

POLICY: dict[str, Any] = {
    "version": POLICY_VERSION,
    "sentence_gap_seconds": 0.95,
    "soft_sentence_gap_seconds": 0.55,
    "clause_gap_seconds": 0.30,
    "max_sentence_words_before_soft_break": 44,
    "max_clause_words_before_soft_break": 22,
    "minimum_gap_for_sentence_length_break_seconds": 0.12,
    "minimum_gap_for_clause_length_break_seconds": 0.02,
    "discourse_clause_leaders": [
        "然后", "所以", "但是", "不过", "而且", "其实", "因为", "如果",
        "同时", "另外", "比如", "包括", "以及", "那", "就是"
    ],
    "terminal_particle_tokens": ["吗", "么", "呢", "吧", "啊", "呀", "拜拜"],
    "boundary_confidence_contract": {
        "source_punctuation": "high",
        "timing_text_heuristic": "low",
        "transcript_end": "not_applicable"
    },
    "deletion_decision": "NOT_INCLUDED"
}

TERMINAL_PUNCTUATION = set("。！？!?")
CLAUSE_PUNCTUATION = set("，、；;：:")
TRAILING_PUNCTUATION = TERMINAL_PUNCTUATION | CLAUSE_PUNCTUATION
QUESTION_PARTICLES = {"吗", "么", "呢"}
TERMINAL_PARTICLES = set(POLICY["terminal_particle_tokens"])
DISCOURSE_CLAUSE_LEADERS = tuple(POLICY["discourse_clause_leaders"])


class ContractError(ValueError):
    """Raised when source words cannot safely support a semantic layer."""


@dataclass(frozen=True)
class Boundary:
    level: str
    punctuation: str
    reason: str
    confidence: str
    gap_seconds: float | None


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_object(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def resolve_path(value: str, report_path: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    beside_report = (report_path.parent / path).resolve()
    if beside_report.exists():
        return beside_report
    return (PROJECT_ROOT / path).resolve()


def display_token(raw_text: Any) -> str:
    """Return a display-only token; source JSON is never mutated."""
    return str(raw_text or "").strip()


def source_trailing_punctuation(text: str) -> str | None:
    stripped = text.rstrip()
    if not stripped:
        return None
    final = stripped[-1]
    if final in TERMINAL_PUNCTUATION:
        return "？" if final in {"?", "？"} else "。"
    if final in CLAUSE_PUNCTUATION:
        return "，"
    return None


def display_without_trailing_punctuation(text: str) -> str:
    value = display_token(text)
    while value and value[-1] in TRAILING_PUNCTUATION:
        value = value[:-1]
    return value


def compact_token(text: str) -> str:
    return re.sub(r"\s+", "", display_without_trailing_punctuation(text))


def is_ascii_word(text: str) -> bool:
    value = compact_token(text)
    return bool(value) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_+.#/-]*", value))


def join_visible_tokens(tokens: list[tuple[str, str]]) -> str:
    """Join words for a display hypothesis without changing source tokens.

    Whitespace is retained only between adjacent Latin/number tokens. Chinese
    word fragments are kept contiguous, matching the source ASR convention.
    """
    result = ""
    previous_raw = ""
    for raw_text, punctuation_after in tokens:
        visible = display_without_trailing_punctuation(raw_text)
        if visible:
            if result and is_ascii_word(previous_raw) and is_ascii_word(visible):
                result += " "
            result += visible
            previous_raw = visible
        if punctuation_after:
            result += punctuation_after
    return result


def last_content_token(words: list[dict[str, Any]]) -> str:
    for word in reversed(words):
        value = compact_token(str(word.get("text", "")))
        if value:
            return value
    return ""


def choose_boundary(
    sentence_words: list[dict[str, Any]],
    clause_words: list[dict[str, Any]],
    current: dict[str, Any],
    next_word: dict[str, Any] | None,
) -> Boundary | None:
    """Produce a boundary hypothesis after ``current``.

    Sentence and comma boundaries are structure hypotheses only.  The later
    candidate decision module must use them as context, not as deletion
    authorization.
    """
    raw_text = str(current.get("text", ""))
    source_mark = source_trailing_punctuation(raw_text)
    if source_mark in {"。", "？"}:
        return Boundary("sentence", source_mark, "source_terminal_punctuation", "high", None)
    if source_mark == "，":
        return Boundary("clause", "，", "source_clause_punctuation", "high", None)

    if next_word is None:
        final_token = last_content_token(sentence_words)
        mark = "？" if final_token in QUESTION_PARTICLES else "。"
        return Boundary("sentence", mark, "end_of_transcript", "not_applicable", None)

    gap = max(
        0.0,
        float(next_word["start_seconds"]) - float(current["end_seconds"]),
    )
    final_token = last_content_token(sentence_words)
    sentence_duration = float(current["end_seconds"]) - float(sentence_words[0]["start_seconds"])

    if gap >= float(POLICY["sentence_gap_seconds"]):
        mark = "？" if final_token in QUESTION_PARTICLES else "。"
        return Boundary("sentence", mark, "timing_gap_sentence", "low", round(gap, 6))
    if (
        gap >= float(POLICY["soft_sentence_gap_seconds"])
        and final_token in TERMINAL_PARTICLES
    ):
        mark = "？" if final_token in QUESTION_PARTICLES else "。"
        return Boundary("sentence", mark, "terminal_particle_with_pause", "low", round(gap, 6))
    if (
        len(sentence_words) >= int(POLICY["max_sentence_words_before_soft_break"])
        and gap >= float(POLICY["minimum_gap_for_sentence_length_break_seconds"])
    ):
        return Boundary("sentence", "。", "long_sentence_pause", "low", round(gap, 6))
    if gap >= float(POLICY["clause_gap_seconds"]):
        return Boundary("clause", "，", "timing_gap_clause", "low", round(gap, 6))
    next_token = compact_token(str(next_word.get("text", "")))
    if (
        len(clause_words) >= 5
        and any(next_token.startswith(marker) for marker in DISCOURSE_CLAUSE_LEADERS)
    ):
        return Boundary("clause", "，", "discourse_leader_clause", "low", round(gap, 6))
    if (
        len(clause_words) >= int(POLICY["max_clause_words_before_soft_break"])
        and gap >= float(POLICY["minimum_gap_for_clause_length_break_seconds"])
        and sentence_duration >= 2.0
    ):
        return Boundary("clause", "，", "long_clause_pause", "low", round(gap, 6))
    return None


def validate_source_transcript(doc: dict[str, Any], expected_track_id: str, expected_rate: int, expected_frames: int) -> list[dict[str, Any]]:
    if doc.get("schema_version") != "ntrack-transcript-v1":
        raise ContractError("source transcript schema must be ntrack-transcript-v1")
    if str(doc.get("track_id", "")) != expected_track_id:
        raise ContractError("source transcript track_id does not match report")
    if int(doc.get("sample_rate_hz", -1)) != expected_rate:
        raise ContractError("source transcript sample_rate_hz does not match report")
    if int(doc.get("frame_count", -1)) != expected_frames:
        raise ContractError("source transcript frame_count does not match report")
    words = doc.get("words")
    if not isinstance(words, list) or not words:
        raise ContractError("source transcript words must be a non-empty array")

    seen: set[str] = set()
    previous_start = -1.0
    duration = expected_frames / expected_rate
    for position, word in enumerate(words, 1):
        if not isinstance(word, dict):
            raise ContractError(f"word {position} must be an object")
        word_id = str(word.get("word_id", ""))
        if not word_id or word_id in seen:
            raise ContractError(f"word {position} has missing or duplicate word_id")
        seen.add(word_id)
        try:
            start = float(word["start_seconds"])
            end = float(word["end_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"word {word_id} has invalid timestamps") from exc
        if start < -0.05 or end <= start or end > duration + 0.25:
            raise ContractError(f"word {word_id} has unsafe interval {start:.6f}-{end:.6f}")
        if start + 0.25 < previous_start:
            raise ContractError(f"word {word_id} starts non-monotonically")
        previous_start = max(previous_start, start)
    return words


def sample_from_seconds(seconds: float, sample_rate_hz: int, frame_count: int) -> int:
    return max(0, min(frame_count, int(round(seconds * sample_rate_hz))))


def build_track_semantic(
    transcript: dict[str, Any],
    transcript_path: Path,
    *,
    expected_track_id: str,
    expected_rate: int,
    expected_frames: int,
) -> dict[str, Any]:
    words = validate_source_transcript(
        transcript, expected_track_id, expected_rate, expected_frames
    )
    track_id = expected_track_id
    source_positions = {str(word["word_id"]): index for index, word in enumerate(words)}
    sentences: list[dict[str, Any]] = []
    word_context_index: dict[str, dict[str, Any]] = {}
    current_words: list[dict[str, Any]] = []
    current_clause_words: list[dict[str, Any]] = []
    completed_clauses: list[tuple[list[dict[str, Any]], Boundary]] = []
    punctuation_after: dict[str, Boundary] = {}
    sentence_number = 0

    def boundary_record(boundary: Boundary) -> dict[str, Any]:
        return {
            "punctuation": boundary.punctuation,
            "reason": boundary.reason,
            "confidence": boundary.confidence,
            "gap_seconds": boundary.gap_seconds,
        }

    for index, word in enumerate(words):
        current_words.append(word)
        current_clause_words.append(word)
        next_word = words[index + 1] if index + 1 < len(words) else None
        boundary = choose_boundary(current_words, current_clause_words, word, next_word)
        if boundary is None:
            continue
        punctuation_after[str(word["word_id"])] = boundary
        if boundary.level == "clause":
            completed_clauses.append((current_clause_words, boundary))
            current_clause_words = []
            continue
        if boundary.level != "sentence":
            raise ContractError(f"unknown boundary level: {boundary.level}")

        completed_clauses.append((current_clause_words, boundary))

        sentence_number += 1
        sentence_id = f"{track_id}:s{sentence_number:06d}"
        sentence_word_ids = [str(item["word_id"]) for item in current_words]
        clauses: list[dict[str, Any]] = []
        clause_context: dict[str, dict[str, Any]] = {}
        clause_breaks: list[dict[str, Any]] = []
        for clause_number, (clause_words, clause_boundary) in enumerate(completed_clauses, 1):
            if not clause_words:
                raise ContractError("semantic clause cannot be empty")
            clause_id = f"{sentence_id}:c{clause_number:03d}"
            clause_word_ids = [str(item["word_id"]) for item in clause_words]
            clause_visible_tokens = []
            for item in clause_words:
                item_boundary = punctuation_after.get(str(item["word_id"]))
                clause_visible_tokens.append((
                    str(item.get("text", "")),
                    item_boundary.punctuation if item_boundary is not None else "",
                ))
            clause_start = float(clause_words[0]["start_seconds"])
            clause_end = float(clause_words[-1]["end_seconds"])
            clause = {
                "clause_id": clause_id,
                "track_id": track_id,
                "word_id_start": clause_word_ids[0],
                "word_id_end": clause_word_ids[-1],
                "word_ids": clause_word_ids,
                "word_index_start": source_positions[clause_word_ids[0]],
                "word_index_end": source_positions[clause_word_ids[-1]],
                "start_seconds": round(clause_start, 6),
                "end_seconds": round(clause_end, 6),
                "start_sample": sample_from_seconds(clause_start, expected_rate, expected_frames),
                "end_sample": sample_from_seconds(clause_end, expected_rate, expected_frames),
                "raw_text_joined": "".join(str(item.get("text", "")) for item in clause_words),
                "text_punctuated": join_visible_tokens(clause_visible_tokens),
                "boundary_after": boundary_record(clause_boundary),
                "boundary_method": POLICY_VERSION,
                "decision_scope": "CONTEXT_ONLY_NOT_A_DELETION_DECISION",
            }
            clauses.append(clause)
            if clause_boundary.level == "clause":
                clause_breaks.append({
                    "after_word_id": clause_word_ids[-1],
                    **boundary_record(clause_boundary),
                })
            for position_in_clause, item in enumerate(clause_words):
                clause_context[str(item["word_id"])] = {
                    "clause_id": clause_id,
                    "position_in_clause": position_in_clause,
                    "clause_word_count": len(clause_word_ids),
                    "clause_word_id_start": clause_word_ids[0],
                    "clause_word_id_end": clause_word_ids[-1],
                    "clause_start_sample": clause["start_sample"],
                    "clause_end_sample": clause["end_sample"],
                }

        visible_tokens: list[tuple[str, str]] = []
        for item in current_words:
            item_id = str(item["word_id"])
            item_boundary = punctuation_after.get(item_id)
            mark = item_boundary.punctuation if item_boundary is not None else ""
            visible_tokens.append((str(item.get("text", "")), mark))

        start = float(current_words[0]["start_seconds"])
        end = float(current_words[-1]["end_seconds"])
        sentence = {
            "sentence_id": sentence_id,
            "track_id": track_id,
            "word_id_start": sentence_word_ids[0],
            "word_id_end": sentence_word_ids[-1],
            "word_ids": sentence_word_ids,
            "word_index_start": source_positions[sentence_word_ids[0]],
            "word_index_end": source_positions[sentence_word_ids[-1]],
            "start_seconds": round(start, 6),
            "end_seconds": round(end, 6),
            "start_sample": sample_from_seconds(start, expected_rate, expected_frames),
            "end_sample": sample_from_seconds(end, expected_rate, expected_frames),
            "raw_text_joined": "".join(str(item.get("text", "")) for item in current_words),
            "text_punctuated": join_visible_tokens(visible_tokens),
            "clauses": clauses,
            "clause_breaks": clause_breaks,
            "boundary_after": boundary_record(boundary),
            "boundary_method": POLICY_VERSION,
            "decision_scope": "CONTEXT_ONLY_NOT_A_DELETION_DECISION",
        }
        sentences.append(sentence)
        for position_in_sentence, item in enumerate(current_words):
            item_id = str(item["word_id"])
            item_boundary = punctuation_after.get(item_id)
            if item_id not in clause_context:
                raise ContractError(f"word {item_id} has no clause context")
            word_context_index[item_id] = {
                "sentence_id": sentence_id,
                "position_in_sentence": position_in_sentence,
                "sentence_word_count": len(sentence_word_ids),
                "sentence_word_id_start": sentence_word_ids[0],
                "sentence_word_id_end": sentence_word_ids[-1],
                "sentence_start_sample": sentence["start_sample"],
                "sentence_end_sample": sentence["end_sample"],
                "punctuation_after": item_boundary.punctuation if item_boundary else "",
                "boundary_after_reason": item_boundary.reason if item_boundary else None,
                "boundary_after_confidence": item_boundary.confidence if item_boundary else None,
                **clause_context[item_id],
            }
        current_words = []
        current_clause_words = []
        completed_clauses = []
        punctuation_after = {}

    if current_words or current_clause_words or completed_clauses:
        raise ContractError("sentence builder did not close final sentence")

    source_word_ids = [str(word["word_id"]) for word in words]
    output_word_ids = [word_id for sentence in sentences for word_id in sentence["word_ids"]]
    coverage_ok = source_word_ids == output_word_ids
    if not coverage_ok or len(word_context_index) != len(source_word_ids):
        raise ContractError("semantic output did not preserve source word coverage/order")

    source = {
        "path": str(transcript_path.resolve()),
        "sha256": sha256_file(transcript_path),
        "track_id": track_id,
        "sample_rate_hz": expected_rate,
        "frame_count": expected_frames,
        "word_count": len(words),
        "engine": transcript.get("engine"),
        "model_ref": transcript.get("model_ref"),
        "source_audio_sha256": transcript.get("source_audio_sha256")
    }
    semantic_view = {
        "schema_version": SCHEMA_VERSION,
        "layer_kind": LAYER_KIND,
        "source_transcript": source,
        "policy": POLICY,
        "sentences": sentences,
        "word_context_index": word_context_index,
        "integrity": {
            "source_word_ids_sha256": sha256_object(source_word_ids),
            "output_word_ids_sha256": sha256_object(output_word_ids),
            "word_count_matches": len(source_word_ids) == len(output_word_ids),
            "word_order_and_coverage_matches": coverage_ok,
            "time_mapping": "sentence start/end are derived from first/last source word seconds, rounded to source sample rate"
        },
        "out_of_scope": {
            "deletion_decision": "NOT_INCLUDED",
            "candidate_generation": "NOT_INCLUDED",
            "edl_generation": "NOT_INCLUDED",
            "audio_modification": "NOT_INCLUDED"
        }
    }
    # File paths are provenance for a local run, not semantic content.  The
    # content hash must stay stable if the exact same transcript is moved or
    # replayed from a different temporary directory.
    semantic_hash_view = {
        **semantic_view,
        "source_transcript": {
            key: value for key, value in source.items() if key != "path"
        }
    }
    result = {
        **semantic_view,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "semantic_content_sha256": sha256_object(semantic_hash_view)
    }
    return result


def load_report(report_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != "p0-mvp-report-v1":
        raise ContractError("input report must be p0-mvp-report-v1")
    if report.get("engineering_gate") != "PASS":
        raise ContractError("input report engineering_gate must be PASS")
    tracks = report.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ContractError("input report must declare non-empty tracks")
    if int(report.get("track_count", -1)) != len(tracks):
        raise ContractError("input report track_count mismatch")
    rate = int(report.get("sample_rate_hz", 0))
    frames = int(report.get("frame_count", 0))
    if rate <= 0 or frames <= 0:
        raise ContractError("input report has invalid shared timeline")
    seen: set[str] = set()
    loaded = []
    for declared in tracks:
        track_id = str(declared.get("track_id", ""))
        if not track_id or track_id in seen:
            raise ContractError("input report track_id must be unique and non-empty")
        seen.add(track_id)
        transcript_path = resolve_path(str(declared.get("transcript_path", "")), report_path)
        if not transcript_path.is_file():
            raise ContractError(f"source transcript missing for {track_id}: {transcript_path}")
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        loaded.append({
            "track_id": track_id,
            "transcript_path": transcript_path,
            "transcript": transcript,
            "sample_rate_hz": rate,
            "frame_count": frames
        })
    return report, loaded


def markdown_report(manifest: dict[str, Any]) -> str:
    lines = [
        "# 语义分句 / 标点假设层运行报告",
        "",
        f"- Run：`{manifest['run_id']}`",
        f"- Episode：`{manifest['episode_id']}`",
        f"- 来源 run：`{manifest['source_run_id']}`",
        f"- 输入 P0 报告 SHA-256：`{manifest['input_report']['sha256']}`",
        f"- 生成方法：`{POLICY_VERSION}`（仅句界/标点假设）",
        "",
        "## 结果",
        "",
        "| 轨道 | 输入词数 | 句子数 | 原词覆盖/顺序 | 输出 SHA-256 |",
        "| --- | ---: | ---: | --- | --- |"
    ]
    for item in manifest["outputs"]:
        integrity = item["integrity"]
        lines.append(
            f"| `{item['track_id']}` | {item['word_count']} | {item['sentence_count']} | "
            f"{integrity['word_order_and_coverage_matches']} | `{item['sha256']}` |"
        )
    lines.extend([
        "",
        "## 边界",
        "",
        "- 每个 sentence 仅引用原始 `word_id`，不修改任何 ASR 词、词序或时间线。",
        "- 每个候选词可经 `word_context_index[word_id]` 找到完整句范围与分句标记。",
        "- 本 run 不生成候选、不判定该不该删、不生成 EDL，也不改动音频。",
        "- 启发式标点只是假设；后续删剪判断模块必须结合完整句、重复模式和声学上下文，且仍受真人审核边界约束。",
        ""
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    report_path = args.input_report.resolve()
    if not report_path.is_file():
        raise SystemExit(f"input report missing: {report_path}")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refuse to overwrite non-empty output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)

    try:
        report, loaded = load_report(report_path)
        outputs = []
        for item in loaded:
            semantic = build_track_semantic(
                item["transcript"], item["transcript_path"],
                expected_track_id=item["track_id"],
                expected_rate=item["sample_rate_hz"],
                expected_frames=item["frame_count"]
            )
            out_path = out / "semantic_transcripts" / f"{item['track_id']}.semantic.json"
            write_json(out_path, semantic)
            outputs.append({
                "track_id": item["track_id"],
                "path": str(out_path.relative_to(out)),
                "sha256": sha256_file(out_path),
                "word_count": semantic["source_transcript"]["word_count"],
                "sentence_count": len(semantic["sentences"]),
                "semantic_content_sha256": semantic["semantic_content_sha256"],
                "integrity": semantic["integrity"]
            })
        manifest = {
            "schema_version": "semantic-transcript-run-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": args.run_id,
            "episode_id": args.episode_id,
            "source_run_id": args.source_run_id,
            "status": "PASS",
            "input_report": {
                "path": str(report_path),
                "sha256": sha256_file(report_path),
                "quality_gate_at_source": report.get("quality_gate"),
                "engineering_gate_at_source": report.get("engineering_gate")
            },
            "generator": {
                "path": str(Path(__file__).resolve()),
                "sha256": sha256_file(Path(__file__).resolve()),
                "policy_version": POLICY_VERSION
            },
            "scope": {
                "sentence_boundary_and_punctuation": "GENERATED",
                "deletion_decision": "NOT_INCLUDED",
                "candidate_generation": "NOT_INCLUDED",
                "edl_generation": "NOT_INCLUDED",
                "audio_modification": "NOT_INCLUDED"
            },
            "outputs": outputs
        }
        write_json(out / "manifest.json", manifest)
        (out / "README.md").write_text(markdown_report(manifest), encoding="utf-8")
    except ContractError as exc:
        raise SystemExit(f"semantic transcript contract failed: {exc}") from exc

    print(json.dumps({"status": "PASS", "out": str(out), "tracks": len(outputs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
