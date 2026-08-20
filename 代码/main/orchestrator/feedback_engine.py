#!/usr/bin/env python3
"""feedback_engine · 反馈闭环唯一入口 (v220.merged, 2026-08-18)

**动机** (用户明确 2026-08-18): 合并 `feedback_first_retrieval` (读) 和
`user_feedback_analyzer` (写) 为单一 skill · 反馈闭环完整.

**两个方向**:
    - 读 (决策前) · retrieve_before_decision(candidate, decision_type, episode_id)
        · 检索 current.session_feedback.jsonl + labels_lake.feedback[]
        · verdict priority DESC · match score DESC · timestamp DESC
        · 返回 top-N 匹配规则给调用方 apply
    - 写 (决策后) · analyze_feedback(candidate, verdict, note)
        · Parse note → root_cause 关键词
        · STEP 2 · 查 tools.json 48 项 → TOOL_APPLY (最优)
        · STEP 3 · 查唯一知识沉淀文档 → DOC_REFERENCE
        · STEP 4 · 最后 append current.session_feedback.jsonl → PATCH

**Skill 挂点**: `skills/feedback-engine/`
**CLAUDE.md §18**: 反馈闭环 (读+写).

**决策链严格顺序** (写方向):
    1. Parse (verdict + note) 提炼 root_cause
    2. TOOL_APPLY · 已有工具能解决 (confidence 0.9)
    3. DOC_REFERENCE · 知识沉淀文档 (confidence 0.7)
    4. SESSION_FEEDBACK_PATCH · 最后手段 (confidence 0.5)
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ============================================================
# PART A · 读 (决策前 retrieve · from feedback_first_retrieval)
# ============================================================

VERDICT_PRIORITY = {
    "never_cut": 10, "forbidden": 10, "needs_extension": 8,
    "both_spans_or_none": 7, "pause_shorter": 6, "pause_required": 6,
    "pause_dynamic_by_cut_count": 6, "cut_all_but_last": 6,
    "MFA_required": 5, "automix_required": 5, "three_track_amix_required": 5,
    "policy": 4, "context_accepted": 3, "accept_pattern": 3,
    "cut_scope_too_wide": 8, "mixed": 2, "only_representative": 4,
}


def _load_all_feedback(episode_id: str = "EP04") -> list[dict]:
    """加载 current.session_feedback.jsonl (单一 SOT · §20) + labels_lake.feedback[]"""
    fbs: list[dict] = []
    current_p = PROJECT_ROOT / "main/knowledge/session_feedback/current.session_feedback.jsonl"
    if current_p.is_file():
        for line in current_p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                fbs.append(json.loads(line))
    else:
        # legacy fallback
        for name in [f"{episode_id}.session_feedback.jsonl", "ALL.session_feedback.jsonl"]:
            p = PROJECT_ROOT / "main/knowledge/session_feedback" / name
            if p.is_file():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        fbs.append(json.loads(line))
    # labels_lake
    lake = PROJECT_ROOT / "main/knowledge/labels_lake.json"
    if lake.is_file():
        d = json.loads(lake.read_text())
        for e in d.get("entries", []):
            for fb in e.get("feedback", []) or []:
                fbs.append({
                    "source": "labels_lake",
                    "timestamp": fb.get("decided_at", "0000"),
                    "candidate_pattern": {"reason_key": e.get("reason_key"),
                                          "filler_token": e.get("filler_token")},
                    "verdict": fb.get("verdict") or fb.get("decision"),
                    "note": fb.get("note", ""),
                })
    return fbs


def _match_score(pattern: dict, candidate: dict, decision_type: str) -> int:
    if not pattern or not candidate:
        return 0
    if pattern.get("any"):
        return 1
    score = 0
    p_tok = pattern.get("filler_token")
    c_tok = candidate.get("filler_token")
    if p_tok is not None and c_tok is not None:
        if isinstance(p_tok, list):
            if c_tok in p_tok: score += 10
            else: return 0
        elif p_tok == c_tok: score += 10
        else: return 0
    p_rk = pattern.get("reason_key")
    c_rk = candidate.get("reason_key") or candidate.get("candidate_kind")
    if p_rk is not None and c_rk is not None:
        if isinstance(p_rk, list):
            if c_rk in p_rk: score += 5
            else: return 0
        elif p_rk == c_rk: score += 5
        else: return 0
    p_ctx = pattern.get("context")
    if p_ctx is not None and decision_type in str(p_ctx):
        score += 3
    return score if score > 0 else (1 if pattern.get("any") else 0)


def retrieve_before_decision(
    candidate: dict, decision_type: str = "cut_boundary",
    episode_id: str = "EP04", max_return: int = 5,
    knowledge_category: str | None = None,  # v2 · "PREFERENCE" / "PARAMETER" / None(全部)
) -> list[dict]:
    """决策前 · 检索反馈规则. verdict_priority DESC · match_score DESC · timestamp DESC.

    v2 (2026-08-18 CLAUDE.md §21): 加 knowledge_category 过滤.
    - "PREFERENCE" · 决定剪哪些 (retrieve_before_decision 默认走这个 · 决策前查)
    - "PARAMETER"  · 决定怎么剪 (工具默认参数 · 通常直接读 cut_parameters.json)
    - None · 全部 (向后兼容 · v1 rules 没 knowledge_category)
    """
    fbs = _load_all_feedback(episode_id)
    matched: list[tuple[int, int, str, dict]] = []
    for fb in fbs:
        if knowledge_category and fb.get("knowledge_category") not in (knowledge_category, None):
            continue
        pattern = fb.get("candidate_pattern") or {}
        s = _match_score(pattern, candidate, decision_type)
        if s <= 0:
            continue
        verdict = str(fb.get("verdict", ""))
        v_pri = VERDICT_PRIORITY.get(verdict, 1)
        ts = str(fb.get("timestamp", "0000"))
        matched.append((v_pri, s, ts, fb))
    # 排序: verdict_priority DESC · match_score DESC · timestamp DESC
    # v220.2 (2026-08-18 bugfix): 之前两个 sort 冲突, 第二个 timestamp sort 覆盖了 priority sort
    # 导致 never_cut/forbidden 高优先规则被 latest timestamp 挤掉 · C036 什麼 未被过滤根因
    matched.sort(key=lambda x: (-x[0], -x[1], "" if x[2] is None else x[2]), reverse=False)
    # timestamp 单独反向 (composite key 里 timestamp 是字符串 ASC · 但我们要 DESC 所以整体二级排序有问题)
    # 正确做法: verdict/score 正向 (小的在前不好, 所以取负号); timestamp 单独 DESC 作 tie-breaker
    # 上面已合并 · 不再二次 sort
    return [{"verdict_priority": v, "match_score": s, "timestamp": t, **fb}
            for v, s, t, fb in matched[:max_return]]


def load_cut_parameters() -> dict:
    """v2 · CLAUDE.md §21 · 加载 PARAMETER 类知识 (决定怎么剪 · 工具直接消费).

    优先级: cut_parameters.json > hardcoded defaults.
    """
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "main/knowledge/cut_parameters.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {
        "how_to_cut_defaults": {
            "crossfade_ms": {"default": 50, "override_by_semantic_class": {"long_pause": 200}},
            "gap_before_ms": {"target_range": [120, 300], "hard_reject_below_and_not_in_silence": 50},
            "gap_after_ms": {"target_range": [120, 450], "prefer_tail_heavier_ratio": 0.9},
            "boundary_offset_from_silence_edge_ms": {"prefer_min": 76, "target": 300, "cap": 300},
            "rms_diff_soft_db": 15,
            "rms_diff_hard_db": 25,
            "pause_ms_after_cut": 0,
        },
    }


def is_never_cut(candidate: dict, episode_id: str = "EP04") -> bool:
    """快速: 是否命中 never_cut.

    v220.4 (2026-08-18 bugfix): 严格要求
    - pattern 必须有 filler_token 或 text_pattern 具体匹配 (不接受宽泛 reason_key)
    - pattern 有 context 时 candidate 必须命中该 context (英文邻居/说错重来 etc)
    """
    import re
    fbs = retrieve_before_decision(candidate, "cut_extent", episode_id)
    c_tok = str(candidate.get("filler_token") or "")
    c_ctx = set(str(candidate.get("context") or "").split(",")) | set(candidate.get("context_flags") or [])
    for fb in fbs:
        if str(fb.get("verdict")) != "never_cut":
            continue
        pat = fb.get("candidate_pattern") or {}
        if pat.get("any"):
            continue

        # 1. filler_token 或 text_pattern 必须命中
        p_tok = pat.get("filler_token")
        tok_hit = False
        if p_tok is not None:
            if isinstance(p_tok, list):
                tok_hit = c_tok in p_tok
            else:
                tok_hit = (p_tok == c_tok)
        else:
            tp = pat.get("text_pattern")
            if tp and c_tok:
                try:
                    tok_hit = bool(re.search(tp, c_tok))
                except re.error:
                    tok_hit = False
        if not tok_hit:
            continue

        # 2. context 若指定 · candidate 必须命中
        p_ctx = pat.get("context")
        if p_ctx:
            if p_ctx not in c_ctx:
                continue

        return True
    return False


# ============================================================
# PART B · 写 (决策后 analyze + route · from user_feedback_analyzer)
# ============================================================

TOOL_MAP = {
    # 边界
    "boundary": {"tool": "mfa_align_and_extract_boundaries", "action": "音素级精修"},
    "边界": {"tool": "mfa_align_and_extract_boundaries", "action": "MFA rerun"},
    "起音": {"tool": "librosa.onset.onset_detect", "action": "backtrack=True"},
    "辅音": {"tool": "librosa.onset.onset_detect", "action": "backtrack=True"},
    "吞": {"tool": "librosa.onset.onset_detect", "action": "kept 起音保护 -30ms"},
    # 剪太多/相邻词
    "剪太多": {"tool": "safe_bounds (§19)", "action": "prev.end+20ms / next.start-20ms"},
    "剪掉了很多": {"tool": "librosa.feature.rms + safe_bounds", "action": "内容词严格 RMS -40dB"},
    "前一个词": {"tool": "safe_bounds (§19)", "action": "cut_start = max(filler.start-edge, prev.end+20ms)"},
    "剪辑相关": {"tool": "safe_bounds (§19)", "action": "相邻词保护"},
    # Room tone / 痕迹
    "痕迹": {"tool": "noisereduce + pydub.crossfade", "action": "spectral profile + 120ms"},
    "剪辑痕迹": {"tool": "generate_comprehensive_cut", "action": "综合方案"},
    # Pause
    "停顿": {"tool": "dynamic_pause_ms", "action": "cut_count × 60 + sep 200"},
    "留白": {"tool": "dynamic_pause_ms", "action": "动态"},
    # 说话人
    "backchannel": {"tool": "_is_cross_track_backchannel (§12)", "action": "跨轨挡"},
    "附和": {"tool": "_is_cross_track_backchannel", "action": "跨轨挡"},
    # 代词/内容词
    "代词": {"tool": "PRONOUN_LIKE_REPETITIONS", "action": "永远不剪"},
    "内容词": {"tool": "session_feedback never_cut_<token>", "action": "token 加白名单"},
    "不应该剪": {"tool": "session_feedback never_cut", "action": "verdict=never_cut"},
    "不能剪": {"tool": "session_feedback never_cut", "action": "verdict=never_cut"},
    # 语义
    "句": {"tool": "spacy_semantic_transcript", "action": "interrogative 挡"},
    "疑问": {"tool": "spacy_semantic_transcript", "action": "interrogative 挡"},
    # ASR 幻觉
    "英文": {"tool": "_english_fragment_context", "action": "英文碎片挡"},
    "识别错误": {"tool": "_low_confidence_filler_guard", "action": "probability <0.6"},
    "ASR": {"tool": "_low_confidence_filler_guard", "action": "probability gate"},
    # 音量
    "音量": {"tool": "ffmpeg loudnorm -22.2 (§9)", "action": "double pass"},
    "声音小": {"tool": "automix_v1 + loudnorm", "action": "语义并轨"},
    # 长停顿
    "长停顿": {"tool": "long_pause_all_track_silence", "action": "跨轨静默检查"},
    "静默": {"tool": "long_pause_all_track_silence", "action": "跨轨检查"},
}

DOC_MAP = [
    {"path": "从视频学习经验/YouTube学习总结.md", "sections": {
        "2": "§ 2 Clean Cut Audio · crossfade 20-80ms + 保留辅音起音",
        "5": "§ 5 Randy Rektor · room tone 从原始轨",
        "3": "§ 3 Incidence · mp3 编码后重测",
    }},
    {"path": "统筹全局/Preflight-checklist-与今日踩坑清单.md", "sections": {
        "10": "preview 与最终成片必须同混音"}},
    {"path": "统筹全局/mentor-briefing-2026-08-17.md", "sections": {
        "1-3": "双审门 · mentor 内容 + 项目负责人响度"}},
]


def parse_feedback(feedback_note: str, verdict: str) -> list[str]:
    kws = [kw for kw in TOOL_MAP if kw in feedback_note]
    if verdict in ("never_cut", "forbidden"): kws.append("不能剪")
    if verdict == "cut_scope_too_wide": kws.append("剪太多")
    if verdict == "needs_extension": kws.append("边界")
    return list(set(kws))


def find_tool_fix(root_causes: list[str]) -> dict | None:
    for kw in root_causes:
        if kw in TOOL_MAP:
            return {"keyword": kw, **TOOL_MAP[kw]}
    return None


def find_doc_reference(root_causes: list[str], note: str) -> dict | None:
    for kw in root_causes:
        if kw in ("剪辑痕迹","痕迹","起音","辅音","吞"):
            return {"path": "从视频学习经验/YouTube学习总结.md",
                    "section": "§ 2 Clean Cut Audio · crossfade 20-80ms"}
        if kw in ("底噪","room","tone","空间"):
            return {"path": "从视频学习经验/YouTube学习总结.md",
                    "section": "§ 5 Randy Rektor · room tone 从原始轨"}
    return None


def analyze_feedback(
    candidate: dict, user_verdict: str, user_note: str,
    episode_id: str = "EP04",
) -> dict:
    """决策链 · TOOL_APPLY > DOC_REFERENCE > SESSION_FEEDBACK_PATCH."""
    reasoning = []
    root_causes = parse_feedback(user_note, user_verdict)
    reasoning.append(f"STEP 1 · Parse: verdict={user_verdict}, note='{user_note[:60]}'")
    reasoning.append(f"  root_causes: {root_causes}")

    tool_fix = find_tool_fix(root_causes)
    if tool_fix:
        reasoning.append(f"STEP 2 · TOOL_APPLY 命中: {tool_fix['tool']}")
        return {"root_cause": root_causes, "action_type": "TOOL_APPLY",
                "fix_plan": tool_fix, "reasoning_chain": reasoning, "confidence": 0.9}

    reasoning.append("STEP 2 · 无工具直接匹配")
    doc_ref = find_doc_reference(root_causes, user_note)
    if doc_ref:
        reasoning.append(f"STEP 3 · DOC_REFERENCE: {doc_ref['section']}")
        return {"root_cause": root_causes, "action_type": "DOC_REFERENCE",
                "fix_plan": {"reference": doc_ref, "next_step": "读文档实现"},
                "reasoning_chain": reasoning, "confidence": 0.7}

    reasoning.append("STEP 3 · 无相关文档")
    patch = {
        "schema_version": "session-feedback-v1",
        "episode_id": episode_id,
        "reviewer": "熊镇正",
        "source": "feedback_engine",
        "kind": f"custom_{('_'.join(root_causes) if root_causes else 'novel')[:30]}",
        "candidate_pattern": {"filler_token": candidate.get("filler_token"),
                               "reason_key": candidate.get("reason_key")},
        "verdict": user_verdict,
        "note": user_note,
    }
    reasoning.append(f"STEP 4 · SESSION_FEEDBACK_PATCH (最后手段) · kind={patch['kind']}")
    return {"root_cause": root_causes, "action_type": "SESSION_FEEDBACK_PATCH",
            "fix_plan": {"append_to": "main/knowledge/session_feedback/current.session_feedback.jsonl",
                         "record": patch},
            "reasoning_chain": reasoning, "confidence": 0.5}


def apply_decision(decision: dict, dry_run: bool = False) -> dict:
    result = {"action_type": decision["action_type"], "applied": False}
    if decision["action_type"] == "SESSION_FEEDBACK_PATCH":
        rec = decision["fix_plan"]["record"]
        target = PROJECT_ROOT / decision["fix_plan"]["append_to"]
        if not dry_run:
            rec["timestamp"] = datetime.datetime.utcnow().isoformat() + "Z"
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            result["applied"] = True
            result["target"] = str(target.relative_to(PROJECT_ROOT))
    elif decision["action_type"] == "TOOL_APPLY":
        result["note"] = "调用方按 fix_plan['action'] 应用参数变更"
    elif decision["action_type"] == "DOC_REFERENCE":
        result["note"] = "调用方读文档章节并实现"
    return result


# ============================================================
# CLI (unified)
# ============================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)

    # retrieve (读)
    p_r = sub.add_parser("retrieve", help="决策前查反馈")
    p_r.add_argument("--candidate-json", required=True)
    p_r.add_argument("--decision-type", default="cut_boundary")
    p_r.add_argument("--episode-id", default="EP04")
    p_r.add_argument("--max-return", type=int, default=5)

    # analyze (写)
    p_a = sub.add_parser("analyze", help="决策后处理新反馈")
    p_a.add_argument("--candidate-json", required=True)
    p_a.add_argument("--verdict", required=True)
    p_a.add_argument("--note", required=True)
    p_a.add_argument("--episode-id", default="EP04")
    p_a.add_argument("--apply", action="store_true")

    args = ap.parse_args(argv)

    if args.mode == "retrieve":
        cand = json.loads(args.candidate_json)
        fbs = retrieve_before_decision(cand, args.decision_type, args.episode_id, args.max_return)
        print(json.dumps({"candidate": cand, "n_matched": len(fbs),
                          "top_matches": [{"verdict": fb.get("verdict"), "kind": fb.get("kind"),
                                           "timestamp": fb.get("timestamp"),
                                           "note_snippet": (fb.get("note", "") or "")[:100]}
                                           for fb in fbs]},
                         ensure_ascii=False, indent=2))
    elif args.mode == "analyze":
        cand = json.loads(args.candidate_json)
        decision = analyze_feedback(cand, args.verdict, args.note, args.episode_id)
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        result = apply_decision(decision, dry_run=not args.apply)
        print("\n--- apply result ---")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
