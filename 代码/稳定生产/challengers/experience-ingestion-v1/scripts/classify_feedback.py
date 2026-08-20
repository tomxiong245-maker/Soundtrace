#!/usr/bin/env python3
"""Deterministic, reviewable feedback classification for the Challenger loop."""
from __future__ import annotations

import re
from typing import Any


CLASSIFICATION_VERSION = "feedback-classification-v1"

# `class` was the original single-label field.  It remains as a compatibility
# alias for `primary_class`, but feedback itself is not single-label: a reviewer
# can simultaneously say that a candidate must be kept *and* that a previous
# cut left an audible seam.  Keep the individual signals instead of allowing a
# generic execution observation to erase the semantic instruction.
MULTILABEL_PRIMARY_POLICY = "multilabel-safety-and-semantic-primary-v1"

# This is deliberately not a confidence ranking.  It selects the old one-label
# compatibility projection after all independently observed meanings have been
# retained in `classes`.  A false-positive statement says the candidate does
# not describe a real event; an explicit keep/cut instruction says what to do
# with its semantic content; only then do ASR or rendering observations decide
# the legacy primary label.
PRIMARY_CLASS_ORDER = (
    "false_positive",
    "semantic_keep",
    "semantic_cut",
    "asr_error",
    "execution_issue",
)

PATTERNS: dict[str, tuple[str, ...]] = {
    "asr_error": (
        "识别错", "转写错", "转写不对", "听错", "漏识", "漏掉", "没识别",
        "识别问题", "英文没", "英文没有", "专名", "人名错", "词边界", "分词错",
        "错字", "字幕错", "拼错", "asr", "transcript", "transcription",
    ),
    "execution_issue": (
        "剪辑痕迹", "剪得太明显", "声音明显小", "声音变小", "音量变小", "变小了",
        "回声", "接缝", "断裂", "不自然", "不干净", "crossfade", "交叉淡化",
        "爆音", "咔", "啪", "剪长了", "剪短了", "边界不对", "边界太长",
        "多剪", "少剪", "听不出剪了但少了", "render", "rendering", "artifacts",
    ),
    "false_positive": (
        "误报", "不是口癖", "不是一个口癖", "完整的词", "完整单词", "内容词",
        "一个词", "完整的单词", "完整的词", "候选不对", "没听出剪了什么", "不该提名", "不应该出现",
        "asr 拆", "拆成", "词内", "专有名词", "没有这个候选", "沒有這個候選",
    ),
    "semantic_keep": (
        "保留", "不用剪", "不剪", "不删", "留着", "活人感", "认可", "连接词",
        "完整句", "语义完整", "这个其实可以不用剪", "不减不减",
        "对别人的认可", "保留活人感",
    ),
    "semantic_cut": (
        "可以剪", "应该剪", "删掉", "剪掉", "去掉", "多余", "重复", "重说",
        "长停顿", "离题", "口癖", "卡顿", "不需要", "前一个", "后一个",
    ),
}


# Direct negation is more reliable than a broad word list.  In particular,
# "根本没有" is a false-positive statement only when it refers to a transient
# event family; treating every occurrence as a false positive would turn e.g.
# "根本没有必要剪" into the wrong category.
_NEGATED_EVENT_TERMS = (
    "咳嗽",
    "咳",
    "碰麦",
    "碰麥",
    "碰到麦",
    "碰到麥",
    "麦克风碰",
    "麥克風碰",
    "mic bump",
    "microphone bump",
)
_NEGATION_PREFIX = r"(?:根本|完全|压根|壓根|并|並)?(?:没有|沒有|没|沒|无|無|并无|並無|不是|未)"
_TRANSIENT_REASON_MARKERS = ("cough_like", "mic_bump_like", "thump_like")


def _negated_false_positive_hits(text: str, reason_key: str) -> list[str]:
    """Return explicit evidence that the nominated sound event is absent.

    The result intentionally records the matched wording, rather than merely
    adding a hidden score: future reviewers can see why the candidate was
    classified as a false positive.
    """
    compact = re.sub(r"\s+", "", text.casefold())
    evidence: list[str] = []
    for term in _NEGATED_EVENT_TERMS:
        compact_term = re.sub(r"\s+", "", term.casefold())
        match = re.search(rf"{_NEGATION_PREFIX}(?:这个|這個|任何|一點|一点)?{re.escape(compact_term)}", compact)
        if match:
            evidence.append(f"否定事件：{match.group(0)}")

    # A reviewer often writes only "根本没有" because the candidate card
    # already says cough/mic-bump.  Permit that shorthand only for the three
    # transient families, never for a normal speech/filler candidate.
    normalized_reason = str(reason_key or "").casefold()
    if any(marker in normalized_reason for marker in _TRANSIENT_REASON_MARKERS):
        shorthand = re.search(
            r"(?:根本|完全|压根|壓根)(?:没有|沒有|没|沒|无|無|并无|並無)(?:这个|這個)?(?:候选|候選|声音|聲音|东西|東西|事件)?",
            compact,
        )
        if shorthand:
            evidence.append(f"瞬态候选否定：{shorthand.group(0)}")
    return evidence


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [pattern for pattern in patterns if pattern.lower() in text.lower()]


def _confidence(evidence: list[str]) -> float:
    return 0.0 if not evidence else round(min(0.99, 0.55 + 0.12 * len(evidence)), 3)


def _choose_primary_class(classes: list[str]) -> tuple[str, str]:
    for label in PRIMARY_CLASS_ORDER:
        if label in classes:
            if label == "false_positive":
                return label, "explicitly negates the nominated candidate/event"
            if label == "semantic_keep":
                return label, "explicit keep instruction outranks technical observations"
            if label == "semantic_cut":
                return label, "explicit cut instruction outranks technical observations"
            return label, "no explicit semantic instruction was detected"
    return "unknown", "no supported feedback class was detected"


def classify_feedback(feedback: Any, *, decision: str = "", reason_key: str = "") -> dict[str, Any]:
    text = re.sub(r"\s+", " ", str(feedback or "")).strip()
    if not text:
        return {
            "schema_version": CLASSIFICATION_VERSION,
            "class": "unknown",
            "primary_class": "unknown",
            "classes": [],
            "confidence": 0.0,
            "evidence": [],
            "evidence_by_class": {},
            "confidence_by_class": {},
            "primary_selection": {
                "policy": MULTILABEL_PRIMARY_POLICY,
                "reason": "no feedback was supplied",
            },
            "source_feedback": "",
            "reason_key": reason_key,
            "decision": decision,
            "ambiguous_with": [],
        }

    evidence_by_class = {name: _hits(text, patterns) for name, patterns in PATTERNS.items()}
    negated_event_hits = _negated_false_positive_hits(text, reason_key)
    if negated_event_hits:
        evidence_by_class["false_positive"].extend(negated_event_hits)
    classes = [name for name in PRIMARY_CLASS_ORDER if evidence_by_class.get(name)]
    label, primary_reason = _choose_primary_class(classes)
    confidence_by_class = {name: _confidence(evidence_by_class[name]) for name in classes}
    evidence = list(evidence_by_class.get(label, ()))
    return {
        "schema_version": CLASSIFICATION_VERSION,
        # Legacy consumers read `class`; retain it as the compatibility alias.
        "class": label,
        "primary_class": label,
        "classes": classes,
        "confidence": confidence_by_class.get(label, 0.0),
        "evidence": evidence,
        "evidence_by_class": {name: evidence_by_class[name] for name in classes},
        "confidence_by_class": confidence_by_class,
        "primary_selection": {
            "policy": MULTILABEL_PRIMARY_POLICY,
            "priority_order": list(PRIMARY_CLASS_ORDER),
            "reason": primary_reason,
        },
        "source_feedback": text,
        "reason_key": reason_key,
        "decision": decision,
        # `ambiguous_with` used to mean an equal-score tie.  Multiple classes
        # are now intentional, separately evidenced observations, not a tie.
        "ambiguous_with": [],
    }


def classify_record(record: dict[str, Any]) -> dict[str, Any]:
    output = dict(record)
    candidate = record.get("candidate") or {}
    label = record.get("label") or {}
    output["feedback_classification"] = classify_feedback(
        label.get("feedback"),
        decision=str(label.get("decision") or ""),
        reason_key=str(candidate.get("reason_key") or "unknown"),
    )
    return output
