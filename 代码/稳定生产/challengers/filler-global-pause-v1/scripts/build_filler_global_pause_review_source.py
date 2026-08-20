#!/usr/bin/env python3
"""FROZEN 2026-08-19 · LLM Takeover

用户 2026-08-19 evening 明确: LLM 完全主导 candidate 生成 + 判决 (Stage 3.5.5).
本文件冻结 · 保留代码作 fallback · 不再是主流水消费者.
详见: 交付/最终交付文档/统筹全局/DEPRECATED_LLM_TAKEOVER_2026-08-19.md

** 何时会被 pipeline 消费 **:
- 若 Stage 3.5.5 LLM 挂 (3 mode 全挂)
- 或 --no-auto-llm-full-pipeline 明确 opt-out
- 否则 pipeline 走 LLM 主导 · 本文件 idle

---

Build review-only filler and global-long-pause candidates for N-track audio.

This isolated Challenger never creates an EDL and never alters audio.  A
long-pause candidate is allowed only where *all* physical tracks are empty in
the word timeline and every source WAV is acoustically quiet in the same
interval.  That second check intentionally catches ASR-blind events such as a
cough, mic bump, or other transient.
"""

from __future__ import annotations

import argparse
import audioop
import hashlib
import json
import math
import re
import struct
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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    from_report = (base.parent / path).resolve()
    return from_report if from_report.exists() else (PROJECT_ROOT / path).resolve()


def clean_token(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").casefold()


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
    raw_words: list[dict[str, Any]], track_id: str
) -> list[dict[str, Any]]:
    """Copy ASR words into display tokens without changing raw transcripts."""
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_words, 1):
        result.append(
            {
                "word_id": f"{track_id}:n{index:06d}",
                "text": display_text(str(raw.get("text", ""))),
                "start_seconds": float(raw["start_seconds"]),
                "end_seconds": float(raw["end_seconds"]),
                "probability": (
                    float(raw["probability"])
                    if raw.get("probability") is not None
                    else None
                ),
                "classification": "unknown",
                "raw_word_ids": [str(raw.get("word_id", ""))],
                "raw_texts": [str(raw.get("text", ""))],
            }
        )
    return result


def load_semantic_context(
    semantic_dir: Path | None,
    track_ids: Iterable[str],
    expected_rate: int,
    expected_frames: int,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load the optional sentence/clause context without changing ASR words.

    The semantic layer is deliberately a separate, non-destructive input.  Its
    keys are the immutable raw ASR word ids; the candidate builder uses it only
    as a sentence-position safety gate and never as an authorization to cut.
    """

    if semantic_dir is None:
        return {}
    semantic_dir = semantic_dir.expanduser().resolve()
    contexts: dict[str, dict[str, dict[str, Any]]] = {}
    for track_id in track_ids:
        path = semantic_dir / f"{track_id}.semantic.json"
        if not path.is_file():
            raise SystemExit(f"semantic transcript is missing for {track_id}: {path}")
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_version") != "semantic-transcript-v1":
            raise SystemExit(f"semantic transcript schema is invalid for {track_id}")
        source = document.get("source_transcript") or {}
        if int(source.get("sample_rate_hz", -1)) != expected_rate:
            raise SystemExit(f"semantic transcript sample rate mismatch for {track_id}")
        index = document.get("word_context_index")
        if not isinstance(index, dict) or not index:
            raise SystemExit(f"semantic transcript has no word_context_index for {track_id}")
        contexts[track_id] = {
            str(word_id): dict(value)
            for word_id, value in index.items()
            if isinstance(value, dict)
        }
        if not contexts[track_id]:
            raise SystemExit(f"semantic transcript context is empty for {track_id}")
    return contexts


def raw_word_ids(words: Iterable[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for word in words:
        values = word.get("raw_word_ids") or [word.get("word_id", "")]
        result.extend(str(value) for value in values if str(value))
    return result


def clause_position_for_proposal(
    proposal: dict[str, Any],
    semantic_context: dict[str, dict[str, Any]],
    head_tail_word_window: int = 1,
) -> str:
    """Classify a proposal relative to its semantic clause.

    This is a guard, not a punctuation model: missing context is explicitly
    returned as ``unknown`` so a conservative v16 rule can block it.
    """

    ids = raw_word_ids(proposal.get("evidence_words") or [])
    if not ids:
        return "unknown"
    items = [semantic_context.get(word_id) for word_id in ids]
    if any(item is None for item in items):
        return "unknown"
    clause_ids = {str(item.get("clause_id", "")) for item in items if item}
    if len(clause_ids) != 1:
        return "cross-clause"
    positions = [int(item.get("position_in_clause", -1)) for item in items if item]
    counts = [int(item.get("clause_word_count", 0)) for item in items if item]
    if not positions or not counts or any(position < 0 or count <= 0 for position, count in zip(positions, counts)):
        return "unknown"
    count = counts[0]
    head = min(positions) < max(1, head_tail_word_window)
    tail = max(positions) >= count - max(1, head_tail_word_window)
    if head and tail:
        return "clause-tail-or-head"
    if head:
        return "clause-head"
    if tail:
        return "clause-tail"
    return "clause-mid"


def lexical_context_for_proposal(
    proposal: dict[str, Any], words: list[dict[str, Any]]
) -> dict[str, dict[str, Any] | None]:
    """Expose only adjacent ASR tokens for exact false-positive guards.

    This does not rewrite, merge, or correct the upstream transcript.  It gives
    a frozen downstream policy enough evidence to recognize a known ASR split
    such as ``额`` + ``度`` or a Latin fragment embedded in an English word.
    """

    deleted_ids = {
        str(word.get("word_id") or "")
        for word in proposal.get("proposed_delete_words") or []
    }
    indexes = [
        index for index, word in enumerate(words)
        if str(word.get("word_id") or "") in deleted_ids
    ]
    if not indexes:
        return {"before": None, "after": None}
    first, last = min(indexes), max(indexes)
    start = float(proposal["start_seconds"])
    end = float(proposal["end_seconds"])

    def compact(word: dict[str, Any], gap: float) -> dict[str, Any]:
        return {
            "word_id": word.get("word_id"),
            "text": str(word.get("text") or ""),
            "gap_seconds": round(max(0.0, gap), 6),
        }

    return {
        "before": (
            compact(words[first - 1], start - float(words[first - 1]["end_seconds"]))
            if first > 0 else None
        ),
        "after": (
            compact(words[last + 1], float(words[last + 1]["start_seconds"]) - end)
            if last + 1 < len(words) else None
        ),
    }


def read_p0_inputs(report_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != "p0-mvp-report-v1":
        raise SystemExit("only p0-mvp-report-v1 is accepted")
    if report.get("engineering_gate") != "PASS":
        raise SystemExit("P0 engineering_gate is not PASS; do not generate candidates")
    declared_tracks = report.get("tracks") or []
    if not declared_tracks or int(report.get("track_count", 0)) != len(declared_tracks):
        raise SystemExit("P0 report has invalid track list")
    track_ids = [str(track.get("track_id", "")) for track in declared_tracks]
    if not all(track_ids) or len(track_ids) != len(set(track_ids)):
        raise SystemExit("P0 report track_id must be non-empty and unique")

    expected_rate = int(report["sample_rate_hz"])
    expected_frames = int(report["frame_count"])
    duration = expected_frames / expected_rate
    loaded: list[dict[str, Any]] = []
    for declared in declared_tracks:
        track_id = str(declared["track_id"])
        transcript_path = resolve_path(str(declared["transcript_path"]), report_path)
        if not transcript_path.is_file():
            raise SystemExit(f"P0 transcript is missing: {transcript_path}")
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        if (
            transcript.get("schema_version") != "ntrack-transcript-v1"
            or transcript.get("track_id") != track_id
        ):
            raise SystemExit(f"P0 transcript does not match report: {track_id}")
        if (
            int(transcript.get("sample_rate_hz", -1)) != expected_rate
            or int(transcript.get("frame_count", -1)) != expected_frames
        ):
            raise SystemExit(f"P0 transcript time base mismatch: {track_id}")
        audio_path = Path(str(transcript.get("source_audio_path", ""))).expanduser()
        if not audio_path.is_file():
            raise SystemExit(f"P0 source audio is missing: {audio_path}")
        raw_words = transcript.get("words") or []
        validate_raw_words(raw_words, duration, track_id)
        loaded.append(
            {
                "track_id": track_id,
                "label": str(declared.get("label") or transcript.get("label") or track_id),
                "audio_path": audio_path.resolve(),
                "audio_sha256": sha256_file(audio_path),
                "raw_transcript_path": transcript_path.resolve(),
                "raw_transcript_sha256": sha256_file(transcript_path),
                "raw_words": raw_words,
            }
        )
    return report, loaded


def parse_wav_pcm(path: Path) -> dict[str, int]:
    """Read PCM / PCM-extensible mono WAV metadata without rewriting the WAV."""
    with path.open("rb") as handle:
        if handle.read(4) != b"RIFF":
            raise SystemExit(f"not RIFF WAV: {path}")
        handle.seek(4, 1)
        if handle.read(4) != b"WAVE":
            raise SystemExit(f"not WAVE: {path}")
        fmt: tuple[int, int, int, int] | None = None
        data_offset: int | None = None
        data_size: int | None = None
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", header)
            payload_start = handle.tell()
            if chunk_id == b"fmt ":
                raw = handle.read(size)
                if len(raw) < 16:
                    raise SystemExit(f"truncated WAV fmt: {path}")
                tag, channels, rate, _, block_align, bits = struct.unpack(
                    "<HHIIHH", raw[:16]
                )
                if tag == 0xFFFE and len(raw) >= 26:
                    tag = struct.unpack("<H", raw[24:26])[0]
                if tag != 1:
                    raise SystemExit(f"only PCM WAV is supported, format={tag}: {path}")
                fmt = channels, rate, block_align, bits
            elif chunk_id == b"data":
                data_offset = payload_start
                data_size = size
            handle.seek(payload_start + size + (size & 1))
    if fmt is None or data_offset is None or data_size is None:
        raise SystemExit(f"missing WAV fmt/data: {path}")
    channels, rate, block_align, bits = fmt
    if channels != 1 or bits not in (16, 24, 32):
        raise SystemExit(f"only mono 16/24/32-bit PCM WAV is supported: {path}")
    return {
        "sample_rate_hz": rate,
        "frame_count": data_size // block_align,
        "bits_per_sample": bits,
        "bytes_per_frame": block_align,
        "data_offset": data_offset,
    }


def dbfs(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-12))


def acoustic_quiet_check(
    audio_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    expected_sample_rate: int,
    expected_frame_count: int,
    frame_seconds: float,
    max_rms_dbfs: float,
    max_peak_dbfs: float,
) -> dict[str, Any]:
    """Fail closed if a wordless region contains audible acoustic activity."""
    info = parse_wav_pcm(audio_path)
    if (
        info["sample_rate_hz"] != expected_sample_rate
        or info["frame_count"] != expected_frame_count
    ):
        raise SystemExit(f"source audio timeline changed: {audio_path}")
    rate = info["sample_rate_hz"]
    first_frame = max(0, min(info["frame_count"], round(start_seconds * rate)))
    last_frame = max(first_frame, min(info["frame_count"], round(end_seconds * rate)))
    samples_per_window = max(1, round(frame_seconds * rate))
    width = info["bits_per_sample"] // 8
    worst_rms, worst_peak = -240.0, -240.0
    loud_windows = 0
    first_loud_at: float | None = None
    with audio_path.open("rb") as handle:
        for sample_start in range(first_frame, last_frame, samples_per_window):
            count = min(samples_per_window, last_frame - sample_start)
            handle.seek(info["data_offset"] + sample_start * info["bytes_per_frame"])
            raw = handle.read(count * info["bytes_per_frame"])
            if len(raw) != count * info["bytes_per_frame"]:
                raise SystemExit(f"truncated WAV data: {audio_path}")
            rms = dbfs(audioop.rms(raw, width) / float(1 << (8 * width - 1)))
            peak = dbfs(audioop.max(raw, width) / float(1 << (8 * width - 1)))
            worst_rms, worst_peak = max(worst_rms, rms), max(worst_peak, peak)
            if rms > max_rms_dbfs or peak > max_peak_dbfs:
                loud_windows += 1
                if first_loud_at is None:
                    first_loud_at = sample_start / rate
    return {
        "quiet": loud_windows == 0,
        "window_seconds": samples_per_window / rate,
        "max_frame_rms_dbfs": round(worst_rms, 2),
        "max_frame_peak_dbfs": round(worst_peak, 2),
        "loud_window_count": loud_windows,
        "first_loud_seconds": (
            round(first_loud_at, 6) if first_loud_at is not None else None
        ),
        "thresholds": {
            "max_frame_rms_dbfs": max_rms_dbfs,
            "max_frame_peak_dbfs": max_peak_dbfs,
        },
    }


def contiguous(words: list[dict[str, Any]], max_gap_seconds: float) -> bool:
    return all(
        float(next_word["start_seconds"]) - float(word["end_seconds"])
        <= max_gap_seconds
        for word, next_word in zip(words, words[1:])
    )


SEGMENT_SEPARATOR_TOKENS = {
    "第一", "第二", "第三", "第四", "第五", "首先", "然后", "其次",
    "另外", "另一方面", "接下来", "最后", "总结", "综上",
    "一方面", "第一个", "第二个", "第三个", "第四个", "第五个",
    "one", "two", "three", "first", "second", "third", "finally",
}


def _is_english_fragment(text: str) -> bool:
    """判定 token 是否是英文字母片段（1-4 chars，如 GoGoFlow 里的 'go'）。
    这类 token 是 ASR 拆合成词的 artifact，重复无意义（'go go flow' ≠ '重复 go'）。"""
    t = str(text or "").strip()
    if not t:
        return False
    return len(t) <= 4 and all(c.isascii() and c.isalpha() for c in t)


def _english_fragment_context(words: list[dict[str, Any]], center_idx: int) -> bool:
    """v20.2 english_fragment_context_guard (2026-08-17 v25 feedback for C014):
    Check if the token at center_idx is an English fragment surrounded by other
    English fragments (i.e. part of a compound like 'GoGoFlow', 'AIOps', etc).
    If so, this is NOT a repetition candidate — it's ASR word-boundary noise."""
    if center_idx < 0 or center_idx >= len(words):
        return False
    center = clean_token(str(words[center_idx].get("text", "")))
    if not _is_english_fragment(center):
        return False
    # look ±2 words for other English fragments
    neighbors_english = 0
    for j in range(max(0, center_idx - 2), min(len(words), center_idx + 3)):
        if j == center_idx:
            continue
        nb = clean_token(str(words[j].get("text", "")))
        if _is_english_fragment(nb):
            neighbors_english += 1
    return neighbors_english >= 1


# v20.5 host backchannel guard (2026-08-18, S · C4 alternative to pyannote):
# 主持人认真听嘉宾时的短应答 "嗯/啊/对/是的" 不该作为口癖候选剪掉.
# 数据源: main/knowledge/speaker_maps/{episode_id}.speaker_map.json (人工声明每轨角色).
HOST_BACKCHANNEL_TOKENS = {"嗯", "啊", "对", "对对", "是", "是的", "好", "好的", "唉"}


# v20.7 cross_track_backchannel (2026-08-18): 用户明确 GF05 事件 · guest_A 的
# "嗯" 在 guest_B 说话时是**跨轨附和**, 不管 speaker_role. 这类候选 never_cut.
def _is_cross_track_backchannel(
    words: list[dict[str, Any]],
    center_idx: int,
    track_id: str,
    other_tracks_words: dict[str, list[dict]] | None = None,
    window_s: float = 3.0,
) -> bool:
    """通用跨轨 backchannel 检测: 短应答 token + 其他轨在附近 window_s 内有语音."""
    if center_idx < 0 or center_idx >= len(words) or not other_tracks_words:
        return False
    center_word = words[center_idx]
    token = clean_token(str(center_word.get("text", "")))
    if token not in HOST_BACKCHANNEL_TOKENS:
        return False
    center_start = float(center_word.get("start_seconds") or 0)
    center_end = float(center_word.get("end_seconds") or center_start)
    # 检查其他轨在 ±window_s 是否有内容 (words)
    for other_tid, other_words in other_tracks_words.items():
        if other_tid == track_id:
            continue
        for ow in other_words:
            ow_s = float(ow.get("start_seconds") or 0)
            ow_e = float(ow.get("end_seconds") or ow_s)
            if ow_e < center_start - window_s:
                continue
            if ow_s > center_end + window_s:
                break
            if ow_s < center_end + window_s and ow_e > center_start - window_s:
                return True
    return False


# v20.6 Q5 · 代词/疑问词豁免 (2026-08-18):
# "什么/怎么/哪里/那个/这个" 类代词重复是自然语气加强, 观感好, **永远不剪**.
# v20.8 (2026-08-18 update): 移除 chain<=2 限制 · 用户第 3 次明确: "作为代词作为代词",
# 不管 chain 3+, 都是自然语气 · 类型化不 per-word.
PRONOUN_LIKE_REPETITIONS = {
    "什么", "怎么", "哪里", "哪个", "那个", "这个",
    "谁", "为什么", "什么什么", "这里", "那里", "怎么样",
}


def _pronoun_like_exemption(token: str, chain_length: int = 0) -> bool:
    """v20.8 · 代词/疑问词永远不生成候选 (无 chain 限制)."""
    return token in PRONOUN_LIKE_REPETITIONS


# v20.6 Q3 · ASR 类型化 probability gate (2026-08-18):
# faster-whisper 每词有 probability. 对所有 filler 类候选生效, 挡低置信度幻觉.
# '额' 是英文 ASR 幻觉的典型 · 但方法不 per-word, 对所有 token 生效.
FILLER_MIN_PROBABILITY = 0.60


def _low_confidence_filler_guard(word: dict, min_prob: float = FILLER_MIN_PROBABILITY) -> bool:
    """返回 True 若 word 的 ASR 置信度太低, 应挡 filler 候选生成.

    支持多种 probability 字段名 (兼容旧 ASR 输出):
      - probability (faster-whisper)
      - prob
      - confidence
    无 probability 字段 → 返回 False (向后兼容旧 transcript.json).
    """
    for key in ("probability", "prob", "confidence"):
        val = word.get(key)
        if val is not None:
            try:
                return float(val) < min_prob
            except (TypeError, ValueError):
                return False
    return False


def _is_host_backchannel(
    words: list[dict[str, Any]],
    center_idx: int,
    speaker_map: dict[str, Any] | None,
    track_id: str,
    other_tracks_words: dict[str, list[dict]] | None = None,
) -> bool:
    """判定当前 word 是不是主持人 backchannel (拦候选生成).

    Args:
        words: 当前 track 的 ASR 词序列
        center_idx: 当前 word 索引
        speaker_map: main/knowledge/speaker_maps/<episode>.speaker_map.json 内容
        track_id: 当前 track (如 track_03)
        other_tracks_words: {track_id: [words]} 其他轨的 ASR 词, 用于判断"嘉宾正在讲话"

    Returns True 若:
        - 当前 track 在 speaker_map 里角色 = host
        - 当前 token 在 backchannel token 白名单里
        - 附近 ±3s 其他轨有语音 (证明主持人在听嘉宾)
    """
    if not speaker_map:
        return False
    role = (speaker_map.get("map", {}).get(track_id, {}) or {}).get("role")
    if role != "host":
        return False
    if center_idx < 0 or center_idx >= len(words):
        return False
    center_word = words[center_idx]
    token = clean_token(str(center_word.get("text", "")))
    if token not in HOST_BACKCHANNEL_TOKENS:
        return False
    # 检查附近 ±3s 其他轨有语音
    center_start = float(center_word.get("start_seconds") or 0)
    center_end = float(center_word.get("end_seconds") or center_start)
    if other_tracks_words:
        for other_tid, other_words in other_tracks_words.items():
            if other_tid == track_id:
                continue
            for ow in other_words:
                ow_s = float(ow.get("start_seconds") or 0)
                ow_e = float(ow.get("end_seconds") or ow_s)
                # 其他轨在 backchannel 窗 ±3s 内有词 → 嘉宾正在讲话
                if ow_e < center_start - 3.0:
                    continue
                if ow_s > center_end + 3.0:
                    break
                if ow_s < center_end + 3.0 and ow_e > center_start - 3.0:
                    return True
        return False
    # 若没提供 other_tracks_words, host role + backchannel token 已足够挡
    return True




def _neighboring_segment_separator(
    words: list[dict[str, Any]], delete_words: list[dict[str, Any]]
) -> str | None:
    """Return the neighboring segment-separator token (like 第三/首先/然后) if
    the deletion is immediately before or after one. Used to signal downstream
    that a self-paced pause should be preserved in place of the deleted token
    to keep the natural rhythm of layered enumeration."""
    if not delete_words or not words:
        return None
    first_del = delete_words[0]
    last_del = delete_words[-1]
    try:
        first_idx = words.index(first_del)
        last_idx = words.index(last_del)
    except ValueError:
        return None
    for offset in (-2, -1, 1, 2):
        j = last_idx + offset if offset > 0 else first_idx + offset
        if 0 <= j < len(words):
            tok = clean_token(str(words[j].get("text", "")))
            if tok in SEGMENT_SEPARATOR_TOKENS:
                return tok
    return None


def proposal_from_words(
    source_track: str,
    evidence_words: list[dict[str, Any]],
    subtype: str,
    proposed_delete_words: list[dict[str, Any]] | None = None,
    all_words: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    delete_words = proposed_delete_words or evidence_words
    proposal = {
        "kind": "filler_hesitation",
        "reason_key": "filler_hesitation",
        "filler_subtype": subtype,
        "filler_token": clean_token(evidence_words[0]["text"]),
        "source_track": source_track,
        "start_seconds": float(delete_words[0]["start_seconds"]),
        "end_seconds": float(delete_words[-1]["end_seconds"]),
        "evidence_words": evidence_words,
        "proposed_delete_words": delete_words,
        "retained_evidence_words": [
            word for word in evidence_words if word not in delete_words
        ],
        "evidence_text": "".join(
            clean_token(word["text"]) for word in evidence_words
        ),
        "proposed_delete_text": "".join(
            clean_token(word["text"]) for word in delete_words
        ),
        # v20 feedback (2026-08-17): filler 也需要 boundary_lock，避免 snap 缩到
        # 词中间导致 "呃" 词头尾残留（C007 case）
        "boundary_lock": True,
        "boundary_lock_reason": "filler_hesitation v20: entire_word_asr_bounds per v19 boundary_strategy",
        # v20 feedback (2026-08-17): 用户明确指出"剪辑不止是剪掉，剪完还要考虑
        # 是否添加停顿" —— 每条 auto-cut 后建议插入一个 micro-pause 模拟自然
        # 节奏；层级分隔词旁的删除应该保留更长的思考停顿。
        "post_cut_pause_ms": 40,   # micro-pause 默认；下游 automix 消费
    }
    if all_words:
        sep = _neighboring_segment_separator(all_words, delete_words)
        if sep:
            proposal["post_cut_pause_ms"] = 350
            proposal["post_cut_pause_reason"] = (
                f"neighboring segment_separator={sep!r}: preserve rhythmic pause"
            )
    return proposal


def word_duration_seconds(word: dict[str, Any]) -> float:
    return float(word["end_seconds"]) - float(word["start_seconds"])


def dense_acknowledgement_indexes(
    words: list[dict[str, Any]],
    acknowledgement_tokens: set[str],
    window_seconds: float,
    minimum_count: int,
) -> set[int]:
    """Return only the individual acknowledgement words in a dense local cluster.

    The returned words stay separate proposals.  Joining several ``嗯`` words into
    one deletion span could accidentally consume the meaningful words between them.
    """

    hits = [
        index
        for index, word in enumerate(words)
        if clean_token(word["text"]) in acknowledgement_tokens
    ]
    dense: set[int] = set()
    for position, index in enumerate(hits):
        start = float(words[index]["start_seconds"])
        cluster = [
            other
            for other in hits[position:]
            if float(words[other]["start_seconds"]) - start <= window_seconds
        ]
        if len(cluster) >= minimum_count:
            dense.update(cluster)
    return dense


def find_filler_proposals(
    words: list[dict[str, Any]], track_id: str, rule: dict[str, Any]
) -> list[dict[str, Any]]:
    """Nominate only conservative hesitation and repeat-like filler candidates."""
    if not rule.get("enabled", False):
        return []
    strong = {clean_token(value) for value in rule.get("strong_tokens", [])}
    weak = {clean_token(value) for value in rule.get("weak_tokens", [])}
    acknowledgements = {
        clean_token(value) for value in rule.get("acknowledgement_tokens", [])
    }
    max_gap = float(rule.get("max_gap_seconds", 0.45))
    weak_max_gap = float(rule.get("weak_max_gap_seconds", max_gap))
    strong_min = int(rule.get("strong_min_consecutive", 1))
    weak_min = int(rule.get("weak_min_consecutive", 2))
    weak_min_duration = float(rule.get("weak_min_duration_seconds", 0.0))
    weak_max_duration = float(rule.get("weak_max_duration_seconds", float("inf")))
    acknowledgement_long_value = rule.get("acknowledgement_single_min_seconds")
    acknowledgement_long_min = (
        float(acknowledgement_long_value)
        if acknowledgement_long_value is not None
        else float("inf")
    )
    acknowledgement_window_value = rule.get("acknowledgement_dense_window_seconds")
    acknowledgement_minimum_value = rule.get("acknowledgement_dense_min_count")
    dense_acknowledgements = dense_acknowledgement_indexes(
        words,
        acknowledgements,
        (
            float(acknowledgement_window_value)
            if acknowledgement_window_value is not None
            else 0.0
        ),
        (
            int(acknowledgement_minimum_value)
            if acknowledgement_minimum_value is not None
            else 10**9
        ),
    )
    weak_density_window = float(rule.get("weak_density_window_seconds", 0.0))
    weak_max_in_window = int(rule.get("weak_max_candidates_per_window", 10**9))
    recent_weak_starts: list[float] = []
    proposals: list[dict[str, Any]] = []
    index = 0
    while index < len(words):
        token = clean_token(words[index]["text"])
        # v20.6 Q3 · ASR probability gate · 类型化: 低置信度 → skip filler 候选
        if _low_confidence_filler_guard(words[index]):
            index += 1
            continue
        if token in strong:
            end = index
            while (
                end + 1 < len(words)
                and clean_token(words[end + 1]["text"]) in strong
                and contiguous(words[end : end + 2], max_gap)
            ):
                end += 1
            selected = words[index : end + 1]
            if len(selected) >= strong_min:
                proposals.append(
                    proposal_from_words(track_id, selected, "strong_hesitation_sound")
                )
            index = end + 1
        elif token in acknowledgements:
            duration = word_duration_seconds(words[index])
            if index in dense_acknowledgements or duration >= acknowledgement_long_min:
                subtype = (
                    "long_and_dense_acknowledgement"
                    if index in dense_acknowledgements and duration >= acknowledgement_long_min
                    else (
                        "dense_acknowledgement"
                        if index in dense_acknowledgements
                        else "long_acknowledgement"
                    )
                )
                proposals.append(proposal_from_words(track_id, [words[index]], subtype))
            index += 1
        elif token in weak:
            end = index
            while (
                end + 1 < len(words)
                and clean_token(words[end + 1]["text"]) == token
                and contiguous(words[end : end + 2], weak_max_gap)
            ):
                end += 1
            selected = words[index : end + 1]
            all_short = all(
                weak_min_duration <= word_duration_seconds(word) <= weak_max_duration
                for word in selected
            )
            proposal_start = float(selected[0]["start_seconds"])
            if weak_density_window > 0:
                recent_weak_starts = [
                    start
                    for start in recent_weak_starts
                    if proposal_start - start < weak_density_window
                ]
            can_nominate_density = len(recent_weak_starts) < weak_max_in_window
            if len(selected) >= weak_min and all_short and can_nominate_density:
                proposals.append(
                    proposal_from_words(
                        track_id,
                        selected,
                        "repeated_weak_filler",
                        proposed_delete_words=selected[:-1],
                    )
                )
                recent_weak_starts.append(proposal_start)
            index = end + 1
        else:
            index += 1
    return proposals


def immediate_repetition_signature(
    left: dict[str, Any],
    right: dict[str, Any],
    words: list[dict[str, Any]],
    rule: dict[str, Any],
) -> dict[str, Any]:
    gap = max(0.0, float(right["start_seconds"]) - float(left["end_seconds"]))
    left_duration = word_duration_seconds(left)
    right_duration = word_duration_seconds(right)
    shortest = min(left_duration, right_duration)
    asymmetry = (
        abs(left_duration - right_duration) / shortest if shortest > 0 else 0.0
    )
    signature = []
    threshold = float((rule.get("stutter_signature") or {}).get("duration_asymmetry_ratio", 0.3))
    tight_gap = float((rule.get("stutter_signature") or {}).get("very_tight_gap_seconds", 0.15))
    if asymmetry >= threshold:
        signature.append("duration_asymmetry")
    if gap <= tight_gap:
        signature.append("very_tight_gap")
    markers = {
        clean_token(value)
        for value in (rule.get("stutter_signature") or {}).get("markers", [])
    }
    left_index = words.index(left)
    right_index = words.index(right)
    for neighbor in words[max(0, left_index - 1) : min(len(words), right_index + 2)]:
        if neighbor is left or neighbor is right:
            continue
        if clean_token(str(neighbor.get("text", ""))) in markers:
            signature.append("stutter_marker")
            break
    return {
        "has_signature": bool(signature),
        "signals": signature,
        "gap_seconds": round(gap, 6),
        "duration_asymmetry_ratio": round(asymmetry, 6),
    }


def find_immediate_repetition_proposals(
    words: list[dict[str, Any]], track_id: str, rule: dict[str, Any]
) -> list[dict[str, Any]]:
    """Find neighboring content-word repetition chains as a separate family.

    A chain is 2+ consecutive words sharing the same token with pair-wise
    gaps ≤ max_gap_seconds. For a chain of length N, propose deleting the
    first (N-1) occurrences and retaining the last one; the candidate
    boundary spans the full ASR word bounds of the deleted words.

    Emit `boundary_lock=true` to signal downstream snap_candidate_boundaries
    that this candidate's boundary is intentionally anchored to whole-word
    ASR bounds (per v19 boundary_strategy) and MUST NOT be shrunk to a
    stable inner segment. Rendering crossfade still handles pop suppression.

    D8 intentionally keeps this family independent from the filler sentence
    position gate. Confidence is annotated, but no automatic decision is made.
    """

    if not rule.get("enabled", False):
        return []
    min_chars = int(rule.get("min_phrase_chars", 2))
    max_chars = int(rule.get("max_phrase_chars", 4))
    max_gap = float(rule.get("max_gap_seconds", 0.3))
    excluded = {clean_token(value) for value in rule.get("exclude_tokens", [])}
    proposals: list[dict[str, Any]] = []
    n = len(words)
    i = 0
    while i < n - 1:
        token = clean_token(str(words[i].get("text", "")))
        if not token or token in excluded:
            i += 1
            continue
        if not (min_chars <= len(token) <= max_chars):
            i += 1
            continue
        # v20.2 english_fragment_context_guard (C014 GoGoFlow.go fix):
        # skip English fragments that are part of a compound (surrounded by
        # other English fragments in ±2 word window). These are ASR word-boundary
        # noise, not real repetition口癖.
        if _english_fragment_context(words, i):
            i += 1
            continue
        # Extend chain from i as long as consecutive words share the token
        # and pair-wise gap is within max_gap.
        j = i
        while j + 1 < n:
            next_token = clean_token(str(words[j + 1].get("text", "")))
            if next_token != token:
                break
            gap = max(
                0.0,
                float(words[j + 1]["start_seconds"]) - float(words[j]["end_seconds"]),
            )
            if gap > max_gap:
                break
            j += 1
        if j == i:
            i += 1
            continue
        chain_len = j - i + 1  # ≥ 2
        # v20.6 Q5 · 代词/疑问词豁免: "什么什么"/"这个这个" chain<=2 是自然语气加强, 不剪.
        # 保留 chain>=3 的真磕巴.
        if _pronoun_like_exemption(token, chain_len):
            i = j + 1  # 跳到 chain 尾之后
            continue
        # v20.6 Q3 · ASR probability gate: 若 chain 里任一词 probability<0.6 → ASR 幻觉可能
        # 类型化拦截 (不 per-word), 对所有短链生效.
        if any(_low_confidence_filler_guard(w) for w in words[i:j + 1]):
            i = j + 1
            continue
        delete_words = words[i:j]  # first (N-1) occurrences
        retained = [words[j]]
        boundary_start = float(words[i]["start_seconds"])
        boundary_end = float(words[j - 1]["end_seconds"])
        # Signature uses first pair for backward compatibility with v18.
        signature = immediate_repetition_signature(
            words[i], words[i + 1], words, rule
        )
        proposals.append(
            {
                "kind": "immediate_repetition",
                "reason_key": "immediate_repetition",
                "filler_subtype": "immediate_repetition",
                "filler_token": token,
                "source_track": track_id,
                "start_seconds": boundary_start,
                "end_seconds": boundary_end,
                "evidence_words": list(words[i : j + 1]),
                "proposed_delete_words": list(delete_words),
                "retained_evidence_words": list(retained),
                "evidence_text": "".join(
                    clean_token(w.get("text", "")) for w in words[i : j + 1]
                ),
                "proposed_delete_text": "".join(
                    clean_token(w.get("text", "")) for w in delete_words
                ),
                "repetition_signature": signature,
                "repetition_chain_length": chain_len,
                "boundary_lock": True,
                "boundary_lock_reason": "immediate_repetition v19 extend_to_last_repeat_occurrence",
                # v20 feedback (2026-08-17): 短的即时重复删完显得断（C034 case）;
                # 补 micro-pause 让节奏自然
                # v20.7 feedback (2026-08-18): 350 default 太长, 减到 200 更自然语速
                "post_cut_pause_ms": (
                    350 if _neighboring_segment_separator(words, list(delete_words)) else 200
                ),
                "confidence_tier": "high" if signature["has_signature"] else "mid",
            }
        )
        # Jump past the chain (the last word is kept; next scan starts after it).
        i = j + 1
    return proposals


def confidence_for_proposal(proposal: dict[str, Any], rules: dict[str, Any]) -> str:
    configured = rules.get("confidence_policy") or {}
    if proposal.get("kind") == "global_long_pause":
        return str(configured.get("global_long_pause", "high"))
    if proposal.get("kind") == "immediate_repetition":
        return str(proposal.get("confidence_tier") or "mid")
    subtype = str(proposal.get("filler_subtype", ""))
    if subtype == "strong_hesitation_sound":
        return str(configured.get("strong_hesitation_sound", "high"))
    if subtype == "repeated_weak_filler":
        return str(configured.get("repeated_weak_filler", "mid"))
    return "mid"


def default_action_for_confidence(confidence_tier: str, rules: dict[str, Any]) -> str:
    policy = rules.get("confidence_policy") or {}
    review_tiers = set(policy.get("review_tiers", ["high", "mid"]))
    return "human_review_required" if confidence_tier in review_tiers else str(
        policy.get("low_tier_action", "auto_preserve_not_sent_to_review")
    )


def rendering_for_proposal(proposal: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Return the A/B audition crossfade parameters for one candidate only."""

    gate = rules.get("rendering_gate") or {}
    is_pause = proposal.get("kind") == "global_long_pause"
    milliseconds = gate.get(
        "pause_boundary_crossfade_ms" if is_pause else "speech_cut_crossfade_ms",
        50 if is_pause else 100,
    )
    return {
        "crossfade_ms": float(milliseconds),
        "curve": str(gate.get("curve", "tri")),
        "fallback_curve": str(gate.get("fallback_curve", "tri")),
        "scope": "audition_preview_only_not_a_human_decision",
    }


def overlap_ratio(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_start, left_end = evidence_interval(left)
    right_start, right_end = evidence_interval(right)
    overlap = max(
        0.0,
        min(left_end, right_end) - max(left_start, right_start),
    )
    shortest = min(left_end - left_start, right_end - right_start)
    return overlap / shortest if shortest > 0 else 0.0


def evidence_interval(proposal: dict[str, Any]) -> tuple[float, float]:
    words = proposal["evidence_words"]
    return float(words[0]["start_seconds"]), float(words[-1]["end_seconds"])


def interval_gap(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_start, left_end = evidence_interval(left)
    right_start, right_end = evidence_interval(right)
    return max(0.0, max(left_start, right_start) - min(left_end, right_end))


def proposal_score(proposal: dict[str, Any]) -> tuple[float, str]:
    usable = [
        float(word["probability"])
        for word in proposal["evidence_words"]
        if word.get("probability") is not None
    ]
    return (sum(usable) / len(usable) if usable else 0.0, proposal["source_track"])


def deduplicate_filler_proposals(
    proposals: list[dict[str, Any]], cross_mic_gap_seconds: float
) -> list[dict[str, Any]]:
    """Collapse one filler caught differently by several microphones.

    Weak filler runs frequently have different ASR lengths on different mics,
    for example ``对对对`` versus ``对对``.  They are one event when the weak
    token matches and their full evidence windows overlap (or nearly touch).
    The retained representative is the one whose proposed cut ends earliest,
    so at least one final response word remains according to every variant.
    """
    result: list[dict[str, Any]] = []
    for proposal in sorted(
        proposals,
        key=lambda item: (
            float(item["start_seconds"]),
            item["evidence_text"],
            item["source_track"],
        ),
    ):
        match = None
        for existing in result:
            both_weak = (
                existing.get("filler_subtype") == "repeated_weak_filler"
                and proposal.get("filler_subtype") == "repeated_weak_filler"
            )
            if both_weak:
                same_event = (
                    existing.get("filler_token") == proposal.get("filler_token")
                    and interval_gap(existing, proposal) <= cross_mic_gap_seconds
                )
            else:
                same_event = (
                    existing["evidence_text"] == proposal["evidence_text"]
                    and overlap_ratio(existing, proposal) >= 0.5
                )
            if same_event:
                match = existing
                break
        if match is None:
            proposal["corroborated_track_ids"] = [proposal["source_track"]]
            proposal["cross_mic_variants"] = [
                {
                    "track_id": proposal["source_track"],
                    "evidence_text": proposal["evidence_text"],
                    "start_seconds": proposal["start_seconds"],
                    "end_seconds": proposal["end_seconds"],
                }
            ]
            result.append(proposal)
            continue
        corroborated = sorted(
            set(match.get("corroborated_track_ids", [])) | {proposal["source_track"]}
        )
        match["corroborated_track_ids"] = corroborated
        variants = list(match.get("cross_mic_variants", []))
        variants.append(
            {
                "track_id": proposal["source_track"],
                "evidence_text": proposal["evidence_text"],
                "start_seconds": proposal["start_seconds"],
                "end_seconds": proposal["end_seconds"],
            }
        )
        if match.get("filler_subtype") == "repeated_weak_filler":
            replace = (
                float(proposal["end_seconds"]),
                float(proposal["start_seconds"]),
            ) < (
                float(match["end_seconds"]),
                float(match["start_seconds"]),
            )
        else:
            replace = proposal_score(proposal) > proposal_score(match)
        if replace:
            replacement = dict(proposal)
            replacement["corroborated_track_ids"] = corroborated
            replacement["cross_mic_variants"] = variants
            result[result.index(match)] = replacement
        else:
            match["cross_mic_variants"] = variants
    return result


def filler_cross_track_decision(
    proposal: dict[str, Any],
    words_by_track: dict[str, list[dict[str, Any]]],
    overlap_seconds: float,
    same_token_boundary_jitter_seconds: float,
) -> tuple[str, list[str], dict[str, list[dict[str, Any]]]]:
    """Only matching filler text on another mic may be treated as bleed."""
    evidence_tokens = {
        clean_token(word["text"]) for word in proposal["evidence_words"]
    }
    conflicts: dict[str, list[dict[str, Any]]] = {}
    spanning_same_token: dict[str, list[dict[str, Any]]] = {}
    cut_start = float(proposal["start_seconds"])
    cut_end = float(proposal["end_seconds"])
    for track_id, words in words_by_track.items():
        if track_id == proposal["source_track"]:
            continue
        for word in words:
            if token_duration_overlap(
                word,
                cut_start,
                cut_end,
            ) < overlap_seconds:
                continue
            token = clean_token(word["text"])
            if not token:
                continue
            if token in evidence_tokens:
                word_start = float(word["start_seconds"])
                word_end = float(word["end_seconds"])
                if (
                    word_start < cut_start - same_token_boundary_jitter_seconds
                    or word_end > cut_end + same_token_boundary_jitter_seconds
                ):
                    spanning_same_token.setdefault(track_id, []).append(word)
                continue
            conflicts.setdefault(track_id, []).append(word)
    if spanning_same_token:
        return (
            "BLOCKED",
            [
                "OTHER_TRACK_SAME_TOKEN_SPANS_CUT_BOUNDARY",
                "NO_FRAME_LEVEL_ACTIVITY",
            ],
            spanning_same_token,
        )
    if conflicts:
        return (
            "BLOCKED",
            ["OTHER_TRACK_CONFLICTING_TRANSCRIPT", "NO_FRAME_LEVEL_ACTIVITY"],
            conflicts,
        )
    return (
        "NEEDS_HUMAN_REVIEW",
        ["NO_FRAME_LEVEL_ACTIVITY", "WHOLE_CANONICAL_TOKEN_BOUNDARY"],
        conflicts,
    )


def merged_global_activity_intervals(
    words_by_track: dict[str, list[dict[str, Any]]], merge_gap_seconds: float
) -> list[tuple[float, float]]:
    intervals = sorted(
        (float(word["start_seconds"]), float(word["end_seconds"]))
        for words in words_by_track.values()
        for word in words
    )
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + merge_gap_seconds:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def retained_pause_seconds(
    silence_seconds: float, rules: dict[str, Any]
) -> tuple[float, dict[str, Any]]:
    """Choose a natural residual pause without changing legacy v1 behavior."""

    bands = rules.get("retention_by_original_silence")
    if not bands:
        retained = float(rules["retain_seconds"])
        return retained, {
            "mode": "fixed_total_retention",
            "retained_silence_seconds": retained,
        }
    if not isinstance(bands, list):
        raise SystemExit("retention_by_original_silence must be an array")
    for band in bands:
        if not isinstance(band, dict):
            raise SystemExit("each pause retention band must be an object")
        maximum = band.get("max_silence_seconds")
        if maximum is None or silence_seconds <= float(maximum):
            head_tail = float(band["head_tail_seconds"])
            if head_tail < 0:
                raise SystemExit("pause head_tail_seconds must be non-negative")
            retained = min(silence_seconds, head_tail * 2.0)
            return retained, {
                "mode": "duration_banded_head_tail_retention",
                "max_silence_seconds": maximum,
                "head_tail_seconds": head_tail,
                "retained_silence_seconds": retained,
            }
    raise SystemExit("pause retention bands must end with a null maximum")


def load_rttm_speaker_intervals(rttm_path: Path) -> list[tuple[float, float, str]]:
    """Parse pyannote RTTM (SPEAKER lines) → sorted (start, end, speaker) list.

    Returns [] on any read/parse error so callers gracefully fall back to
    the pre-guard behaviour (用户 2026-08-19 · turnover guard 是加固层 ·
    不允许因 diarization 缺失/损坏就丢候选，只允许缺失时降级)。
    """
    intervals: list[tuple[float, float, str]] = []
    try:
        with rttm_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                # RTTM: SPEAKER <file> <chan> <start> <dur> <NA> <NA> <spk> <conf> <NA>
                if not parts or parts[0] != "SPEAKER" or len(parts) < 8:
                    continue
                try:
                    start = float(parts[3])
                    dur = float(parts[4])
                except ValueError:
                    continue
                if dur <= 0.0:
                    continue
                intervals.append((start, start + dur, parts[7]))
    except OSError:
        return []
    intervals.sort(key=lambda item: item[0])
    return intervals


def speaker_at_time(
    intervals: list[tuple[float, float, str]], moment: float
) -> str | None:
    """Return speaker label covering `moment`, or None if no interval overlaps."""
    if not intervals:
        return None
    for start, end, speaker in intervals:
        if start <= moment < end:
            return speaker
        if start > moment:
            break
    return None


def global_pause_proposals(
    words_by_track: dict[str, list[dict[str, Any]]],
    track_ids: list[str],
    duration: float,
    rules: dict[str, Any],
    opening_protection: float,
    closing_protection: float,
    diarization_rttm_path: Path | None = None,
) -> list[dict[str, Any]]:
    if not rules.get("enabled", False):
        return []
    intervals = merged_global_activity_intervals(
        words_by_track, float(rules.get("activity_merge_gap_seconds", 0.05))
    )
    minimum = float(rules["min_silence_seconds"])
    maximum = float(rules["max_silence_seconds"])

    # 用户 2026-08-19 明确根治 · 话轮转换的 pause 是自然衔接 · 阈值抬到 2s 或直接抑制
    guard_rule = rules.get("speaker_turnover_guard") or {}
    guard_enabled = bool(guard_rule.get("enabled", False))
    guard_raise_min_to = float(
        guard_rule.get("min_silence_seconds_when_turnover", 2.0)
    )
    guard_suppress = bool(guard_rule.get("suppress_if_turnover", False))
    guard_edge_probe = float(guard_rule.get("edge_probe_seconds", 0.1))

    speaker_intervals: list[tuple[float, float, str]] = []
    diarization_used = False
    if guard_enabled and diarization_rttm_path is not None:
        rttm_path = Path(diarization_rttm_path)
        if rttm_path.is_file():
            speaker_intervals = load_rttm_speaker_intervals(rttm_path)
            diarization_used = bool(speaker_intervals)

    proposals: list[dict[str, Any]] = []
    for (_, previous_end), (next_start, _) in zip(intervals, intervals[1:]):
        gap_start = max(previous_end, opening_protection)
        gap_end = min(next_start, duration - closing_protection)
        silence = gap_end - gap_start

        left_speaker: str | None = None
        right_speaker: str | None = None
        is_turnover = False
        if diarization_used:
            left_probe = max(0.0, gap_start - guard_edge_probe)
            right_probe = min(duration, gap_end + guard_edge_probe)
            left_speaker = speaker_at_time(speaker_intervals, left_probe)
            right_speaker = speaker_at_time(speaker_intervals, right_probe)
            is_turnover = bool(
                left_speaker
                and right_speaker
                and left_speaker != right_speaker
            )

        effective_min = minimum
        turnover_action: str | None = None
        if is_turnover:
            if guard_suppress:
                turnover_action = "suppressed_by_speaker_turnover_guard"
                effective_min = float("inf")
            else:
                turnover_action = "raised_min_silence_by_speaker_turnover_guard"
                effective_min = max(minimum, guard_raise_min_to)

        retained, retention_rule = retained_pause_seconds(silence, rules)
        if silence < effective_min or silence > maximum or silence <= retained:
            continue
        # Keep half of the retained natural pause on each side of the cut.
        delete_start = gap_start + retained / 2.0
        delete_end = gap_end - retained / 2.0
        global_silence_payload: dict[str, Any] = {
            "start_seconds": gap_start,
            "end_seconds": gap_end,
            "original_silence_seconds": round(silence, 6),
            "retained_silence_seconds": retained,
            "proposed_removed_seconds": round(delete_end - delete_start, 6),
            "retention_rule": retention_rule,
            "all_track_ids": track_ids,
        }
        if diarization_used:
            global_silence_payload["speaker_turnover_guard"] = {
                "used": True,
                "left_speaker": left_speaker,
                "right_speaker": right_speaker,
                "is_turnover": is_turnover,
                "action": turnover_action,
                "effective_min_silence_seconds": (
                    None
                    if effective_min == float("inf")
                    else effective_min
                ),
                "rttm_path": str(diarization_rttm_path),
            }
        proposals.append(
            {
                "kind": "global_long_pause",
                "reason_key": "global_long_pause",
                "source_track": track_ids[0],
                "source_track_note": "兼容审核包字段；此候选代表所有物理轨共同空白。",
                "start_seconds": delete_start,
                "end_seconds": delete_end,
                "evidence_words": [],
                "evidence_text": "global_shared_silence",
                "global_silence": global_silence_payload,
            }
        )
    return proposals


def make_review_display(proposal: dict[str, Any]) -> dict[str, Any]:
    if proposal["kind"] == "global_long_pause":
        pause = proposal["global_silence"]
        return {
            "mode": "all_tracks_context",
            "requires_audio_review": True,
            "summary": (
                f"所有物理轨在 {pause['original_silence_seconds']:.2f} 秒内都没有转写活动，"
                "并已通过逐轨声学安静检查。建议只压缩中间 "
                f"{pause['proposed_removed_seconds']:.2f} 秒，保留约 "
                f"{pause['retained_silence_seconds']:.2f} 秒自然停顿。"
            ),
        }
    if proposal["kind"] == "immediate_repetition":
        signature = proposal.get("repetition_signature") or {}
        signal = "；有犹豫特征" if signature.get("has_signature") else "；无明确犹豫特征，按保守规则送审"
        return {
            "mode": "source_track",
            "requires_audio_review": False,
            "summary": "相邻完全重复，建议保留第二次并仅试听是否自然" + signal + "。",
        }
    return {
        "mode": "source_track",
        "requires_audio_review": False,
        "summary": "待人工确认的犹豫音/重复口癖；系统不会自动删。",
    }


def build(
    p0_report_path: Path,
    out: Path,
    rules_path: Path,
    episode_id: str | None = None,
    created_at: str | None = None,
    semantic_dir: Path | None = None,
    diarization_rttm_path: Path | None = None,
) -> dict[str, Any]:
    p0_report_path = p0_report_path.resolve()
    rules_path = rules_path.resolve()
    report, loaded_tracks = read_p0_inputs(p0_report_path)
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    if rules.get("policy") != "review_only_no_automatic_accept":
        raise SystemExit("rules must keep review_only_no_automatic_accept")
    sample_rate = int(report["sample_rate_hz"])
    frame_count = int(report["frame_count"])
    duration = frame_count / sample_rate
    p0_sha = sha256_file(p0_report_path)
    rules_sha = sha256_file(rules_path)
    canonical_dir = out / "canonical_transcripts"
    words_by_track: dict[str, list[dict[str, Any]]] = {}
    manifest_tracks: list[dict[str, Any]] = []

    for track in loaded_tracks:
        words = canonicalize_words(track["raw_words"], track["track_id"])
        words_by_track[track["track_id"]] = words
        canonical_path = canonical_dir / f"{track['track_id']}.canonical.json"
        write_json(
            canonical_path,
            {
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
                    "method": "display_copy_only_raw_asr_immutable",
                    "rules_sha256": rules_sha,
                    "raw_asr_immutable": True,
                },
                "words": words,
            },
        )
        manifest_tracks.append(
            {
                "track_id": track["track_id"],
                "label": track["label"],
                "source_key": track["track_id"],
                "audio_path": str(track["audio_path"]),
                "transcript_path": str(canonical_path.resolve()),
                "semantic_transcript_path": (
                    str((semantic_dir / f"{track['track_id']}.semantic.json").resolve())
                    if semantic_dir is not None
                    else None
                ),
            }
        )

    track_ids = [track["track_id"] for track in loaded_tracks]
    semantic_contexts = load_semantic_context(
        semantic_dir,
        track_ids,
        sample_rate,
        frame_count,
    )
    raw_fillers = [
        proposal
        for track_id, words in words_by_track.items()
        for proposal in find_filler_proposals(words, track_id, rules["filler_hesitation"])
    ]
    repetition_rule = rules.get("immediate_repetition") or {}
    raw_repetitions = [
        proposal
        for track_id, words in words_by_track.items()
        for proposal in find_immediate_repetition_proposals(words, track_id, repetition_rule)
    ]
    if repetition_rule.get("enabled", False):
        repetition_keys = {
            (
                proposal["source_track"],
                round(float(proposal["start_seconds"]), 3),
                proposal.get("proposed_delete_text", ""),
            )
            for proposal in raw_repetitions
        }
        raw_fillers = [
            proposal
            for proposal in raw_fillers
            if not any(
                proposal.get("filler_subtype") == "repeated_weak_filler"
                and proposal.get("source_track") == source_track
                and abs(float(proposal.get("start_seconds", 0.0)) - start) <= 0.08
                and proposal.get("proposed_delete_text", "") == delete_text
                for source_track, start, delete_text in repetition_keys
            )
        ]
    fillers = deduplicate_filler_proposals(
        raw_fillers,
        float(
            rules["filler_hesitation"].get(
                "cross_mic_event_merge_gap_seconds", 0.25
            )
        ),
    )
    pauses = global_pause_proposals(
        words_by_track,
        track_ids,
        duration,
        rules["global_long_pause"],
        float(rules.get("opening_protection_seconds", 0.0)),
        float(rules.get("closing_protection_seconds", 0.0)),
        diarization_rttm_path=diarization_rttm_path,
    )
    overlap_seconds = float(
        rules["cross_track_guard"].get("conflicting_word_overlap_seconds", 0.05)
    )
    same_token_boundary_jitter = float(
        rules["filler_hesitation"].get(
            "same_token_boundary_jitter_seconds", 0.08
        )
    )
    reviewable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    def append_item(
        proposal: dict[str, Any],
        safety_status: str,
        reason_codes: list[str],
        conflicts: dict[str, list[dict[str, Any]]],
        acoustic_guard: dict[str, Any] | None = None,
    ) -> None:
        start = max(0.0, float(proposal["start_seconds"]))
        end = min(duration, float(proposal["end_seconds"]))
        item: dict[str, Any] = {
            "reason_key": proposal["reason_key"],
            "candidate_kind": proposal["kind"],
            "source_track": proposal["source_track"],
            "start_sample": round(start * sample_rate),
            "end_sample": round(end * sample_rate),
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": round(end - start, 6),
            "context_start_seconds": max(0.0, start - float(rules.get("context_seconds", 5.0))),
            "context_end_seconds": min(duration, end + float(rules.get("context_seconds", 5.0))),
            "safety_status": safety_status,
            "reason_codes": reason_codes,
            "evidence_words": proposal["evidence_words"],
            "evidence_text": proposal["evidence_text"],
            "corroborated_track_ids": proposal.get("corroborated_track_ids", []),
            "conflicting_words_by_track": conflicts,
            "boundary_policy": (
                "centered_global_pause_compression_preserve_natural_silence"
                if proposal["kind"] == "global_long_pause"
                else "whole_canonical_token_no_padding"
            ),
            "review_display": make_review_display(proposal),
            "confidence_tier": confidence_for_proposal(proposal, rules),
            "default_action": default_action_for_confidence(
                confidence_for_proposal(proposal, rules), rules
            ),
            "rendering": rendering_for_proposal(proposal, rules),
            "provenance": {
                "p0_report_sha256": p0_sha,
                "rules_sha256": rules_sha,
                "candidate_policy": rules["policy"],
                "raw_asr_immutable": True,
            },
        }
        for key in (
            "filler_subtype",
            "source_track_note",
            "global_silence",
            "retention_rule",
            "proposed_delete_words",
            "retained_evidence_words",
            "proposed_delete_text",
            "filler_token",
            "cross_mic_variants",
            "repetition_signature",
            "clause_position",
        ):
            if key in proposal:
                item[key] = proposal[key]
        if proposal["kind"] in {"filler_hesitation", "immediate_repetition"}:
            item["lexical_context"] = lexical_context_for_proposal(
                proposal, words_by_track.get(str(proposal.get("source_track")), [])
            )
        if acoustic_guard is not None:
            item["acoustic_guard"] = acoustic_guard
        if item["default_action"] == "auto_preserve_not_sent_to_review":
            item["safety_status"] = "AUTO_PRESERVE"
            item["reason_codes"] = [*reason_codes, "LOW_CONFIDENCE_DEFAULT_PRESERVE"]
        (reviewable if safety_status == "NEEDS_HUMAN_REVIEW" and item["default_action"] == "human_review_required" else blocked).append(item)

    for proposal in [*fillers, *deduplicate_filler_proposals(raw_repetitions, float(rules.get("filler_hesitation", {}).get("cross_mic_event_merge_gap_seconds", 0.25)))]:
        if float(proposal["start_seconds"]) < float(
            rules.get("opening_protection_seconds", 0.0)
        ):
            append_item(
                proposal,
                "BLOCKED",
                ["OPENING_PROTECTION", "WHOLE_CANONICAL_TOKEN_BOUNDARY"],
                {},
            )
            continue
        if float(proposal["end_seconds"]) > duration - float(
            rules.get("closing_protection_seconds", 0.0)
        ):
            append_item(
                proposal,
                "BLOCKED",
                ["CLOSING_PROTECTION", "WHOLE_CANONICAL_TOKEN_BOUNDARY"],
                {},
            )
            continue
        if proposal["end_seconds"] - proposal["start_seconds"] < float(
            rules.get("min_candidate_seconds", 0.1)
        ):
            append_item(
                proposal,
                "BLOCKED",
                ["CANDIDATE_TOO_SHORT", "WHOLE_CANONICAL_TOKEN_BOUNDARY"],
                {},
            )
            continue
        status, reasons, conflicts = filler_cross_track_decision(
            proposal,
            words_by_track,
            overlap_seconds,
            same_token_boundary_jitter,
        )
        if (
            proposal.get("kind") == "filler_hesitation"
            and (rules.get("sentence_position_gate") or {}).get("enabled", False)
        ):
            semantic = semantic_contexts.get(str(proposal.get("source_track")))
            position = clause_position_for_proposal(
                proposal,
                semantic or {},
                int((rules.get("sentence_position_gate") or {}).get("head_tail_word_window", 1)),
            )
            proposal["clause_position"] = position
            block_positions = set((rules.get("sentence_position_gate") or {}).get("block_positions", []))
            if position in block_positions:
                status = "BLOCKED"
                reasons = [*reasons, f"SENTENCE_POSITION_GATE_D6_{position.upper().replace('-', '_')}"]
        elif proposal.get("kind") == "immediate_repetition":
            # D8: immediate repetition is deliberately exempt from D6.
            proposal["clause_position"] = clause_position_for_proposal(
                proposal,
                semantic_contexts.get(str(proposal.get("source_track"))) or {},
                int((rules.get("sentence_position_gate") or {}).get("head_tail_word_window", 1)),
            )
        append_item(proposal, status, reasons, conflicts)

    pause_rule = rules["global_long_pause"]
    track_by_id = {track["track_id"]: track for track in loaded_tracks}
    for proposal in pauses:
        silence = proposal["global_silence"]
        acoustic_guard = {
            track_id: acoustic_quiet_check(
                track_by_id[track_id]["audio_path"],
                float(silence["start_seconds"]),
                float(silence["end_seconds"]),
                expected_sample_rate=sample_rate,
                expected_frame_count=frame_count,
                frame_seconds=float(pause_rule["acoustic_frame_seconds"]),
                max_rms_dbfs=float(pause_rule["max_frame_rms_dbfs"]),
                max_peak_dbfs=float(pause_rule["max_frame_peak_dbfs"]),
            )
            for track_id in track_ids
        }
        noisy_tracks = [
            track_id for track_id, result in acoustic_guard.items() if not result["quiet"]
        ]
        if noisy_tracks:
            append_item(
                proposal,
                "BLOCKED",
                [
                    "ACOUSTIC_ACTIVITY_ON_TRACKS",
                    *[f"ACOUSTIC_ACTIVITY_{track_id}" for track_id in noisy_tracks],
                ],
                {},
                acoustic_guard,
            )
        else:
            append_item(
                proposal,
                "NEEDS_HUMAN_REVIEW",
                [
                    "ALL_TRACKS_NO_TRANSCRIPT_ACTIVITY",
                    "ALL_TRACKS_ACOUSTIC_QUIET",
                    "PAUSE_COMPRESSED_NOT_REMOVED",
                ],
                {},
                acoustic_guard,
            )

    all_items = sorted(
        reviewable + blocked,
        key=lambda item: (
            item["start_seconds"],
            item["candidate_kind"],
            item["source_track"],
            item["evidence_text"],
        ),
    )
    for index, item in enumerate(all_items, 1):
        item["candidate_id"] = f"C{index:03d}"
    reviewable = [item for item in all_items if item["safety_status"] == "NEEDS_HUMAN_REVIEW"]
    blocked = [item for item in all_items if item["safety_status"] == "BLOCKED"]

    effective_episode_id = episode_id or str(report.get("episode_id") or "EP04")
    created = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "schema_version": "ntrack-input-v1",
        "episode_id": effective_episode_id,
        "purpose": "FILLER_AND_GLOBAL_LONG_PAUSE_REVIEW_ONLY_CHALLENGER",
        "note": "轨道是物理麦编号；不猜测性别或人物身份。",
        "tracks": manifest_tracks,
    }
    counts = {
        "raw_filler_proposals": len(raw_fillers),
        "raw_immediate_repetition_proposals": len(raw_repetitions),
        "deduplicated_filler_proposals": len(fillers),
        "global_pause_proposals_before_acoustic_guard": len(pauses),
        "reviewable": len(reviewable),
        "reviewable_fillers": sum(
            item["candidate_kind"] == "filler_hesitation" for item in reviewable
        ),
        "reviewable_immediate_repetitions": sum(
            item["candidate_kind"] == "immediate_repetition" for item in reviewable
        ),
        "reviewable_global_long_pauses": sum(
            item["candidate_kind"] == "global_long_pause" for item in reviewable
        ),
        "blocked": len(blocked),
        "blocked_acoustic": sum(
            "ACOUSTIC_ACTIVITY_ON_TRACKS" in item["reason_codes"] for item in blocked
        ),
    }
    source_package = {
        "schema_version": "ntrack-review-source-v1",
        "episode_id": effective_episode_id,
        "generated_at": created,
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "candidate_policy": rules["policy"],
        "safety_note": (
            "所有候选只供真人审核；不会自动批准、生成 EDL 或修改原始音频。"
            "长停顿还要求所有轨道同时无转写活动且通过声学安静检查。"
        ),
        "input_provenance": {
            "p0_report_path": str(p0_report_path),
            "p0_report_sha256": p0_sha,
            "rules_path": str(rules_path),
            "rules_sha256": rules_sha,
            "track_manifest_path": str((out / "tracks.manifest.json").resolve()),
        },
        "counts": counts,
        "candidates": reviewable,
    }
    blocked_package = {
        "schema_version": "ntrack-blocked-candidates-v1",
        "episode_id": effective_episode_id,
        "generated_at": created,
        "source_package_sha256": sha256_bytes(source_package),
        "counts": counts,
        "candidates": blocked,
    }
    report_doc = {
        "schema_version": "filler-global-pause-report-v1",
        "status": (
            "READY_FOR_REVIEW_PACKAGE"
            if reviewable
            else "READY_WITH_ZERO_REVIEWABLE_CANDIDATES"
        ),
        "episode_id": effective_episode_id,
        "track_count": len(loaded_tracks),
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "candidate_counts": counts,
        "automatic_cutting": "DISABLED",
        "next_step": (
            "Create A/B previews with review-product-v1, then require a human accept/reject "
            "decision for every candidate before any separate EDL/render step."
        ),
    }
    write_json(out / "tracks.manifest.json", manifest)
    write_json(out / "candidate_source.json", source_package)
    write_json(out / "blocked_candidates.json", blocked_package)
    write_json(out / "bridge_report.json", report_doc)
    return report_doc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build review-only N-track filler and global long-pause candidates"
    )
    parser.add_argument("--p0-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "rules/candidate-generation.filler-global-pause-v1.json",
    )
    parser.add_argument("--episode-id")
    parser.add_argument("--created-at", help="UTC ISO timestamp for reproducible fixtures")
    parser.add_argument(
        "--semantic-dir",
        type=Path,
        help="optional semantic-transcript-v1 semantic_transcripts directory; required by conservative sentence-position rules",
    )
    parser.add_argument(
        "--diarization-rttm",
        type=Path,
        default=None,
        help="optional pyannote-audio RTTM file for the merged/global timeline; enables speaker_turnover_guard on global_long_pause candidates (fallback: no guard)",
    )
    args = parser.parse_args()
    result = build(
        args.p0_report,
        args.out.resolve(),
        args.rules,
        args.episode_id,
        args.created_at,
        args.semantic_dir,
        args.diarization_rttm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
