#!/usr/bin/env python3
"""optuna_refine · Optuna TPE 驱动的迭代精修 · 官方数据驱动 delta.

**用途**：把 iterate_until_clean.py 里"硬编码 delta"换成 **Optuna Bayesian TPE**——
每候选跑一个 study · Optuna 从 NISQA discontinuity 曲面自己学采样方向 · **不用我拍脑袋**。

**Warm-start 机制**（用户 2026-08-19 明确要求 · 解决 cold-start 问题）:
  - 从 YouTube learning policy (session_feedback jsonl 里 reviewer="系统 (YouTube ...)"条目)
  - 从 mentor gold cut_parameters.json (target_range / median_observed / override_by_semantic_class)
  - 合并出**按候选 kind 分**的起始点字典
  - 用 study.enqueue_trial(warm_start) 让 Optuna 先 evaluate 已知好起点
  - **TPE 不再冷启动**,前 2 次跑 warm start,后 3 次 TPE 学 → 5 次内基本收敛

**引用**:
  - Optuna: A Next-generation Hyperparameter Optimization Framework
    Akiba et al. KDD 2019
    https://optuna.org/  · MIT
  - TPE (Tree-structured Parzen Estimator)
    Bergstra et al. NIPS 2011
  - Warm start / prior injection: 学界标准 (Bergstra 2013 · Hyperopt · 官方 Optuna FAQ)

**接口**::

    from optuna_refine import iterate_optuna_study
    trace = iterate_optuna_study(candidate, ctx, render_and_verify_fn, policy)

**seed=42** · 结果可 replay · provenance 全在 study.trials_dataframe().
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List


# ---------------------------------------------------------------------------
# 2026-08-20 · warm-start 接文件版
# 用户明确 (原话 "能不能接进去") · warm-start 从硬编码升级为文件读入.
# 数据源:
#   - mentor gold: knowledge/cut_parameters.json (mentor 59 手工剪的中位数)
#   - YouTube learning: knowledge/session_feedback/current.session_feedback.jsonl
#                       里 reviewer=YouTube 的条目
# Fallback: 文件缺失走硬编码 (兼容旧 pipeline).
# 硬编码保留在 _HARDCODED_WARM_START_BY_KIND · 参考实现与保底 · 主 pipeline
# 默认从文件读 · 通过 policy["warm_start_sources"]["disable_warm_start_file"]=True
# 可强制走硬编码 (回归/AB 用).
#
# 仅接以下两维 (直接证据充分):
#   crossfade_ms      ← how_to_cut_defaults.crossfade_ms.override_by_semantic_class
#   room_tone_pad_ms  ← cut_verify_thresholds.check4_route_crossfade_strategy
#                                            .butt_splice_room_tone_pad_ms
# 未接三维 (schema 冲突 · 见 design_md · risks_md R4/R5):
#   post_cut_pause_ms · asymmetric_head_pad_ms · boundary_offset_ms → 保留硬编码
# ---------------------------------------------------------------------------
# Warm-start · 按候选 kind 定合理起点 · 来源 YouTube learning + mentor gold
# ---------------------------------------------------------------------------

_HARDCODED_WARM_START_BY_KIND: Dict[str, List[Dict[str, Any]]] = {
    # 每 kind 提供 2 组 warm start · Optuna trial 0/1 先 evaluate · trial 2+ TPE 学
    "filler_hesitation": [
        {
            # 来源: mentor gold cut_parameters.json.crossfade.override_by_semantic_class.filler_hesitation=50
            #      + gap_after_ms.p25=120 保守起点
            "crossfade_ms": 50,
            "post_cut_pause_ms": 40,
            "asymmetric_head_pad_ms": 40,
            "boundary_offset_ms": 0,
            "room_tone_pad_ms": 10,
        },
        {
            # 变体 · YouTube Clean Cut Audio § 2 · crossfade 80ms 中文长辅音自适应
            "crossfade_ms": 80,
            "post_cut_pause_ms": 60,
            "asymmetric_head_pad_ms": 60,
            "boundary_offset_ms": -10,  # 边界内缩让开辅音
            "room_tone_pad_ms": 15,
        },
    ],
    "immediate_repetition": [
        {
            # mentor gold · immediate_rep default crossfade 50 · pause 略长
            "crossfade_ms": 50,
            "post_cut_pause_ms": 60,
            "asymmetric_head_pad_ms": 40,
            "boundary_offset_ms": 0,
            "room_tone_pad_ms": 10,
        },
        {
            "crossfade_ms": 70,
            "post_cut_pause_ms": 80,
            "asymmetric_head_pad_ms": 50,
            "boundary_offset_ms": -10,
            "room_tone_pad_ms": 15,
        },
    ],
    "global_long_pause": [
        {
            # mentor gold cut_parameters.crossfade.override.long_pause=200 · gap_after p75=440
            "crossfade_ms": 200,
            "post_cut_pause_ms": 350,
            "asymmetric_head_pad_ms": 60,
            "boundary_offset_ms": 0,
            "room_tone_pad_ms": 20,
        },
        {
            "crossfade_ms": 150,
            "post_cut_pause_ms": 280,
            "asymmetric_head_pad_ms": 80,
            "boundary_offset_ms": 0,
            "room_tone_pad_ms": 15,
        },
    ],
    "self_correction": [
        {
            # mentor gold · self_correction crossfade 50 (但内容跳跃 · 一般 pause 长)
            "crossfade_ms": 80,
            "post_cut_pause_ms": 100,
            "asymmetric_head_pad_ms": 60,
            "boundary_offset_ms": -10,
            "room_tone_pad_ms": 15,
        },
        {
            "crossfade_ms": 100,
            "post_cut_pause_ms": 150,
            "asymmetric_head_pad_ms": 80,
            "boundary_offset_ms": -20,
            "room_tone_pad_ms": 20,
        },
    ],
    # cough_like 用 source_track_gate · 不进 Optuna 迭代
    "_default_": [
        {
            # 无 kind 匹配时的通用起点 · YouTube crossfade 80 (Clean Cut Audio § 2 默认)
            "crossfade_ms": 80,
            "post_cut_pause_ms": 100,
            "asymmetric_head_pad_ms": 50,
            "boundary_offset_ms": 0,
            "room_tone_pad_ms": 15,
        },
        {
            "crossfade_ms": 50,
            "post_cut_pause_ms": 60,
            "asymmetric_head_pad_ms": 40,
            "boundary_offset_ms": 0,
            "room_tone_pad_ms": 10,
        },
    ],
}


# ---------------------------------------------------------------------------
# 文件驱动 warm-start · 数据源常量 + kind alias + 读/合成函数
# ---------------------------------------------------------------------------

DEFAULT_MENTOR_GOLD_PATH = Path(
    "/Users/renting/Desktop/minglue/剪辑项目/交付/最终交付文档/knowledge/cut_parameters.json"
)
DEFAULT_YOUTUBE_FEEDBACK_PATH = Path(
    "/Users/renting/Desktop/minglue/剪辑项目/交付/最终交付文档/knowledge/session_feedback/current.session_feedback.jsonl"
)

# optuna kind → mentor gold 字段 key · alias 表 · 见 design_md · Excavate D3
# 未决 alias 需人签字 (risks_md R3):
#   immediate_repetition ↔ immediate_rep · global_long_pause ↔ long_pause
KIND_ALIAS_TO_GOLD: Dict[str, Dict[str, Optional[str]]] = {
    "filler_hesitation":    {"crossfade": "filler_hesitation", "head_pad": "filler_hesitation"},
    "immediate_repetition": {"crossfade": "immediate_rep",     "head_pad": None},
    "global_long_pause":    {"crossfade": "long_pause",        "head_pad": "pause"},
    "self_correction":      {"crossfade": "self_correction",   "head_pad": None},
    "_default_":            {"crossfade": None,                "head_pad": None},
}


def _read_mentor_gold_params(path: Optional[Path]) -> Dict[str, Any]:
    """读 cut_parameters.json · 失败返回 {} 并 stderr warn · 不抛异常."""
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        import sys
        print(f"[warm_start] read mentor_gold failed · {p} · {e} · fallback hardcoded",
              file=sys.stderr)
        return {}


def _read_youtube_feedback_rules(path: Optional[Path]) -> Dict[str, List[Dict[str, Any]]]:
    """读 session_feedback jsonl 里 reviewer='系统 (YouTube ...)' 条目 → per-kind param dict.

    **当前实现返回 {}** · 原因: Excavate C2 证实 schema 无 machine-readable 参数字段 ·
    数值全嵌散文 (candidate_pattern/note/action_taken). 未来若 jsonl 加 parameters 字段 ·
    在此扩展 · 不影响 caller 契约.
    """
    return {}


def _compose_starts_for_kind(kind: str,
                             gold: Dict[str, Any],
                             youtube_rules: Dict[str, List[Dict[str, Any]]],
                             fallback: Dict[str, List[Dict[str, Any]]]
                             ) -> List[Dict[str, Any]]:
    """按 kind 合成起点列表 · 用 fallback 作模板 · 仅覆盖有直接证据的两维.

    覆盖规则:
      - crossfade_ms:     只改起点 A · 从 mentor gold override_by_semantic_class 读
      - room_tone_pad_ms: 只改起点 A · 从 gold check4.butt_splice_room_tone_pad_ms 读
      - 起点 B 保持硬编码 (探索用变体)
      - 其它三维全保留硬编码 (schema 冲突未决)
    """
    templates_src = fallback.get(kind) or fallback["_default_"]
    templates = [deepcopy(t) for t in templates_src]

    # ---- crossfade_ms · from mentor gold ---------------------------------
    alias_cf = KIND_ALIAS_TO_GOLD.get(kind, {}).get("crossfade")
    how_to_cut = gold.get("how_to_cut_defaults", {}) if isinstance(gold, dict) else {}
    cf_block = how_to_cut.get("crossfade_ms", {}) if isinstance(how_to_cut, dict) else {}
    gold_cf_map = cf_block.get("override_by_semantic_class", {}) if isinstance(cf_block, dict) else {}
    default_cf = cf_block.get("default") if isinstance(cf_block, dict) else None
    crossfade_val: Optional[int] = None
    if alias_cf and isinstance(gold_cf_map, dict):
        cand = gold_cf_map.get(alias_cf)
        if isinstance(cand, (int, float)):
            crossfade_val = int(cand)
    if crossfade_val is None and isinstance(default_cf, (int, float)):
        # _default_ kind 或 alias 缺失 · 用 gold default
        if alias_cf is None and kind == "_default_":
            crossfade_val = int(default_cf)
    if crossfade_val is not None and templates:
        templates[0]["crossfade_ms"] = crossfade_val

    # ---- room_tone_pad_ms · from mentor gold -----------------------------
    verify = gold.get("cut_verify_thresholds", {}) if isinstance(gold, dict) else {}
    check4 = verify.get("check4_route_crossfade_strategy", {}) if isinstance(verify, dict) else {}
    room_tone_val = check4.get("butt_splice_room_tone_pad_ms") if isinstance(check4, dict) else None
    if isinstance(room_tone_val, (int, float)) and templates:
        templates[0]["room_tone_pad_ms"] = int(room_tone_val)

    # ---- youtube_rules 目前 {} · no-op · 保留合并循环骨架 ---------------
    _yt_for_kind = youtube_rules.get(kind, []) if isinstance(youtube_rules, dict) else []
    for _rule in _yt_for_kind:  # pragma: no cover - 当前始终为空
        # 未来: 按 _rule 里的 parameters 覆盖 templates
        pass

    return templates


def load_warm_start_from_files(
    mentor_gold_json: Optional[Path] = None,
    youtube_feedback_jsonl: Optional[Path] = None,
    fallback_hardcoded: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """派生 kind → List[param dict] · 全 kind 全 5 维覆盖 · file 缺失回退 hardcoded.

    - mentor_gold_json:      cut_parameters.json 路径 · None → 走 DEFAULT_MENTOR_GOLD_PATH
    - youtube_feedback_jsonl: session_feedback jsonl · None → 走 DEFAULT_YOUTUBE_FEEDBACK_PATH
    - fallback_hardcoded:    kind → List[param dict] · None → 用 _HARDCODED_WARM_START_BY_KIND
    """
    fallback = fallback_hardcoded or _HARDCODED_WARM_START_BY_KIND
    gold = _read_mentor_gold_params(mentor_gold_json)
    youtube = _read_youtube_feedback_rules(youtube_feedback_jsonl)
    return {
        kind: _compose_starts_for_kind(kind, gold, youtube, fallback)
        for kind in fallback.keys()
    }


def _kind_provenance_map(kind: str, gold: Dict[str, Any]) -> Dict[str, str]:
    """生成单个 kind 每维 source 溯源 · 写到 trace metadata."""
    alias_cf = KIND_ALIAS_TO_GOLD.get(kind, {}).get("crossfade")
    how_to_cut = gold.get("how_to_cut_defaults", {}) if isinstance(gold, dict) else {}
    cf_block = how_to_cut.get("crossfade_ms", {}) if isinstance(how_to_cut, dict) else {}
    gold_cf_map = cf_block.get("override_by_semantic_class", {}) if isinstance(cf_block, dict) else {}
    cf_hit = bool(alias_cf) and isinstance(gold_cf_map, dict) and (gold_cf_map.get(alias_cf) is not None)
    verify = gold.get("cut_verify_thresholds", {}) if isinstance(gold, dict) else {}
    check4 = verify.get("check4_route_crossfade_strategy", {}) if isinstance(verify, dict) else {}
    rt_hit = isinstance(check4, dict) and (check4.get("butt_splice_room_tone_pad_ms") is not None)
    return {
        "crossfade_ms": (
            f"mentor_gold.override_by_semantic_class.{alias_cf}" if cf_hit else "hardcoded"
        ),
        "room_tone_pad_ms": (
            "mentor_gold.cut_verify_thresholds.check4.butt_splice_room_tone_pad_ms"
            if rt_hit else "hardcoded"
        ),
        "post_cut_pause_ms": "hardcoded",
        "asymmetric_head_pad_ms": "hardcoded",
        "boundary_offset_ms": "hardcoded",
    }


def _get_warm_start(candidate: dict,
                    table: Optional[Dict[str, List[Dict[str, Any]]]] = None
                    ) -> List[Dict[str, Any]]:
    """按候选 kind 选 warm start · 找不到用 _default_.

    table: 可选 · 由 load_warm_start_from_files 派生 · 缺省走 _HARDCODED_WARM_START_BY_KIND.
    """
    kind = candidate.get("candidate_kind") or candidate.get("kind") or "_default_"
    src = table if table is not None else _HARDCODED_WARM_START_BY_KIND
    return src.get(kind, src["_default_"])


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_discontinuity(checks: dict, fallback: float = 3.5) -> float:
    """从 cut-verify checks 拉 NISQA discontinuity · 拉不到用 fallback."""
    nisqa = checks.get("nisqa") or {}
    if nisqa.get("skipped"):
        return fallback
    scores = nisqa.get("mos_scores") or {}
    d = scores.get("discontinuity")
    return float(d) if d is not None else fallback


def _all_pre_checks_pass(overall_verdict: str) -> bool:
    """前 4 项 check 是否 PASS · 只有 PASS 才允许 converge."""
    return overall_verdict in ("CLEAN_BUTT_SPLICE", "CLEAN_SHORT_CROSSFADE")


def _content_penalty_and_ok(checks: dict) -> tuple[float, Optional[bool], int]:
    """从 checks.content_verification 提取 (penalty, content_ok, still_present_count).

    用户 2026-08-19 关键洞察 · 目标词还在 = 严重问题 · penalty 必须 >= NISQA 满分级别
    · 否则 Optuna 会用 discontinuity 高分把 content_ok=False 的参数选进来.

    penalty 规则:
      - content_ok True  → 0.0
      - content_ok False → 5.0 * still_present_count (每漏一个词加 5.0 · 5.0 = 一整个 NISQA 满分)
      - None / skipped / error → 0.0 (不影响 · 让 objective 只看 pre_check + disc)
    """
    cv = checks.get("content_verification") if isinstance(checks, dict) else None
    if not isinstance(cv, dict):
        return 0.0, None, 0
    ok = cv.get("content_ok")
    still = cv.get("target_words_still_present") or []
    count = len(still) if isinstance(still, list) else 0
    if ok is True:
        return 0.0, True, 0
    if ok is False:
        return 5.0 * max(1, count), False, count
    return 0.0, None, count


def iterate_optuna_study(candidate: dict, ctx: dict,
                        render_and_verify_fn: Any,
                        policy: dict) -> dict:
    """用 Optuna TPE + warm start 学参数 · 返回与 rule-based iterate() schema 兼容的 trace.

    2026-08-19 更新: 加 warm-start 机制 · 用 study.enqueue_trial 从 YouTube learning +
    mentor gold 派生的合理起点开始 · TPE 不冷启动 · 5 次内基本能收敛。
    """
    try:
        import optuna
        from optuna.samplers import TPESampler
    except ImportError:
        return {
            "candidate_id": candidate.get("candidate_id"),
            "final_verdict": "optuna_unavailable",
            "error": "optuna not installed · fallback to rule-based iteration",
            "iterations": [],
        }

    max_iter = policy.get("max_iterations", 5)
    hard_bounds = policy.get("hard_boundaries", {})

    # ---- Warm-start 数据源 · 2026-08-20 用户要求 · 从文件读 · fallback 硬编码 ----
    ws_cfg = policy.get("warm_start_sources") or {}
    mentor_gold_path: Optional[Path]
    youtube_path: Optional[Path]
    if ws_cfg.get("disable_warm_start_file"):
        warm_start_table = _HARDCODED_WARM_START_BY_KIND
        ws_fallback_reason = "disable_flag"
        mentor_gold_path = None
        youtube_path = None
        _gold_for_provenance: Dict[str, Any] = {}
    else:
        _mg = ws_cfg.get("mentor_gold_json")
        _yt = ws_cfg.get("youtube_feedback_jsonl")
        mentor_gold_path = Path(_mg) if _mg else DEFAULT_MENTOR_GOLD_PATH
        youtube_path = Path(_yt) if _yt else DEFAULT_YOUTUBE_FEEDBACK_PATH
        if mentor_gold_path.exists():
            ws_fallback_reason = ""
        else:
            ws_fallback_reason = f"file_missing:{mentor_gold_path}"
        warm_start_table = load_warm_start_from_files(
            mentor_gold_json=mentor_gold_path,
            youtube_feedback_jsonl=youtube_path,
            fallback_hardcoded=_HARDCODED_WARM_START_BY_KIND,
        )
        _gold_for_provenance = _read_mentor_gold_params(mentor_gold_path)

    # 阈值 · 收敛 criteria
    criteria = policy.get("acceptance_criteria", {}).get("must_pass", {})
    disc_min = 3.5
    dc = criteria.get("nisqa_discontinuity_score", {})
    if isinstance(dc, dict) and "min" in dc:
        disc_min = float(dc["min"])

    cid = candidate.get("candidate_id", "?")

    # 收集每次 trial 的完整信息 · Optuna study.trials 只存 params/value · 我们要更多
    trial_history: list[dict] = []
    converged_early = False
    final_params: Optional[dict] = None

    def objective(trial: "optuna.Trial") -> float:
        nonlocal converged_early, final_params
        # search space · 从 hard_bounds 读上下界
        cf_lo, cf_hi = hard_bounds.get("crossfade_ms", [30, 150])
        pp_lo, pp_hi = hard_bounds.get("post_cut_pause_ms", [40, 400])
        hp_lo, hp_hi = hard_bounds.get("asymmetric_head_pad_ms", [40, 120])
        bo_lo, bo_hi = hard_bounds.get("boundary_offset_ms", [-30, 30])
        rt_lo, rt_hi = hard_bounds.get("room_tone_pad_ms", [0, 30])

        params = {
            "crossfade_ms": trial.suggest_int("crossfade_ms", int(cf_lo), int(cf_hi)),
            "post_cut_pause_ms": trial.suggest_int("post_cut_pause_ms", int(pp_lo), int(pp_hi)),
            "asymmetric_head_pad_ms": trial.suggest_int("asymmetric_head_pad_ms", int(hp_lo), int(hp_hi)),
            "boundary_offset_ms": trial.suggest_int("boundary_offset_ms", int(bo_lo), int(bo_hi)),
            "room_tone_pad_ms": trial.suggest_int("room_tone_pad_ms", int(rt_lo), int(rt_hi)),
        }

        # 剪 + 验
        checks, overall_verdict = render_and_verify_fn(candidate, params, ctx)
        disc = _extract_discontinuity(checks, fallback=3.0)
        pre_pass = _all_pre_checks_pass(overall_verdict)

        # 用户 2026-08-19 · 内容验证 penalty · 剪完 clip ASR 若目标词还在 · 严重 penalty
        content_penalty, content_ok, still_present_count = _content_penalty_and_ok(checks)

        # objective · 最小化 (5.0 - disc) · pre_check FAIL 加惩罚 · content_ok=False 加惩罚
        loss = 5.0 - disc + content_penalty
        if not pre_pass:
            loss += 2.0  # 前 4 项失败惩罚

        # 判定这次是 warm-start trial 还是 TPE trial
        from_warm_start = trial.number < len(_get_warm_start(candidate, warm_start_table))
        trial_history.append({
            "n": trial.number,
            "params": params,
            "checks": checks,
            "overall_verdict": overall_verdict,
            "discontinuity": disc,
            "loss": loss,
            "pre_check_pass": pre_pass,
            "content_ok": content_ok,
            "content_penalty": content_penalty,
            "target_words_still_present_count": still_present_count,
            "triggered_rule": "optuna_warm_start" if from_warm_start else "optuna_tpe",
            "adjustments": [{"param": k, "to": v,
                             "delta_source": "warm_start" if from_warm_start else "optuna_tpe"}
                            for k, v in params.items()],
        })

        # 早停 · 前 4 项 PASS + disc ≥ 阈值 + content_ok 不为 False → 停 Optuna study
        # (content_ok True 或 None/skipped 都允许早停 · 只有明确 False 阻断)
        if pre_pass and disc >= disc_min and content_ok is not False:
            converged_early = True
            final_params = params
            raise optuna.TrialPruned()  # 用 Pruned 而不是 stop_study · Optuna 4.9 API

        return loss

    # 用 warm start 数量作 n_startup_trials · TPE 从 warm start 之后开始学
    warm_starts = _get_warm_start(candidate, warm_start_table)
    n_startup = len(warm_starts)

    study = optuna.create_study(
        direction="minimize",
        sampler=TPESampler(seed=42, n_startup_trials=n_startup),  # warm start 数就是 startup
    )

    # 关键 · enqueue warm start · Optuna 会先 evaluate 这几组
    for ws in warm_starts:
        # 把 warm start 参数 clamp 到 hard_bounds 内（防超边界）
        clamped = {}
        for k, v in ws.items():
            bounds = hard_bounds.get(k)
            if bounds:
                clamped[k] = int(max(bounds[0], min(bounds[1], v)))
            else:
                clamped[k] = v
        study.enqueue_trial(clamped)

    try:
        study.optimize(objective, n_trials=max_iter, catch=(Exception,))
    except optuna.exceptions.TrialPruned:
        pass  # 早停信号 · 已收敛

    # 收敛判定
    if converged_early and final_params:
        best_params = final_params
        final_verdict = "clean"
        escalated = False
    else:
        # 用完 max_iter 仍未收敛 · 取 study.best_params
        try:
            best_params = study.best_params
        except (ValueError, RuntimeError):
            best_params = {}
        final_verdict = "escaped_max_iterations"
        escalated = True

    # v3 (2026-08-19): escaped 候选的 last_best_params · Stage 6.10 用相对最优参数
    # · 独立从 trial_history 取最小 loss · 即使 study.best_params 因 pruned/exception 失败也保底
    # · pre_check_pass 的 trial 优先 · 否则退回 loss 最小的
    last_best_params: dict = {}
    last_best_loss: Optional[float] = None
    last_best_source = "none"
    if converged_early and final_params:
        last_best_params = deepcopy(final_params)
        last_best_source = "converged_trial"
        # loss = 5.0 - disc (pre_pass) · 从 trial_history 找匹配 params 的 loss
        for h in trial_history:
            if h.get("params") == final_params:
                last_best_loss = h.get("loss")
                break
    else:
        # 优先 pre_check_pass 的 trial
        passed = [h for h in trial_history if h.get("pre_check_pass")]
        pool = passed if passed else trial_history
        if pool:
            best_hist = min(pool, key=lambda h: h.get("loss", float("inf")))
            last_best_params = deepcopy(best_hist.get("params") or {})
            last_best_loss = best_hist.get("loss")
            last_best_source = ("min_loss_pre_pass_trial" if passed
                                else "min_loss_all_trials")
    # 若 study.best_params 之前拿失败 · 用 last_best_params 兜底
    if not best_params and last_best_params:
        best_params = deepcopy(last_best_params)

    return {
        "candidate_id": cid,
        "candidate_kind": candidate.get("candidate_kind") or candidate.get("kind"),
        "warm_start_used": [dict(ws) for ws in warm_starts],
        "initial_params": trial_history[0]["params"] if trial_history else {},
        "iterations": trial_history,
        "iterations_used": len(trial_history),
        "iterations_max": max_iter,
        "converged": converged_early,
        "escalated_to_human_review": escalated,
        "final_verdict": final_verdict,
        "final_params": best_params,
        "last_best_params": last_best_params,
        "last_best_loss": last_best_loss,
        "last_best_source": last_best_source,
        "optimization_backend": "optuna_tpe_with_warm_start",
        "optuna_version": optuna.__version__,
        "seed": 42,
        "n_startup_trials": n_startup,
        "search_space": {
            k: [hard_bounds.get(k, [None, None])[0], hard_bounds.get(k, [None, None])[1]]
            for k in ["crossfade_ms", "post_cut_pause_ms", "asymmetric_head_pad_ms",
                     "boundary_offset_ms", "room_tone_pad_ms"]
        },
        "warm_start_source": {
            "mentor_gold_json": str(mentor_gold_path) if mentor_gold_path else None,
            "youtube_feedback_jsonl": str(youtube_path) if youtube_path else None,
            "disable_warm_start_file": bool(ws_cfg.get("disable_warm_start_file", False)),
            "per_kind_provenance": {
                (candidate.get("candidate_kind") or candidate.get("kind") or "_default_"):
                    _kind_provenance_map(
                        candidate.get("candidate_kind") or candidate.get("kind") or "_default_",
                        _gold_for_provenance,
                    ),
            },
            "hardcoded_fallback_reason": ws_fallback_reason,
        },
        "computed_at_utc": _utc_now(),
    }

