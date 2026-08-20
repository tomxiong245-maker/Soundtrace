#!/usr/bin/env python3
"""iterate_until_clean · 迭代式剪口精修主入口 (iterative refinement / reward-guided policy iteration).

**目的**：给一个已被 gate 判 auto_cut 的候选 · 用当前参数剪 → NISQA + cut-verify 判"干净了吗" →
不干净就按 refinement-policy-v1.json 调参 · 重剪 · 再判 · 直到 clean 或 3 次上限。

**受启发于**：iZotope RX Assistant · Adobe Enhance Speech Assistant · reward-guided policy iteration
· 学界 quality-driven parameter search。

**核心约束（严格！)**：
  · 只调 execution 层参数 (crossfade_ms / pause_ms / boundary_offset ± 10ms / room_tone_pad_ms)
  · **不改** start_seconds / end_seconds / action_type (语义层 · M3 元规则)
  · **不 revisit gate 判决** (gate 说要剪 · iteration 不 revisit '剪不剪')
  · 3 次上限 · 不过转 human_review (M3 fallback)
  · 参数硬边界 · 见 refinement-policy-v1.json.hard_boundaries

**收敛条件 (从 policy JSON 读)**:
  · cut_verify_overall_verdict ∈ {CLEAN_BUTT_SPLICE, CLEAN_SHORT_CROSSFADE}
  · NISQA discontinuity ≥ 3.5
  · NISQA MOS delta overall ≥ -0.5

**输入**:
  --candidate-json  单候选或多候选 JSON
  --transcript-dir  三轨 ASR
  --raw-track-map   JSON string
  --render-fn       (可选 · 默认调 external render script) · 每次迭代重剪的入口
  --cut-params-json main/knowledge/cut_parameters.json (读初始参数)
  --policy-json     refinement-policy-v1.json (读迭代规则)
  --nisqa-venv-python opt-in · 若不提供 · discontinuity 检不了 · 退回只用前 4 项
  --out             输出 refinement_trace.json

**输出 schema**: iterative-refinement-v1
  {schema_version, candidates: [{
    candidate_id, initial_params, iterations: [...], final_params, final_verdict,
    iterations_used, iterations_max, escalated_to_human_review
  }]}

**Provenance**: 每次迭代记 params_before/after · 触发的 check · 调的规则 · 全部可追溯回放.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Atomic write · 防止 pipeline 中途 exit 导致 refinement_trace.json 半写
# 2026-08-19: Stage 6.7 partial_trace 每 5 candidates flush 一次 · 防丢失
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, payload: dict) -> None:
    """写 tmp 文件 · fsync · rename → 保证读者看到的 JSON 永远完整。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

SKILL_DIR = Path(__file__).resolve().parent
CHALLENGER_ROOT = SKILL_DIR.parent
PROJECT_ROOT = CHALLENGER_ROOT.parents[2]

DEFAULT_POLICY = CHALLENGER_ROOT / "rules/refinement-policy-v1.json"
DEFAULT_CUT_PARAMS = PROJECT_ROOT / "main/knowledge/cut_parameters.json"
VERIFY_CUT_PLAN = PROJECT_ROOT / "skills/cut-verify/scripts/verify_cut_plan.py"
CONTENT_VERIFY_CUT = SKILL_DIR / "content_verify_cut.py"

# 用户 2026-08-19 关键洞察 · content_ok 加入 objective
# · 只对有可读文本的 candidate_kind 做 ASR 内容验证
# · global_long_pause / transient_events / cough_like 剪的是静音/瞬态 · 无文本可验 · 跳过
CONTENT_VERIFY_APPLICABLE_KINDS = {
    "filler_hesitation",
    "immediate_repetition",
    "self_correction",
}


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_target_words(cand: dict) -> list:
    """从候选反推 target_words · 应剪掉的目标词列表.

    优先级 (design result 2026-08-19):
      1. proposed_delete_text (语义类候选都有此字段 · 已实测)
      2. 从 text_tracks[source_track_id].words 反推区间 [start-0.02, end+0.02] 内的 word.text
    返回 [] 表示没法确定 target · 调用者应跳过 content_verify.
    """
    pdt = cand.get("proposed_delete_text")
    if pdt:
        return [pdt]
    src = cand.get("source_track_id") or cand.get("track_id")
    st = cand.get("start_seconds")
    et = cand.get("end_seconds")
    if not src or st is None or et is None:
        return []
    tt = cand.get("text_tracks", {})
    if not isinstance(tt, dict):
        return []
    track = tt.get(src) or {}
    words = track.get("words", []) if isinstance(track, dict) else []
    out = []
    for w in words:
        try:
            ws = float(w.get("start_seconds", 0))
            we = float(w.get("end_seconds", 0))
            if ws >= float(st) - 0.02 and we <= float(et) + 0.02:
                t = w.get("text")
                if t:
                    out.append(t)
        except (TypeError, ValueError):
            continue
    return out


def load_policy(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_initial_params(cut_params_json: Path) -> dict:
    """从 cut_parameters.json 读初始参数默认值."""
    if not cut_params_json.is_file():
        # 不存在时用硬编码 fallback (M3 缺省值)
        return {
            "crossfade_ms": 50,
            "post_cut_pause_ms": 40,
            "asymmetric_head_pad_ms": 40,
            "room_tone_pad_ms": 10,
            "boundary_offset_ms": 0,
        }
    d = json.loads(cut_params_json.read_text(encoding="utf-8"))
    # cut_parameters.json 各 key 位置约定见 §21 CLAUDE.md
    return {
        "crossfade_ms": d.get("crossfade", {}).get("default_ms", 50),
        "post_cut_pause_ms": d.get("post_cut_pause", {}).get("default_ms", 40),
        "asymmetric_head_pad_ms": d.get("asymmetric_head_pad", {}).get("default_ms", 40),
        "room_tone_pad_ms": d.get("room_tone_pad", {}).get("default_ms", 10),
        "boundary_offset_ms": 0,
    }


def within_bound(val: float, cap: list) -> float:
    """clamp val 到 cap [min, max] 范围内."""
    lo, hi = cap[0], cap[1]
    return max(lo, min(hi, val))


def _resolve_data_driven_delta(delta_source: str, current_value: float,
                                cut_params: dict, remaining_iterations: int) -> Optional[float]:
    """从 cut_parameters.json 数据驱动算 delta · 不拍脑袋。

    delta_source 支持:
      - "cut_parameters.<field>.target_range_center_minus_current"
        → target = mean(target_range) · delta = (target - current) / max(1, remaining)
      - "cut_parameters.<field>.median_observed_minus_current"
        → target = median_observed · delta = (target - current) / max(1, remaining)
      - "cut_parameters.<field>.default_ms"
        → target = default · delta = (target - current) / max(1, remaining)
      - "fallback_only" → return None (调用者用 delta_fallback)

    返回 None = 无数据支撑 · 调用者应用 delta_fallback。
    """
    if not delta_source or delta_source == "fallback_only":
        return None

    # 解析路径 "cut_parameters.<field_path>.<metric>"
    # e.g. "cut_parameters.gap_after.target_range_center_minus_current"
    prefix = "cut_parameters."
    if not delta_source.startswith(prefix):
        return None
    tail = delta_source[len(prefix):]
    parts = tail.rsplit(".", 1)
    if len(parts) != 2:
        return None
    field_path, metric = parts

    # 到 cut_params 里查 field_path (dot-separated)
    node = cut_params.get("how_to_cut_defaults", cut_params)
    for key in field_path.split("."):
        # 支持简写: gap_after 实际 key 是 gap_after_ms
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, dict) and (key + "_ms") in node:
            node = node[key + "_ms"]
        else:
            return None

    # 提取 target 值
    target = None
    if metric == "target_range_center_minus_current":
        tr = node.get("target_range") if isinstance(node, dict) else None
        if tr and len(tr) == 2:
            target = (tr[0] + tr[1]) / 2.0
    elif metric == "median_observed_minus_current":
        target = node.get("median_observed") if isinstance(node, dict) else None
    elif metric == "default_ms":
        target = node.get("default_ms") or node.get("default") if isinstance(node, dict) else None

    if target is None:
        return None

    distance = target - current_value
    delta = distance / max(1, remaining_iterations)
    return delta


def apply_adjustment(params: dict, rule: dict, hard_bounds: dict,
                     cut_params: Optional[dict] = None,
                     remaining_iterations: int = 5) -> tuple[dict, list]:
    """按 rule.action 调 params · 返回 (new_params, changes)。

    v2 (2026-08-19): 优先从 cut_parameters.json 数据驱动算 delta · fallback 到硬编码。
    changes: [{param: str, from: value, to: value, rule_id: str, delta_source: str}]
    """
    action = rule.get("action", {})
    param_name = action.get("param")
    changes = []
    new_params = deepcopy(params)
    cut_params = cut_params or {}

    if not param_name or param_name not in new_params:
        return new_params, changes

    old = float(new_params[param_name])

    # v2 · 优先数据驱动
    delta_val = None
    delta_source_used = "hardcoded_fallback"
    delta_source = action.get("delta_source")
    if delta_source:
        driven = _resolve_data_driven_delta(delta_source, old, cut_params, remaining_iterations)
        if driven is not None:
            delta_val = driven
            delta_source_used = delta_source
    # fallback · 硬编码 delta_fallback 或旧 schema 的 delta
    if delta_val is None:
        fallback = action.get("delta_fallback") or action.get("delta", "0")
        if isinstance(fallback, str) and fallback.startswith("±"):
            delta_val = float(fallback[1:])
        elif isinstance(fallback, str):
            delta_val = float(fallback)
        else:
            delta_val = float(fallback)

    # cap: rule 自己的 cap · 与 hard_bounds 取交集
    cap = action.get("cap") or hard_bounds.get(param_name, [-1e9, 1e9])
    target = old + delta_val
    target = within_bound(target, cap)
    target = within_bound(target, hard_bounds.get(param_name, cap))
    if target != old:
        new_params[param_name] = target
        changes.append({
            "param": param_name,
            "from": old,
            "to": target,
            "rule_id": rule.get("id", "?"),
            "delta_source": delta_source_used,
            "delta_value": delta_val,
        })

    # also · 另加参数
    also = action.get("also", {})
    for k, v in also.items():
        if k.startswith("add_") and k.endswith("_source"):
            # 数据驱动 also · e.g. add_room_tone_pad_ms_source
            actual_param = k[len("add_"):-len("_source")]
            if actual_param in new_params:
                # 试数据驱动 · 失败 fallback
                fallback_key = k.replace("_source", "_fallback")
                fb = also.get(fallback_key, 10)
                old2 = float(new_params[actual_param])
                driven2 = _resolve_data_driven_delta(v, old2, cut_params, remaining_iterations)
                delta2 = driven2 if driven2 is not None else float(fb)
                cap2 = also.get("cap", hard_bounds.get(actual_param, [-1e9, 1e9]))
                target2 = within_bound(old2 + delta2, hard_bounds.get(actual_param, cap2))
                if target2 != old2:
                    new_params[actual_param] = target2
                    changes.append({
                        "param": actual_param,
                        "from": old2,
                        "to": target2,
                        "rule_id": rule.get("id", "?") + "(also)",
                        "delta_source": v if driven2 is not None else "hardcoded_fallback",
                        "delta_value": delta2,
                    })
        elif k.startswith("add_") and not k.endswith(("_fallback", "_source", "_cap")):
            # 旧 schema · add_xxx = number
            actual_param = k[len("add_"):]
            if actual_param in new_params:
                old2 = float(new_params[actual_param])
                delta2 = float(v)
                cap2 = also.get("cap", hard_bounds.get(actual_param, [-1e9, 1e9]))
                target2 = within_bound(old2 + delta2, hard_bounds.get(actual_param, cap2))
                if target2 != old2:
                    new_params[actual_param] = target2
                    changes.append({
                        "param": actual_param,
                        "from": old2,
                        "to": target2,
                        "rule_id": rule.get("id", "?") + "(also)",
                        "delta_source": "hardcoded_legacy",
                        "delta_value": delta2,
                    })

    return new_params, changes


def detect_failure_triggers(checks: dict) -> list[str]:
    """从 cut-verify checks 结果提取失败触发点 · 返回触发 id 列表."""
    triggers = []
    rhythm = checks.get("rhythm_gap", {})
    if rhythm.get("verdict") == "RHYTHM_TOO_TIGHT" or "TOO_TIGHT" in str(rhythm.get("verdict", "")):
        triggers.append("cut_verify_check_3_rhythm_TOO_TIGHT")
    if rhythm.get("verdict") == "RHYTHM_TOO_LOOSE" or "TOO_LOOSE" in str(rhythm.get("verdict", "")):
        triggers.append("cut_verify_check_3_rhythm_TOO_LOOSE")

    silence = checks.get("silence_location", {})
    if silence.get("verdict") == "NEEDS_CROSSFADE":
        triggers.append("cut_verify_check_2_silence_NEEDS_CROSSFADE")

    nisqa = checks.get("nisqa", {})
    if not nisqa.get("skipped"):
        scores = nisqa.get("mos_scores", {})
        disc = scores.get("discontinuity", 5.0)
        if disc < 3.5:
            triggers.append("nisqa_discontinuity_low")
        # delta trigger 需要上下文 · 若 verdict 是 REJECT_QUALITY_REGRESSION
        if nisqa.get("verdict") == "REJECT_QUALITY_REGRESSION":
            triggers.append("nisqa_mos_delta_regression")

    return triggers


def check_convergence(overall_verdict: str, checks: dict, policy: dict) -> tuple[bool, str]:
    """判是否达标 · 返回 (converged, reason)."""
    criteria = policy.get("acceptance_criteria", {}).get("must_pass", {})
    accepted_verdicts = criteria.get("cut_verify_overall_verdict", [])
    if overall_verdict not in accepted_verdicts:
        return False, f"overall_verdict={overall_verdict} not in {accepted_verdicts}"

    # discontinuity 检查
    disc_criterion = criteria.get("nisqa_discontinuity_score", {})
    min_disc = disc_criterion.get("min", 3.5) if isinstance(disc_criterion, dict) else 3.5
    nisqa = checks.get("nisqa", {})
    if not nisqa.get("skipped"):
        scores = nisqa.get("mos_scores", {})
        disc = scores.get("discontinuity")
        if disc is not None and disc < min_disc:
            return False, f"discontinuity={disc} < {min_disc}"

    # delta 检查
    delta_criterion = criteria.get("nisqa_mos_delta_overall", {})
    min_delta = delta_criterion.get("min", -0.5) if isinstance(delta_criterion, dict) else -0.5
    # delta 需要外部提供 · 这里若 nisqa verdict = REJECT_QUALITY_REGRESSION 就算 fail
    if nisqa.get("verdict") == "REJECT_QUALITY_REGRESSION":
        return False, f"nisqa REJECT_QUALITY_REGRESSION · delta < {min_delta}"

    return True, "all criteria pass"


def render_and_verify(candidate: dict, params: dict, ctx: dict) -> tuple[dict, str]:
    """给一个候选 + 当前参数 · **用 pydub 现场剪一段 clip**（Optuna auto-iterate 用）·
    再用 verify_cut_plan 判 · 返回 (checks, overall_verdict).

    ctx: {transcript_dir, raw_track_map, cut_params_json, tmp_dir, nisqa_venv_python, render_root}
    """
    tmp_dir = ctx["tmp_dir"]
    cid = candidate.get("candidate_id") or "?"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 单候选 JSON
    single_json = tmp_dir / f"{cid}.iterating.candidate.json"
    single_json.write_text(json.dumps({"candidates": [candidate]}, ensure_ascii=False), encoding="utf-8")

    # 现场用 pydub 剪 clip
    render_root = tmp_dir / "iter_clips"
    render_root.mkdir(parents=True, exist_ok=True)
    track_id = candidate.get("source_track_id") or candidate.get("track_id") or "track_01"
    raw_wav = ctx["raw_track_map"].get(track_id)
    if raw_wav and Path(raw_wav).is_file():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from render_clip_for_iteration import render_clip
            clip_out = render_root / f"{cid}.wav"
            render_clip(
                raw_wav=Path(raw_wav),
                start_seconds=float(candidate.get("start_seconds", 0)),
                end_seconds=float(candidate.get("end_seconds", 0)),
                crossfade_ms=int(params.get("crossfade_ms", 50)),
                pause_ms=int(params.get("post_cut_pause_ms", 40)),
                head_pad_ms=int(params.get("asymmetric_head_pad_ms", 40)),
                boundary_offset_ms=int(params.get("boundary_offset_ms", 0)),
                room_tone_pad_ms=int(params.get("room_tone_pad_ms", 10)),
                out_path=clip_out,
            )
        except Exception as exc:
            # 剪失败 · fallback · 让 verify_cut_plan skip Check 5
            print(f"[render_and_verify] render_clip failed for {cid}: {exc}", file=sys.stderr)

    # 调 verify_cut_plan
    verify_out = tmp_dir / f"{cid}.iter.verified.json"
    cmd = [
        sys.executable, str(VERIFY_CUT_PLAN),
        "--candidate-json", str(single_json),
        "--transcript-dir", str(ctx["transcript_dir"]),
        "--raw-track-map", json.dumps(ctx["raw_track_map"], ensure_ascii=False),
        "--cut-params-json", str(ctx["cut_params_json"]),
        "--tmp-dir", str(tmp_dir / f"{cid}.cvtmp"),
        "--out", str(verify_out),
    ]
    if ctx.get("nisqa_venv_python"):
        cmd += ["--nisqa-venv-python", str(ctx["nisqa_venv_python"])]
        cmd += ["--render-root", str(render_root)]  # 我们刚剪的 clip 在这

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"error": proc.stderr[:400]}, "ERROR"

    doc = json.loads(verify_out.read_text(encoding="utf-8"))
    cand_result = doc["candidates"][0]
    checks = cand_result["checks"]

    # 用户 2026-08-19 关键洞察 · 加 ASR 内容验证 · 检查目标词是否真被剪掉
    # · 仅对语义类 candidate_kind 生效 (filler_hesitation / immediate_repetition / self_correction)
    # · global_long_pause / transient_events / cough_like 剪的是静音/瞬态 · 无文本 · 跳过
    kind = candidate.get("candidate_kind") or candidate.get("kind") or ""
    rendered_clip = render_root / f"{cid}.wav"
    if kind in CONTENT_VERIFY_APPLICABLE_KINDS and CONTENT_VERIFY_CUT.is_file() and rendered_clip.is_file():
        target_words = _get_target_words(candidate)
        if target_words:
            content_out_json = tmp_dir / f"{cid}.content.json"
            try:
                cv_cmd = [
                    sys.executable, str(CONTENT_VERIFY_CUT),
                    "--clip-path", str(rendered_clip),
                    "--target-words", json.dumps(target_words, ensure_ascii=False),
                    "--asr-lang", "zh",
                    "--out-json", str(content_out_json),
                ]
                subprocess.run(cv_cmd, capture_output=True, text=True, check=False)
                if content_out_json.is_file():
                    checks["content_verification"] = json.loads(
                        content_out_json.read_text(encoding="utf-8")
                    )
                else:
                    checks["content_verification"] = {
                        "content_ok": None,
                        "skipped": True,
                        "skip_reason": "content_verify_cut produced no output",
                    }
            except Exception as exc:
                checks["content_verification"] = {
                    "content_ok": None,
                    "error": str(exc)[:400],
                }
        else:
            checks["content_verification"] = {
                "content_ok": None,
                "skipped": True,
                "skip_reason": "no target_words derivable from candidate",
            }
    else:
        # 记录跳过原因 · 便于 trace 里回放
        if kind not in CONTENT_VERIFY_APPLICABLE_KINDS:
            skip_reason = f"kind={kind} not in {sorted(CONTENT_VERIFY_APPLICABLE_KINDS)}"
        elif not CONTENT_VERIFY_CUT.is_file():
            skip_reason = f"content_verify_cut.py missing at {CONTENT_VERIFY_CUT}"
        else:
            skip_reason = f"rendered_clip missing at {rendered_clip}"
        checks["content_verification"] = {
            "content_ok": None,
            "skipped": True,
            "skip_reason": skip_reason,
        }

    return checks, cand_result["overall_verdict"]


def iterate(candidate: dict, initial_params: dict, policy: dict, ctx: dict) -> dict:
    """核心迭代循环。"""
    max_iter = policy.get("max_iterations", 5)
    hard_bounds = policy.get("hard_boundaries", {})
    rules = {r["trigger"]: r for r in policy.get("adjustment_rules", [])}
    # v2 · 数据驱动 delta 需要 cut_parameters
    cut_params_path = ctx.get("cut_params_json")
    cut_params_data = {}
    if cut_params_path and Path(cut_params_path).is_file():
        try:
            cut_params_data = json.loads(Path(cut_params_path).read_text(encoding="utf-8"))
        except Exception:
            pass

    iterations = []
    params = deepcopy(initial_params)
    converged = False
    final_verdict = "unknown"
    escalated = False

    for n in range(max_iter):
        # 剪 + 验
        checks, overall_verdict = render_and_verify(candidate, params, ctx)

        # 收敛?
        conv, reason = check_convergence(overall_verdict, checks, policy)

        entry = {
            "n": n,
            "params_before": deepcopy(params),
            "checks": checks,
            "overall_verdict": overall_verdict,
            "converged_check": conv,
            "converged_reason": reason,
            "adjustments": [],
            "triggered_rule": None,
        }

        if conv:
            iterations.append(entry)
            converged = True
            final_verdict = "clean"
            break

        # 检查失败 · 找触发规则
        triggers = detect_failure_triggers(checks)
        entry["triggers_detected"] = triggers

        applied = False
        for t in triggers:
            rule = rules.get(t)
            if rule:
                # v2 · 传 cut_params + remaining_iterations 让 delta 数据驱动
                remaining = max_iter - n
                new_params, changes = apply_adjustment(params, rule, hard_bounds,
                                                        cut_params=cut_params_data,
                                                        remaining_iterations=remaining)
                if changes:
                    entry["triggered_rule"] = rule["id"]
                    entry["adjustments"] = changes
                    entry["params_after"] = deepcopy(new_params)
                    params = new_params
                    applied = True
                    break  # 一次只调一条规则 · 保守
        if not applied:
            entry["adjustments"] = []
            entry["note"] = "no matching adjustment rule · will escalate"

        iterations.append(entry)

        if not applied:
            # 无匹配规则 · 早退到 human_review
            escalated = True
            final_verdict = "escaped_no_rule_match"
            break

    if not converged and not escalated:
        # 用完 iterations · 未收敛
        escalated = True
        final_verdict = "escaped_max_iterations"

    # v3 (2026-08-19): escaped 候选也要给 last_best_params · 让 Stage 6.10 用相对最优参数
    # · rule-based 路径没有 loss 函数 · 用"最后一次调过参的 params_after"作 last_best
    # · 若一次也没调过 · 用 initial_params
    last_best_params = deepcopy(params)  # 循环出来时 params 已是最后一次调整后的
    last_best_reason = "last_iteration_params" if iterations else "initial_params_no_iteration"

    return {
        "candidate_id": candidate.get("candidate_id"),
        "initial_params": initial_params,
        "iterations": iterations,
        "iterations_used": len(iterations),
        "iterations_max": max_iter,
        "converged": converged,
        "escalated_to_human_review": escalated,
        "final_verdict": final_verdict,
        "final_params": params,
        "last_best_params": last_best_params,
        "last_best_reason": last_best_reason,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate-json", required=True, type=Path)
    ap.add_argument("--transcript-dir", required=True, type=Path)
    ap.add_argument("--raw-track-map", required=True, type=str)
    ap.add_argument("--cut-params-json", type=Path, default=DEFAULT_CUT_PARAMS)
    ap.add_argument("--policy-json", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--nisqa-venv-python", type=Path, default=None,
                    help="opt-in · Challenger B venv python · 启用 NISQA 收敛判决")
    ap.add_argument("--render-root", type=Path, default=None,
                    help="剪好的 clip 存放目录 (<cid>.wav|.mp3)")
    ap.add_argument("--tmp-dir", type=Path, default=Path("/tmp/iterative_refinement"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--use-optuna", action="store_true",
                    help="opt-in · 用 Optuna TPE 替换 rule-based delta · 官方 Bayesian 数据驱动 · KDD 2019 · MIT · 用户 2026-08-19 明确 '不要用我自己搞的' 后引入")
    ap.add_argument("--mentor-gold-warmstart-json", type=Path, default=None,
                    help="Warm-start 挖掘的 mentor gold 参数 · None → optuna_refine.DEFAULT_MENTOR_GOLD_PATH · 与 --cut-params-json 概念分离 (后者是运行时参数, 前者是 warm-start 挖掘源)")
    ap.add_argument("--youtube-feedback-jsonl", type=Path, default=None,
                    help="Warm-start 用 YouTube learning session_feedback jsonl · None → optuna_refine.DEFAULT_YOUTUBE_FEEDBACK_PATH")
    ap.add_argument("--disable-warm-start-file", action="store_true",
                    help="强制走硬编码 warm start · 回归测试与 A/B 用 · 2026-08-20 加")
    args = ap.parse_args(argv)

    args.tmp_dir.mkdir(parents=True, exist_ok=True)
    raw_track_map = json.loads(args.raw_track_map)
    policy = load_policy(args.policy_json)
    policy["warm_start_sources"] = {
        "mentor_gold_json": str(args.mentor_gold_warmstart_json) if args.mentor_gold_warmstart_json else None,
        "youtube_feedback_jsonl": str(args.youtube_feedback_jsonl) if args.youtube_feedback_jsonl else None,
        "disable_warm_start_file": bool(args.disable_warm_start_file),
    }
    initial_params = load_initial_params(args.cut_params_json)

    ctx = {
        "transcript_dir": args.transcript_dir,
        "raw_track_map": raw_track_map,
        "cut_params_json": args.cut_params_json,
        "tmp_dir": args.tmp_dir,
        "nisqa_venv_python": args.nisqa_venv_python,
        "render_root": args.render_root,
        "render_fn": None,  # 上游负责剪 · 骨架版本不带 render_fn
    }

    cd = json.loads(args.candidate_json.read_text(encoding="utf-8"))
    candidates = cd.get("candidates") if isinstance(cd, dict) and "candidates" in cd else [cd]

    out_path = args.out or args.candidate_json.parent / "refinement_trace.json"
    partial_path = out_path.with_suffix(out_path.suffix + ".partial")

    def _build_out(partial_list: list, *, is_partial: bool) -> dict:
        return {
            "schema_version": "iterative-refinement-v1",
            "policy_source": str(args.policy_json),
            "cut_params_source": str(args.cut_params_json),
            "candidates": partial_list,
            "summary": {
                "total": len(partial_list),
                "clean": sum(1 for r in partial_list if r.get("final_verdict") == "clean"),
                "escaped_max": sum(1 for r in partial_list
                                    if r.get("final_verdict") == "escaped_max_iterations"),
                "escaped_no_rule": sum(1 for r in partial_list
                                        if r.get("final_verdict") == "escaped_no_rule_match"),
                "skipped_mute_only_count": sum(1 for r in partial_list
                                                if r.get("final_verdict") == "SKIPPED_MUTE_ONLY_KIND"),
                "avg_iterations": (sum(r.get("iterations_used", 0) for r in partial_list) /
                                   max(1, len(partial_list))),
                "is_partial": is_partial,
                "processed": len(partial_list),
                "total_expected": len(candidates),
            },
            "computed_at_utc": _utc_now(),
        }

    trace_candidates: list = []
    PARTIAL_FLUSH_EVERY = 5
    # 用户 2026-08-19 明确 · cough_like/transient_events 走 source_track_gate_only · 不进 Optuna
    blacklist_kinds = set(policy.get("optuna_kind_blacklist", {}).get("kinds", []))
    for idx, c in enumerate(candidates):
        cand_kind = c.get("candidate_kind") or c.get("kind")
        if blacklist_kinds and cand_kind in blacklist_kinds:
            result = {
                "candidate_id": c.get("candidate_id"),
                "candidate_kind": cand_kind,
                "final_verdict": "SKIPPED_MUTE_ONLY_KIND",
                "iterations_used": 0,
                "iterations_max": policy.get("max_iterations", 10),
                "converged": False,
                "escalated_to_human_review": False,
                "skip_reason": "kind in optuna_kind_blacklist · use source_track_gate_only instead",
            }
            trace_candidates.append(result)
            continue
        if args.use_optuna:
            # Optuna 路径 · 数据驱动 delta · 见 optuna_refine.py
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from optuna_refine import iterate_optuna_study
            result = iterate_optuna_study(c, ctx, render_and_verify, policy)
        else:
            # Rule-based 路径 · 硬编码 delta 或 cut_parameters 驱动
            result = iterate(c, initial_params, policy, ctx)
        trace_candidates.append(result)

        # partial_trace flush · 每 5 candidates 落一次盘 · 防 pipeline 中途 exit 丢失
        if (idx + 1) % PARTIAL_FLUSH_EVERY == 0 and (idx + 1) < len(candidates):
            try:
                _atomic_write_json(partial_path, _build_out(trace_candidates, is_partial=True))
                print(f"[partial_trace] flushed {idx + 1}/{len(candidates)} → {partial_path}",
                      file=sys.stderr)
            except Exception as exc:
                print(f"[partial_trace] flush failed: {exc}", file=sys.stderr)

    out = _build_out(trace_candidates, is_partial=False)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(out_path, out)
    # 成功写完整 trace 后清 partial · 避免下游误读旧 partial
    try:
        if partial_path.exists():
            partial_path.unlink()
    except Exception:
        pass
    print(f"wrote {out_path}")
    print(f"summary: {json.dumps(out['summary'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
