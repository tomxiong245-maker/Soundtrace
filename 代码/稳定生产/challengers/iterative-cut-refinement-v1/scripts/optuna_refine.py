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
# Warm-start · 按候选 kind 定合理起点 · 来源 YouTube learning + mentor gold
# ---------------------------------------------------------------------------

WARM_START_BY_KIND: Dict[str, List[Dict[str, Any]]] = {
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


def _get_warm_start(candidate: dict) -> List[Dict[str, Any]]:
    """按候选 kind 选 warm start · 找不到用 _default_."""
    kind = candidate.get("candidate_kind") or candidate.get("kind") or "_default_"
    return WARM_START_BY_KIND.get(kind, WARM_START_BY_KIND["_default_"])


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
        from_warm_start = trial.number < len(_get_warm_start(candidate))
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
    warm_starts = _get_warm_start(candidate)
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
        "warm_start_source": "YouTube learning policy (session_feedback) + mentor gold (cut_parameters.json) · hardcoded per-kind · TODO: 未来从 file 读取",
        "computed_at_utc": _utc_now(),
    }

