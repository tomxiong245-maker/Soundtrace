#!/usr/bin/env python3
"""run_end_to_end — zero-touch pipeline: N 轨原始 WAV → 成品 mp3

跨过人审的自动 machine-assisted-draft 交付路径。
适用于**新一期节目**或**已有 candidate 的 run 快速重跑**。

**关键行为**:
- 走 policy_v2 白名单 + autocut_gate v3 判决 auto-cut 集合
- 无正 signal 但无历史反例 + 短时长 + 非保护区 → 允许 auto-cut
- 有历史反例 (hr>0) → 强制 human_review_required
- 输出 machine_assisted_draft 状态（**不是 human_approved**）—— 遵守 CLAUDE.md 边界

**两种运行模式**:

1. `--from-existing-run <run_dir>` (快速 · 5-10 min)
   复用已有 run 的 candidates + calibration + policy_application; 只跑 gate + 渲染

2. `--from-raw-wav <track1.wav track2.wav ...>` (完整 · 30-60 min)
   跑 orchestrator start (denoise + ASR + candidates + snap + policy) 到 CANDIDATES_FROZEN
   然后走模式 1

Usage:
    # 模式 1（今晚 EP04 验收）
    python3 run_end_to_end.py \\
        --episode-id EP04-AUTO-VERIFY \\
        --from-existing-run main/runs/EP04/EP04-machine-assisted-draft-20260817-002 \\
        --music-template reference-linear-v2-guest-cued-outro \\
        --out-dir main/runs/EP04-AUTO-VERIFY-20260817/

    # 模式 2（EP05 新一期节目）
    python3 run_end_to_end.py \\
        --episode-id EP05 \\
        --from-raw-wav track_01.wav track_02.wav track_03.wav \\
        --music-template reference-linear-v2-guest-cued-outro \\
        --out-dir main/runs/EP05-AUTO-20260818/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]  # …/剪辑项目
sys.path.insert(0, str(PROJECT_ROOT / "main"))
from tools.tool_lookup import script_for as _script_for  # noqa: E402


# --------- helpers ---------------------------------------------------------

def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(str(x) for x in cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=False)


def _load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, value: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SEGMENT_SEPARATOR_TOKENS = {
    "第一", "第二", "第三", "第四", "第五", "首先", "然后", "其次",
    "另外", "另一方面", "接下来", "最后", "总结", "综上",
    "一方面", "第一个", "第二个", "第三个", "第四个", "第五个",
    "one", "two", "three", "first", "second", "third", "finally",
}


def enrich_candidates_v20(candidates: list[dict], transcript_dir: Path | None) -> None:
    """v20 feedback (2026-08-17): 给老候选补上 boundary_lock 与 post_cut_pause_ms
    字段。老 codex draft 的 candidates 缺这些新字段；不改候选生成器（champion 边界），
    在这里做一次 post-fetch enrich 即可。

    - filler_hesitation / immediate_repetition → boundary_lock=True, post_cut_pause_ms=40/60
    - 若附近 ±3s 出现 segment_separator（"第三个" 等）→ post_cut_pause_ms=350
    """
    # Load per-track word timeline for segment_separator lookup
    words_by_track: dict[str, list[dict]] = {}
    if transcript_dir and transcript_dir.is_dir():
        for tp in transcript_dir.glob("track_*.transcript.json"):
            try:
                d = _load_json(tp)
                label = tp.stem.replace(".transcript", "")
                words_by_track[label] = d.get("words", [])
            except Exception:
                pass

    def _has_separator_nearby(track_id: str, t_center: float, radius_s: float = 3.0) -> str | None:
        words = words_by_track.get(track_id) or []
        for w in words:
            s = w.get("start_seconds")
            if s is None:
                continue
            if abs(float(s) - t_center) <= radius_s:
                tok = str(w.get("text", "")).strip()
                if tok in SEGMENT_SEPARATOR_TOKENS:
                    return tok
        return None

    for c in candidates:
        kind = c.get("candidate_kind") or c.get("kind") or ""
        if kind not in ("filler_hesitation", "immediate_repetition"):
            continue
        # skip if already enriched
        if c.get("boundary_lock") is True and "post_cut_pause_ms" in c:
            continue
        c["boundary_lock"] = True
        c["boundary_lock_reason"] = (
            "v20 enrich: entire-word ASR bounds per v19 boundary_strategy"
        )
        default_ms = 40 if kind == "filler_hesitation" else 60
        t_center = float(c.get("start_seconds") or 0)
        source_track = c.get("source_track_id") or c.get("source_track") or "track_01"
        sep = _has_separator_nearby(source_track, t_center)
        if sep:
            c["post_cut_pause_ms"] = 350
            c["post_cut_pause_reason"] = f"neighboring segment_separator={sep!r}"
        else:
            c["post_cut_pause_ms"] = default_ms



def stage_reuse_from_run(source_run: Path, target_run: Path) -> None:
    """Copy candidates/calibration/policy artifacts from an existing run
    into the target run directory so gate can operate on them."""
    target_run.mkdir(parents=True, exist_ok=True)
    copy_names = [
        "all_candidates.json",
        "calibration_source.json",
        "policy_application.json",
        "input_manifest.json",
        "run_identity.json",
        "processing_manifest.json",
        "analysis_manifest.json",
        "analysis_reuse_manifest.json",
    ]
    for name in copy_names:
        src = source_run / name
        if src.is_file():
            shutil.copy2(src, target_run / name)
    print(f"[stage] reused {len(list(target_run.glob('*.json')))} JSON files from {source_run.name}")


def stage_orchestrator_start(
    input_dir: Path, episode_id: str, reuse_analysis_run: Path | None = None,
    review_budget: int | None = None,
) -> Path:
    """Run orchestrator `start` to produce candidates in a fresh run.
    Returns the newly created run directory."""
    orch = PROJECT_ROOT / "main/orchestrator/delivery_orchestrator.py"
    cmd = [
        sys.executable, str(orch), "start",
        "--input-dir", str(input_dir),
        "--episode-id", episode_id,
    ]
    if reuse_analysis_run:
        cmd += ["--reuse-analysis-run", str(reuse_analysis_run)]
    if review_budget is not None:
        cmd += ["--review-budget", str(review_budget)]
    _run(cmd)
    # Orchestrator creates main/runs/<episode>/<run_id>/; caller should
    # discover which one it made. This is a placeholder — orchestrator prints
    # the run_id on stdout; user should capture it. For now, glob the newest.
    runs_dir = PROJECT_ROOT / "main/runs" / episode_id
    if not runs_dir.is_dir():
        raise SystemExit(f"expected orchestrator to create {runs_dir}")
    newest = max(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    return newest


def stage_wordlevel_selfcorrection(
    transcript_dir: Path, target_run: Path
) -> Path | None:
    """Run detect_self_correction_wordlevel across transcript files.
    Returns path to the produced candidates JSON, or None if transcripts missing."""
    transcripts = sorted(transcript_dir.glob("track_*.transcript.json"))
    if not transcripts:
        print(f"[stage] no track_*.transcript.json in {transcript_dir}; skipping wordlevel self_correction")
        return None
    tool = _script_for("detect_self_correction_wordlevel")
    rules = PROJECT_ROOT / "稳定生产/challengers/self-correction-v1/rules/self-correction-wordlevel.v1.json"
    out = target_run / "self_correction_wordlevel.json"
    cmd = [sys.executable, str(tool)]
    for t in transcripts:
        label = t.stem.replace(".transcript", "")
        cmd += ["--transcript", f"{label}={t}"]
    cmd += ["--rules", str(rules), "--out", str(out)]
    _run(cmd)
    return out


def stage_autocut_gate(
    target_run: Path,
    labels_lake: Path,
    policy_v2: Path,
    episode_duration_s: float,
    wordlevel_json: Path | None = None,
    case_embeddings_json: Path | None = None,
) -> Path:
    """Run apply_autocut_gate on the run's candidates."""
    tool = _script_for("apply_autocut_gate")
    out_dir = target_run / "autocut_gate"
    cmd = [
        sys.executable, str(tool),
        "--candidates", str(target_run / "all_candidates.json"),
        "--policy", str(policy_v2),
        "--policy-application", str(target_run / "policy_application.json"),
        "--calibration-source", str(target_run / "calibration_source.json"),
        "--labels-lake", str(labels_lake),
        "--episode-duration-seconds", str(episode_duration_s),
        "--out", str(out_dir),
    ]
    if wordlevel_json and wordlevel_json.is_file():
        cmd += ["--extra-candidates", str(wordlevel_json)]
    # G8 case_embedding gate · 若 Stage 3.9 已跑且写出 case_embedding_retrieval.json
    # 则 pass 进 gate; 否则 gate 内 G8 silent skip (fallback).
    if case_embeddings_json and case_embeddings_json.is_file():
        cmd += ["--case-embeddings-json", str(case_embeddings_json)]
    _run(cmd)
    return out_dir


def stage_edl_from_gate(
    target_run: Path,
    gate_out_dir: Path,
    calibration_source: Path,
    sample_rate_hz: int = 48000,
) -> Path:
    """Build a machine_assisted_draft.edl.json from the gate's auto_cut set.
    Uses candidate boundaries from calibration_source (which has snapped
    start_sample/end_sample). Adds rendering_gate crossfade per rules."""
    auto_cut = _load_json(gate_out_dir / "auto_cut.json").get("candidates", [])
    cal = _load_json(calibration_source)
    cal_by_id = {str(c.get("candidate_id")): c for c in cal.get("candidates", [])}

    # 用户 2026-08-19 · Stage 3.7 落地后 · LLM 唯一候选决定者
    # 若 target/llm_verdicts.json 存在 · 只用 verdict == "KEEP_CUT" 的 candidate_id
    # 未在 verdicts 里的候选 (含 REJECT_KEEP / NEEDS_REVIEW / 上游 pre-excluded) 一律不进 EDL
    # 若 verdicts 文件缺失 · fallback 到原 auto_cut · 保持向后兼容
    llm_verdicts_path = target_run / "llm_verdicts.json"
    llm_keep_cut_ids: set[str] | None = None
    if llm_verdicts_path.is_file():
        try:
            vd = _load_json(llm_verdicts_path)
            llm_keep_cut_ids = {
                str(v.get("candidate_id"))
                for v in vd.get("verdicts", [])
                if v.get("verdict") == "KEEP_CUT"
            }
            print(f"[stage 5 · EDL] LLM 决定 · 只用 {len(llm_keep_cut_ids)} KEEP_CUT 候选 (auto_cut 原 {len(auto_cut)})")
        except Exception as _exc:
            print(f"[stage 5 · EDL] 读 llm_verdicts.json 失败: {_exc}; fallback 到原 auto_cut · {len(auto_cut)} 全候选进 EDL")
            llm_keep_cut_ids = None
    else:
        print(f"[stage 5 · EDL] llm_verdicts.json 不存在 · fallback 到原 auto_cut · {len(auto_cut)} 全候选进 EDL (向后兼容)")

    # BREAK-03 (2026-08-19): 若 run 目录里有 refinement_trace.json (Stage 6.7
    # Optuna 迭代结果)，读取每个 candidate 的 final_params (crossfade_ms /
    # post_cut_pause_ms / asymmetric_head_pad_ms / boundary_offset_ms /
    # room_tone_pad_ms) 并注入到 src 上，用于生成 render_sync_cuts。
    # 修复：之前 crossfade 硬编码 4800 samples，导致 Optuna 调出的参数无法
    # 落到最终 EDL / 渲染。
    refinement_trace_path = target_run / "refinement_trace.json"
    tuned_by_id: dict[str, dict] = {}
    if refinement_trace_path.is_file():
        try:
            trace = _load_json(refinement_trace_path)
            for tc in trace.get("candidates", []) or []:
                fp = tc.get("final_params") or {}
                if fp:
                    tuned_by_id[str(tc.get("candidate_id"))] = fp
        except Exception:
            tuned_by_id = {}

    identity = _load_json(target_run / "run_identity.json")
    frame_count = int(identity.get("frame_count") or 0)

    actions: list[dict] = []
    render_sync_cuts: list[dict] = []
    for c in auto_cut:
        cid = str(c.get("candidate_id"))
        # 用户 2026-08-19 · LLM 唯一候选决定者 · REJECT_KEEP / 未判定的候选不进 EDL
        if llm_keep_cut_ids is not None and cid not in llm_keep_cut_ids:
            continue
        src = cal_by_id.get(cid, c)
        fp = tuned_by_id.get(cid, {})  # Optuna final_params (may be empty)
        start = int(src.get("start_sample") or 0)
        end = int(src.get("end_sample") or 0)
        # v20.1 feedback: self_correction 的 cut_scope=both_spans → 剪掉 pre+retry
        # 双段 (用户: "要么都留要么都剪"). retry_span.end_seconds 覆盖 end.
        cut_scope = c.get("cut_scope") or src.get("cut_scope") or "pre_only"
        if cut_scope == "both_spans":
            retry = c.get("retry_span") or src.get("retry_span") or {}
            retry_end_s = retry.get("end_seconds")
            if retry_end_s is not None:
                end = int(float(retry_end_s) * sample_rate_hz)
        if end <= start:
            continue
        # ---- crossfade 从 candidate/final_params 读，不再硬编码 4800 ----
        cf_ms = (
            fp.get("crossfade_ms")
            or src.get("cut_verify_crossfade_ms")
            or c.get("cut_verify_crossfade_ms")
            or src.get("crossfade_ms")
            or c.get("crossfade_ms")
            or 100.0
        )
        try:
            cf_ms = float(cf_ms)
        except Exception:
            cf_ms = 100.0
        crossfade_samples = int(cf_ms * sample_rate_hz / 1000)
        # ---- post_cut_pause_ms (含 cut_verify_pause_ms 别名) ----
        # v20 feedback (2026-08-17): 剪切后按 candidate 的 post_cut_pause_ms 建议
        # 插入 micro-pause (natural rhythm)，尤其在 segment_separator 附近保留更长
        # 停顿；SC005/C023 case 修复
        pause_ms = (
            fp.get("post_cut_pause_ms")
            or src.get("cut_verify_pause_ms")
            or c.get("cut_verify_pause_ms")
            or src.get("post_cut_pause_ms")
            or c.get("post_cut_pause_ms")
            or 0
        )
        try:
            pause_ms = float(pause_ms)
        except Exception:
            pause_ms = 0.0
        pause_samples = int(pause_ms * sample_rate_hz / 1000)
        # ---- boundary_offset_ms (Optuna 微移剪口) ----
        bo_ms = (
            fp.get("boundary_offset_ms")
            or src.get("boundary_offset_ms")
            or c.get("boundary_offset_ms")
            or 0
        )
        try:
            bo_ms = float(bo_ms)
        except Exception:
            bo_ms = 0.0
        boundary_offset_samples = int(bo_ms * sample_rate_hz / 1000)
        # ---- head_pad (asymmetric_head_pad_ms) / room_tone_pad_ms ----
        head_ms = (
            fp.get("asymmetric_head_pad_ms")
            or src.get("asymmetric_head_pad_ms")
            or c.get("asymmetric_head_pad_ms")
            or fp.get("head_pad_ms")
            or src.get("head_pad_ms")
            or 0
        )
        try:
            head_ms = float(head_ms)
        except Exception:
            head_ms = 0.0
        head_pad_samples = int(head_ms * sample_rate_hz / 1000)
        room_ms = (
            fp.get("room_tone_pad_ms")
            or src.get("room_tone_pad_ms")
            or c.get("room_tone_pad_ms")
            or 0
        )
        try:
            room_ms = float(room_ms)
        except Exception:
            room_ms = 0.0
        room_tone_pad_samples = int(room_ms * sample_rate_hz / 1000)
        # ---- 施加 boundary_offset 到 start/end (对称) ----
        if boundary_offset_samples:
            start = max(0, start + boundary_offset_samples)
            end = max(start + 1, end + boundary_offset_samples)
        actions.append({
            "action_id": f"autocut-{cid}",
            "action_type": "global_sync_cut",
            "candidate_id": cid,
            "start_sample": start,
            "end_sample": end,
            "applies_to_all_tracks": True,
            "decision": "machine_proposed_accept",
            "decision_provenance": "autocut_gate_v1",
            "risk_level": src.get("risk_level", "low"),
            "post_cut_pause_ms": pause_ms,
        })
        render_sync_cuts.append({
            "start_sample": start,
            "end_sample": end,
            "source_action_ids": [f"autocut-{cid}"],
            "crossfade_samples": crossfade_samples,
            "crossfade_ms": cf_ms,
            "insert_silence_samples": pause_samples,
            "post_cut_pause_ms": pause_ms,
            "boundary_offset_ms": bo_ms,
            "boundary_offset_samples": boundary_offset_samples,
            "head_pad_ms": head_ms,
            "head_pad_samples": head_pad_samples,
            "room_tone_pad_ms": room_ms,
            "room_tone_pad_samples": room_tone_pad_samples,
            "params_source": "optuna_final_params" if fp else "candidate_defaults",
        })

    # 用户 2026-08-19 · Stage 3.5.5 LLM 完全主导 · LLM 可能发现 rules auto_cut 里没有的候选
    # · 若 llm_verdicts 里的 KEEP_CUT 候选不在 auto_cut · 直接从 verdict 构 render_sync_cut
    # · params (crossfade / pause) 走默认 100 ms / 0 · Stage 6.7 Optuna 后续会补细
    # · applies_to_tracks 从 verdict 的 source_track_id 取 · fallback all_tracks
    if llm_keep_cut_ids:
        existing_cut_ids = {a.get("candidate_id") for a in actions}
        try:
            _vd_edl = _load_json(llm_verdicts_path)
        except Exception:
            _vd_edl = {}
        _llm_extra = 0
        for v in _vd_edl.get("verdicts", []) or []:
            if v.get("verdict") != "KEEP_CUT":
                continue
            cid = str(v.get("candidate_id"))
            if cid in existing_cut_ids:
                continue
            try:
                start_s = float(v.get("start_seconds") or 0)
                end_s = float(v.get("end_seconds") or 0)
            except Exception:
                continue
            start_smp = int(start_s * sample_rate_hz)
            end_smp = int(end_s * sample_rate_hz)
            if end_smp <= start_smp:
                continue
            cf_ms_llm = 100.0
            crossfade_samples_llm = int(cf_ms_llm * sample_rate_hz / 1000)
            pause_ms_llm = 0.0
            pause_samples_llm = 0
            src_track_id = v.get("source_track_id") or v.get("track_id")
            actions.append({
                "action_id": f"llm-{cid}",
                "action_type": "global_sync_cut",
                "candidate_id": cid,
                "start_sample": start_smp,
                "end_sample": end_smp,
                "applies_to_tracks": [src_track_id] if src_track_id else None,
                "applies_to_all_tracks": True,
                "decision": "machine_proposed_accept",
                "decision_provenance": "llm_full_pipeline_v1",
                "risk_level": "low",
                "post_cut_pause_ms": pause_ms_llm,
                "params_source": "llm_full_pipeline",
            })
            render_sync_cuts.append({
                "start_sample": start_smp,
                "end_sample": end_smp,
                "source_action_ids": [f"llm-{cid}"],
                "crossfade_samples": crossfade_samples_llm,
                "crossfade_ms": cf_ms_llm,
                "insert_silence_samples": pause_samples_llm,
                "post_cut_pause_ms": pause_ms_llm,
                "boundary_offset_ms": 0.0,
                "boundary_offset_samples": 0,
                "head_pad_ms": 0.0,
                "head_pad_samples": 0,
                "room_tone_pad_ms": 0.0,
                "room_tone_pad_samples": 0,
                "params_source": "llm_full_pipeline",
            })
            _llm_extra += 1
        if _llm_extra:
            print(f"[stage 5 · EDL] LLM discover · {_llm_extra} candidates 不在 auto_cut · 直接从 llm_verdicts 构 cut (bypass rules)")

    edl = {
        "schema_version": "delivery-edl-v1",
        "episode_id": identity.get("episode_id"),
        "run_id": identity.get("run_id"),
        "variant": "machine_assisted_draft",
        "sample_rate_hz": sample_rate_hz,
        "frame_count": frame_count,
        "provenance": "autocut_gate_v3 zero-touch",
        "actions": actions,
        "render_sync_cuts": sorted(render_sync_cuts, key=lambda x: x["start_sample"]),
        "source_track_gates": [],
        "decision_summary": {"machine_proposed_accept": len(actions)},
        "autocut_policy": {
            "id": "APPROVED_FOR_WHITELIST_KINDS_ONLY",
            "status": "APPROVED_FOR_WHITELIST_KINDS_ONLY",
        },
    }
    edl_path = target_run / "machine_assisted_draft.edl.json"
    _write_json(edl_path, edl)
    print(f"[stage] EDL: {len(render_sync_cuts)} cuts -> {edl_path.name}")
    return edl_path


def stage_automix(
    tracks: list[Path],
    music: Path,
    edl: Path | None,
    release_spec: Path,
    music_template_file: Path,
    template_id: str,
    output_mp3: Path,
    tmp_dir: Path,
) -> None:
    """Invoke automix_v1 with double-pass loudnorm and (optional) EDL cuts."""
    tool = PROJECT_ROOT / "稳定生产/challengers/automix-v1/scripts/automix_v1.py"
    cmd = [
        sys.executable, str(tool),
        "--tracks", *[str(t) for t in tracks],
        "--music", str(music),
        "--release-spec", str(release_spec),
        "--music-template", str(music_template_file),
        "--template-id", template_id,
        "--output", str(output_mp3),
        "--tmp-dir", str(tmp_dir),
        "--loudnorm-passes", "2",
    ]
    if edl and edl.is_file():
        cmd += ["--edl", str(edl)]
    _run(cmd)


def stage_audit_report(target_run: Path, gate_out_dir: Path) -> Path:
    """Generate a human-readable audit_report.md summarizing what was
    auto-cut and what was left for human review."""
    ac = _load_json(gate_out_dir / "auto_cut.json").get("candidates", [])
    rev = _load_json(gate_out_dir / "review_required.json").get("candidates", [])
    summary = _load_json(gate_out_dir / "summary.json")

    lines = [
        f"# 自动剪辑审计报告 · {target_run.name}",
        "",
        f"**输出**: `render/*.mp3`（machine_assisted_draft 状态）",
        f"**总候选**: {summary['summary']['total_candidates']}",
        f"**Auto-cut**: **{summary['summary']['auto_cut_eligible_count']}**",
        f"**留人审**: {summary['summary']['human_review_required_count']}",
        "",
        "## 自动剪辑的片段（machine_assisted_draft）",
        "",
        "| # | Candidate | 类型 | 内容 | 时间 |",
        "|---|---|---|---|---|",
    ]
    for i, c in enumerate(ac, 1):
        kind = c.get("candidate_kind", "?")
        if kind == "self_correction":
            content = f"{c.get('abandoned_span', {}).get('text','?')}→{c.get('retry_span', {}).get('text','?')}"
        else:
            content = str(c.get("filler_token") or c.get("proposed_delete_text") or "?")
        t = float(c.get("start_seconds", 0))
        lines.append(
            f"| {i} | {c.get('candidate_id')} | {kind} | {content} | {t:.1f}s |"
        )

    lines += [
        "",
        f"## 留人审 · {len(rev)} 条",
        "",
        "见 `autocut_gate/review_required.json`。每条 gate 失败原因见 `autocut_gate/gate_report.json`。",
        "",
    ]

    # Stage 6.7 · iterative refinement (Optuna TPE) 侧车引用 (2026-08-19)
    # · orphan 修补: audit_report 原不提 refinement_trace.json · 人审看不到 Optuna 收敛证据
    refine_trace = target_run / "refinement_trace.json"
    if refine_trace.is_file():
        try:
            _rt = _load_json(refine_trace)
            _summ = _rt.get("summary", {}) if isinstance(_rt, dict) else {}
            _cands = _rt.get("candidates", []) if isinstance(_rt, dict) else []
            _conv = sum(1 for c in _cands if (c.get("verdict") or "").upper() == "CONVERGED")
            _esc = sum(1 for c in _cands if (c.get("verdict") or "").upper() == "ESCAPED")
            lines += [
                "## Stage 6.7 · Optuna 迭代 refinement",
                "",
                f"- 候选参与迭代: {len(_cands)}",
                f"- CONVERGED: {_conv} · ESCAPED: {_esc}",
                f"- 侧车: `refinement_trace.json`" + (f" · summary: `{_summ}`" if _summ else ""),
                "- 如需第二轮 · 填 `audit_verdicts.json` 后 rerun · 触发 Stage 6.9 re_iterate_from_audit",
                "",
            ]
        except Exception as _exc:
            lines += [f"## Stage 6.7 · refinement_trace 读失败: {_exc}", ""]

    # Stage 6.8 · case embedding retrieval 侧车引用 (2026-08-19)
    # · orphan 修补: audit_report 原不提 case_embedding_retrieval.json · 人审看不到 mentor 相似 case
    ce_out = target_run / "case_embedding_retrieval.json"
    if ce_out.is_file():
        try:
            _ce = _load_json(ce_out)
            _n = _ce.get("count", 0) if isinstance(_ce, dict) else 0
            _idx = _ce.get("index_source", "?") if isinstance(_ce, dict) else "?"
            lines += [
                "## Stage 6.8 · mentor gold 相似 case (Whisper + FAISS)",
                "",
                f"- 命中候选: {_n}",
                f"- 索引源: `{_idx}`",
                "- 侧车: `case_embedding_retrieval.json` · 每候选 top-3 similar cases · 供 mentor 参考",
                "",
            ]
        except Exception as _exc:
            lines += [f"## Stage 6.8 · case_embedding_retrieval 读失败: {_exc}", ""]

    # audit_verdicts template 引用 (Stage 7 收尾)
    av_tmpl = target_run / "audit_verdicts.json.template"
    av_actual = target_run / "audit_verdicts.json"
    if av_actual.is_file() or av_tmpl.is_file():
        lines += [
            "## 人审 · audit_verdicts 填写入口",
            "",
            "- 模板: `audit_verdicts.json.template` · 人审填 `verdict=APPROVED|REJECTED` 保存为 `audit_verdicts.json`",
            "- REJECTED 触发 Stage 6.9 二轮 Optuna (`re_iterate_from_audit.py` · skip_warm_start + seed=43)",
            "- 二轮仍 escape → verdict=SECOND_ROUND_ESCAPED · M3 兜底交人审",
            "",
        ]

    lines += [
        "## 边界与警示",
        "",
        "- 本 run 处于 `MACHINE_ASSISTED_DRAFT_RENDERED` 状态；**不是 human_approved**。",
        "- 发布前必须经过项目负责人整片试听。",
        "- 若发现某条 auto-cut 误判 → 手工 reject 后 rebuild labels_lake → 下次 gate 自动收严。",
    ]
    out = target_run / "audit_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# --------- main -----------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-id", required=True)
    ap.add_argument("--out-dir", type=Path, required=True,
                    help="Target run directory (will be created)")
    ap.add_argument("--music", type=Path,
                    default=PROJECT_ROOT / "音频参考库/raw material/第三集/片头片尾music.mp3")
    ap.add_argument("--release-spec", type=Path,
                    default=PROJECT_ROOT / "稳定生产/challengers/release-policy-v2/timing/release_specs.v2.json")
    ap.add_argument("--music-template-file", type=Path,
                    default=PROJECT_ROOT / "稳定生产/challengers/release-policy-v2/timing/music_templates.v2.json")
    # 注意：JSON key 是 "reference-linear-v2"；
    # "reference-linear-v2-guest-cued-outro" 是 legacy_alias，不是 dict key。
    # 见 稳定生产/challengers/release-policy-v2/timing/{release_specs.v2,music_templates.v2}.json
    ap.add_argument("--template-id", default="reference-linear-v2")
    ap.add_argument("--policy-v2", type=Path,
                    default=PROJECT_ROOT / "稳定生产/challengers/release-policy-v2/rules/editing_policy.guards-v2.json")
    ap.add_argument("--labels-lake", type=Path,
                    default=PROJECT_ROOT / "main/knowledge/labels_lake.json")

    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-existing-run", type=Path,
                      help="Reuse candidates from an existing run directory (Mode 1)")
    mode.add_argument("--from-raw-wav", type=Path, nargs="+",
                      help="Fresh run from raw WAV tracks (Mode 2)")

    ap.add_argument("--tracks-for-automix", type=Path, nargs="+",
                    help="Denoised or raw WAV tracks for automix step. "
                         "If --from-raw-wav given, defaults to those. "
                         "If --from-existing-run, must be specified.")
    ap.add_argument("--reuse-analysis-run", type=Path,
                    help="Mode 2 only: reuse denoise+ASR from a prior run")
    ap.add_argument("--episode-duration-seconds", type=float,
                    help="Override episode duration (auto-detected if omitted)")
    ap.add_argument("--allow-missing-speaker-map", action="store_true",
                    help="(CLAUDE.md §12 fail-closed 豁免) 允许 speaker_map 缺失时降级为 warn+skip；"
                         "仅供 fixture / 遗留 run；生产 EP0X 请**先建 map** 再跑。")
    ap.add_argument("--pyannote-enabled", action="store_true", default=True,
                    help="pyannote-audio 4.0.7 RTTM 替代能量启发式做 host backchannel guard。"
                         "**默认开** (用户 2026-08-19 明确要求全接入 4 个 Challenger)。"
                         "会先跑 Challenger C run_diarization.py 生成 <run>/diarization/<track_id>.rttm "
                         "（首跑联网拉权重），再让 speaker_role_filter 从 RTTM 查区间。用 --no-pyannote-enabled 关。")
    ap.add_argument("--no-pyannote-enabled", dest="pyannote_enabled", action="store_false")
    ap.add_argument("--pyannote-model-tag", default=None, type=str,
                    help="可选 · 覆盖 run_diarization.py 默认 model_tag "
                         "(pyannote/speaker-diarization-community-1)。仅在 --pyannote-enabled 时生效。")
    ap.add_argument("--nisqa-python", type=Path, default=None,
                    help="opt-in · NISQA venv/system Python 路径 · 启用 cut-verify Check 5 (无参考 MOS 打分) + Stage 6.5 benchmark 报告")
    ap.add_argument("--auto-iterate-refine", action="store_true", default=True,
                    help="Stage 6.7 · 自动跑 iterative refinement + Optuna TPE · 对 NEEDS_HUMAN_REVIEW 候选做 5 次上限迭代 · 默认开 · 用 --no-auto-iterate-refine 关")
    ap.add_argument("--no-auto-iterate-refine", dest="auto_iterate_refine", action="store_false")
    ap.add_argument("--auto-case-embedding", action="store_true", default=True,
                    help="Stage 6.8 · 自动跑 audio embedding case retrieval · 从 mentor gold case_memory 检索最相似历史 case · 默认开 · 用 --no-auto-case-embedding 关 · index 不存在时静默 skip")
    ap.add_argument("--no-auto-case-embedding", dest="auto_case_embedding", action="store_false")
    ap.add_argument("--stage45-check5", action="store_true", default=False,
                    help="Stage 4.5 Check 5 (NISQA gate) · **默认关** · 用户 2026-08-19 明确全走 Optuna. 打开会让 Check 5 判定 NEEDS_HUMAN_REVIEW · 但当前流程更倾向让 Stage 6.7 Optuna 兜底.")
    ap.add_argument("--auto-second-round-optuna", action="store_true", default=True,
                    help="Stage 6.9 · 检测 audit_verdicts.json 里 REJECTED · 触发第二轮 Optuna (skip_warm_start + seed=43 + max_iter=10). 默认开.")
    ap.add_argument("--no-auto-second-round-optuna", dest="auto_second_round_optuna", action="store_false")
    ap.add_argument("--auto-optuna-rerender", action="store_true", default=True,
                    help="Stage 6.10 · Optuna converged 候选参数回写 + re-render 覆盖成品 · 默认开 · 用户 2026-08-19 明确要求")
    ap.add_argument("--no-auto-optuna-rerender", dest="auto_optuna_rerender", action="store_false")
    ap.add_argument("--auto-llm-semantic-filter", action="store_true", default=True,
                    help="Stage 3.7 · LLM 唯一候选决定者 · 用户 2026-08-19 明确 · 默认开")
    ap.add_argument("--no-auto-llm-semantic-filter", dest="auto_llm_semantic_filter", action="store_false")
    ap.add_argument("--auto-llm-full-pipeline", action="store_true", default=True,
                    help="Stage 3.5.5 · LLM 完全主导 · 从 transcript 一步扫出候选 · 默认开 (回忆 wpl29rgl6 成功版 · 绕过 rules candidate)")
    ap.add_argument("--no-auto-llm-full-pipeline", dest="auto_llm_full_pipeline", action="store_false")
    ap.add_argument("--auto-asr-prob-gate", action="store_true", default=True,
                    help="Stage 3.5.6 · ASR probability 硬约束 · <0.60 for filler/rep · 默认开")
    ap.add_argument("--no-auto-asr-prob-gate", dest="auto_asr_prob_gate", action="store_false")
    ap.add_argument("--review-budget", type=int, default=None,
                    help="传给 delivery_orchestrator.py 的 --review-budget · 默认由 orchestrator 用 20. 用户 2026-08-19 EP05 5min 候选多 · 需要放宽.")
    args = ap.parse_args(argv)

    target = args.out_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    print(f"=== run_end_to_end target run: {target}")

    # Stage 1: Populate candidates in target run
    transcript_dir: Path | None = None
    if args.from_existing_run:
        stage_reuse_from_run(args.from_existing_run.resolve(), target)
        # Try to find transcript dir for wordlevel self_correction
        # Look in reuse manifest
        reuse_manifest = target / "analysis_reuse_manifest.json"
        if reuse_manifest.is_file():
            rm = _load_json(reuse_manifest)
            src_run_dir = rm.get("source_run_dir")
            if src_run_dir:
                candidate_ana = Path(src_run_dir) / "analysis"
                if candidate_ana.is_dir():
                    transcript_dir = candidate_ana
        # Also try the source run itself
        if not transcript_dir:
            ana = args.from_existing_run.resolve() / "analysis"
            if ana.is_dir():
                transcript_dir = ana
    else:
        # Mode 2: run orchestrator start
        input_dir = args.from_raw_wav[0].parent  # orchestrator wants a dir
        new_run = stage_orchestrator_start(
            input_dir, args.episode_id, args.reuse_analysis_run,
            review_budget=getattr(args, "review_budget", None),
        )
        stage_reuse_from_run(new_run, target)
        transcript_dir = new_run / "analysis"

    # Stage 2: wordlevel self_correction (best-effort)
    wordlevel_json = None
    if transcript_dir:
        try:
            wordlevel_json = stage_wordlevel_selfcorrection(transcript_dir, target)
        except subprocess.CalledProcessError as exc:
            print(f"[warn] wordlevel self_correction failed: {exc}; continuing without")
            wordlevel_json = None

    # Stage 3: Determine episode duration
    ep_dur = args.episode_duration_seconds
    if ep_dur is None:
        # Try run_identity, then input_manifest
        for fname in ("run_identity.json", "input_manifest.json"):
            p = target / fname
            if not p.is_file():
                continue
            doc = _load_json(p)
            fc = int(doc.get("frame_count") or 0)
            sr = int(doc.get("sample_rate_hz") or 48000)
            if fc and sr:
                ep_dur = fc / sr
                break
        if ep_dur is None:
            raise SystemExit("cannot determine episode duration; pass --episode-duration-seconds")
    print(f"[stage] episode duration: {ep_dur:.1f}s")
    return _run_downstream_stages(args, target, ep_dur, transcript_dir, wordlevel_json)


# ---------- module-level helpers ----------

# ---------- pyannote RTTM 消费路径 (opt-in) ----------------------------------
# 融入 Challenger C · speaker-diarization-v1 · 2026-08-19
#
# 目标: 用 pyannote-audio 4.0.7 RTTM 替代原能量启发式的"±3s 其他轨有 ASR words"
# 判定, 更稳地识别"嘉宾正在讲话". 走 opt-in flag `--pyannote-enabled` · 默认关.
#
# 契约:
#   - flag OFF (默认): stage_speaker_role_filter 行为与之前 100% 一致
#   - flag ON:
#       stage_pyannote_diarize 对每条 track wav 调 run_diarization.py
#         → <run>/diarization/<track_id>.rttm + manifest.json (SHA 记录)
#       stage_speaker_role_filter 收到 rttm_dir 参数
#         → "其他轨活动" 用 RTTM turns 而不是 ASR words
#
# 不下载额外权重 · run_diarization.py 首跑走 HF login 缓存拉 community-1

def _load_rttm_intervals(rttm_path: Path) -> list[tuple[float, float, str]]:
    """解析 pyannote RTTM → [(start_s, end_s, speaker_label), ...] · 按 start 排序.

    RTTM 每行:
      SPEAKER <file_id> <chan> <start_s> <dur_s> <NA> <NA> <spk_label> <NA> <NA>
    """
    out: list[tuple[float, float, str]] = []
    if not rttm_path.is_file():
        return out
    for line in rttm_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 8 or parts[0] != "SPEAKER":
            continue
        try:
            start = float(parts[3])
            dur = float(parts[4])
        except ValueError:
            continue
        spk = parts[7]
        out.append((start, start + dur, spk))
    out.sort(key=lambda x: x[0])
    return out


def _rttm_intervals_as_words(intervals: list[tuple[float, float, str]]) -> list[dict]:
    """把 RTTM 区间伪装成 ASR word list · 让 _is_host_backchannel 无缝消费 (它只用
    start_seconds/end_seconds 判断时间重叠, 不看 token 文本)."""
    return [
        {"start_seconds": s, "end_seconds": e, "text": spk, "_rttm": True}
        for (s, e, spk) in intervals
    ]


def stage_pyannote_diarize(
    target_run: Path,
    denoised_tracks: list[Path],
    *,
    model_tag: str | None = None,
    auth_token: str | None = None,
    force: bool = False,
) -> Path | None:
    """opt-in helper: 对每条 track WAV 调 Challenger C 的 run_diarization.py
    产出 RTTM · 缓存到 <run>/diarization/<track_id>.rttm · 写 manifest.json (含 SHA).

    Args:
        target_run: run 目录 (RTTM 写这里)
        denoised_tracks: 每轨 denoised 16k mono WAV 绝对路径
        model_tag: 可选 · 传给 run_diarization.py --model-tag
        auth_token: 可选 · 传给 run_diarization.py --auth-token
        force: True → 无视缓存重跑

    Returns:
        <target_run>/diarization/ 目录 (若至少 1 条 RTTM 就绪); 全失败 → None.
    """
    if not denoised_tracks:
        print("[pyannote] no input tracks; skip diarization")
        return None
    out_dir = target_run / "diarization"
    out_dir.mkdir(parents=True, exist_ok=True)
    diar_script = (
        PROJECT_ROOT
        / "稳定生产/challengers/speaker-diarization-v1/scripts/run_diarization.py"
    )
    if not diar_script.is_file():
        print(f"[pyannote] run_diarization.py not found at {diar_script}; skip")
        return None

    manifest: dict[str, Any] = {
        "schema_version": "diarization-manifest-v1",
        "model_tag": model_tag or "pyannote/speaker-diarization-community-1",
        "tracks": {},
    }
    ok = 0
    for i, wav in enumerate(denoised_tracks, start=1):
        wav = Path(wav)
        tid = wav.stem  # e.g. track_01, track_01.denoised, EP04-track_01.denoised, ZOOM0012_Tr2_first5min
        # Normalize track_id · BREAK-04 fix (2026-08-19 revised):
        #   下游 candidates 里 track_id 恒为 "track_XX" (source_track / track_id 字段)
        #   speaker_role_filter glob 也是 track_*.rttm · 消费端契约固定
        #   producer 必须写成 track_XX.rttm 才能被消费到
        #   历史方案 (regex 抽 track_\d+ + split fallback) 对
        #   "ZOOM0012_Tr2_first5min.wav" 这类外部命名 fail → 落成
        #   ZOOM0012_Tr2_first5min.rttm · consumer glob miss · fallback 到
        #   ASR heuristic · pyannote 白跑
        # 现改: 以 enumerate 序号 1-indexed 生成 track_{i:02d} · 与
        # tracks_for_automix / from_raw_wav 的传入顺序契约一致
        # (与 candidates.source_track 依同一顺序标号 track_01/02/...)
        norm_tid = f"track_{i:02d}"
        rttm_out = out_dir / f"{norm_tid}.rttm"
        if rttm_out.is_file() and rttm_out.stat().st_size > 0 and not force:
            print(f"[pyannote] reuse cached RTTM: {rttm_out.relative_to(target_run)}")
        else:
            cmd = [
                sys.executable, str(diar_script),
                "--input-wav", str(wav),
                "--output-rttm", str(rttm_out),
            ]
            if model_tag:
                cmd += ["--model-tag", model_tag]
            if auth_token:
                cmd += ["--auth-token", auth_token]
            try:
                _run(cmd, check=True)
            except subprocess.CalledProcessError as exc:
                print(f"[pyannote] {norm_tid} diarization failed: {exc}; continuing")
                continue
        if not rttm_out.is_file():
            print(f"[pyannote] {norm_tid} produced no RTTM; skipping")
            continue
        sha = hashlib.sha256(rttm_out.read_bytes()).hexdigest()
        manifest["tracks"][norm_tid] = {
            "rttm": rttm_out.name,
            "sha256": sha,
            "source_wav": str(wav),
        }
        ok += 1
    _write_json(out_dir / "manifest.json", manifest)
    if ok == 0:
        print("[pyannote] no RTTM produced; falling back to energy heuristic")
        return None
    print(f"[pyannote] {ok}/{len(denoised_tracks)} tracks diarized → {out_dir}")
    return out_dir


def stage_speaker_role_filter(
    target_run: Path,
    speaker_map_path: Path,
    transcript_dir: Path,
    *,
    rttm_dir: Path | None = None,
) -> Path | None:
    """v20.5 (2026-08-18, S · C4 alternative to pyannote): 用手工声明的
    speaker_map (host/guest_A/guest_B) 从源头挡掉主持人 backchannel 候选.

    v20.7 (2026-08-19 · 融入 Challenger C · opt-in RTTM 路径):
      - rttm_dir=None (default): 走原能量启发式, 用 ASR words 判"嘉宾在讲话"
      - rttm_dir 有值且目录内有 track_*.rttm: 用 pyannote RTTM turns 判"嘉宾在讲话"
        (仍需 transcript_dir 提供当前 track ASR words 用于 center_idx 定位)

    数据源:
      main/knowledge/speaker_maps/{episode_id}.speaker_map.json (人工声明)
      main/runs/EP0X-.../analysis/track_XX.transcript.json (三轨 ASR 词)
      target_run/diarization/track_XX.rttm (opt-in · Challenger C 产出)

    做法:
      对 all_candidates.json 里每候选:
        - 若 track_id 在 speaker_map 里 role=host
        - 若 filler_token 在 HOST_BACKCHANNEL_TOKENS
        - 若 ±3s 其他轨有语音活动 (ASR words 或 RTTM turns) → 嘉宾在讲话
        → filter 掉候选 (记 filter_reason=host_backchannel_guard)

    输出:
      target_run/host_backchannel_filter.json (记录 filtered 候选)
      target_run/all_candidates.json (更新, filtered 候选加 filtered_reason 字段)
    """
    if not speaker_map_path.is_file():
        print(f"[speaker-role-filter] skip: no speaker_map at {speaker_map_path}")
        return None
    speaker_map = _load_json(speaker_map_path)
    if not transcript_dir.is_dir():
        print(f"[speaker-role-filter] skip: no transcript_dir at {transcript_dir}")
        return None

    # 加载三轨 ASR words 用于"嘉宾正在讲话"判定 + 当前 track 的 center_idx 定位
    all_track_words: dict[str, list[dict]] = {}
    for p in sorted(transcript_dir.glob("track_*.transcript.json")):
        tid = p.stem.replace(".transcript", "")
        words = _load_json(p).get("words", [])
        all_track_words[tid] = words

    # v20.7 · opt-in RTTM 路径: 若给了 rttm_dir 且目录里有 track_*.rttm,
    # 用 RTTM turns 覆盖"其他轨 words" (语音活动更稳)
    rttm_by_track: dict[str, list[dict]] | None = None
    filter_mode = "asr-words-heuristic"
    if rttm_dir is not None and rttm_dir.is_dir():
        collected: dict[str, list[dict]] = {}
        for rttm_p in sorted(rttm_dir.glob("track_*.rttm")):
            tid = rttm_p.stem
            intervals = _load_rttm_intervals(rttm_p)
            # 保留 empty list: RTTM 文件存在但 turn 为空 = "该轨无人讲话",
            # 这是**信号**, 不是"无数据". 让 _is_host_backchannel 看到
            # other_tracks_words={tid: []} (truthy dict) · 命中 guard 分支.
            collected[tid] = _rttm_intervals_as_words(intervals)
        if collected:
            rttm_by_track = collected
            filter_mode = "pyannote-rttm"
            print(
                f"[speaker-role-filter] pyannote RTTM mode · "
                f"{len(rttm_by_track)} tracks from {rttm_dir}"
            )
        else:
            print(
                f"[speaker-role-filter] rttm_dir={rttm_dir} 无 track_*.rttm · "
                f"fallback ASR-words heuristic"
            )

    # 导入 helper (延迟 import 避免循环依赖)
    import sys as _sys
    filler_scripts = PROJECT_ROOT / "稳定生产/challengers/filler-global-pause-v1/scripts"
    if str(filler_scripts) not in _sys.path:
        _sys.path.insert(0, str(filler_scripts))
    from build_filler_global_pause_review_source import (
        _is_host_backchannel,
        HOST_BACKCHANNEL_TOKENS,
        clean_token,
    )

    filtered_out: list[dict] = []
    kept = 0
    for fname in ("all_candidates.json", "calibration_source.json"):
        p = target_run / fname
        if not p.is_file():
            continue
        doc = _load_json(p)
        cands = doc.get("candidates") if isinstance(doc, dict) else doc
        new_cands = []
        for c in cands or []:
            track_id = str(c.get("source_track") or c.get("source_track_id") or "")
            filler = str(c.get("filler_token") or c.get("proposed_delete_text") or "").strip()
            filler = clean_token(filler)
            # 快速判定: 若不在 backchannel token 白名单 → 不过滤 (fast path)
            if filler not in HOST_BACKCHANNEL_TOKENS or not track_id:
                new_cands.append(c)
                kept += 1
                continue
            # 定位该候选在主轨 ASR 里的 word index
            track_words = all_track_words.get(track_id, [])
            center_time = (float(c.get("start_seconds") or 0) + float(c.get("end_seconds") or 0)) / 2
            center_idx = None
            for i, w in enumerate(track_words):
                ws = float(w.get("start_seconds") or 0)
                we = float(w.get("end_seconds") or ws)
                if ws <= center_time <= we + 0.05:
                    if clean_token(str(w.get("text", ""))) == filler:
                        center_idx = i
                        break
            if center_idx is None:
                new_cands.append(c)
                kept += 1
                continue
            # 其他轨 words dict · pyannote RTTM 优先, 否则退回 ASR words
            if rttm_by_track is not None:
                other_tracks = {
                    tid: ws for tid, ws in rttm_by_track.items() if tid != track_id
                }
            else:
                other_tracks = {
                    tid: ws for tid, ws in all_track_words.items() if tid != track_id
                }
            if _is_host_backchannel(track_words, center_idx, speaker_map, track_id, other_tracks):
                c["filtered_reason"] = "host_backchannel_guard"
                c["speaker_map_relpath"] = str(speaker_map_path.relative_to(PROJECT_ROOT))
                c["filter_mode"] = filter_mode
                filtered_out.append({
                    "candidate_id": c.get("candidate_id"),
                    "kind": c.get("kind") or c.get("candidate_kind"),
                    "filler_token": filler,
                    "track_id": track_id,
                    "start_seconds": c.get("start_seconds"),
                })
            else:
                new_cands.append(c)
                kept += 1
        if isinstance(doc, dict):
            doc["candidates"] = new_cands
            doc.setdefault("filter_history", []).append({
                "stage": "speaker_role_filter",
                "mode": filter_mode,
                "kept": len(new_cands),
                "filtered": len(cands or []) - len(new_cands),
                "speaker_map": str(speaker_map_path.relative_to(PROJECT_ROOT)),
                "rttm_dir": (
                    str(rttm_dir.relative_to(target_run))
                    if rttm_by_track is not None and rttm_dir is not None
                    else None
                ),
            })
            _write_json(p, doc)

    out = target_run / "host_backchannel_filter.json"
    _write_json(out, {
        "schema_version": "host-backchannel-filter-v1",
        "speaker_map_relpath": str(speaker_map_path.relative_to(PROJECT_ROOT)),
        "filter_mode": filter_mode,
        "kept_count": kept,
        "filtered_count": len(filtered_out),
        "filtered": filtered_out,
    })
    print(
        f"[speaker-role-filter] mode={filter_mode} · "
        f"filtered {len(filtered_out)} host backchannel · kept {kept}"
    )
    return out


def stage_experience_lookup(
    target_run: Path,
    case_store_aggregated: Path | None = None,
) -> Path | None:
    """v20.3 (2026-08-17 evolution path 2 接入): 对每候选查 case_store 里
    reason_key 匹配的历史真人决策 → 写回 candidate.experience_context 字段。
    autocut_gate G5 会消费该字段作 signal (similar_case_accept>=2 且
    reject==0 → 通行)。

    数据源: LABEL-LEARNING-v*/preference_snapshot/aggregated.json (65 records).
    """
    from collections import defaultdict
    if case_store_aggregated is None:
        candidates_snap = sorted(
            PROJECT_ROOT.glob("main/runs/LABEL-LEARNING-*/preference_snapshot/aggregated.json")
        )
        if not candidates_snap:
            print("[experience] no case_store found, skip")
            return None
        case_store_aggregated = candidates_snap[-1]
    print(f"[experience] using case_store {case_store_aggregated.relative_to(PROJECT_ROOT)}")

    snap = _load_json(case_store_aggregated)
    records = snap.get("records", [])
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        rk = str((r.get("candidate") or {}).get("reason_key") or "")
        if rk:
            by_reason[rk].append(r)

    context_summary = {rk: {"total": len(v),
                             "accept": sum(1 for x in v if x.get("label", {}).get("decision") == "accept"),
                             "reject": sum(1 for x in v if x.get("label", {}).get("decision") == "reject")}
                       for rk, v in by_reason.items()}

    for fname in ("all_candidates.json", "calibration_source.json"):
        p = target_run / fname
        if not p.is_file():
            continue
        doc = _load_json(p)
        cands = doc.get("candidates") if isinstance(doc, dict) else doc
        for c in cands or []:
            rk = str(c.get("reason_key") or c.get("candidate_kind") or "")
            similar = by_reason.get(rk, [])
            filler = str(c.get("filler_token") or c.get("proposed_delete_text") or "").strip()
            exact = [r for r in similar
                     if filler and str((r.get("candidate") or {}).get("proposed_text") or "") == filler]
            all_accept = sum(1 for x in similar if x.get("label", {}).get("decision") == "accept")
            all_reject = sum(1 for x in similar if x.get("label", {}).get("decision") == "reject")
            exact_accept = sum(1 for x in exact if x.get("label", {}).get("decision") == "accept")
            exact_reject = sum(1 for x in exact if x.get("label", {}).get("decision") == "reject")
            c["experience_context"] = {
                "reason_key_matches": len(similar),
                "reason_key_accept_count": all_accept,
                "reason_key_reject_count": all_reject,
                "exact_token_matches": len(exact),
                "exact_token_accept_count": exact_accept,
                "exact_token_reject_count": exact_reject,
                "case_store_relpath": str(case_store_aggregated.relative_to(PROJECT_ROOT)),
                "top_case_ids": [x.get("case_id", "?") for x in similar[:5]],
            }
        _write_json(p, doc)

    out = target_run / "experience_context.json"
    _write_json(out, {
        "schema_version": "experience-context-v1",
        "case_store_relpath": str(case_store_aggregated.relative_to(PROJECT_ROOT)),
        "records_total": len(records),
        "by_reason_key": context_summary,
    })
    print(f"[experience] {len(records)} records across {len(by_reason)} reason keys")
    return out


def _run_downstream_stages(args, target, ep_dur, transcript_dir, wordlevel_json):
    """Stages 3.1 through 7 · restored from mis-indented dead code (2026-08-18 bugfix)."""
    tracks_for_mix = args.tracks_for_automix or args.from_raw_wav
    if not tracks_for_mix:
        raise SystemExit("need --tracks-for-automix or --from-raw-wav to specify audio for mixing")
    # ------ Stage 3.4 · Speaker role filter (v20.5, S · C4 alternative to pyannote) ------
    # v20.6 · Stage 3.1 auto_speaker_role (自动判 host/guest, 若人工 map 缺失)
    speaker_map_manual = PROJECT_ROOT / "main" / "knowledge" / "speaker_maps" / f"{args.episode_id}.speaker_map.json"
    speaker_map_auto = PROJECT_ROOT / "main" / "knowledge" / "speaker_maps" / f"{args.episode_id}.speaker_map.auto.json"
    if not speaker_map_manual.is_file() and transcript_dir and transcript_dir.is_dir():
        try:
            _run([
                sys.executable, str(PROJECT_ROOT / "main/orchestrator/auto_speaker_role.py"),
                "--episode-id", args.episode_id,
                "--analysis-dir", str(transcript_dir),
                "--episode-duration-seconds", f"{ep_dur}",
                "--out", str(speaker_map_auto),
            ], check=False)
            print(f"[auto-speaker-role] wrote {speaker_map_auto.name}")
        except Exception as exc:
            print(f"[auto-speaker-role] warn: {exc}")

    # v20.6 · Stage 3.2 spacy_semantic_transcript (对每轨 ASR 分句 · Q1)
    if transcript_dir and transcript_dir.is_dir():
        spacy_out_dir = target / "spacy_semantic"
        spacy_out_dir.mkdir(exist_ok=True, parents=True)
        spacy_script = PROJECT_ROOT / "稳定生产/challengers/semantic-transcript-v1/scripts/spacy_semantic_transcript.py"
        # 用 miniforge python 跑 (装了 spacy)
        conda_python = Path.home() / "miniforge3" / "bin" / "python"
        if conda_python.exists() and spacy_script.is_file():
            for p in sorted(transcript_dir.glob("track_*.transcript.json")):
                tid = p.stem.replace(".transcript", "")
                out_p = spacy_out_dir / f"{tid}.spacy_semantic.json"
                try:
                    _run([
                        str(conda_python), str(spacy_script),
                        "--transcript", str(p),
                        "--output", str(out_p),
                    ], check=False)
                except Exception as exc:
                    print(f"[spacy-semantic] warn {tid}: {exc}")
            print(f"[spacy-semantic] output in {spacy_out_dir.name}")

    # v20.6 · Stage 3.3 stage_feedback_lookup (Q4 备注记忆机制)
    try:
        from main.orchestrator.session_feedback import (
            inject_into_candidates as _inject_fb,
            load_lake_feedback as _load_lake_fb,
            load_session_feedback as _load_session_fb,
        )
        session_fb = _load_session_fb(args.episode_id, project_root=PROJECT_ROOT)
        # 加 ALL 跨期反馈 (若存在)
        session_fb.extend(_load_session_fb("ALL", project_root=PROJECT_ROOT))
        lake_fb = _load_lake_fb(project_root=PROJECT_ROOT)
        for fname in ("all_candidates.json", "calibration_source.json"):
            p = target / fname
            if not p.is_file():
                continue
            doc = _load_json(p)
            cands = doc.get("candidates") if isinstance(doc, dict) else doc
            _, fb_summary = _inject_fb(cands or [], session_fb, lake_fb)
            _write_json(p, doc)
            print(f"[feedback-lookup] {fname}: {fb_summary}")
    except Exception as exc:
        print(f"[feedback-lookup] warn: {exc}")

# ------ Stage 3.4 · Speaker role filter (v20.5, S · C4 alternative to pyannote) ------
    # v20.7 · opt-in pyannote RTTM 消费路径 (--pyannote-enabled)
    #   flag OFF → rttm_dir=None, speaker_role_filter 100% 走原能量启发式
    #   flag ON  → 先调 Challenger C run_diarization.py 落 RTTM, 再传给 filter
    pyannote_rttm_dir: Path | None = None
    if getattr(args, "pyannote_enabled", False):
        diar_inputs_raw = args.tracks_for_automix or args.from_raw_wav or []
        diar_inputs = [Path(p) for p in diar_inputs_raw if Path(p).is_file()]
        if not diar_inputs:
            print("[pyannote] --pyannote-enabled 但没有可用 track WAV; skip · "
                  "speaker_role_filter fallback 能量启发式")
        else:
            try:
                pyannote_rttm_dir = stage_pyannote_diarize(
                    target,
                    diar_inputs,
                    model_tag=getattr(args, "pyannote_model_tag", None),
                )
            except Exception as exc:
                print(f"[pyannote] warn: {exc}; fallback 能量启发式")
                pyannote_rttm_dir = None

    speaker_map_candidates = [
        PROJECT_ROOT / "main" / "knowledge" / "speaker_maps" / f"{args.episode_id}.speaker_map.json",
    ]
    for sm in speaker_map_candidates:
        if sm.is_file():
            try:
                stage_speaker_role_filter(target, sm, transcript_dir, rttm_dir=pyannote_rttm_dir)
            except Exception as exc:
                print(f"[speaker-role-filter] warn: {exc}; continuing")
            break
    else:
        # CLAUDE.md §12 fail-closed: 缺 speaker_map 不再默默 skip
        msg = (
            f"speaker_map required for {args.episode_id} but none found under "
            f"main/knowledge/speaker_maps/{args.episode_id}.speaker_map.json. "
            "主持人 backchannel 会污染候选池 (§12 · 20-pack 事件的核心 bug)。"
        )
        if args.allow_missing_speaker_map:
            print(f"[speaker-role-filter] WARN (--allow-missing-speaker-map): {msg}")
        else:
            raise SystemExit(
                f"[speaker-role-filter] FAIL · {msg}\n"
                "修复：先建 main/knowledge/speaker_maps/<episode>.speaker_map.json（人工声明每轨 role），"
                "或临时加 --allow-missing-speaker-map 跳过（仅限 fixture / 遗留 run）。"
            )

# ------ Stage 3.4b · 回填 all_candidates.json null 字段（bug fix 2026-08-19）------
# 修 MFA refined_count=0 根因 · v13 all_candidates.json 里 filler_token/start_seconds 全 null
# 从同 run 的 review_package.json 或 candidate_source.json 回填 5 字段
# 模板：main/orchestrator/predict_cut_artifact.py:204-209
    all_cand_path = target / "all_candidates.json"
    if all_cand_path.is_file():
        pkg_source = None
        for candidate_source in [
            target / "review_bundle" / "review_package.json",
            target / "candidates" / "candidate_source.json",
        ]:
            if candidate_source.is_file():
                pkg_source = candidate_source
                break
        if pkg_source:
            pkg_doc = _load_json(pkg_source)
            pkg_cands = pkg_doc.get("candidates") or pkg_doc.get("reviewable_candidates") or []
            pkg_lookup = {c.get("candidate_id"): c for c in pkg_cands if c.get("candidate_id")}
            all_doc = _load_json(all_cand_path)
            cands = all_doc.get("candidates", [])
            filled = 0
            for c in cands:
                p = pkg_lookup.get(c.get("candidate_id"), {})
                for k in ("filler_token", "proposed_delete_text", "start_seconds", "end_seconds", "source_track_id"):
                    if p.get(k) is not None and c.get(k) is None:
                        c[k] = p[k]
                        filled += 1
            if filled:
                _write_json(all_cand_path, all_doc)
                print(f"[bugfix-3.4b] 回填 {filled} 个字段到 all_candidates.json（源: {pkg_source.name}）· 修 MFA refined_count=0 根因")

# ------ Stage 3.5 · MFA 精修候选边界 (v26 accept 后固化, CLAUDE.md §8) ------
    mfa_bin = Path.home() / "miniforge3" / "bin" / "mfa"
    mfa_out = target / "mfa_boundaries.json"
    if mfa_bin.exists() and transcript_dir and transcript_dir.is_dir():
        mfa_tool = PROJECT_ROOT / "稳定生产/challengers/mfa-alignment-v1/scripts/mfa_align_and_extract_boundaries.py"
        try:
            _run([
                sys.executable, str(mfa_tool),
                "--candidates", str(target / "all_candidates.json"),
                "--tracks", *[str(t) for t in tracks_for_mix or []],
                "--asr-transcript-dir", str(transcript_dir),
                "--context-seconds", "5",
                "--head-pad-ms", "50", "--tail-pad-ms", "50",
                "--out", str(mfa_out),
            ], check=False)
            # apply refined boundaries to all_candidates + calibration_source
            if mfa_out.is_file():
                mfa_doc = _load_json(mfa_out)
                mfa_by_cid = {r["candidate_id"]: r for r in mfa_doc.get("refined", [])}
                for f in ("all_candidates.json", "calibration_source.json"):
                    p = target / f
                    if not p.is_file():
                        continue
                    doc = _load_json(p)
                    cands = doc.get("candidates") if isinstance(doc, dict) else doc
                    for c in cands or []:
                        cid = str(c.get("candidate_id"))
                        m = mfa_by_cid.get(cid)
                        if not m:
                            continue
                        # 存 ASR 原值供 audit
                        if "asr_start_sample" not in c:
                            c["asr_start_sample"] = c.get("start_sample")
                            c["asr_end_sample"] = c.get("end_sample")
                            c["asr_start_seconds"] = c.get("start_seconds")
                            c["asr_end_seconds"] = c.get("end_seconds")
                        c["start_sample"] = int(round(m["refined_start_raw"] * 48000))
                        c["end_sample"] = int(round(m["refined_end_raw"] * 48000))
                        c["start_seconds"] = float(m["refined_start_raw"])
                        c["end_seconds"] = float(m["refined_end_raw"])
                        c["boundary_source"] = "mfa_mandarin_v26"
                        c["mfa_refined"] = m
                    _write_json(p, doc)
                print(f"[mfa] refined {mfa_doc.get('refined_count', 0)} candidates; "
                      f"skipped {mfa_doc.get('skipped_count', 0)} (OOV / not matched)")
        except Exception as exc:
            print(f"[mfa] warn: {exc}; continuing with ASR boundaries")
    else:
        print(f"[mfa] skipped: mfa_bin={mfa_bin.exists()} transcript_dir={transcript_dir}")

    # Stage 3.5.6 · ASR probability 硬约束 (用户 2026-08-19 明确)
    # · 唯一硬约束: word.probability < 0.60 for filler/rep kind → REJECT · 不塞 LLM
    # · 出处: EP04 实测 (正常词 >=0.75 · 幻觉词 <=0.49) + silvacarl2 社区共识 (github/whisper#679)
    # · 只对 filler_hesitation / immediate_repetition · 避免误伤专名/生僻词
    if getattr(args, "auto_asr_prob_gate", True):
        try:
            all_cands = target / "all_candidates.json"
            if all_cands.is_file() and transcript_dir and transcript_dir.is_dir():
                print(f"\n[stage 3.5.6 · ASR probability gate · 唯一硬约束 · < 0.60 for filler/rep]")

                # Load transcripts
                transcripts = {}
                for tf in transcript_dir.glob("track_*.transcript.json"):
                    tid = tf.stem.replace(".transcript", "")
                    transcripts[tid] = _load_json(tf)

                # Load candidates
                cd = _load_json(all_cands)
                cands = cd.get("candidates", cd if isinstance(cd, list) else [])

                # Filter
                filtered = []
                rejected_by_prob = []
                TARGET_KINDS = {"filler_hesitation", "immediate_repetition"}
                MIN_PROB = 0.60

                for c in cands:
                    kind = c.get("candidate_kind") or c.get("kind")
                    if kind not in TARGET_KINDS:
                        filtered.append(c)
                        continue
                    tid = c.get("source_track_id", "track_01")
                    start, end = c.get("start_seconds", 0), c.get("end_seconds", 0)
                    words = transcripts.get(tid, {}).get("words", [])
                    # 收集候选区间内 word probability
                    probs = [w.get("probability", 1.0) for w in words
                             if start <= w.get("start_seconds", 0) < end
                             and w.get("probability") is not None]
                    if probs:
                        avg_prob = sum(probs) / len(probs)
                        if avg_prob < MIN_PROB:
                            rejected_by_prob.append({
                                **c,
                                "rejected_reason": f"ASR 硬约束 · avg prob {avg_prob:.3f} < 0.60 · 疑似幻听",
                                "avg_prob": avg_prob,
                            })
                            continue
                    filtered.append(c)

                # 覆盖 all_candidates.json · 保留原 backup
                _write_json(target / "all_candidates.pre_asr_gate.json", cd)
                if isinstance(cd, dict):
                    cd["candidates"] = filtered
                    _write_json(all_cands, cd)
                else:
                    _write_json(all_cands, filtered)

                # 保留 rejected 侧车 (审计)
                _write_json(target / "asr_prob_rejected.json", {
                    "schema": "asr-prob-hard-gate-v1",
                    "min_prob": MIN_PROB,
                    "target_kinds": list(TARGET_KINDS),
                    "rejected_count": len(rejected_by_prob),
                    "rejected": rejected_by_prob,
                    "source": "EP04 实测 + silvacarl2 社区共识 (github/whisper#679)",
                    "computed_at_utc": _utc_now(),
                })
                print(f"[stage 3.5.6] filtered · 原 {len(cands)} -> 过 {len(filtered)} · rejected {len(rejected_by_prob)}")
        except Exception as exc:
            print(f"[stage 3.5.6] ASR prob gate failed: {exc}; continuing")

    # ---- Stage 3.5.5 · LLM 全流程主导 (用户 2026-08-19 · 回忆 wpl29rgl6 成功版) ----
    # · LLM 完全主导 · 不管 rules 出的 all_candidates.json · **完全绕过 rules candidate**
    # · 一步 · 从 transcript 扫出 KEEP_CUT 列表 · 直接写 llm_verdicts.json
    # · Stage 5 EDL 从 llm_verdicts.json 读 · 若 candidate 不在 auto_cut · 直接构 cut
    # · Stage 3.7 (rules 二审) 若 3.5.5 已产 verdicts · skip (保留作 fallback)
    # · 若 llm_full_pipeline.py 缺失 · 静默 fallback 到原 rules pipeline (backward compat)
    stage355_produced_verdicts = False
    if getattr(args, "auto_llm_full_pipeline", True):
        try:
            llm_full_script = PROJECT_ROOT / "skills/candidate-semantic-veto/scripts/llm_full_pipeline.py"
            if llm_full_script.is_file() and transcript_dir and transcript_dir.is_dir():
                print(f"\n[stage 3.5.5 · LLM full pipeline] 用户 2026-08-19 · LLM 完全主导 · 绕过 rules")
                llm_verdicts_out = target / "llm_verdicts.json"
                _run([
                    sys.executable, str(llm_full_script),
                    "--transcripts-dir", str(transcript_dir),
                    "--out", str(llm_verdicts_out),
                ], check=False)
                if llm_verdicts_out.is_file():
                    _vd_355 = _load_json(llm_verdicts_out)
                    _keep_355 = [
                        v for v in _vd_355.get("verdicts", [])
                        if v.get("verdict") == "KEEP_CUT"
                    ]
                    _total_355 = (
                        _vd_355.get("summary", {}).get("total")
                        or _vd_355.get("summary", {}).get("keep_cut")
                        or len(_keep_355)
                    )
                    print(f"[stage 3.5.5] LLM 输出 {_total_355} KEEP_CUT 候选 (bypass rules)")
                    stage355_produced_verdicts = True
            else:
                print(f"[stage 3.5.5] llm_full_pipeline.py 不存在 or transcript_dir 缺 · silent skip · fallback 到原 Stage 3.7 rules 二审 · path={llm_full_script}")
        except Exception as exc:
            print(f"[stage 3.5.5] LLM full pipeline failed: {exc}; continuing (fallback to rules pipeline)")

    # Stage 4: apply autocut gate
    # v20 enrich: 老 codex candidates 缺 boundary_lock/post_cut_pause_ms 字段，
    # 在 gate 之前补一次（也补到 calibration_source 供 EDL 生成读取）
    for f in ("all_candidates.json", "calibration_source.json"):
        p = target / f
        if not p.is_file():
            continue
        doc = _load_json(p)
        cands = doc.get("candidates") if isinstance(doc, dict) else doc
        if not cands:
            continue
        enrich_candidates_v20(cands, transcript_dir)
        _write_json(p, doc)

    # Stage 3.9 · Pre-gate case_embedding retrieval (challenger case-memory-embedding-v1)
    # · 在 gate 前 · 对 all_candidates.json 每候选查 mentor gold case_memory 拿 top-3 相似 case
    # · 写 target/case_embedding_retrieval.json · 供 apply_autocut_gate 的 G8 门消费
    # · 若 index 未 build (main/knowledge/case_embeddings/index.faiss 不存在) · 静默 skip
    #   · G8 也会 silent pass · pipeline 不受影响 (fallback per user 2026-08-19 directive)
    pre_gate_case_embed_json: Path | None = None
    if getattr(args, "auto_case_embedding", True):
        try:
            _pg_case_index = PROJECT_ROOT / "main/knowledge/case_embeddings/index.faiss"
            _pg_case_meta = PROJECT_ROOT / "main/knowledge/case_embeddings/meta.jsonl"
            _pg_retrieve = PROJECT_ROOT / "稳定生产/challengers/case-memory-embedding-v1/scripts/retrieve_similar_cases.py"
            _pg_all_cands = target / "all_candidates.json"
            if _pg_case_index.exists() and _pg_case_meta.exists() and _pg_retrieve.exists() and _pg_all_cands.is_file():
                print(f"\n[stage 3.9 · pre-gate case embedding retrieval] index={_pg_case_index.name}")
                _pg_doc = _load_json(_pg_all_cands)
                _pg_cands = _pg_doc.get("candidates", []) if isinstance(_pg_doc, dict) else _pg_doc
                _pg_tracks = args.tracks_for_automix or args.from_raw_wav or []
                _pg_by_cid: dict[str, list[dict]] = {}
                for _pgc in _pg_cands or []:
                    _pg_cid = _pgc.get("candidate_id")
                    if not _pg_cid:
                        continue
                    _pg_track_id = _pgc.get("track_id") or _pgc.get("source_track_id") or "track_01"
                    try:
                        _pg_idx = int(str(_pg_track_id).split("_")[-1]) - 1
                    except Exception:
                        _pg_idx = 0
                    if _pg_idx < 0 or _pg_idx >= len(_pg_tracks):
                        continue
                    _pg_query_wav = _pg_tracks[_pg_idx]
                    _pg_start = _pgc.get("start_seconds")
                    _pg_end = _pgc.get("end_seconds")
                    if _pg_start is None or _pg_end is None:
                        continue
                    _pg_out = target / f"similar_cases_{_pg_cid}.json"
                    try:
                        _run([
                            sys.executable, str(_pg_retrieve),
                            "--index", str(_pg_case_index),
                            "--meta", str(_pg_case_meta),
                            "--query-wav", str(_pg_query_wav),
                            "--start", str(_pg_start),
                            "--end", str(_pg_end),
                            "--top-k", "3",
                            "--min-score", "0.5",
                            "--out-json", str(_pg_out),
                        ], check=False)
                        if _pg_out.is_file():
                            _pg_by_cid[str(_pg_cid)] = _load_json(_pg_out).get("top_k", [])
                    except Exception as _pg_inner:
                        print(f"[stage 3.9] {_pg_cid} retrieve failed: {_pg_inner}; skip")
                if _pg_by_cid:
                    pre_gate_case_embed_json = target / "case_embedding_retrieval.json"
                    _write_json(pre_gate_case_embed_json, {
                        "schema": "case-embedding-retrieval-v1",
                        "stage": "3.9_pre_gate",
                        "index_source": str(_pg_case_index.relative_to(PROJECT_ROOT)),
                        "candidates_with_similar_cases": _pg_by_cid,
                        "count": len(_pg_by_cid),
                    })
                    print(f"[stage 3.9] retrieved {len(_pg_by_cid)} candidate similar-cases → G8 gate will consume · {pre_gate_case_embed_json.relative_to(PROJECT_ROOT)}")
                else:
                    print(f"[stage 3.9] no similar-cases retrieved · G8 will silent skip")
            else:
                print(f"[stage 3.9] embedding index 尚未构建 · G8 gate silent skip · 需先跑 build_case_embeddings.py (path: {_pg_case_index})")
        except Exception as _pg_exc:
            print(f"[stage 3.9] pre-gate case embedding retrieval failed: {_pg_exc}; continuing · G8 silent skip")

    gate_out = stage_autocut_gate(
        target, args.labels_lake, args.policy_v2, ep_dur, wordlevel_json,
        case_embeddings_json=pre_gate_case_embed_json,
    )

    # Stage 3.7 · LLM 语义 filter (用户 2026-08-19 明确 · LLM 唯一候选决定者)
    # · 在 autocut_gate 之后 · cut-verify 之前 · 写 target/llm_verdicts.json
    # · Stage 5 (stage_edl_from_gate) 检测到该文件后 · 只用 KEEP_CUT 候选进 EDL
    # · Stage 4.5 cut-verify 保留 · 但仅作诊断 · 不影响 EDL
    # · 脚本缺失 or 判定失败 · 静默 fallback (Stage 5 无 verdicts → 用原 auto_cut)
    # · 用户 2026-08-19 · 若 Stage 3.5.5 已产 llm_verdicts (LLM 完全主导) · 跳过 3.7 避免重复
    #   Stage 3.7 保留作 3.5.5 缺失/失败时的 rules-二审 fallback
    if stage355_produced_verdicts:
        print(f"[stage 3.7] skip · Stage 3.5.5 已产 llm_verdicts.json (LLM 完全主导) · 保留 3.7 作 3.5.5 缺失时的 fallback")
    elif getattr(args, "auto_llm_semantic_filter", True):
        try:
            llm_filter_script = PROJECT_ROOT / "skills/candidate-semantic-veto/scripts/llm_semantic_filter.py"
            if llm_filter_script.is_file():
                all_cands = target / "all_candidates.json"
                llm_verdicts = target / "llm_verdicts.json"
                if all_cands.is_file():
                    print(f"\n[stage 3.7 · LLM semantic filter] 用户 2026-08-19 · LLM 唯一候选决定")
                    _run([
                        sys.executable, str(llm_filter_script),
                        "--candidates", str(all_cands),
                        "--transcripts-dir", str(transcript_dir) if transcript_dir else "",
                        "--out", str(llm_verdicts),
                    ], check=False)
                    if llm_verdicts.is_file():
                        vd = _load_json(llm_verdicts)
                        print(f"[stage 3.7] verdict summary: {json.dumps(vd.get('summary', {}), ensure_ascii=False)}")
            else:
                print(f"[stage 3.7] llm_semantic_filter.py 不存在 · silent skip · fallback 到原 auto_cut · path={llm_filter_script}")
        except Exception as exc:
            print(f"[stage 3.7] LLM filter failed: {exc}; continuing (Stage 5 will fallback to old logic)")

    # Stage 4.5 · cut-verify · 剪口干净度 4 项 check（2026-08-19 v22 硬边界 · CLAUDE.md §22）
    # · 幻觉检测 · 静音位置校验 · 节奏 gap · 拼接策略路由
    # · 输出 verified_edl.json 侧车 · gate 后 EDL 生成前
    # · 若命中 filler ASR-word 扩展经验 · 覆盖 candidate cut 范围
    # · Check 5 (NISQA · opt-in) · 若 --nisqa-venv-python 提供且 render clips 存在 · 补 MOS 打分
    # · 2026-08-19 用户明确 · Stage 3.7 落地后 · cut-verify **降级为诊断** ·
    #   不影响 EDL · EDL 由 Stage 5 从 llm_verdicts.json 决定 · verified_edl.json
    #   仅保留其 recommended_params 侧写字段 (crossfade / room_tone / priority_level)
    #   供 Stage 6.7 Optuna 使用 · 不再充当 EDL 白名单
    try:
        verify_script = PROJECT_ROOT / "skills/cut-verify/scripts/verify_cut_plan.py"
        if verify_script.is_file() and transcript_dir and transcript_dir.is_dir():
            tracks_for_mix_verify = args.tracks_for_automix or args.from_raw_wav
            if tracks_for_mix_verify:
                raw_track_map = {}
                for i, t in enumerate(tracks_for_mix_verify, 1):
                    raw_track_map[f"track_{i:02d}"] = str(Path(t).resolve())
                verify_out = target / "verified_edl.json"
                verify_tmp = target / "tmp_cut_verify"
                verify_tmp.mkdir(exist_ok=True)
                candidates_json = target / "all_candidates.json"
                if candidates_json.is_file():
                    verify_cmd = [
                        sys.executable, str(verify_script),
                        "--candidate-json", str(candidates_json),
                        "--transcript-dir", str(transcript_dir),
                        "--raw-track-map", json.dumps(raw_track_map, ensure_ascii=False),
                        "--tmp-dir", str(verify_tmp),
                        "--out", str(verify_out),
                    ]
                    # 用户 2026-08-19 明确"全走 Optuna" · 默认关 Stage 4.5 Check 5 · 让 Stage 6.7 兜底
                    # 保留 CLI 反开 · 若真要 Check 5 做 gate · 加 --stage45-check5
                    if getattr(args, "stage45_check5", False) and getattr(args, "nisqa_python", None):
                        verify_cmd += ["--nisqa-venv-python", str(args.nisqa_python)]
                        render_root_stage4 = target / "current_audit_clips"
                        if render_root_stage4.exists():
                            verify_cmd += ["--render-root", str(render_root_stage4)]
                    _run(verify_cmd, check=False)
                    if verify_out.is_file():
                        vd = _load_json(verify_out)
                        summary = vd.get("summary", {})
                        print(f"[cut-verify] {summary}")
                        # 消费 recommended_params + crossfade_strategy → 覆盖 candidates
                        # BREAK-03 后续 (2026-08-19): 原只写 2/5 · 补齐 room_tone_pad_ms /
                        # priority_level / why · Stage 6.7 optuna / stage_edl_from_gate 用
                        rec_by_id: dict[str, dict[str, Any]] = {}
                        for _vc in vd.get("candidates", []):
                            _cid = _vc.get("candidate_id")
                            _rec = dict(_vc.get("recommended_params", {}) or {})
                            _cs = (_vc.get("checks", {}) or {}).get("crossfade_strategy", {}) or {}
                            # priority_level / why 只在 checks · 不在 recommended_params
                            if _cs.get("priority_level") is not None:
                                _rec["priority_level"] = _cs.get("priority_level")
                            if _cs.get("why"):
                                _rec["why"] = _cs.get("why")
                            rec_by_id[_cid] = _rec
                        for f in ("all_candidates.json", "calibration_source.json"):
                            p = target / f
                            if not p.is_file():
                                continue
                            doc = _load_json(p)
                            cands = doc.get("candidates") if isinstance(doc, dict) else doc
                            for c in cands or []:
                                cid = c.get("candidate_id")
                                rec = rec_by_id.get(cid, {})
                                if rec.get("crossfade_ms") is not None:
                                    c["cut_verify_crossfade_ms"] = rec["crossfade_ms"]
                                if rec.get("room_tone_pad_ms") is not None:
                                    c["cut_verify_room_tone_pad_ms"] = rec["room_tone_pad_ms"]
                                if rec.get("strategy"):
                                    c["cut_verify_strategy"] = rec["strategy"]
                                if rec.get("priority_level") is not None:
                                    c["cut_verify_priority_level"] = rec["priority_level"]
                                if rec.get("why"):
                                    c["cut_verify_why"] = rec["why"]
                            _write_json(p, doc)
        else:
            print(f"[cut-verify] skipped: script={verify_script.exists()} transcript_dir={transcript_dir}")
    except Exception as exc:
        print(f"[cut-verify] warn: {exc}; continuing without cut-verify (fallback to default xfade)")

    # Stage 5: build EDL from auto_cut
    edl = stage_edl_from_gate(
        target, gate_out, target / "calibration_source.json"
    )

    # Stage 6: automix + music + double-pass loudnorm → mp3
    # 用户 2026-08-19 · automix 优先用 denoised · 若无 fallback raw
    _raw_tracks = args.tracks_for_automix or args.from_raw_wav
    if not _raw_tracks:
        raise SystemExit("need --tracks-for-automix or --from-raw-wav to specify audio for mixing")
    denoised_wavs: list[str] = []
    denoise_dir = target / "denoise"
    if denoise_dir.is_dir():
        for i in range(1, 10):
            candidate = denoise_dir / f"track_{i:02d}.deepfiltered.wav"
            if candidate.is_file():
                denoised_wavs.append(str(candidate))
            else:
                break
    if denoised_wavs and len(denoised_wavs) == len(_raw_tracks):
        tracks_for_mix = denoised_wavs
        print(f"[stage 6 · automix] 用 denoised · {len(denoised_wavs)} 轨 · 从 {denoise_dir}")
    else:
        tracks_for_mix = _raw_tracks
        print(f"[stage 6 · automix] fallback 用 raw · denoised 不齐 (found {len(denoised_wavs)} vs need {len(_raw_tracks)})")
    render_dir = target / "render"
    render_dir.mkdir(exist_ok=True)
    tmp_dir = target / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    output_mp3 = render_dir / f"{args.episode_id}.machine_assisted_draft.mp3"
    # 用户 2026-08-19 明确要求: automix 挂了 · Stage 6.5/6.7/6.8/6.9 依然要跑 (Optuna refinement_trace.json 证据)
    # verified_edl.json 已在 stage_edl_from_gate 阶段写好 · Stage 6.7 不依赖 output_mp3
    # Stage 6.5 (NISQA benchmark) 内部有 output_mp3.exists() 检查 · automix 挂时会自动 skip
    try:
        stage_automix(
            [Path(t) for t in tracks_for_mix],
            args.music,
            edl,
            args.release_spec,
            args.music_template_file,
            args.template_id,
            output_mp3,
            tmp_dir,
        )
    except Exception as exc:
        import traceback as _tb
        _write_json(target / "automix_error.json", {
            "error": str(exc),
            "type": exc.__class__.__name__,
            "traceback": _tb.format_exc(),
            "note": "automix 挂 · Stage 6.5/6.7/6.8/6.9 依然跑 · 用户 2026-08-19 要求 refinement_trace.json 证据",
        })
        print(f"[stage 6 · automix] FAILED (continue for Stage 6.7 Optuna): {exc.__class__.__name__}: {exc}")

    # Stage 7: audit report
    audit = stage_audit_report(target, gate_out)

    # audit_verdicts.template.json 自动生成 · 用户 2026-08-19 明确 · 第三次要求
    # · 让人审知道每个候选长什么样 · 填 verdict=APPROVED/REJECTED
    # · 保存为 audit_verdicts.json (去 .template) · 下次 rerun pipeline 触发 Stage 6.9
    try:
        verified_edl = target / "verified_edl.json"
        refinement_trace = target / "refinement_trace.json"
        template_path = target / "audit_verdicts.template.json"
        actual_path = target / "audit_verdicts.json"
        if actual_path.is_file():
            print(f"[audit_verdicts.template] {actual_path.name} 已存在 · skip template 生成")
        else:
            template_candidates = []
            if verified_edl.is_file():
                vd = _load_json(verified_edl)
                for c in vd.get("candidates", []):
                    cur_cut = c.get("current_cut") or {}
                    template_candidates.append({
                        "candidate_id": c.get("candidate_id"),
                        "kind": c.get("candidate_kind") or c.get("kind"),
                        "source_track_id": c.get("source_track_id"),
                        "start_seconds": cur_cut.get("start_seconds", c.get("start_seconds")),
                        "end_seconds": cur_cut.get("end_seconds", c.get("end_seconds")),
                        "proposed_delete_text": c.get("proposed_delete_text", ""),
                        "current_verdict_from_verify": c.get("overall_verdict") or c.get("verdict"),
                        "verdict": "",   # 人审填 APPROVED / REJECTED
                        "reason": "",    # 人审填理由 (可选)
                    })
            # 若有 refinement_trace · 标记 escaped 候选 (二轮 Optuna 优先处理这些)
            # · refinement_trace.json 顶层是 "candidates" 列表 (schema_version=iterative-refinement-v1)
            # · 每条含 escalated_to_human_review: bool · Optuna 10 iter 未收敛 → True
            if refinement_trace.is_file():
                try:
                    rt = _load_json(refinement_trace)
                    escaped_ids = {r.get("candidate_id") for r in rt.get("candidates", [])
                                   if r.get("escalated_to_human_review")}
                    for tc in template_candidates:
                        if tc["candidate_id"] in escaped_ids:
                            tc["_note"] = "Optuna 10 iter 未收敛 · 建议人审优先"
                except Exception:
                    pass
            template = {
                "schema": "audit-verdicts-v1",
                "run_id": target.name,
                "generated_at_utc": _utc_now(),
                "instructions": (
                    "填 candidates[].verdict = APPROVED (剪) 或 REJECTED (不剪) · "
                    "另存为 audit_verdicts.json (去掉 .template 后缀) · "
                    "rerun pipeline 触发 Stage 6.9 二轮 Optuna refinement"
                ),
                "candidates": template_candidates,
            }
            _write_json(template_path, template)
            print(f"[audit_verdicts.template] 生成 · {len(template_candidates)} 候选 · {template_path.name}")
    except Exception as exc:
        print(f"[audit_verdicts.template] 生成失败: {exc}; continuing")

    # Stage 6.5 · NISQA benchmark (opt-in via --nisqa-python)
    # · 拿成品 mp3 · 打无参考 MOS 分 · 输出 NISQA_BENCHMARK.md 侧车
    # · 用户 2026-08-19 明确要求"参数 benchmark 返回 · 剪到干净为止"的 signal 源
    if getattr(args, "nisqa_python", None) and output_mp3.exists():
        try:
            nisqa_out = target / "nisqa_benchmark.json"
            nisqa_bench_md = target / "NISQA_BENCHMARK.md"
            check_nisqa_script = PROJECT_ROOT / "稳定生产/challengers/nisqa-cutverify-v1/scripts/check_nisqa_mos.py"
            if check_nisqa_script.exists():
                print(f"\n[stage 6.5 · NISQA benchmark] running on {output_mp3.name}")
                _run([
                    str(args.nisqa_python), str(check_nisqa_script),
                    "--clip-path", str(output_mp3),
                    "--out-json", str(nisqa_out),
                    "--mode", "overall",
                ], check=False)
                if nisqa_out.is_file():
                    nisqa_data = _load_json(nisqa_out)
                    scores = nisqa_data.get("scores", {})
                    # 写人读版 benchmark 报告
                    lines = [
                        f"# NISQA Benchmark · {args.episode_id}",
                        "",
                        f"> **输入**: `{output_mp3.relative_to(PROJECT_ROOT)}`",
                        f"> **模型**: NISQA v2.0 (Fraunhofer · CNN-LSTM · 90k+ 样本)",
                        f"> **时间**: {nisqa_data.get('computed_at_utc', '?')}",
                        "",
                        "## 5 维 MOS 分",
                        "",
                        "| 维度 | 分 (1-5) | 判定 |",
                        "|---|---|---|",
                    ]
                    for k, v in scores.items():
                        if v is None: continue
                        tag = "🟢 Good" if v >= 4.0 else ("🟡 Fair" if v >= 3.0 else "🔴 Poor")
                        lines.append(f"| {k} | {v:.2f} | {tag} |")
                    disc = scores.get("discontinuity")
                    lines.append("")
                    lines.append("## 收敛判决")
                    lines.append("")
                    if disc is not None:
                        conv = "**PASS_CLEAN**" if disc >= 3.5 else "**NEEDS_HUMAN_REVIEW** (discontinuity < 3.5)"
                        lines.append(f"- discontinuity = {disc:.2f} · 阈值 3.5 · {conv}")
                    lines.append("")
                    lines.append("## 迭代提示")
                    lines.append("")
                    lines.append("若 discontinuity < 3.5 且 overall > 2.5 · 可跑 iterative-cut-refinement-v1 (最多 3 轮)")
                    lines.append("命令: `python3 稳定生产/challengers/iterative-cut-refinement-v1/scripts/iterate_until_clean.py --candidate-json ... --nisqa-venv-python ...`")
                    nisqa_bench_md.write_text("\n".join(lines), encoding="utf-8")
                    print(f"[stage 6.5] NISQA benchmark: {nisqa_bench_md.relative_to(PROJECT_ROOT)}")
                    print(f"[stage 6.5] scores: {json.dumps(scores, ensure_ascii=False)}")
        except Exception as exc:
            print(f"[stage 6.5] NISQA benchmark failed: {exc}; continuing")

    # Stage 6.7 · 自动 iterative refinement (Optuna TPE + warm start · 用户 2026-08-19 明确要求)
    # · 对 verified_edl.json 里 verdict = NEEDS_HUMAN_REVIEW 的候选做 5 次上限 iteration
    # · warm start 按 candidate_kind 从 mentor gold + YouTube learning 派生
    # · 每 iteration 用 pydub 现场剪 clip · NISQA 打分 · Optuna TPE 学最优参数
    # · 默认开 · 用 --no-auto-iterate-refine 关
    if getattr(args, "auto_iterate_refine", True) and getattr(args, "nisqa_python", None):
        try:
            iterate_script = PROJECT_ROOT / "稳定生产/challengers/iterative-cut-refinement-v1/scripts/iterate_until_clean.py"
            verified_edl = target / "verified_edl.json"
            if iterate_script.exists() and verified_edl.exists():
                # 从 verified_edl.json 找需要 refine 的候选（verdict != CLEAN 都拿去 iterate）
                vd = _load_json(verified_edl)
                # 用户 2026-08-19 明确"全走 Optuna" · 所有非 REJECT 候选都进 Optuna
                # Optuna warm-start 让好候选 1 iter 就 pruned() 早停 · 平均 30s/好候选
                need_refine = [c for c in vd.get("candidates", [])
                               if (c.get("verdict") or c.get("overall_verdict") or "").upper() not in ("REJECT", "REJECTED")]
                if need_refine:
                    print(f"\n[stage 6.7 · auto iterative refinement · 全走 Optuna模式] {len(need_refine)} 候选进 Optuna TPE 迭代（10 次上限 · warm start · 好候选 1 iter 早停）")
                    # 组装 candidates.json (with cut params)
                    refine_input = target / "refine_input.json"
                    _write_json(refine_input, {"candidates": [
                        {
                            "candidate_id": c.get("candidate_id"),
                            "candidate_kind": c.get("current_cut", {}).get("kind"),
                            "source_track_id": c.get("current_cut", {}).get("track_id"),
                            "start_seconds": c.get("current_cut", {}).get("start_seconds"),
                            "end_seconds": c.get("current_cut", {}).get("end_seconds"),
                        } for c in need_refine
                    ]})
                    # raw track map
                    tracks_for_iterate = args.tracks_for_automix or args.from_raw_wav
                    raw_map_iterate = {f"track_{i+1:02d}": str(Path(t).resolve()) for i, t in enumerate(tracks_for_iterate)}
                    refine_trace = target / "refinement_trace.json"
                    _run([
                        sys.executable, str(iterate_script),
                        "--candidate-json", str(refine_input),
                        "--transcript-dir", str(transcript_dir),
                        "--raw-track-map", json.dumps(raw_map_iterate, ensure_ascii=False),
                        "--nisqa-venv-python", str(args.nisqa_python),
                        "--render-root", str(target / "iterative_refinement_clips"),
                        "--tmp-dir", str(target / "iterative_refinement_tmp"),
                        "--out", str(refine_trace),
                        "--use-optuna",  # 关键 · Optuna TPE + warm start
                    ], check=False)
                    if refine_trace.exists():
                        rt = _load_json(refine_trace)
                        summ = rt.get("summary", {})
                        print(f"[stage 6.7] Optuna refinement done · summary: {json.dumps(summ, ensure_ascii=False)}")
                else:
                    print("[stage 6.7] 无候选进 iterative refinement · 全部前 4+5 项 PASS 或已 REJECT")
        except Exception as exc:
            print(f"[stage 6.7] auto iterative refinement failed: {exc}; continuing")

    # Stage 6.8 · Case Embedding Retrieval (Challenger case-memory-embedding-v1)
    # · 从 mentor gold case_memory 检索最相似的历史 case · 用 Whisper encoder + FAISS
    # · 若 index 尚未 build (main/knowledge/case_embeddings/index.faiss 不存在) · 静默 skip
    # · 若存在 · 对 verified_edl.json 每候选调 retrieve_similar_cases.py 拿 top-3 similar cases
    # · 用户 2026-08-19 明确"mentor 要找一个干净的 · 我们乘此机会接入"
    if getattr(args, "auto_case_embedding", True):
        try:
            case_index = PROJECT_ROOT / "main/knowledge/case_embeddings/index.faiss"
            case_meta = PROJECT_ROOT / "main/knowledge/case_embeddings/meta.jsonl"
            retrieve_script = PROJECT_ROOT / "稳定生产/challengers/case-memory-embedding-v1/scripts/retrieve_similar_cases.py"
            verified_edl = target / "verified_edl.json"
            if case_index.exists() and case_meta.exists() and retrieve_script.exists() and verified_edl.exists():
                print(f"\n[stage 6.8 · case embedding retrieval] index={case_index.name}")
                vd = _load_json(verified_edl)
                similar_cases_by_cid = {}
                tracks_for_ce = args.tracks_for_automix or args.from_raw_wav
                for c in vd.get("candidates", []):
                    cid = c.get("candidate_id")
                    cut = c.get("current_cut", {})
                    track_id = cut.get("track_id") or "track_01"
                    track_idx = int(track_id.split("_")[-1]) - 1
                    if track_idx < 0 or track_idx >= len(tracks_for_ce):
                        continue
                    query_wav = tracks_for_ce[track_idx]
                    out_json = target / f"similar_cases_{cid}.json"
                    try:
                        _run([
                            sys.executable, str(retrieve_script),
                            "--index", str(case_index),
                            "--meta", str(case_meta),
                            "--query-wav", str(query_wav),
                            "--start", str(cut.get("start_seconds", 0)),
                            "--end", str(cut.get("end_seconds", 0)),
                            "--top-k", "3",
                            "--min-score", "0.5",
                            "--out-json", str(out_json),
                        ], check=False)
                        if out_json.is_file():
                            similar_cases_by_cid[cid] = _load_json(out_json).get("top_k", [])
                    except Exception as inner:
                        print(f"[stage 6.8] {cid} retrieve failed: {inner}; skip")
                # 写侧车 · 供 audit_report 消费
                if similar_cases_by_cid:
                    ce_out = target / "case_embedding_retrieval.json"
                    _write_json(ce_out, {
                        "schema": "case-embedding-retrieval-v1",
                        "index_source": str(case_index.relative_to(PROJECT_ROOT)),
                        "candidates_with_similar_cases": similar_cases_by_cid,
                        "count": len(similar_cases_by_cid),
                    })
                    print(f"[stage 6.8] retrieved {len(similar_cases_by_cid)} candidate similar-cases · {ce_out.relative_to(PROJECT_ROOT)}")
            else:
                print(f"[stage 6.8] embedding index 尚未构建 · skip · 需先跑 build_case_embeddings.py 从 mentor gold 建索引 (path: {case_index})")
        except Exception as exc:
            print(f"[stage 6.8] case embedding retrieval failed: {exc}; continuing")

    # Stage 6.9 · 人审 REJECTED 触发第二轮 Optuna (用户 2026-08-19 明确要求)
    # · 检测 target/audit_verdicts.json (人审填的) · 若有 verdict=REJECTED · 触发 re_iterate_from_audit.py
    # · 二轮用 skip_warm_start + seed=43 + max_iter=10 · 探索第一轮未采样区
    # · 若二轮仍 escape · verdict=SECOND_ROUND_ESCAPED · M3 兜底交人审
    if getattr(args, "auto_second_round_optuna", True):
        try:
            audit_verdicts = target / "audit_verdicts.json"
            if audit_verdicts.is_file():
                av = _load_json(audit_verdicts)
                rejected = [c for c in av.get("candidates", []) if (c.get("verdict") or "").upper() in ("REJECTED","REJECT")]
                if rejected:
                    print(f"\n[stage 6.9 · 二轮 Optuna] 检测 {len(rejected)} 个 REJECTED · 触发 re_iterate_from_audit.py")
                    re_iterate_script = PROJECT_ROOT / "稳定生产/challengers/iterative-cut-refinement-v1/scripts/re_iterate_from_audit.py"
                    if re_iterate_script.is_file():
                        cmd = [sys.executable, str(re_iterate_script), "--run-dir", str(target)]
                        if getattr(args, "nisqa_python", None):
                            cmd += ["--nisqa-python", str(args.nisqa_python)]
                        _run(cmd, check=False)
                        print(f"[stage 6.9] 二轮完成 · 结果见 {target.name}/second_round_summary.json")
                    else:
                        print(f"[stage 6.9] re_iterate_from_audit.py 不存在 · skip")
                else:
                    print(f"[stage 6.9] audit_verdicts.json 存在但无 REJECTED · skip 二轮")
            else:
                print(f"[stage 6.9] audit_verdicts.json 不存在（人审未完成）· skip 二轮 · 首轮完成即可")
        except Exception as exc:
            print(f"[stage 6.9] 二轮 Optuna 失败: {exc}; continuing")

    # Stage 6.10 · Optuna 优化参数回写 + re-render (用户 2026-08-19 明确要求)
    # · 读 refinement_trace.json · converged 候选的 final_params 回写 all_candidates.json + verified_edl.json
    # · 用户 2026-08-19 二次修订 · 只对 LLM KEEP_CUT ∩ Optuna converged 的候选 apply 参数
    #   (若 LLM 判 REJECT_KEEP · 候选本身不进 EDL · Optuna 参数无处落 · 避免误剪 + 无效 re-render)
    # · 若有交集 · 用新 EDL rerun automix · 附加 .optuna_rerendered.mp3 保存新版
    # · 若无交集 · print 提示 · 不 re-render
    if getattr(args, "auto_optuna_rerender", True):
        try:
            refine_trace = target / "refinement_trace.json"
            if refine_trace.is_file():
                # 读 llm_verdicts.json (若存在) · 只 apply LLM KEEP_CUT 的候选参数
                llm_verdicts_p_610 = target / "llm_verdicts.json"
                llm_keep_ids_610: set | None = None
                if llm_verdicts_p_610.is_file():
                    try:
                        _lv = _load_json(llm_verdicts_p_610)
                        llm_keep_ids_610 = {
                            str(v.get("candidate_id"))
                            for v in _lv.get("verdicts", [])
                            if v.get("verdict") == "KEEP_CUT"
                        }
                        print(f"[stage 6.10] LLM KEEP_CUT 候选: {len(llm_keep_ids_610)} · 只对这些 apply Optuna 参数")
                    except Exception as _exc:
                        print(f"[stage 6.10] 读 llm_verdicts.json 失败: {_exc}; fallback 到全 converged apply (向后兼容)")
                        llm_keep_ids_610 = None
                else:
                    print("[stage 6.10] llm_verdicts.json 不存在 · fallback 到全 converged apply (向后兼容)")
                rt = _load_json(refine_trace)
                results = rt.get("results", rt.get("candidates", []))
                converged_by_cid = {}
                skipped_reject = []
                for r in results:
                    if r.get("converged") and r.get("final_params"):
                        cid = r.get("candidate_id")
                        if llm_keep_ids_610 is not None and str(cid) not in llm_keep_ids_610:
                            skipped_reject.append(cid)
                            continue
                        converged_by_cid[cid] = r["final_params"]
                if skipped_reject:
                    print(f"[stage 6.10] skip 非 KEEP_CUT 的 converged 候选 {len(skipped_reject)} 个: {skipped_reject}")
                if converged_by_cid:
                    print(f"\n[stage 6.10 · Optuna re-render] {len(converged_by_cid)} converged 候选 · 参数回写 + 重渲染")
                    # 回写 verified_edl.json
                    verified_edl_p = target / "verified_edl.json"
                    if verified_edl_p.is_file():
                        vd_v = _load_json(verified_edl_p)
                        updated = 0
                        for c in vd_v.get("candidates", []):
                            cid = c.get("candidate_id")
                            if cid in converged_by_cid:
                                fp = converged_by_cid[cid]
                                for k, v in fp.items():
                                    c[k] = v
                                    if k == "crossfade_ms":
                                        c["cut_verify_crossfade_ms"] = v
                                c["params_source"] = "optuna_stage_6_10"
                                updated += 1
                        vd_v["_stage_6_10_updated_at_utc"] = _utc_now()
                        vd_v["_stage_6_10_updated_count"] = updated
                        _write_json(verified_edl_p, vd_v)
                        print(f"[stage 6.10] verified_edl.json 更新 {updated} 候选")
                    # 回写 all_candidates.json
                    all_cands_json = target / "all_candidates.json"
                    if all_cands_json.is_file():
                        ac = _load_json(all_cands_json)
                        cands_list = ac.get("candidates") if isinstance(ac, dict) else ac
                        updated_ac = 0
                        for c in cands_list or []:
                            cid = c.get("candidate_id")
                            if cid in converged_by_cid:
                                fp = converged_by_cid[cid]
                                for k, v in fp.items():
                                    c[k] = v
                                    if k == "crossfade_ms":
                                        c["cut_verify_crossfade_ms"] = v
                                c["params_source"] = "optuna_stage_6_10"
                                updated_ac += 1
                        _write_json(all_cands_json, ac)
                        print(f"[stage 6.10] all_candidates.json 更新 {updated_ac} 候选")
                    # 重新生成 EDL + 触发 automix re-render → .optuna_rerendered.mp3
                    tracks_for_mix_v2 = args.tracks_for_automix or args.from_raw_wav
                    if tracks_for_mix_v2:
                        edl_v2 = stage_edl_from_gate(target, gate_out, target / "calibration_source.json")
                        output_mp3_v2 = render_dir / f"{args.episode_id}.optuna_rerendered.mp3"
                        try:
                            stage_automix(
                                [Path(t) for t in tracks_for_mix_v2],
                                args.music, edl_v2, args.release_spec,
                                args.music_template_file, args.template_id,
                                output_mp3_v2, tmp_dir,
                            )
                            print(f"[stage 6.10] Optuna re-render 成品: {output_mp3_v2.relative_to(PROJECT_ROOT)}")
                            _write_json(target / "stage_6_10_provenance.json", {
                                "schema": "stage-6-10-provenance-v1",
                                "refinement_applied_at_utc": _utc_now(),
                                "converged_candidates": list(converged_by_cid.keys()),
                                "llm_keep_cut_filter_applied": llm_keep_ids_610 is not None,
                                "llm_keep_cut_ids": sorted(llm_keep_ids_610) if llm_keep_ids_610 else None,
                                "skipped_reject_converged": skipped_reject,
                                "rerendered_mp3": str(output_mp3_v2.relative_to(PROJECT_ROOT)),
                                "original_mp3": str(output_mp3.relative_to(PROJECT_ROOT)),
                            })
                            # 用新成品跑一次 NISQA benchmark 出 delta
                            if getattr(args, "nisqa_python", None):
                                nisqa_v2_out = target / "nisqa_benchmark_optuna_v2.json"
                                _run([
                                    str(args.nisqa_python),
                                    str(PROJECT_ROOT / "稳定生产/challengers/nisqa-cutverify-v1/scripts/check_nisqa_mos.py"),
                                    "--clip-path", str(output_mp3_v2),
                                    "--out-json", str(nisqa_v2_out),
                                    "--mode", "overall",
                                ], check=False)
                                if nisqa_v2_out.is_file():
                                    print(f"[stage 6.10] Optuna re-render NISQA: {nisqa_v2_out.relative_to(PROJECT_ROOT)}")
                        except Exception as inner:
                            print(f"[stage 6.10] automix re-render failed: {inner}; keep original mp3")
                else:
                    if llm_keep_ids_610 is not None and skipped_reject:
                        print(f"[stage 6.10] converged 候选全被 LLM 判 REJECT_KEEP · 0 交集 · skip re-render · 成品用 default")
                    else:
                        print("[stage 6.10] refinement_trace 里 0 converged · skip re-render · 用户可听默认参数版")
            else:
                print("[stage 6.10] refinement_trace.json 不存在 · skip")
        except Exception as exc:
            print(f"[stage 6.10] failed: {exc}; continuing")

    print("\n=== DONE ===")
    print(f"成品:   {output_mp3.relative_to(PROJECT_ROOT)}")
    print(f"审计:   {audit.relative_to(PROJECT_ROOT)}")
    print(f"Gate 输出: {gate_out.relative_to(PROJECT_ROOT)}/")
    print(f"EDL:    {edl.relative_to(PROJECT_ROOT)}")
    if getattr(args, "nisqa_python", None):
        print(f"NISQA:  {(target / 'NISQA_BENCHMARK.md').relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
