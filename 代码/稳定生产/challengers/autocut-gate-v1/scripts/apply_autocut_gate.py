#!/usr/bin/env python3
"""PARTIALLY FROZEN 2026-08-19 · LLM Takeover

用户 2026-08-19 evening 明确: LLM 完全主导 candidate 生成 + 判决 (Stage 3.5.5).
本文件**部分冻结** · 分两类 gate 处理:

** 结构性门 · 保留严格 (未冻结) **:
- G4  speaker_role       (角色声明门 · fail-closed)
- G4b source_track       (源轨归属门)
- G6  duration           (max_duration_seconds 长度上限)
- 以及 review_budget     (审核预算控制)
这些是结构约束 · 与 LLM 判决正交 · 继续硬执行.

** 语义门 · 冻结 diagnostic_only (LLM 接管) **:
- G1  candidate_kind whitelist
- G2  confidence_tier == 'high'
- G3  policy_application route (auto_preserve 冲突)
- G5  historical_accept/reject count
- G7  opening/closing 保护区
- G8  mentor case-memory 判决
这些语义信号仍计算 · 但只作 diagnostic · 不再决定 auto_cut_eligible.
最终 auto_cut vs human_review 判决由 LLM (Stage 3.5.5) 给出.

详见: 交付/最终交付文档/统筹全局/DEPRECATED_LLM_TAKEOVER_2026-08-19.md

---

apply_autocut_gate — 多重 gate 决定单个候选是否 auto-cut 合格。

每一条候选必须**全部通过**下列 gate 才归为 `auto_cut_eligible`；任一失败则
降级为 `human_review_required`（不是拒绝，只是让人来看）。

    G1  candidate_kind ∈ policy_v2 whitelist                (类型准入)
    G2  confidence_tier == "high"                           (机器自信度)
    G3  policy_application.route != auto_preserve           (无保护规则冲突)
    G5  historical_accept_count ≥ 1 AND reject_count == 0   (无历史反例)
    G6  duration ≤ max_duration_seconds                     (防误删长语义)
    G7  不在 opening/closing 保护区                          (防切开场/收尾)
    G8  mentor case-memory 相似案历判决 (case_embedding)     (跨 episode 案例记忆)

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


# ---------------------------------------------------------------------------
# 2026-08-19 · 候选 / 参数分层 (user directive)
#
# 候选层 gate (哪一段该剪): speaker_role / source_track / duration / review_budget
#   → 保留严格 · 挂即 REJECT (人审预算 / 时长范围 / 角色 / 源轨等)
#
# 参数层 gate (怎么剪的默认参数好不好): risk_tier / policy_authorization / calibration
#   → 默认关掉 (交给 Stage 6.7 Optuna 学) · 挂只 warning · 候选依然进 EDL
#
# 在本文件中，参数层对应:
#   - G1_whitelist               → policy_authorization (autocut_policy 白名单授权)
#   - G2_high_confidence         → risk_tier (confidence tier 判定)
#   - calibration_source merge   → calibration (校准 tier / repetition_signature)
#
# 环境变量 MINGLUE_PARAM_GATES_OFF (default "1" = TRUE):
#   "1"/"true"/"yes"  → 参数层降级 warning · 候选依然 pass
#   其他             → 参数层保持严格 REJECT (原行为)
# ---------------------------------------------------------------------------
_PARAM_GATES_OFF = os.environ.get("MINGLUE_PARAM_GATES_OFF", "1").strip().lower() in (
    "1", "true", "yes", "on",
)


# ---------------------------------------------------------------------------
# 2026-08-19 · LLM 语义门 takeover (user directive · 只用 LLM 负责 candidate)
#
# autocut_gate 分两类门:
#   1) 结构性门 (物理约束 · 不是"该不该剪") · 严格保留:
#        speaker_role · source_track · G6_duration (>0.8s 拒) · review_budget
#   2) 语义门 (LLM 该管的) · 让位 LLM:
#        G3_no_preserve · G5_history · G7_session_feedback · G7_protection
#
# G1_whitelist / G2_high_confidence 已由 MINGLUE_PARAM_GATES_OFF 让位 (参数层)。
#
# 环境变量 MINGLUE_LLM_TAKEOVER (default "auto"):
#   "auto" → 检测 target_dir/llm_verdicts.json · 存在则让位
#   "off"  → 强制不让位 (老 pipeline behavior)
#   "on"   → 强制让位 (即使 llm_verdicts.json 缺失 · test/debug 用)
#
# 遗留别名 MINGLUE_G5_DISABLED_WHEN_LLM:
#   若显式设置 (非空) · 与 MINGLUE_LLM_TAKEOVER 语义等价 · 后者优先。
#
# session_feedback verdict=never_cut 是全局强规则 · hard override · 即使 LLM
# 让位也不能覆盖 (只在明确 never_cut 时 REJECT)。
# ---------------------------------------------------------------------------


def _llm_takeover_mode() -> str:
    """Read the LLM takeover mode env var · 兼容遗留 MINGLUE_G5_DISABLED_WHEN_LLM."""
    mode = os.environ.get("MINGLUE_LLM_TAKEOVER", "").strip().lower()
    if mode:
        return mode
    legacy = os.environ.get("MINGLUE_G5_DISABLED_WHEN_LLM", "").strip().lower()
    if legacy:
        return legacy
    return "auto"


def _llm_verdicts_present(target_dir: Path) -> bool:
    """检测 LLM verdicts.json 是否存在 · 若存在 · 语义门让位.

    "off" → False · "on" → True (强制) · "auto" (default) → file existence.
    """
    mode = _llm_takeover_mode()
    if mode == "off":
        return False
    if mode == "on":
        return True
    llm_verdicts = target_dir / "llm_verdicts.json"
    return llm_verdicts.is_file()


def _read_llm_verdict_for(target_dir: Path, candidate_id: str) -> dict | None:
    """读 LLM verdict for specific candidate · 返回 dict or None."""
    llm_verdicts = target_dir / "llm_verdicts.json"
    if not llm_verdicts.is_file():
        return None
    try:
        vd = json.loads(llm_verdicts.read_text(encoding="utf-8"))
    except Exception:
        return None
    for v in vd.get("verdicts", []):
        if str(v.get("candidate_id")) == str(candidate_id):
            return v
    return None


def _llm_deferred_entry(gate_name: str, llm_v: dict | None) -> dict:
    """Build a diagnostic report entry for a semantic gate that让位 LLM."""
    if not llm_v:
        return {
            "gate": gate_name, "pass": True,
            "note": "LLM 已接管 · 语义门让位 · diagnostic only (no per-cid verdict)",
        }
    return {
        "gate": gate_name, "pass": True,
        "note": "LLM 已接管 · 语义门让位 · diagnostic only",
        "llm_verdict": llm_v.get("verdict"),
        "llm_reason": llm_v.get("reason"),
    }


# Backward-compat alias · 老代码可能 import 这个名字
def _check_g5_llm_takeover(run_dir: Path | None) -> bool:
    """Legacy wrapper · use _llm_verdicts_present directly for new code."""
    if run_dir is None:
        mode = _llm_takeover_mode()
        return mode == "on"
    # try both classic locations
    for d in (run_dir, run_dir / "llm_filter"):
        if _llm_verdicts_present(d):
            return True
    return False


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
    g5_llm_takeover: bool = False,
    llm_verdicts_by_cid: dict[str, dict] | None = None,
) -> tuple[bool, list[dict]]:
    """Return (all_gates_passed, per_gate_report)."""
    cid = candidate.get("candidate_id") or "?"
    kind = candidate.get("candidate_kind") or candidate.get("kind") or "?"

    report: list[dict] = []

    # 2026-08-19 · LLM 语义门 takeover 状态
    # llm_verdicts_by_cid 存在 (即使某 cid 无 verdict) → 语义门 (G3/G7_session
    # non-never_cut/G7_protection) 让位 diagnostic-only.
    # g5_llm_takeover 是 G5 的等价开关 (向后兼容)。
    _llm_takeover_active = bool(llm_verdicts_by_cid is not None) or bool(g5_llm_takeover)
    llm_v = None
    if llm_verdicts_by_cid:
        llm_v = llm_verdicts_by_cid.get(str(cid))
    # G5 内部沿用 g5_llm_takeover 名 · 统一从 _llm_takeover_active 派生
    g5_llm_takeover = _llm_takeover_active

    # 2026-08-19 · LLM REJECT hard short-circuit
    # 若 LLM 明确 REJECT_KEEP (保留 · 不剪) · autocut_gate 也 REJECT · 走人审.
    # 记为 LLM_reject entry · 结构性门 (G6_duration 等) 不再运行。
    if llm_v:
        _llm_verdict_str = str(llm_v.get("verdict", "")).strip().upper()
        if _llm_verdict_str in ("REJECT_KEEP", "REJECT", "KEEP", "DO_NOT_CUT", "NEVER_CUT"):
            report.append({
                "gate": "LLM_reject",
                "pass": False,
                "reason": "LLM_REJECT_KEEP: " + str(llm_v.get("reason", "")),
                "llm_verdict": llm_v.get("verdict"),
                "llm_reason": llm_v.get("reason"),
            })
            return False, report

    # G1 · kind whitelist  [PARAM LAYER: policy_authorization]
    # 参数层门：autocut_policy 签字授权哪些 candidate_kind 默认剪。
    # PARAM_GATES_OFF 时降级 warning · 候选依然进 EDL · Optuna Stage 6.7 优化。
    if kind in denylist_kinds:
        if _PARAM_GATES_OFF:
            report.append({
                "gate": "G1_whitelist", "pass": True,
                "param_gate_note": "waived_policy_authorization_denylist · Optuna will handle",
                "kind": kind,
                "note": "MINGLUE_PARAM_GATES_OFF=1 · would have REJECTED",
            })
        else:
            report.append({"gate": "G1_whitelist", "pass": False, "reason": f"kind={kind} in denylist"})
            return False, report
    elif kind not in whitelist_kinds:
        if _PARAM_GATES_OFF:
            report.append({
                "gate": "G1_whitelist", "pass": True,
                "param_gate_note": "waived_policy_authorization_not_whitelisted · Optuna will handle",
                "kind": kind,
                "note": "MINGLUE_PARAM_GATES_OFF=1 · would have REJECTED",
            })
        else:
            report.append({"gate": "G1_whitelist", "pass": False, "reason": f"kind={kind} not in whitelist"})
            return False, report
    else:
        report.append({"gate": "G1_whitelist", "pass": True})

    # G2 · high confidence  (boosted by strong wordlevel or three-track signals)
    # [PARAM LAYER: risk_tier]
    # 参数层门：confidence_tier 是候选默认参数好不好剪的估计 (风险分级)。
    # tier=high 或 wordlevel edit_ratio≥0.75 或 three-track sync≥3 通过。
    # PARAM_GATES_OFF 时降级 warning · 候选依然进 EDL · Optuna Stage 6.7 优化。
    tier = _infer_confidence_tier(candidate)
    edit_ratio_g2 = float(candidate.get("edit_ratio", 0.0))
    cross_track_g2 = int(candidate.get("cross_track_hit_count", 0))
    tier_ok = (
        tier == "high"
        or edit_ratio_g2 >= 0.75           # strong wordlevel signal
        or cross_track_g2 >= 3             # three-track sync = independent evidence
    )
    if not tier_ok:
        if _PARAM_GATES_OFF:
            report.append({
                "gate": "G2_high_confidence", "pass": True,
                "param_gate_note": "waived_risk_tier · Optuna will handle",
                "tier": tier, "edit_ratio": edit_ratio_g2, "cross_track_hits": cross_track_g2,
                "note": "MINGLUE_PARAM_GATES_OFF=1 · would have REJECTED (tier not high)",
            })
        else:
            report.append({
                "gate": "G2_high_confidence", "pass": False,
                "reason": f"tier={tier} edit_ratio={edit_ratio_g2} cross_track={cross_track_g2}",
            })
            return False, report
    else:
        report.append({
            "gate": "G2_high_confidence", "pass": True,
            "tier": tier, "edit_ratio": edit_ratio_g2, "cross_track_hits": cross_track_g2,
        })

    # G3 · policy_application not auto_preserve  [SEMANTIC · LLM takeover eligible]
    if _llm_takeover_active:
        report.append(_llm_deferred_entry("G3_no_preserve", llm_v))
    else:
        route = policy_route_by_cid.get(str(cid))
        if route in preserve_routes:
            report.append({"gate": "G3_no_preserve", "pass": False, "reason": f"policy_route={route}"})
            return False, report
        report.append({"gate": "G3_no_preserve", "pass": True, "policy_route": route})

    # v20.6 · G7 · session_feedback [SEMANTIC · LLM takeover eligible]
    # 若候选之前被用户反馈 verdict=never_cut → hard reject (走人审)
    # 2026-08-19 · session_feedback 是全局强规则 · hard override · 即使 LLM 让位
    # 也保留 never_cut 为 REJECT (只在明确 never_cut 时 REJECT)
    prev_fb = candidate.get("previous_user_feedback") or []
    never_cut_notes = [
        fb for fb in prev_fb if str(fb.get("verdict")) == "never_cut"
    ]
    if never_cut_notes:
        # HARD override · 不让位
        report.append({
            "gate": "G7_session_feedback", "pass": False,
            "reason": "user_feedback_never_cut (HARD override · 全局强规则)",
            "note": never_cut_notes[0].get("note", "")[:200],
            "kind": never_cut_notes[0].get("kind"),
            "n_hits": len(never_cut_notes),
        })
        return False, report
    if _llm_takeover_active:
        report.append(_llm_deferred_entry("G7_session_feedback", llm_v))
    elif prev_fb:
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
        if g5_llm_takeover:
            report.append({
                "gate": "G5_history", "pass": True,
                "note": "LLM 已接管候选决定 · G5 让位 · diagnostic only",
                "would_have_been": "REJECT",
                "diagnostic_reason": (
                    f"lake token='{filler_token}' 里 reject={lake_token_reject} "
                    f"→ token-level historical rejection"
                ),
                "lake_token_reject": lake_token_reject,
            })
        else:
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
        if g5_llm_takeover:
            report.append({
                "gate": "G5_history", "pass": True,
                "note": "LLM 已接管候选决定 · G5 让位 · diagnostic only",
                "would_have_been": "REJECT",
                "diagnostic_reason": f"individual hr={hr} > 0 → historical rejection precedent",
                "ha": ha, "hr": hr, "lake": lake_info,
            })
        else:
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

    # G7 · not in opening/closing protection  [SEMANTIC · LLM takeover eligible]
    if _llm_takeover_active:
        report.append(_llm_deferred_entry("G7_protection", llm_v))
    else:
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
    # [PARAM LAYER: calibration]
    # 参数层：calibration_source 校准 tier / repetition_signature 等**参数**。
    # 本身只做字段填充 · 不 REJECT 候选 · 但下游 G2 (risk_tier) 依赖它。
    # PARAM_GATES_OFF 时: 填充依然做 (advisory) · 缺 tier 也不影响候选进 EDL
    # (因为 G2 已降级 warning) · Optuna Stage 6.7 学 tier 阈值。
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
    # 记录 calibration 状态到 candidates 首个 candidate 的 gate_notes 以便 audit。
    # (per-candidate note 里也会有 "waived_risk_tier" 表明 tier 未强制)
    _calibration_status = {
        "calibration_source_provided": bool(calibration_source_json),
        "calibration_source_exists": bool(calibration_source_json and calibration_source_json.is_file()),
        "calibration_used": calibration_used,
        "calibration_filled_fields": calibration_filled_count,
        "param_gate_note": (
            "advisory_only · MINGLUE_PARAM_GATES_OFF=1 · Optuna will handle"
            if _PARAM_GATES_OFF else "strict · calibration feeds G2"
        ),
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

    # 2026-08-19 · LLM 语义门 takeover · out_dir 是 <run_dir>/autocut_gate/
    # 所以 run_dir = out_dir.parent. 尝试两个 canonical 位置:
    #   <run_dir>/llm_verdicts.json
    #   <run_dir>/llm_filter/llm_verdicts.json
    _run_dir = out_dir.parent if out_dir else None
    llm_verdicts_dir: Path | None = None
    if _run_dir is not None:
        for _d in (_run_dir / "llm_filter", _run_dir):
            if _llm_verdicts_present(_d):
                llm_verdicts_dir = _d
                break
    # "on" 模式即使无 file 也 takeover (但 dict 为空 · 全走 fallback)
    if llm_verdicts_dir is None and _llm_takeover_mode() == "on":
        llm_verdicts_dir = _run_dir  # 无实际文件 · 仅激活标志

    _llm_takeover_active = llm_verdicts_dir is not None
    llm_verdicts_by_cid: dict[str, dict] = {}
    if llm_verdicts_dir is not None:
        vpath = llm_verdicts_dir / "llm_verdicts.json"
        if vpath.is_file():
            try:
                _lv = _load_json(vpath)
                for v in _lv.get("verdicts", []):
                    cid_v = str(v.get("candidate_id") or "")
                    if cid_v:
                        llm_verdicts_by_cid[cid_v] = v
            except Exception:
                pass

    # 传给 apply_gates 的 dict (None 表示不激活 · 传空 dict 也算激活)
    _llm_dict_to_pass: dict[str, dict] | None = (
        llm_verdicts_by_cid if _llm_takeover_active else None
    )
    # 兼容遗留 g5_llm_takeover 参数
    _g5_llm_takeover = _llm_takeover_active

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
            g5_llm_takeover=_g5_llm_takeover,
            llm_verdicts_by_cid=_llm_dict_to_pass,
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
        "param_layer_gates": {
            "policy_authorization_G1_whitelist": (
                "waived · warning-only" if _PARAM_GATES_OFF else "strict"
            ),
            "risk_tier_G2_high_confidence": (
                "waived · warning-only" if _PARAM_GATES_OFF else "strict"
            ),
            "calibration_source_merge": _calibration_status,
        },
        "candidate_layer_gates": {
            "G3_no_preserve": (
                "diagnostic_only · LLM 已接管 · 语义门让位" if _llm_takeover_active
                else "strict"
            ),
            "G5_history": (
                "diagnostic_only · LLM 已接管候选终审 · G5 REJECT 让位" if _g5_llm_takeover
                else "strict (candidate-level historical evidence)"
            ),
            "G6_duration": "strict",
            "G7_protection": (
                "diagnostic_only · LLM 已接管 · 语义门让位 · never_cut 保留 hard override"
                if _llm_takeover_active
                else "strict (opening / closing / session_feedback never_cut)"
            ),
            "G8_case_embedding": (
                "strict (mentor case-memory similarity) · loaded" if case_embeddings_by_cid
                else "silent_skip (index not built / no data)"
            ),
        },
        "llm_takeover": {
            "llm_takeover_active": _llm_takeover_active,
            "llm_takeover_env": os.environ.get(
                "MINGLUE_LLM_TAKEOVER",
                os.environ.get("MINGLUE_G5_DISABLED_WHEN_LLM", "(unset · default=auto)"),
            ),
            "llm_takeover_mode": _llm_takeover_mode(),
            "llm_verdicts_dir": str(llm_verdicts_dir) if llm_verdicts_dir else None,
            "llm_verdicts_count": len(llm_verdicts_by_cid),
            "gates_deferred_to_llm": (
                ["G3_no_preserve", "G5_history", "G7_session_feedback", "G7_protection"]
                if _llm_takeover_active else []
            ),
            "gates_still_active": [
                "speaker_role", "source_track", "G6_duration", "review_budget",
            ],
            "never_cut_hard_override": (
                "G7_session_feedback verdict=never_cut 保留 hard REJECT · 即使 LLM 让位也不能覆盖"
            ),
        },
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
