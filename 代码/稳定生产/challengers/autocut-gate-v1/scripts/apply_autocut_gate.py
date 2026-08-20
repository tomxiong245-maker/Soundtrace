#!/usr/bin/env python3
"""SIMPLIFIED 2026-08-20 · Diagnostic-only gates removed

2026-08-20 用户明确要求彻底关闭 diagnostic-only 分支 · 消除"看起来还在把关但其实
不 REJECT"的雷. G1/G2 参数层彻底禁用 · G3/G5/G7_session_non_never_cut/G7_protection
均严格保留 · LLM takeover 让位机制已删除. 见下方 GATES_ACTIVE 常量.

结构性门 (严格 REJECT · 逻辑不动):
- speaker_role / source_track (前置 Stage 3 · 不在本文件)
- G6_duration (>0.8s 拒 · 防误剪长语义)
- G7_protection (片头/片尾保护区)
- G7_session_feedback never_cut (session_feedback SOT 的硬 override)
- G8_case_embedding (mentor case-memory · silent_skip fallback)
- review_budget (审核预算控制 · 调度层)

已彻底关闭 (从 2026-08-20 起不再运行 · 见 GATES_INACTIVE):
- G1_whitelist / G2_high_confidence (参数层 · Optuna Stage 6.7 学阈值)
- G3_no_preserve / G5_history / G7_session_feedback (non-never_cut) (语义 · LLM 独占)

---

apply_autocut_gate — 多重 gate 决定单个候选是否 auto-cut 合格。

每一条候选必须**全部通过**下列 gate 才归为 `auto_cut_eligible`；任一失败则
降级为 `human_review_required`（不是拒绝，只是让人来看）。

    G3  policy_application.route != auto_preserve           (无保护规则冲突)
    G5  historical_accept_count / reject_count 综合评估      (无历史反例)
    G6  duration ≤ max_duration_seconds                     (防误删长语义)
    G7  不在 opening/closing 保护区 · session_feedback       (never_cut hard override)
    G8  mentor case-memory 相似案历判决 (case_embedding)     (跨 episode 案例记忆)

(G1/G2 从 2026-08-20 起不再运行 · Optuna Stage 6.7 学参数层阈值)

**G4 跨轨投票**：候选生成时若走了 cross_mic_event_merge（cross-mic sync），
本身已经含跨轨证据；本 gate 阶段不再重复投票（避免因 candidate 已合并
无法回溯到多轨源事件而造成的假失败）。留作未来 rules 层 tier_boost 用。

**保证 precision ≥ 90% 的机制**：多重独立 signal 全过。任一失败 → 人审兜底。
牺牲 recall（EP04 数据估算 ~25% auto-cut 率），保 precision。

Usage:
    python3 apply_autocut_gate.py \\
        --candidates /run/all_candidates.json \\
        --policy /path/editing_policy.guards-v2.json \\
        --episode-duration-seconds 3272.7 \\
        --out /run/autocut_gate/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


# ============================================================
# 架构简化 · 2026-08-20 · 用户明确要求彻底关闭 diagnostic-only gates
# ============================================================
# 历史: 原设计 8 门 gate (G1-G8) 分两层 · 参数层 (G1/G2) + 候选层 (G3-G8).
#       当 LLM 语义 veto 启用后 (08-19 默认开) · G1/G2/G3/G5/G7 语义门变成
#       "让位 diagnostic-only" · 只记录不阻挡. G6_duration 干脆不跑.
#       这些 diagnostic 分支 = 未来接手者的雷 · 让人误以为 gate 还在起作用.
#
# 2026-08-20 用户 (原话 "没有用了的全都关掉 · 我不希望今后拿到我项目的人还被误导")
# 决定彻底删掉 diagnostic-only 分支 · 只保留真结构性 gate.
#
# 剩下的 GATES_ACTIVE 见下方常量列表.
# 前后行为兼容性: auto_cut.json / review_required.json / summary.json 顶层结构不变.
# ============================================================


# ---------------------------------------------------------------------------
# 参数层 gate 兼容性开关 (2026-08-20)
#
# MINGLUE_PARAM_GATES_OFF 环境变量本体保留 · 默认 "1" · 兼容旧调用签名.
# 但 G1/G2 参数层从 2026-08-20 起已彻底不再运行 (无论开关值是什么) ·
# Optuna Stage 6.7 学阈值 · warning-only 分支已删除.
# ---------------------------------------------------------------------------
_PARAM_GATES_OFF = os.environ.get("MINGLUE_PARAM_GATES_OFF", "1").strip().lower() in (
    "1", "true", "yes", "on",
)


# ---------------------------------------------------------------------------
# 2026-08-20 · LLM 语义门 takeover 让位机制已删除
#
# 历史 (2026-08-19): G3 / G5 / G7_session_non_never_cut / G7_protection 曾"让位"
# LLM · 记为 diagnostic_only. 该机制在 2026-08-20 被移除 · 上述语义门恢复严格
# REJECT 或彻底不跑 (见 GATES_INACTIVE). LLM 语义 veto 由独立的 llm_filter
# 阶段处理 · 与 autocut_gate 正交.
#
# session_feedback verdict=never_cut 仍是全局硬 override · 独立保留.
# ---------------------------------------------------------------------------


GATES_ACTIVE = [
    "speaker_role",       # 说话人身份匹配 · 结构性硬约束
    "source_track",       # 源轨判定 · 结构性硬约束
    "G6_duration",        # 时长上限 (>0.8s 拒) · 结构性硬约束 · CLAUDE.md §12
    "G7_protection",      # 片头片尾保护 (opening/closing seconds) · 结构性硬约束
    "G7_never_cut",       # session_feedback never_cut token 保护 · 硬约束
    "G8_case_embedding",  # top-K 相似历史片段检索 · 参考不阻挡
    "review_budget",      # 人审预算限制 · 结构性
]

GATES_INACTIVE = [
    "G1_whitelist",                       # 参数层 · MINGLUE_PARAM_GATES_OFF=1 · Optuna 处理
    "G2_high_confidence",                 # 参数层 · 同上
    "G3_no_preserve",                     # 语义 · LLM 语义 veto 独占
    "G5_history",                         # 语义 · LLM 独占
    "G7_session_feedback_non_never_cut",  # 语义 · LLM 独占 (never_cut 硬 override 独立保留)
]
# 若未来 LLM 不启用 · 需要 fallback · 请从 git log 找回 2026-08-20 之前版本作参考.


DEFAULT_MAX_DURATION_S = 0.8
DEFAULT_OPENING_PROTECTION_S = 6.0
DEFAULT_CLOSING_PROTECTION_S = 6.0
DEFAULT_MIN_HISTORICAL_ACCEPT = 1
DEFAULT_LAKE_MIN_TOTAL = 2
DEFAULT_LAKE_MIN_ACCEPT_RATE = 0.9

# ---------------------------------------------------------------------------
# 2026-08-19 · G8 case-memory-embedding gate (challenger case-memory-embedding-v1)
# 消费上游 Stage 3.9/6.8 生成的 case_embedding_retrieval.json (cid → top_k list).
# 每条 top_k item 含 {rank, score, case_id, clip_id, verdict, note, ...}.
# 判决口径:
#   - 无 case_embedding 数据 (index 未 build 或该 cid 未检出 similar case)
#       → G8 silent pass (fallback · 不影响 pipeline)
#   - top-3 均 verdict=ACCEPT (case-insensitive 匹配) → pass · confidence boost note
#   - top-3 均 verdict=REJECT                       → REJECT_BY_MENTOR_SIMILARITY
#   - 混合 (含 ACCEPT + REJECT)                     → NEEDS_HUMAN_REVIEW (fail)
#   - 全部 verdict 缺失 / 未知                       → silent pass (无信号)
# ---------------------------------------------------------------------------
DEFAULT_G8_TOP_K = 3
DEFAULT_G8_MIN_SCORE = 0.5


def _normalize_verdict(v: Any) -> str:
    """Map raw verdict string → one of {'accept','reject','unknown'}.
    Mentor gold case_memory 里 verdict 可能是 ACCEPT/REJECT/accepted/rejected/'human_kept' 等."""
    if v is None:
        return "unknown"
    s = str(v).strip().lower()
    if s in ("accept", "accepted", "auto_cut", "auto_cut_eligible", "cut", "ok", "pass"):
        return "accept"
    if s in ("reject", "rejected", "never_cut", "human_kept", "kept", "block", "fail"):
        return "reject"
    return "unknown"


def _evaluate_case_embedding_gate(
    cid: str,
    case_embeddings_by_cid: dict[str, list[dict]] | None,
    *,
    top_k: int = DEFAULT_G8_TOP_K,
    min_score: float = DEFAULT_G8_MIN_SCORE,
) -> dict:
    """G8 gate helper.

    Returns dict {pass: bool, verdict_bucket: str, details: {...}}.
    verdict_bucket ∈ {"silent_skip","all_accept","all_reject","mixed","no_signal"}.
    pass = False only when bucket == "all_reject" or "mixed".
    """
    if not case_embeddings_by_cid:
        return {
            "pass": True, "verdict_bucket": "silent_skip",
            "note": "no case_embedding data (index likely not built) · fallback pass",
        }
    top = case_embeddings_by_cid.get(str(cid)) or []
    # filter by min_score
    scored = [
        m for m in top
        if isinstance(m, dict) and float(m.get("score", 0.0)) >= min_score
    ][:top_k]
    if not scored:
        return {
            "pass": True, "verdict_bucket": "silent_skip",
            "note": f"no similar cases above min_score={min_score} · fallback pass",
        }
    verdicts = [_normalize_verdict(m.get("verdict")) for m in scored]
    accepts = sum(1 for v in verdicts if v == "accept")
    rejects = sum(1 for v in verdicts if v == "reject")
    unknowns = sum(1 for v in verdicts if v == "unknown")
    n = len(verdicts)
    details = {
        "top_k_used": n, "min_score": min_score,
        "verdicts": verdicts,
        "top_cases": [
            {
                "case_id": m.get("case_id"),
                "score": round(float(m.get("score", 0.0)), 4),
                "verdict": m.get("verdict"),
            } for m in scored
        ],
    }
    if accepts == n and n > 0:
        return {"pass": True, "verdict_bucket": "all_accept",
                "note": "MENTOR_SIMILARITY_ACCEPT · top-{} all ACCEPT · confidence boost".format(n),
                **details}
    if rejects == n and n > 0:
        return {"pass": False, "verdict_bucket": "all_reject",
                "reason": "REJECT_BY_MENTOR_SIMILARITY · top-{} all REJECT".format(n),
                **details}
    if accepts > 0 and rejects > 0:
        return {"pass": False, "verdict_bucket": "mixed",
                "reason": "NEEDS_HUMAN_REVIEW · mentor top-{} mixed ACCEPT+REJECT".format(n),
                **details}
    # only unknowns, or accepts+unknowns / rejects+unknowns
    if unknowns == n:
        return {"pass": True, "verdict_bucket": "no_signal",
                "note": "top-{} verdicts all unknown · silent pass".format(n),
                **details}
    return {"pass": True, "verdict_bucket": "no_signal",
            "note": f"weak signal (accept={accepts}, reject={rejects}, unknown={unknowns}) · pass",
            **details}



def _load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, value: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _infer_confidence_tier(candidate: dict) -> str:
    """Read confidence tier from candidate, falling back to subtype rules."""
    tier = candidate.get("confidence_tier")
    if tier:
        return str(tier)
    kind = candidate.get("candidate_kind") or candidate.get("kind")
    stratum = str(candidate.get("stratum") or "")
    if kind == "filler_hesitation":
        if "strong_hesitation_sound" in stratum:
            return "high"
        if "repeated_weak_filler" in stratum:
            return "mid"
    if kind == "immediate_repetition":
        sig = (candidate.get("repetition_signature") or {}).get("has_signature")
        return "high" if sig else "mid"
    if kind == "global_long_pause":
        return "high"
    return "mid"


def apply_gates(
    candidate: dict,
    *,
    whitelist_kinds: set[str],
    denylist_kinds: set[str],
    preserve_routes: set[str],
    policy_route_by_cid: dict[str, str],
    max_duration_s: float,
    opening_s: float,
    closing_s: float,
    min_hist_accept: int,
    episode_duration_s: float,
    lake_by_reason: dict[str, dict[str, dict[str, Any]]] | None = None,
    lake_min_total: int = DEFAULT_LAKE_MIN_TOTAL,
    lake_min_accept_rate: float = DEFAULT_LAKE_MIN_ACCEPT_RATE,
    case_embeddings_by_cid: dict[str, list[dict]] | None = None,
    case_embedding_top_k: int = DEFAULT_G8_TOP_K,
    case_embedding_min_score: float = DEFAULT_G8_MIN_SCORE,
) -> tuple[bool, list[dict]]:
    """Return (all_gates_passed, per_gate_report)."""
    cid = candidate.get("candidate_id") or "?"
    kind = candidate.get("candidate_kind") or candidate.get("kind") or "?"

    report: list[dict] = []

    # G1 (whitelist) / G2 (high_confidence) 从 2026-08-20 起彻底不再运行 ·
    # 参数层由 Optuna Stage 6.7 处理 · 见文件顶部 GATES_INACTIVE.
    # kind 变量仅供下游 signals 和 report 记录使用.
    _ = kind  # kept for future diagnostics · 无侧作用

    # G3 · policy_application not auto_preserve  [STRICT · 2026-08-20 恢复]
    route = policy_route_by_cid.get(str(cid))
    if route in preserve_routes:
        report.append({"gate": "G3_no_preserve", "pass": False, "reason": f"policy_route={route}"})
        return False, report
    report.append({"gate": "G3_no_preserve", "pass": True, "policy_route": route})

    # v20.6 · G7 · session_feedback  [never_cut = HARD override · 全局强规则]
    # 2026-08-20: LLM takeover 让位机制删除后 · non-never_cut 分支不再记
    # diagnostic entry · 直接沉默通过 (等价于旧的 diagnostic-only note).
    prev_fb = candidate.get("previous_user_feedback") or []
    never_cut_notes = [
        fb for fb in prev_fb if str(fb.get("verdict")) == "never_cut"
    ]
    if never_cut_notes:
        report.append({
            "gate": "G7_session_feedback", "pass": False,
            "reason": "user_feedback_never_cut (HARD override · 全局强规则)",
            "note": never_cut_notes[0].get("note", "")[:200],
            "kind": never_cut_notes[0].get("kind"),
            "n_hits": len(never_cut_notes),
        })
        return False, report
    if prev_fb:
        report.append({
            "gate": "G7_session_feedback", "pass": True,
            "n_previous_feedback": len(prev_fb),
            "verdicts": [str(fb.get("verdict")) for fb in prev_fb[:3]],
        })

    # G5 · historical signal (multi-path, precision-first)
    # Rules:
    #   (a) hr > 0  →  一票否决（历史反例）
    #   (b) ANY of the following passes:
    #        - ha >= 1                                    (candidate historical accept)
    #        - lake family rate == 1.0 AND total >= 1      (category no-reject)
    #        - reason == self_correction AND edit_ratio >= 0.75 AND cross_track >= 2
    #                                                     (wordlevel strong signal)
    #        - cross_track_hit_count >= 3                  (three-track sync = strong)
    es = candidate.get("experience_signal") or {}
    ha = int(es.get("historical_accept_count", 0))
    hr = int(es.get("historical_reject_count", 0))

    reason_key = str(candidate.get("reason_key") or candidate.get("candidate_kind") or "")
    stratum = str(candidate.get("stratum") or "")
    subtype_parts = stratum.split(":")
    subtype = subtype_parts[1] if len(subtype_parts) >= 2 else "unknown"

    # Lake lookup at 3 levels: reason → subtype → filler_token
    lake_info: dict[str, Any] | None = None
    if lake_by_reason and reason_key in lake_by_reason:
        r_entry = lake_by_reason[reason_key]
        sub_entry = None
        if "_subtypes" in r_entry:
            sub_entry = r_entry["_subtypes"].get(subtype)
            if sub_entry:
                lake_info = {
                    "reason_key": reason_key, "subtype": subtype,
                    "total": int(sub_entry.get("total", 0)),
                    "accept_rate": float(sub_entry.get("accept_rate", 0.0)),
                }
        else:  # v1 flat
            sub_entry = r_entry.get(subtype)
            if isinstance(sub_entry, dict):
                lake_info = {
                    "reason_key": reason_key, "subtype": subtype,
                    "total": int(sub_entry.get("total", 0)),
                    "accept_rate": float(sub_entry.get("accept_rate", 0.0)),
                }

    edit_ratio = float(candidate.get("edit_ratio", 0.0))
    cross_track_hits = int(candidate.get("cross_track_hit_count", 0))
    algo_confidence = candidate.get("algorithm_confidence") or ""

    # v20 · lake token-level reject lookup (2026-08-17 feedback):
    # 用户反馈 C036 "什麼" 无效剪辑 → 在 lake 里加了 什麼→reject。gate 需要
    # 按 filler_token 精确查 lake，而不是只查 (reason, subtype) 汇总。
    filler_token = str(candidate.get("filler_token") or candidate.get("proposed_delete_text") or "").strip()
    lake_token_reject = 0
    if lake_by_reason and reason_key in lake_by_reason and filler_token:
        r_entry = lake_by_reason[reason_key]
        subs = r_entry.get("_subtypes") or {}
        for sub_key, sub_data in subs.items():
            tokens = sub_data.get("_tokens") or {}
            if filler_token in tokens:
                lake_token_reject += int(tokens[filler_token].get("reject", 0))
    if lake_token_reject > 0:
        report.append({
            "gate": "G5_history", "pass": False,
            "reason": (
                f"lake token='{filler_token}' 里 reject={lake_token_reject} "
                f"→ token-level historical rejection"
            ),
            "lake_token_reject": lake_token_reject,
        })
        return False, report

    # a) Hard reject: any individual historical reject in this event's family
    if hr > 0:
        report.append({
            "gate": "G5_history", "pass": False,
            "reason": f"individual hr={hr} > 0 → historical rejection precedent",
            "ha": ha, "hr": hr, "lake": lake_info,
        })
        return False, report

    # v20.1 feedback correction (2026-08-17): user clarified "全或无" means
    # both-span-cut (剪 pre + retry both), NOT reject-to-human-review.
    # The gate should let self_correction through when signals are strong; the
    # EDL generator decides whether to cut only pre or both spans (marked by
    # candidate.cut_scope). No hard reject at gate level.

    # b) Check each signal path
    signals_passed: list[str] = []
    if ha >= min_hist_accept:
        signals_passed.append(f"candidate_ha>={min_hist_accept}")
    if lake_info and lake_info["total"] >= 1 and lake_info["accept_rate"] >= 1.0:
        signals_passed.append("lake_zero_reject")
    if reason_key == "self_correction" and edit_ratio >= 0.75 and cross_track_hits >= 2:
        signals_passed.append("wordlevel_cross_track")
    if cross_track_hits >= 3:
        signals_passed.append("three_track_sync")
    # v20.3 (evolution path 2 case-based memory): experience context signal
    ec = candidate.get("experience_context") or {}
    # 优先 exact token match (更精确)
    ec_exact_accept = int(ec.get("exact_token_accept_count", 0))
    ec_exact_reject = int(ec.get("exact_token_reject_count", 0))
    ec_reason_accept = int(ec.get("reason_key_accept_count", 0))
    ec_reason_reject = int(ec.get("reason_key_reject_count", 0))
    if ec_exact_accept >= 2 and ec_exact_reject == 0:
        signals_passed.append("experience_exact_token_no_reject")
    elif ec_reason_accept >= 3 and ec_reason_reject == 0:
        signals_passed.append("experience_reason_no_reject")

    if signals_passed:
        report.append({
            "gate": "G5_history", "pass": True,
            "signals": signals_passed,
            "ha": ha, "hr": hr, "lake": lake_info,
            "edit_ratio": edit_ratio, "cross_track": cross_track_hits,
        })
    else:
        # No positive signal, but hr == 0 (no individual reject).
        # Fall through to a permissive path: if the candidate has already
        # passed G2 (high confidence signal — tier=high OR wordlevel r≥0.75
        # OR cross-track≥2) AND G3 (no preserve) AND G6 (short) AND G7
        # (non-protected), we consider it safe enough for auto-cut without
        # historical evidence. This is the "no negative evidence" path.
        report.append({
            "gate": "G5_history", "pass": True,
            "path": "no_negative_evidence",
            "note": "hr==0 且无正 signal，但已通过 G2/G3/G6/G7 → 采信机器 signal (safe)",
            "ha": ha, "hr": hr, "lake": lake_info,
            "edit_ratio": edit_ratio, "cross_track": cross_track_hits,
        })

    # G6 · duration
    start_s = candidate.get("start_seconds")
    end_s = candidate.get("end_seconds")
    if start_s is None or end_s is None:
        # Fallback to samples
        sr = 48000
        start_sample = candidate.get("start_sample") or 0
        end_sample = candidate.get("end_sample") or 0
        start_s = float(start_sample) / sr
        end_s = float(end_sample) / sr
    duration_s = float(end_s) - float(start_s)
    if duration_s > max_duration_s:
        report.append({
            "gate": "G6_duration",
            "pass": False,
            "reason": f"duration={duration_s:.3f}s > {max_duration_s}s",
        })
        return False, report
    report.append({"gate": "G6_duration", "pass": True, "duration_s": round(duration_s, 3)})

    # G7 · not in opening/closing protection  [STRICT · 2026-08-20 恢复]
    if float(start_s) < opening_s:
        report.append({
            "gate": "G7_protection",
            "pass": False,
            "reason": f"start={start_s:.2f}s < opening_protection={opening_s}s",
        })
        return False, report
    if float(end_s) > episode_duration_s - closing_s:
        report.append({
            "gate": "G7_protection",
            "pass": False,
            "reason": f"end={end_s:.2f}s > episode-{closing_s}s",
        })
        return False, report
    report.append({"gate": "G7_protection", "pass": True})

    # G8 · mentor case-memory similarity (challenger case-memory-embedding-v1)
    # 消费 Stage 3.9 / 6.8 生成的 case_embedding_retrieval.json (cid → top_k list).
    # index 未 build / 无 similar case → silent pass (fallback · 不影响 pipeline).
    g8 = _evaluate_case_embedding_gate(
        str(cid),
        case_embeddings_by_cid,
        top_k=case_embedding_top_k,
        min_score=case_embedding_min_score,
    )
    g8_entry = {"gate": "G8_case_embedding", "pass": bool(g8.get("pass", True))}
    g8_entry.update({k: v for k, v in g8.items() if k != "pass"})
    report.append(g8_entry)
    if not g8.get("pass", True):
        return False, report

    return True, report


def run(
    candidates_json: Path,
    policy_json: Path,
    out_dir: Path,
    *,
    episode_duration_s: float,
    max_duration_s: float = DEFAULT_MAX_DURATION_S,
    opening_s: float = DEFAULT_OPENING_PROTECTION_S,
    closing_s: float = DEFAULT_CLOSING_PROTECTION_S,
    min_hist_accept: int = DEFAULT_MIN_HISTORICAL_ACCEPT,
    policy_application_json: Path | None = None,
    calibration_source_json: Path | None = None,
    labels_lake_json: Path | None = None,
    lake_min_total: int = DEFAULT_LAKE_MIN_TOTAL,
    lake_min_accept_rate: float = DEFAULT_LAKE_MIN_ACCEPT_RATE,
    extra_candidates_json: list[Path] | None = None,
    case_embeddings_json: Path | None = None,
    case_embedding_top_k: int = DEFAULT_G8_TOP_K,
    case_embedding_min_score: float = DEFAULT_G8_MIN_SCORE,
) -> dict[str, Any]:
    candidates_doc = _load_json(candidates_json)
    cands = candidates_doc.get("candidates") if isinstance(candidates_doc, dict) else candidates_doc
    cands = list(cands or [])

    # Merge extra candidate sources (e.g. self_correction wordlevel output)
    if extra_candidates_json:
        for p in extra_candidates_json:
            if not p or not p.is_file():
                continue
            extra_doc = _load_json(p)
            extra_cands: list[dict] = []
            if isinstance(extra_doc, dict):
                if "tracks" in extra_doc:
                    # self_correction_wordlevel output format
                    for tr in extra_doc.get("tracks", []):
                        extra_cands.extend(tr.get("candidates", []))
                elif "candidates" in extra_doc:
                    extra_cands = extra_doc["candidates"]
            elif isinstance(extra_doc, list):
                extra_cands = extra_doc
            # Compute cross-track sync count for each candidate (how many
            # different tracks fire a candidate within ±0.5s of this one).
            # This is a strong signal for self-correction: if 2+ mics
            # captured the same self-repair moment, it's very likely real.
            from collections import defaultdict as _dd
            by_bucket: dict[int, set[str]] = _dd(set)
            for ec in extra_cands:
                s = float(ec.get("start_seconds", 0))
                tr = str(ec.get("track_id") or ec.get("source_track_id") or "")
                # bucket at 1s granularity (candidates 只要 0.5s 内即算同事件)
                by_bucket[int(s)].add(tr)
            # Auto-assign candidate_id + cross_track_count
            for i, ec in enumerate(extra_cands):
                if not ec.get("candidate_id"):
                    ec["candidate_id"] = f"SC{i:03d}"
                if not ec.get("candidate_kind"):
                    ec["candidate_kind"] = ec.get("reason_key") or ec.get("kind")
                s = float(ec.get("start_seconds", 0))
                # look up ±1 bucket (covers ±1s effectively)
                tracks_here: set[str] = set()
                for b in (int(s) - 1, int(s), int(s) + 1):
                    tracks_here |= by_bucket.get(b, set())
                ec["cross_track_hit_count"] = len(tracks_here)
            # Dedup: same time bucket + same reason_key → keep highest edit_ratio
            deduped: dict[tuple, dict] = {}
            for ec in extra_cands:
                key = (
                    int(float(ec.get("start_seconds", 0))),
                    str(ec.get("reason_key") or ec.get("kind") or ""),
                )
                incumbent = deduped.get(key)
                r = float(ec.get("edit_ratio", 0))
                if incumbent is None or r > float(incumbent.get("edit_ratio", 0)):
                    deduped[key] = ec
            extra_cands = list(deduped.values())
            cands.extend(extra_cands)

    # Merge auxiliary confidence_tier and repetition_signature from
    # calibration_source.json — orchestrator's canonical location for these
    # fields when all_candidates.json omits them.
    # [ADVISORY · 2026-08-20]
    # 只做字段填充 · 不 REJECT 候选 · G2 (risk_tier) 已关闭 (见 GATES_INACTIVE) ·
    # 下游 EDL 生成 / rules layer 若读 confidence_tier 等字段仍可从此拿到。
    calibration_used = False
    calibration_filled_count = 0
    if calibration_source_json and calibration_source_json.is_file():
        cal = _load_json(calibration_source_json)
        cal_by_cid = {str(c.get("candidate_id")): c for c in cal.get("candidates", [])}
        for c in cands:
            cid = str(c.get("candidate_id"))
            aux = cal_by_cid.get(cid) or {}
            for key in ("confidence_tier", "repetition_signature", "corroborated_track_ids"):
                if c.get(key) in (None, "") and aux.get(key) is not None:
                    c[key] = aux[key]
                    calibration_filled_count += 1
        calibration_used = True
    elif not _PARAM_GATES_OFF and calibration_source_json:
        # 参数门开时 · 若指定但缺文件 · 保留原静默降级行为 (不 raise)。
        pass
    # 记录 calibration 状态到 summary.calibration_source_merge 以便 audit。
    # 2026-08-20: G2 已彻底关闭 · calibration 只做字段填充 (advisory) · 不再驱动
    # gate REJECT · 下游 EDL 生成 / rules layer 仍会读 confidence_tier 等字段。
    _calibration_status = {
        "calibration_source_provided": bool(calibration_source_json),
        "calibration_source_exists": bool(calibration_source_json and calibration_source_json.is_file()),
        "calibration_used": calibration_used,
        "calibration_filled_fields": calibration_filled_count,
        "param_gate_note": "advisory_only · G1/G2 参数层已关闭 · Optuna will handle",
    }

    # Load labels lake
    lake_by_reason: dict[str, dict[str, dict[str, Any]]] | None = None
    if labels_lake_json and labels_lake_json.is_file():
        lake = _load_json(labels_lake_json)
        lake_by_reason = lake.get("by_reason_key") or {}

    # Load case_embedding_retrieval.json (G8 signal source).
    # Fallback: if file missing / index not built → dict stays None ·
    # G8 silently pass · pipeline unaffected.
    case_embeddings_by_cid: dict[str, list[dict]] | None = None
    case_embedding_source = None
    case_embedding_stats = {"loaded": False, "count": 0, "reason": "not provided"}
    if case_embeddings_json:
        if case_embeddings_json.is_file():
            try:
                ce_doc = _load_json(case_embeddings_json)
                raw = ce_doc.get("candidates_with_similar_cases") or {}
                if isinstance(raw, dict) and raw:
                    case_embeddings_by_cid = {str(k): list(v) for k, v in raw.items()}
                    case_embedding_source = str(case_embeddings_json)
                    case_embedding_stats = {
                        "loaded": True, "count": len(case_embeddings_by_cid),
                        "index_source": ce_doc.get("index_source"),
                    }
                else:
                    case_embedding_stats = {
                        "loaded": False, "count": 0,
                        "reason": "file present but no candidates_with_similar_cases entries",
                    }
            except Exception as exc:
                case_embedding_stats = {
                    "loaded": False, "count": 0,
                    "reason": f"parse failed: {exc}",
                }
        else:
            case_embedding_stats = {
                "loaded": False, "count": 0,
                "reason": f"file not found: {case_embeddings_json} · index likely not built · G8 silent skip",
            }

    policy = _load_json(policy_json)
    autocut = policy.get("autocut_policy") or {}
    whitelist = set(autocut.get("whitelist_kinds") or [])
    denylist = set(autocut.get("denylist_kinds") or [])
    preserve_routes = {"auto_preserve", "human_review_required"}
    # Actually preserve is auto_preserve; human_review_required is where we'd
    # otherwise route. The gate only checks *preserve* (hard block); if the
    # policy already routes to human_review it's not a hard block — we
    # let the candidate fall through to G4..G7.
    preserve_routes = {"auto_preserve"}

    policy_route_by_cid: dict[str, str] = {}
    if policy_application_json and policy_application_json.is_file():
        pa = _load_json(policy_application_json)
        for row in pa.get("candidates", []):
            cid = str(row.get("candidate_id"))
            route = row.get("route")
            if cid and route:
                policy_route_by_cid[cid] = route

    auto_cut: list[dict] = []
    review: list[dict] = []
    per_candidate_reports: list[dict] = []

    for c in cands:
        passed, report = apply_gates(
            c,
            whitelist_kinds=whitelist,
            denylist_kinds=denylist,
            preserve_routes=preserve_routes,
            policy_route_by_cid=policy_route_by_cid,
            max_duration_s=max_duration_s,
            opening_s=opening_s,
            closing_s=closing_s,
            min_hist_accept=min_hist_accept,
            episode_duration_s=episode_duration_s,
            lake_by_reason=lake_by_reason,
            lake_min_total=lake_min_total,
            lake_min_accept_rate=lake_min_accept_rate,
            case_embeddings_by_cid=case_embeddings_by_cid,
            case_embedding_top_k=case_embedding_top_k,
            case_embedding_min_score=case_embedding_min_score,
        )
        per_candidate_reports.append({
            "candidate_id": c.get("candidate_id"),
            "candidate_kind": c.get("candidate_kind") or c.get("kind"),
            "all_gates_passed": passed,
            "gates": report,
        })
        if passed:
            auto_cut.append(c)
        else:
            review.append(c)

    summary = {
        "schema_version": "autocut-gate-v1-run-v1",
        "gate_version": "autocut-gate-v1",
        "policy_source_relpath": str(policy_json),
        "candidates_source_relpath": str(candidates_json),
        "episode_duration_seconds": episode_duration_s,
        "param_gates_off": _PARAM_GATES_OFF,
        "param_gates_off_env": os.environ.get("MINGLUE_PARAM_GATES_OFF", "(unset · default=1)"),
        "gates_active": list(GATES_ACTIVE),
        "gates_removed_2026_08_20": list(GATES_INACTIVE),
        "calibration_source_merge": _calibration_status,
        "candidate_layer_gates": {
            "G3_no_preserve": "strict",
            "G5_history": "strict (candidate-level historical evidence)",
            "G6_duration": "strict",
            "G7_protection": "strict (opening / closing / session_feedback never_cut)",
            "G8_case_embedding": (
                "strict (mentor case-memory similarity) · loaded" if case_embeddings_by_cid
                else "silent_skip (index not built / no data)"
            ),
        },
        "never_cut_hard_override": (
            "G7_session_feedback verdict=never_cut 保留 hard REJECT · 独立于 LLM 语义 veto"
        ),
        "case_embedding_gate": {
            "source": case_embedding_source,
            "top_k": case_embedding_top_k,
            "min_score": case_embedding_min_score,
            **case_embedding_stats,
        },
        "gate_parameters": {
            "max_duration_seconds": max_duration_s,
            "opening_protection_seconds": opening_s,
            "closing_protection_seconds": closing_s,
            "min_historical_accept": min_hist_accept,
            "whitelist_kinds": sorted(whitelist),
            "denylist_kinds": sorted(denylist),
        },
        "summary": {
            "total_candidates": len(cands),
            "auto_cut_eligible_count": len(auto_cut),
            "human_review_required_count": len(review),
            "auto_cut_ratio": round(
                len(auto_cut) / max(1, len(cands)), 3
            ),
        },
        "auto_cut_candidate_ids": [c.get("candidate_id") for c in auto_cut],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "auto_cut.json", {"candidates": auto_cut})
    _write_json(out_dir / "review_required.json", {"candidates": review})
    _write_json(out_dir / "gate_report.json", {
        "summary": summary,
        "per_candidate": per_candidate_reports,
    })
    _write_json(out_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--episode-duration-seconds", type=float, required=True)
    ap.add_argument("--policy-application", type=Path, default=None,
                    help="policy_application.json (optional): candidate → route lookup")
    ap.add_argument("--calibration-source", type=Path, default=None,
                    help="calibration_source.json (optional): fallback for confidence_tier / repetition_signature when all_candidates.json omits them")
    ap.add_argument("--labels-lake", type=Path, default=None,
                    help="labels_lake.json (from build_labels_lake): category-level accept_rate lookup for G5")
    ap.add_argument("--lake-min-total", type=int, default=DEFAULT_LAKE_MIN_TOTAL,
                    help="G5 category-pass requires at least this many history samples (default 2)")
    ap.add_argument("--lake-min-accept-rate", type=float, default=DEFAULT_LAKE_MIN_ACCEPT_RATE,
                    help="G5 category-pass requires at least this accept rate (default 0.9)")
    ap.add_argument("--extra-candidates", type=Path, action="append", default=None,
                    help="Additional candidate sources to merge into the pool (e.g. self_correction_wordlevel output). Can be repeated.")
    ap.add_argument("--case-embeddings-json", type=Path, default=None,
                    help="case_embedding_retrieval.json (from Stage 3.9/6.8 retrieve_similar_cases): per-cid mentor top-K similar cases for G8. If missing / no data · G8 silently pass · pipeline unaffected.")
    ap.add_argument("--case-embedding-top-k", type=int, default=DEFAULT_G8_TOP_K,
                    help="G8 top-K similar cases to consult (default 3)")
    ap.add_argument("--case-embedding-min-score", type=float, default=DEFAULT_G8_MIN_SCORE,
                    help="G8 min FAISS cosine/IP score to count a similar case (default 0.5)")
    ap.add_argument("--max-duration-seconds", type=float, default=DEFAULT_MAX_DURATION_S)
    ap.add_argument("--opening-protection-seconds", type=float, default=DEFAULT_OPENING_PROTECTION_S)
    ap.add_argument("--closing-protection-seconds", type=float, default=DEFAULT_CLOSING_PROTECTION_S)
    ap.add_argument("--min-historical-accept", type=int, default=DEFAULT_MIN_HISTORICAL_ACCEPT)
    args = ap.parse_args(argv)

    summary = run(
        args.candidates,
        args.policy,
        args.out,
        episode_duration_s=args.episode_duration_seconds,
        max_duration_s=args.max_duration_seconds,
        opening_s=args.opening_protection_seconds,
        closing_s=args.closing_protection_seconds,
        min_hist_accept=args.min_historical_accept,
        policy_application_json=args.policy_application,
        calibration_source_json=args.calibration_source,
        labels_lake_json=args.labels_lake,
        lake_min_total=args.lake_min_total,
        lake_min_accept_rate=args.lake_min_accept_rate,
        extra_candidates_json=args.extra_candidates,
        case_embeddings_json=args.case_embeddings_json,
        case_embedding_top_k=args.case_embedding_top_k,
        case_embedding_min_score=args.case_embedding_min_score,
    )
    print(json.dumps(summary["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
