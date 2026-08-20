#!/usr/bin/env python3
"""evaluate_candidate_safety — Challenger cross-track-safety-v1 的单一决策函数。

输入：
    candidate: {reason_key, source_track, start_seconds, end_seconds}
    words:     {"female": [{s,e,cls,t}, ...], "male": [...]}
    context_seconds: 上下文半径（默认 5.0）

输出：
    {
      "decision": "SAFE" | "BLOCK" | "NEEDS_HUMAN_REVIEW" | "FAIL_CLOSED",
      "reason_codes": [str, ...],
      "cut_stats": {"female": {...}, "male": {...}},
      "context_stats": {"female": {...}, "male": {...}}
    }

规则（与 rules/candidate-generation.safety-v1.json 严格对齐）：
  0. FAIL_CLOSED  — 任一 word 缺 cls（None / "unknown"）
  1. BLOCK        — 源轨在 cut 窗口内无词（SOURCE_HAS_NO_WORDS）
  2. BLOCK        — 源轨 primary=0 或 primary<bleed（SOURCE_NOT_PRIMARY）
  3. BLOCK        — 另一轨 cut 窗口内 primary>0（OTHER_TRACK_PRIMARY_SPEECH）
  4. NEEDS_HUMAN  — 任一轨 cut 窗口 ambiguous/total > 0.5（AMBIGUOUS_ACTIVITY）
  5. SAFE         — 其余
可能同时触发多条 BLOCK 规则，reason_codes 累积；只要有 BLOCK 类，决策就是 BLOCK。
FAIL_CLOSED 优先级最高（数据缺失优于任何"看似安全"的判定）。
"""
from __future__ import annotations

from typing import Any


def _words_in_window(words: list[dict], start: float, end: float) -> list[dict]:
    """半开区间重叠：word.start < end AND word.end > start"""
    return [w for w in words if w["s"] < end and w["e"] > start]


def _cls_counts(ws: list[dict]) -> dict[str, int]:
    out = {"primary": 0, "bleed": 0, "ambiguous": 0, "unknown": 0, "total": len(ws)}
    for w in ws:
        c = w.get("cls")
        if c in ("primary", "bleed", "ambiguous"):
            out[c] += 1
        else:
            out["unknown"] += 1
    return out


def evaluate_candidate_safety(
    candidate: dict[str, Any],
    words: dict[str, list[dict]],
    context_seconds: float = 5.0,
) -> dict[str, Any]:
    src = candidate["source_track"]
    other = "male" if src == "female" else "female"
    cs, ce = float(candidate["start_seconds"]), float(candidate["end_seconds"])
    ws, we = max(0.0, cs - context_seconds), ce + context_seconds

    if src not in words or other not in words:
        return {
            "decision": "FAIL_CLOSED",
            "reason_codes": ["MISSING_ACTIVITY_DATA"],
            "cut_stats": {},
            "context_stats": {},
            "note": f"missing track key: {src if src not in words else other}",
        }

    reason_codes: list[str] = []

    src_cut = _words_in_window(words[src], cs, ce)
    other_cut = _words_in_window(words[other], cs, ce)
    src_ctx = _words_in_window(words[src], ws, we)
    other_ctx = _words_in_window(words[other], ws, we)

    # 规则 0：cls 缺失 → FAIL_CLOSED
    for ws_batch in (src_cut, other_cut, src_ctx, other_ctx):
        for w in ws_batch:
            if w.get("cls") in (None, "unknown", ""):
                return {
                    "decision": "FAIL_CLOSED",
                    "reason_codes": ["MISSING_ACTIVITY_DATA"],
                    "cut_stats": {src: _cls_counts(src_cut), other: _cls_counts(other_cut)},
                    "context_stats": {src: _cls_counts(src_ctx), other: _cls_counts(other_ctx)},
                }

    src_c = _cls_counts(src_cut)
    other_c = _cls_counts(other_cut)
    src_ctx_c = _cls_counts(src_ctx)
    other_ctx_c = _cls_counts(other_ctx)

    # 规则 1：源轨 cut 窗口无词
    if src_c["total"] == 0:
        reason_codes.append("SOURCE_HAS_NO_WORDS")

    # 规则 2：源轨 primary 主导性
    if src_c["total"] > 0:
        if src_c["primary"] == 0 or src_c["primary"] < src_c["bleed"]:
            reason_codes.append("SOURCE_NOT_PRIMARY")

    # 规则 3：另一轨 cut 窗口 primary
    if other_c["primary"] > 0:
        reason_codes.append("OTHER_TRACK_PRIMARY_SPEECH")

    if reason_codes:
        return {
            "decision": "BLOCK",
            "reason_codes": reason_codes,
            "cut_stats": {src: src_c, other: other_c},
            "context_stats": {src: src_ctx_c, other: other_ctx_c},
        }

    # 规则 4：ambiguous 占比 > 0.5（两轨任一）
    for label, cnts in ((src, src_c), (other, other_c)):
        if cnts["total"] > 0 and cnts["ambiguous"] / cnts["total"] > 0.5:
            return {
                "decision": "NEEDS_HUMAN_REVIEW",
                "reason_codes": ["AMBIGUOUS_ACTIVITY"],
                "cut_stats": {src: src_c, other: other_c},
                "context_stats": {src: src_ctx_c, other: other_ctx_c},
                "note": f"{label} ambiguous ratio = {cnts['ambiguous']}/{cnts['total']}",
            }

    return {
        "decision": "SAFE",
        "reason_codes": [],
        "cut_stats": {src: src_c, other: other_c},
        "context_stats": {src: src_ctx_c, other: other_ctx_c},
    }
