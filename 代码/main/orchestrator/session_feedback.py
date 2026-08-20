#!/usr/bin/env python3
"""session_feedback · Q4 · 备注记忆机制 (v20.6, 2026-08-18)

**动机**: 用户明确指出"同一个音频反馈过很多次还问, 有些问题一直出现". 根因是
`labels_lake` 只存 accept/reject 决定, feedback 备注字段丢. 本模块把用户
chat 反馈 + 历史 human_decisions.feedback 都消费为下游 signal.

**数据源**:
    main/knowledge/session_feedback/EP0X.session_feedback.jsonl
        · 每 turn 用户 chat 反馈 append (含 kind/candidate_pattern/verdict/note)
    main/knowledge/labels_lake.json
        · entries[].feedback[] (build_labels_lake 从 human_decisions 汇总)

**下游消费**:
    run_end_to_end.py Stage 3.3 stage_feedback_lookup 调 inject_into_candidates
    apply_autocut_gate G7 消费 candidate.previous_user_feedback:
        verdict=never_cut  → hard reject (不进 auto_cut)
        verdict=needs_extension → 记录到 gate report, 不 reject
        verdict=pause_required → 提示 candidate 需检查 pause_ms

**Schema**:
    session-feedback-v1:
        timestamp: ISO 8601
        episode_id: str (可以是 EP04, ALL 表跨期通用)
        reviewer: str
        source: claude-code-session / api-save / manual
        kind: boundary/segment_pause/scope/english_fragment/host_backchannel/
              volume/boundary_precision/self_write/representativeness/pronoun_exemption
        candidate_pattern: dict (filler_token/reason_key/context/speaker_role 等)
        verdict: never_cut/needs_extension/pause_required/both_spans_or_none
        note: 用户原话
        action_taken: 系统响应描述 (可空)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_session_feedback(episode_id: str, project_root: Path | None = None) -> list[dict[str, Any]]:
    """加载指定 episode + ALL 跨期反馈."""
    root = project_root or PROJECT_ROOT
    feedback_dir = root / "main" / "knowledge" / "session_feedback"
    if not feedback_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for name in [f"{episode_id}.session_feedback.jsonl", "ALL.session_feedback.jsonl"]:
        p = feedback_dir / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_lake_feedback(project_root: Path | None = None) -> list[dict[str, Any]]:
    """从 labels_lake.json 提取 entries[].feedback[] 展平列表."""
    root = project_root or PROJECT_ROOT
    p = root / "main" / "knowledge" / "labels_lake.json"
    if not p.is_file():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for entry in d.get("entries", []):
        for fb in entry.get("feedback", []) or []:
            out.append({
                "source": "lake",
                "reason_key": entry.get("reason_key"),
                "filler_token": entry.get("filler_token"),
                "candidate_pattern": {
                    "reason_key": entry.get("reason_key"),
                    "filler_token": entry.get("filler_token"),
                },
                "verdict": fb.get("verdict") or fb.get("decision"),
                "note": fb.get("note") or fb.get("feedback") or "",
                "action_taken": fb.get("action_taken"),
            })
    return out


def _pattern_matches(pattern: dict, candidate: dict) -> bool:
    """反馈 pattern vs 候选逐字段匹配 · 支持 list 或 dict 或 bool.

    pattern={"any": True}  → 全匹配
    pattern={"filler_token": "呃"} → 单值精确
    pattern={"filler_token": ["嗯", "啊"]} → 任一命中
    pattern={"reason_key": "immediate_repetition", "context": "before_segment_separator"}
        → 全 key 命中(context 特殊字段, 若候选无则跳过)
    """
    if not pattern:
        return False
    if pattern.get("any"):
        return True
    for k, v in pattern.items():
        if k == "any":
            continue
        cand_v = candidate.get(k)
        if k == "context" and cand_v is None:
            # context 是 pattern-only 字段 (如 english_compound, before_segment_separator)
            # 候选没直接暴露, 跳过这条 key
            continue
        if k == "speaker_role" and cand_v is None:
            continue
        if isinstance(v, list):
            if cand_v not in v:
                return False
        else:
            if cand_v != v:
                return False
    return True


def match_feedback_to_candidate(
    candidate: dict,
    feedback_list: list[dict],
) -> list[dict]:
    """对一个候选找出所有匹配的历史反馈."""
    hits: list[dict] = []
    for fb in feedback_list:
        pattern = fb.get("candidate_pattern") or {}
        if _pattern_matches(pattern, candidate):
            hits.append(fb)
    return hits


def inject_into_candidates(
    candidates: list[dict],
    session_feedback: list[dict],
    lake_feedback: list[dict],
) -> tuple[list[dict], dict]:
    """对每候选写 previous_user_feedback 字段. 返回 (candidates_updated, summary)."""
    total_hits = 0
    never_cut_hits = 0
    for c in candidates:
        matched_session = match_feedback_to_candidate(c, session_feedback)
        matched_lake = match_feedback_to_candidate(c, lake_feedback)
        matched = matched_session + matched_lake
        if not matched:
            continue
        # 存 minimal 结构 · 保 note + verdict + source + kind
        c["previous_user_feedback"] = [
            {
                "source": fb.get("source", "session"),
                "kind": fb.get("kind"),
                "verdict": fb.get("verdict"),
                "note": fb.get("note", "")[:200],
                "action_taken": fb.get("action_taken", "")[:200] if fb.get("action_taken") else None,
                "timestamp": fb.get("timestamp"),
            }
            for fb in matched
        ]
        total_hits += len(matched)
        if any(fb.get("verdict") == "never_cut" for fb in matched):
            never_cut_hits += 1
    return candidates, {
        "total_hits": total_hits,
        "never_cut_hits": never_cut_hits,
        "candidates_with_feedback": sum(1 for c in candidates if c.get("previous_user_feedback")),
    }


def candidate_has_never_cut_feedback(candidate: dict) -> bool:
    """G7 快速检查: 是否有 verdict=never_cut 反馈."""
    fbs = candidate.get("previous_user_feedback") or []
    return any(str(fb.get("verdict")) == "never_cut" for fb in fbs)


def candidate_has_needs_extension_feedback(candidate: dict) -> bool:
    """MFA 精修已解决 · 报到 gate report 但不 reject."""
    fbs = candidate.get("previous_user_feedback") or []
    return any(str(fb.get("verdict")) == "needs_extension" for fb in fbs)
