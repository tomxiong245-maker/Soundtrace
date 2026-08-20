#!/usr/bin/env python3
"""Event-level identity and reviewed-candidate routing.

This module is deliberately independent from the delivery orchestrator.  It
answers one narrow question: *is this candidate the same audio event that a
human has already reviewed?*  Candidate IDs, semantic hashes and run IDs are
not identities: boundary snapping can change all of them while the source
event stays the same.

The matching contract is intentionally conservative and explainable:

* episode and source input/audio identity must match;
* physical ``track_id`` must match;
* normalized proposed text must match (NFKC, punctuation/whitespace removed,
  traditional Chinese mapped to simplified Chinese, ASCII case-folded);
* interval overlap is the intersection divided by the shorter interval and
  must be at least 0.80;
* endpoint drift of at most 50 ms is an exact reuse.  A semantic match with a
  larger drift is a boundary-review route: the old semantic decision may be
  reused, but the old listening approval must not be copied to a new cut.

The module never writes decisions, EDLs or audio.  ``load_run_events`` is a
read-only convenience for producing an audit report from existing run
artifacts.  The richer route names are the stable API used by callers:

``already_reviewed_exact``
    Same event and effectively unchanged boundaries.  A semantic decision and
    old feedback may be displayed; an execution-only rejection remains an
    execution issue rather than a semantic rejection.
``semantic_reuse_boundary_review``
    Same event, but the candidate boundaries moved by more than 50 ms.  Reuse
    the semantic label only; require a new boundary/listening check.
``rejected_false_positive``
    A historical reject explains that the candidate was not present, was an
    ASR/recognition artifact, or should be semantically retained.  The new
    candidate can be suppressed as a false positive.
``rejected_execution_issue``
    A historical reject only reports a bad cut (boundary, level, click,
    discontinuity, etc.).  Do not learn ``do not delete``; keep the candidate
    for execution repair/review.
``new_event``
    No safe historical match, or the historical record lacks the identity
    needed to prove one.

The matching layer exposes broad ``category`` aliases (EXACT,
SEMANTIC_REUSE, BOUNDARY_REVIEW, NEW) for compatibility with earlier
prototypes, while ``route`` is the production-facing value.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


OVERLAP_THRESHOLD = 0.80
BOUNDARY_DRIFT_THRESHOLD_SECONDS = 0.050


class MatchCategory(str, Enum):
    EXACT = "EXACT"
    SEMANTIC_REUSE = "SEMANTIC_REUSE"
    BOUNDARY_REVIEW = "BOUNDARY_REVIEW"
    NEW = "NEW"


class EventRoute(str, Enum):
    ALREADY_REVIEWED_EXACT = "already_reviewed_exact"
    SEMANTIC_REUSE_BOUNDARY_REVIEW = "semantic_reuse_boundary_review"
    REJECTED_FALSE_POSITIVE = "rejected_false_positive"
    REJECTED_EXECUTION_ISSUE = "rejected_execution_issue"
    NEW_EVENT = "new_event"


class FeedbackClass(str, Enum):
    NONE = "none"
    FALSE_POSITIVE = "false_positive"
    SEMANTIC_RETAIN = "semantic_retain"
    EXECUTION_ISSUE = "execution_issue"
    RECOGNITION_ISSUE = "recognition_issue"
    UNKNOWN_REJECT = "unknown_reject"


# This is intentionally small and deterministic.  It covers the traditional
# forms encountered in the existing EP04 labels while also handling common
# single-character variants without pulling a new dependency into production.
TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "麼": "么", "個": "个", "這": "这", "那": "那", "對": "对",
        "們": "们", "為": "为", "說": "说", "時": "时", "現": "现",
        "應": "应", "還": "还", "會": "会", "後": "后", "過": "过",
        "問": "问", "題": "题", "來": "来", "萬": "万", "點": "点",
        "當": "当", "業": "业", "產": "产", "廠": "厂", "動": "动",
        "務": "务", "義": "义", "習": "习", "學": "学", "識": "识",
        "話": "话", "號": "号", "聲": "声", "音": "音", "聽": "听",
        "頭": "头", "邊": "边", "界": "界", "討": "讨", "論": "论",
        "轉": "转", "寫": "写", "長": "长", "頓": "顿", "標": "标",
        "錄": "录", "語": "语", "簡": "简", "體": "体", "覺": "觉",
        "開": "开", "發": "发", "網": "网", "絡": "络", "資": "资",
        "軟": "软", "檔": "档", "檢": "检",
    }
)

_PUNCT_OR_SPACE = re.compile(r"[\s\W_]+", flags=re.UNICODE)


def normalize_event_text(value: Any) -> str:
    """Return stable text used for event matching, not display.

    Raw ASR text is never changed on disk.  This function only creates a
    comparison key, so ``什麼`` and ``什么`` match while punctuation, commas
    and ASR spacing do not split an otherwise identical event.
    """

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).translate(TRADITIONAL_TO_SIMPLIFIED)
    text = text.casefold()
    # Keep Unicode letters/digits; discard punctuation and whitespace.  CJK
    # characters are Unicode word characters, as are ASCII letters/digits.
    return _PUNCT_OR_SPACE.sub("", text)


def _first_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    direct = _first_value(
        candidate,
        "proposed_delete_text",
        "filler_token",
        "deleted_text",
        "text",
        "display_text",
    )
    if direct is not None:
        return str(direct)

    # A few older bundles only kept text in the source track's word list.
    track_id = _first_value(candidate, "source_track_id", "track_id")
    tracks = candidate.get("text_tracks")
    start = _number(candidate, "start_seconds")
    end = _number(candidate, "end_seconds")
    if isinstance(tracks, Mapping) and track_id in tracks:
        track = tracks.get(track_id)
        words = track.get("words") if isinstance(track, Mapping) else None
        if isinstance(words, Sequence):
            selected: list[str] = []
            for word in words:
                if not isinstance(word, Mapping):
                    continue
                ws = _number(word, "start_seconds")
                we = _number(word, "end_seconds")
                if start is None or end is None or ws is None or we is None:
                    continue
                if we > start and ws < end:
                    selected.append(str(word.get("text", "")))
            if selected:
                return "".join(selected)
    return ""


def _number(mapping: Mapping[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _track_id(candidate: Mapping[str, Any]) -> str | None:
    value = _first_value(candidate, "source_track_id", "track_id", "track")
    return str(value) if value is not None else None


def _track_audio_sha(manifest: Mapping[str, Any], track_id: str | None) -> str | None:
    if not isinstance(manifest, Mapping):
        return None
    tracks = manifest.get("tracks")
    if isinstance(tracks, Mapping):
        if track_id and isinstance(tracks.get(track_id), Mapping):
            track = tracks[track_id]
            return _first_value(
                track,
                "audio_sha256",
                "source_audio_sha256",
                "raw_sha256",
                "input_audio_sha256",
            )
        tracks = list(tracks.values())
    if isinstance(tracks, Sequence):
        for track in tracks:
            if not isinstance(track, Mapping):
                continue
            if track_id is not None and str(track.get("track_id")) != str(track_id):
                continue
            value = _first_value(
                track,
                "audio_sha256",
                "source_audio_sha256",
                "raw_sha256",
                "input_audio_sha256",
            )
            if value:
                return str(value)
    return None


def _resolve_audio_identity(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    track_id: str | None,
) -> str | None:
    direct = _first_value(
        candidate,
        "source_audio_sha256",
        "audio_sha256",
        "raw_audio_sha256",
        "input_audio_sha256",
        "source_input_identity",
    )
    if direct:
        return str(direct)
    if context:
        direct_context = _first_value(
            context,
            "source_audio_sha256",
            "audio_sha256",
            "raw_audio_sha256",
            "input_audio_sha256",
            "source_input_identity",
        )
        if direct_context:
            return str(direct_context)
        value = _track_audio_sha(context, track_id)
        if value:
            return value
        nested = context.get("input_manifest")
        value = _track_audio_sha(nested, track_id) if isinstance(nested, Mapping) else None
        if value:
            return value
    return None


def _interval(candidate: Mapping[str, Any], sample_rate_hz: float | None = None) -> tuple[float | None, float | None]:
    # Samples are preferred: they are the canonical edit coordinates and avoid
    # the small rounding differences visible in snapped candidate seconds.
    start_sample = _number(candidate, "start_sample")
    end_sample = _number(candidate, "end_sample")
    sr = sample_rate_hz or _number(candidate, "sample_rate_hz")
    if start_sample is not None and end_sample is not None and sr and sr > 0:
        return start_sample / sr, end_sample / sr
    return _number(candidate, "start_seconds"), _number(candidate, "end_seconds")


@dataclass(frozen=True)
class EventIdentity:
    episode_id: str | None
    source_audio_identity: str | None
    track_id: str | None
    normalized_text: str
    start_seconds: float | None
    end_seconds: float | None
    sample_rate_hz: float | None = None
    candidate_id: str | None = None
    run_id: str | None = None

    @property
    def event_key(self) -> str:
        """Stable human-readable key; candidate ID/run ID are excluded."""

        episode = self.episode_id or "unknown-episode"
        audio = self.source_audio_identity or "unknown-audio"
        track = self.track_id or "unknown-track"
        text = self.normalized_text or "unknown-text"
        return f"{episode}|{audio}|{track}|{text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "source_audio_identity": self.source_audio_identity,
            "track_id": self.track_id,
            "normalized_text": self.normalized_text,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "sample_rate_hz": self.sample_rate_hz,
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "event_key": self.event_key,
        }


def canonical_event_identity(
    candidate: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> EventIdentity:
    """Build an identity from a candidate and optional run/input context."""

    context = context or {}
    episode = _first_value(candidate, "episode_id") or _first_value(context, "episode_id")
    run_id = _first_value(candidate, "run_id") or _first_value(context, "run_id")
    track_id = _track_id(candidate)
    sample_rate = _number(candidate, "sample_rate_hz") or _number(context, "sample_rate_hz")
    start, end = _interval(candidate, sample_rate)
    audio = _resolve_audio_identity(candidate, context, track_id)
    return EventIdentity(
        episode_id=str(episode) if episode is not None else None,
        source_audio_identity=audio,
        track_id=track_id,
        normalized_text=normalize_event_text(_candidate_text(candidate)),
        start_seconds=start,
        end_seconds=end,
        sample_rate_hz=sample_rate,
        candidate_id=str(candidate.get("candidate_id")) if candidate.get("candidate_id") is not None else None,
        run_id=str(run_id) if run_id is not None else None,
    )


def interval_overlap_ratio(a: EventIdentity, b: EventIdentity) -> float | None:
    """Return intersection / shorter interval (the overlap coefficient)."""

    if None in (a.start_seconds, a.end_seconds, b.start_seconds, b.end_seconds):
        return None
    a_start, a_end = float(a.start_seconds), float(a.end_seconds)
    b_start, b_end = float(b.start_seconds), float(b.end_seconds)
    a_duration, b_duration = a_end - a_start, b_end - b_start
    if a_duration <= 0 or b_duration <= 0:
        return None
    intersection = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    return intersection / min(a_duration, b_duration)


def boundary_drift_seconds(a: EventIdentity, b: EventIdentity) -> float | None:
    if None in (a.start_seconds, a.end_seconds, b.start_seconds, b.end_seconds):
        return None
    return max(
        abs(float(a.start_seconds) - float(b.start_seconds)),
        abs(float(a.end_seconds) - float(b.end_seconds)),
    )


def _same_identity(a: EventIdentity, b: EventIdentity) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not a.episode_id or not b.episode_id or a.episode_id != b.episode_id:
        reasons.append("episode_id 不同或缺失")
    if not a.source_audio_identity or not b.source_audio_identity or a.source_audio_identity != b.source_audio_identity:
        reasons.append("source input/audio identity 不同或缺失")
    if not a.track_id or not b.track_id or a.track_id != b.track_id:
        reasons.append("track_id 不同或缺失")
    if not a.normalized_text or not b.normalized_text or a.normalized_text != b.normalized_text:
        reasons.append("规范化文本不同或缺失")
    return not reasons, reasons


_FALSE_POSITIVE_TERMS = (
    "没有", "根本没有", "不存在", "没听到", "没看到", "误报", "不是", "不是口癖",
    "识别", "转写", "asr", "英文", "繁体", "简体", "一个完整的词", "完整的词", "完整词", "完整的单词",
    "完整句", "完整的句", "完整句子", "保留", "活人感", "认可", "回应",
)
_EXECUTION_TERMS = (
    "剪辑痕迹", "剪辑的时候", "声音明显小", "声音小", "音量", "响度", "边界",
    "咔哒", "咔嗒", "不自然", "不干净", "断裂", "回声", "爆音", "click", "level",
)


def classify_feedback(feedback: Any) -> FeedbackClass:
    """Classify feedback for routing; preserve the original text elsewhere."""

    text = unicodedata.normalize("NFKC", str(feedback or "")).casefold().strip()
    if not text:
        return FeedbackClass.NONE
    if any(term.casefold() in text for term in _EXECUTION_TERMS):
        return FeedbackClass.EXECUTION_ISSUE
    if any(term.casefold() in text for term in _FALSE_POSITIVE_TERMS):
        if any(term.casefold() in text for term in ("识别", "转写", "asr", "英文", "繁体", "简体")):
            return FeedbackClass.RECOGNITION_ISSUE
        if any(term.casefold() in text for term in ("没有", "根本没有", "不存在", "误报")):
            return FeedbackClass.FALSE_POSITIVE
        return FeedbackClass.SEMANTIC_RETAIN
    return FeedbackClass.UNKNOWN_REJECT


def _decision_value(event: Mapping[str, Any]) -> str | None:
    value = _first_value(event, "decision", "review_decision")
    if value is None and isinstance(event.get("label"), Mapping):
        value = _first_value(event["label"], "decision", "review_decision")
    return str(value) if value is not None else None


def _flatten_history_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Accept both run rows and ``experience-case-v1``-style nested rows."""

    candidate = event.get("candidate")
    flattened = dict(candidate) if isinstance(candidate, Mapping) else dict(event)
    # Top-level identity/provenance should still be available to callers that
    # enriched an experience case with audio SHA or an identity context.
    for key in (
        "episode_id",
        "run_id",
        "source_audio_sha256",
        "audio_sha256",
        "raw_audio_sha256",
        "input_audio_sha256",
        "source_input_identity",
        "_identity_context",
    ):
        if event.get(key) not in (None, ""):
            flattened[key] = event[key]
    label = event.get("label")
    if isinstance(label, Mapping):
        for key in ("decision", "review_decision", "feedback", "reviewer", "decided_at"):
            if label.get(key) not in (None, ""):
                flattened[key] = label[key]
    for key in ("decision", "review_decision", "feedback", "reviewer", "decided_at"):
        if event.get(key) not in (None, ""):
            flattened[key] = event[key]
    return flattened


@dataclass(frozen=True)
class EventMatch:
    candidate: EventIdentity
    historical: EventIdentity | None
    category: MatchCategory
    route: EventRoute
    overlap_ratio: float | None
    boundary_drift_seconds: float | None
    historical_decision: str | None = None
    historical_feedback: str = ""
    feedback_class: FeedbackClass = FeedbackClass.NONE
    semantic_decision: str | None = None
    suppress_candidate: bool = False
    boundary_review_required: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> bool:
        return self.historical is not None

    @property
    def semantic_category(self) -> MatchCategory:
        """Semantic identity class, separate from the required review action."""

        if self.category == MatchCategory.BOUNDARY_REVIEW:
            return MatchCategory.SEMANTIC_REUSE
        return self.category

    @property
    def reuse_class(self) -> str:
        """Compatibility label for the original EXACT/SEMANTIC/NEW proposal."""

        if self.route == EventRoute.ALREADY_REVIEWED_EXACT:
            return "EXACT_REUSE"
        if self.route == EventRoute.SEMANTIC_REUSE_BOUNDARY_REVIEW:
            return "SEMANTIC_REUSE_WITH_BOUNDARY_REVIEW"
        if self.route == EventRoute.NEW_EVENT:
            return "NEW_OR_ASR_REVIEW"
        return self.route.value.upper()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "historical": self.historical.to_dict() if self.historical else None,
            "category": self.category.value,
            "semantic_category": self.semantic_category.value,
            "reuse_class": self.reuse_class,
            "semantic_match": self.category in (MatchCategory.EXACT, MatchCategory.SEMANTIC_REUSE, MatchCategory.BOUNDARY_REVIEW),
            "route": self.route.value,
            "overlap_ratio": self.overlap_ratio,
            "boundary_drift_seconds": self.boundary_drift_seconds,
            "boundary_drift_ms": round(self.boundary_drift_seconds * 1000, 3) if self.boundary_drift_seconds is not None else None,
            "historical_decision": self.historical_decision,
            "historical_feedback": self.historical_feedback,
            "feedback_class": self.feedback_class.value,
            "semantic_decision": self.semantic_decision,
            "suppress_candidate": self.suppress_candidate,
            "boundary_review_required": self.boundary_review_required,
            "reasons": list(self.reasons),
        }


def _candidate_identity_for_history(value: EventIdentity | Mapping[str, Any]) -> EventIdentity:
    if isinstance(value, EventIdentity):
        return value
    context = value.get("_identity_context") if isinstance(value, Mapping) else None
    return canonical_event_identity(value, context=context if isinstance(context, Mapping) else None)


def _route_for_match(
    candidate: EventIdentity,
    historical: EventIdentity,
    historical_event: Mapping[str, Any],
    overlap: float,
    drift: float | None,
) -> tuple[MatchCategory, EventRoute, str | None, bool, bool, FeedbackClass, list[str]]:
    decision = _decision_value(historical_event)
    feedback = str(historical_event.get("feedback") or "")
    feedback_class = classify_feedback(feedback) if decision and "reject" in decision else FeedbackClass.NONE
    reasons: list[str] = [f"同一 episode/input/track/规范化文本，overlap={overlap:.3f}≥{OVERLAP_THRESHOLD:.2f}"]

    if feedback_class in (FeedbackClass.FALSE_POSITIVE, FeedbackClass.RECOGNITION_ISSUE, FeedbackClass.SEMANTIC_RETAIN):
        reasons.append(f"历史 reject 反馈归类为 {feedback_class.value}，可以抑制同类误报")
        return (
            MatchCategory.BOUNDARY_REVIEW if drift is not None and drift > BOUNDARY_DRIFT_THRESHOLD_SECONDS else MatchCategory.EXACT,
            EventRoute.REJECTED_FALSE_POSITIVE,
            None,
            True,
            drift is not None and drift > BOUNDARY_DRIFT_THRESHOLD_SECONDS,
            feedback_class,
            reasons,
        )

    if feedback_class == FeedbackClass.EXECUTION_ISSUE:
        reasons.append("历史 reject 只描述剪口执行质量；不能学习成语义保留")
        return (
            MatchCategory.BOUNDARY_REVIEW if drift is not None and drift > BOUNDARY_DRIFT_THRESHOLD_SECONDS else MatchCategory.EXACT,
            EventRoute.REJECTED_EXECUTION_ISSUE,
            None,
            False,
            drift is not None and drift > BOUNDARY_DRIFT_THRESHOLD_SECONDS,
            feedback_class,
            reasons,
        )

    if drift is not None and drift <= BOUNDARY_DRIFT_THRESHOLD_SECONDS:
        reasons.append(f"边界最大漂移 {drift * 1000:.1f}ms≤{BOUNDARY_DRIFT_THRESHOLD_SECONDS * 1000:.0f}ms")
        return MatchCategory.EXACT, EventRoute.ALREADY_REVIEWED_EXACT, decision, False, False, feedback_class, reasons

    reasons.append(
        "边界漂移超过 50ms：只复用语义决定，旧剪口听感批准不能继承；需要边界复核"
    )
    return MatchCategory.BOUNDARY_REVIEW, EventRoute.SEMANTIC_REUSE_BOUNDARY_REVIEW, decision, False, True, feedback_class, reasons


def classify_candidate_against_history(
    candidate: EventIdentity | Mapping[str, Any],
    historical_events: Iterable[EventIdentity | Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> EventMatch:
    """Compare one candidate to human-reviewed events and return one route.

    The best match is selected by overlap after identity equality.  A missing
    input SHA/track/text never matches, which is the fail-closed NEW route.
    """

    if isinstance(candidate, EventIdentity):
        candidate_identity = candidate
    else:
        candidate_context = context
        if candidate_context is None:
            embedded_context = candidate.get("_identity_context")
            if isinstance(embedded_context, Mapping):
                candidate_context = embedded_context
        candidate_identity = canonical_event_identity(candidate, context=candidate_context)
    best: tuple[float, EventIdentity, Mapping[str, Any], float | None, list[str]] | None = None
    for raw_event in historical_events:
        event_mapping = _flatten_history_event(raw_event) if isinstance(raw_event, Mapping) else {"decision": None}
        history_identity = _candidate_identity_for_history(event_mapping)
        same, identity_reasons = _same_identity(candidate_identity, history_identity)
        if not same:
            continue
        overlap = interval_overlap_ratio(candidate_identity, history_identity)
        if overlap is None or overlap < OVERLAP_THRESHOLD:
            continue
        drift = boundary_drift_seconds(candidate_identity, history_identity)
        if best is None or overlap > best[0]:
            best = (overlap, history_identity, event_mapping, drift, identity_reasons)

    if best is None:
        return EventMatch(
            candidate=candidate_identity,
            historical=None,
            category=MatchCategory.NEW,
            route=EventRoute.NEW_EVENT,
            overlap_ratio=None,
            boundary_drift_seconds=None,
            reasons=("没有找到满足来源、轨道、规范化文本和 overlap≥0.80 的历史事件；需要新审核或 ASR 检查",),
        )

    overlap, historical_identity, historical_event, drift, identity_reasons = best
    category, route, semantic_decision, suppress, boundary_review, feedback_class, reasons = _route_for_match(
        candidate_identity, historical_identity, historical_event, overlap, drift
    )
    return EventMatch(
        candidate=candidate_identity,
        historical=historical_identity,
        category=category,
        route=route,
        overlap_ratio=overlap,
        boundary_drift_seconds=drift,
        historical_decision=_decision_value(historical_event),
        historical_feedback=str(historical_event.get("feedback") or ""),
        feedback_class=feedback_class,
        semantic_decision=semantic_decision,
        suppress_candidate=suppress,
        boundary_review_required=boundary_review,
        reasons=tuple(reasons),
    )


# Friendly alias for callers that use the wording from the product discussion.
compare_candidate_to_history = classify_candidate_against_history


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_candidate_records(run_dir: Path) -> list[dict[str, Any]]:
    package_path = run_dir / "review_bundle" / "review_package.json"
    calibration_path = run_dir / "calibration_source.json"
    all_candidates_path = run_dir / "all_candidates.json"
    records: dict[str, dict[str, Any]] = {}
    for path in (all_candidates_path, calibration_path, package_path):
        if not path.is_file():
            continue
        doc = _read_json(path)
        for row in doc.get("candidates", []):
            cid = row.get("candidate_id")
            if cid is None:
                continue
            records.setdefault(str(cid), {}).update(row)
    return list(records.values())


def load_run_events(run_dir: str | Path, *, include_unreviewed: bool = True) -> list[dict[str, Any]]:
    """Load candidate + decision rows from a run without changing the run."""

    run_path = Path(run_dir).expanduser().resolve()
    input_manifest: dict[str, Any] = {}
    input_path = run_path / "input_manifest.json"
    if input_path.is_file():
        input_manifest = _read_json(input_path)
    context = {
        "episode_id": input_manifest.get("episode_id") or run_path.name.split("-", 1)[0],
        "run_id": input_manifest.get("run_id") or run_path.name,
        "sample_rate_hz": input_manifest.get("sample_rate_hz"),
        "input_manifest": input_manifest,
    }
    decisions_by_id: dict[str, dict[str, Any]] = {}
    for filename in ("human_decisions.json", "human_decisions.raw_review_ui.json"):
        path = run_path / filename
        if not path.is_file():
            continue
        doc = _read_json(path)
        rows = doc.get("decisions", []) if isinstance(doc, Mapping) else []
        for row in rows:
            if isinstance(row, Mapping) and row.get("candidate_id") is not None:
                decisions_by_id[str(row["candidate_id"])] = dict(row)
        # The normalized decisions file is the authoritative one when present.
        if filename == "human_decisions.json":
            break

    events: list[dict[str, Any]] = []
    for candidate in _merge_candidate_records(run_path):
        cid = str(candidate.get("candidate_id"))
        decision = decisions_by_id.get(cid, {})
        merged = dict(candidate)
        merged.update({k: v for k, v in decision.items() if k not in ("candidate_id",)})
        merged["episode_id"] = context["episode_id"]
        merged["run_id"] = context["run_id"]
        merged["_identity_context"] = context
        if include_unreviewed or decision:
            events.append(merged)
    return events


def compare_run_candidates(
    current_run_dir: str | Path,
    historical_run_dir: str | Path,
    *,
    include_unreviewed: bool = True,
) -> list[EventMatch]:
    history = load_run_events(historical_run_dir, include_unreviewed=False)
    current = load_run_events(current_run_dir, include_unreviewed=include_unreviewed)
    return [classify_candidate_against_history(row, history) for row in current]


def build_run_report(
    current_run_dir: str | Path,
    historical_run_dir: str | Path,
    *,
    include_unreviewed: bool = True,
) -> dict[str, Any]:
    matches = compare_run_candidates(current_run_dir, historical_run_dir, include_unreviewed=include_unreviewed)
    counts: dict[str, int] = {}
    for match in matches:
        counts[match.route.value] = counts.get(match.route.value, 0) + 1
    return {
        "schema_version": "event-identity-routing-v1",
        "overlap_threshold": OVERLAP_THRESHOLD,
        "boundary_drift_threshold_ms": BOUNDARY_DRIFT_THRESHOLD_SECONDS * 1000,
        "current_run": str(Path(current_run_dir).resolve()),
        "historical_run": str(Path(historical_run_dir).resolve()),
        "summary": counts,
        "matches": [match.to_dict() for match in matches],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-run", type=Path, required=True)
    parser.add_argument("--historical-run", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = build_run_report(args.current_run, args.historical_run)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
