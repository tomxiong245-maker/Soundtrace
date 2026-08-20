#!/usr/bin/env python3
"""analyze_cut_plans · experience-driven-review skill 的量化前端.

对 run 内 EDL 的每条 cut · 提 6 个 PARAMETER 指标 · 匹配 mentor gold ·
检索相关 PARAMETER 规则 · 输出 cut_plan_diff.json 供 LLM 消费.

Usage:
  python3 analyze_cut_plans.py \\
    --run-dir main/runs/EP04-AUDIT-ALL-20260818 \\
    --gold-edl main/runs/EP04-GOLD-EDL-20260818-1548/gold_edl.json \\
    --ep-id EP04 \\
    --transcript-dir main/runs/EP04/EP04-v13-20260813-2002/analysis \\
    --out main/runs/EP04-AUDIT-ALL-20260818/cut_plan_diff.json

不写 EDL / 不改 session_feedback / 不改 EDL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "main"))

try:
    from orchestrator.feedback_engine import retrieve_before_decision, load_cut_parameters
except Exception as exc:
    print(f"[warn] feedback_engine import failed: {exc}", file=sys.stderr)
    retrieve_before_decision = None
    load_cut_parameters = None


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_transcript_words(transcript_dir: Path) -> dict[str, list[dict]]:
    """三轨 ASR words · key=track_id."""
    result: dict[str, list[dict]] = {}
    if not transcript_dir or not transcript_dir.is_dir():
        return result
    for p in sorted(transcript_dir.glob("track_*.transcript.json")):
        tid = p.stem.replace(".transcript", "")
        d = _load(p)
        result[tid] = d.get("words", [])
    return result


def _find_gap_before_after(words: list[dict], cut_start_s: float, cut_end_s: float) -> tuple[float | None, float | None, dict | None, dict | None]:
    """返回 (gap_before_ms, gap_after_ms, prev_word, next_word)."""
    prev_word, next_word = None, None
    for w in words:
        ws = float(w.get("start_seconds") or 0)
        we = float(w.get("end_seconds") or ws)
        if we <= cut_start_s + 0.001:
            prev_word = w
        elif ws >= cut_end_s - 0.001 and next_word is None:
            next_word = w
            break
    gb = (cut_start_s - float(prev_word.get("end_seconds") or 0)) * 1000 if prev_word else None
    ga = (float(next_word.get("start_seconds") or 0) - cut_end_s) * 1000 if next_word else None
    return gb, ga, prev_word, next_word


def analyze(run_dir: Path, gold_edl_path: Path | None, ep_id: str,
            transcript_dir: Path | None, out_path: Path) -> dict:
    # ---- 1. 读 EDL ----
    edl_paths = [run_dir / "machine_assisted_draft.edl.json", run_dir / "human_approved.edl.json"]
    edl_path = next((p for p in edl_paths if p.is_file()), None)
    if not edl_path:
        raise SystemExit(f"no EDL in {run_dir}")
    edl = _load(edl_path)
    sr = int(edl.get("sample_rate_hz") or 48000)
    actions = edl.get("actions") or edl.get("global_sync_actions") or []
    render_cuts_by_aid = {}
    for rc in edl.get("render_sync_cuts") or []:
        for aid in rc.get("source_action_ids") or []:
            render_cuts_by_aid[aid] = rc

    # ---- 2. 读 mentor gold (可选) ----
    gold_by_cid: dict[str, dict] = {}
    mentor_meta: dict = {}
    if gold_edl_path and gold_edl_path.is_file():
        gedl = _load(gold_edl_path)
        for g in gedl.get("gold_cuts", []):
            gold_by_cid[g.get("candidate_id")] = g
        mentor_meta = gedl.get("learned_mentor_rules", {}).get("mentor_metadata", {})

    # ---- 3. 读 ASR words ----
    all_words = _load_transcript_words(transcript_dir) if transcript_dir else {}

    # ---- 4. 读 cut_parameters ----
    cut_params_defaults = {}
    if load_cut_parameters:
        try:
            cut_params_defaults = load_cut_parameters()
        except Exception as exc:
            print(f"[warn] load_cut_parameters failed: {exc}", file=sys.stderr)

    # ---- 5. 读 candidate_source (拿 track / kind / token) ----
    cand_source_path = run_dir / "candidate_source.json"
    all_cand_path = run_dir / "all_candidates.json"
    src = None
    for p in (cand_source_path, all_cand_path):
        if p.is_file():
            src = _load(p)
            break
    cands_by_id: dict[str, dict] = {}
    if src:
        cs = src.get("candidates") or (src if isinstance(src, list) else [])
        for c in cs:
            cid = c.get("candidate_id")
            if cid:
                cands_by_id[cid] = c

    # ---- 6. 逐候选量化 ----
    out_cands = []
    for act in actions:
        if act.get("action_type") and act.get("action_type") != "global_sync_cut":
            continue
        aid = str(act.get("action_id") or "")
        cid = str(act.get("candidate_id") or "")
        start_sample = int(act.get("start_sample") or 0)
        end_sample = int(act.get("end_sample") or 0)
        start_s = start_sample / sr
        end_s = end_sample / sr
        dur_ms = (end_s - start_s) * 1000

        cand = cands_by_id.get(cid, {})
        kind = cand.get("candidate_kind") or cand.get("kind") or "?"
        track = cand.get("source_track_id") or cand.get("track_id") or ""
        token = (cand.get("filler_token") or cand.get("proposed_delete_text")
                 or (cand.get("abandoned_span") or {}).get("text") or "")

        rc = render_cuts_by_aid.get(aid, {})
        xf_samples = int(rc.get("crossfade_samples") or 0)
        pause_samples = int(rc.get("insert_silence_samples") or 0)
        crossfade_ms = xf_samples / sr * 1000
        post_pause_ms = pause_samples / sr * 1000

        # gap_before / gap_after
        gap_before = gap_after = None
        prev_word = next_word = None
        if track and track in all_words:
            gap_before, gap_after, prev_word, next_word = _find_gap_before_after(
                all_words[track], start_s, end_s)

        # gold reference
        gold = gold_by_cid.get(cid)
        gold_ref = None
        if gold:
            gold_dur = float(gold.get("duration_ms") or 0)
            gold_pause = float(gold.get("pause_ms_in_gold") or 0)
            gold_ref = {
                "matched_by": "candidate_id",
                "gold_cut_id": gold.get("gold_cut_id"),
                "gold_start_seconds": gold.get("start_seconds"),
                "gold_end_seconds": gold.get("end_seconds"),
                "gold_duration_ms": gold_dur,
                "pause_ms_in_gold": gold_pause,
                "duration_delta_ms": dur_ms - gold_dur,  # 正=过剪
                "pause_delta_ms": post_pause_ms - gold_pause,
            }

        # retrieve PARAMETER rules
        retrieved = []
        if retrieve_before_decision:
            try:
                cand_probe = {
                    "candidate_kind": kind, "kind": kind,
                    "reason_key": cand.get("reason_key") or kind,
                    "filler_token": token,
                    "proposed_delete_text": token,
                    "source_track_id": track,
                }
                # 先查 PARAMETER 层
                rules_p = retrieve_before_decision(
                    cand_probe, decision_type="cut_boundary",
                    episode_id=ep_id, max_return=5, knowledge_category="PARAMETER")
                for r in rules_p:
                    retrieved.append({
                        "kind": r.get("kind"),
                        "verdict": r.get("verdict"),
                        "match_score": r.get("match_score"),
                        "knowledge_category": r.get("knowledge_category"),
                        "case_ids": r.get("case_ids") or r.get("case_refs") or [],
                        "note": (r.get("note") or "")[:200],
                    })
            except Exception as exc:
                print(f"[warn] retrieve failed for {cid}: {exc}", file=sys.stderr)

        # 简易 gap_analysis
        ga = {}
        if gold_ref:
            ga["boundary_over_cuts_by_ms"] = gold_ref["duration_delta_ms"]
            ga["pause_extra_ms"] = gold_ref["pause_delta_ms"]
        # target zone from cut_parameters
        target = (cut_params_defaults.get("how_to_cut_defaults") or {})
        gb_target = (target.get("gap_before_ms") or {}).get("target_range") or [120, 300]
        ga_target = (target.get("gap_after_ms") or {}).get("target_range") or [120, 450]
        if gap_before is not None:
            ga["gap_before_ms"] = round(gap_before, 1)
            ga["gap_before_in_target_zone"] = gb_target[0] <= gap_before <= gb_target[1]
        if gap_after is not None:
            ga["gap_after_ms"] = round(gap_after, 1)
            ga["gap_after_in_target_zone"] = ga_target[0] <= gap_after <= ga_target[1]
        if gap_before is not None and gap_after is not None:
            ga["tail_heavier"] = gap_after >= 0.9 * gap_before

        # §19 邻词保护警告
        prev_end_gap = (start_s - float(prev_word.get("end_seconds") or 0)) * 1000 if prev_word else None
        next_start_gap = (float(next_word.get("start_seconds") or 0) - end_s) * 1000 if next_word else None
        neighbor_risk = False
        if prev_end_gap is not None and prev_end_gap < 20:
            neighbor_risk = True
            ga["prev_word_risk_lt_20ms"] = round(prev_end_gap, 1)
        if next_start_gap is not None and next_start_gap < 20:
            neighbor_risk = True
            ga["next_word_risk_lt_20ms"] = round(next_start_gap, 1)

        out_cands.append({
            "candidate_id": cid,
            "action_id": aid,
            "kind": kind,
            "track": track,
            "token": token,
            "current_plan": {
                "start_seconds": round(start_s, 3),
                "end_seconds": round(end_s, 3),
                "cut_duration_ms": round(dur_ms, 1),
                "gap_before_ms": round(gap_before, 1) if gap_before is not None else None,
                "gap_after_ms": round(gap_after, 1) if gap_after is not None else None,
                "crossfade_ms": round(crossfade_ms, 1),
                "post_cut_pause_ms": round(post_pause_ms, 1),
                "prev_word_text": (prev_word or {}).get("text"),
                "next_word_text": (next_word or {}).get("text"),
            },
            "gold_reference": gold_ref,
            "gap_analysis": ga,
            "applied_rules": retrieved,
            "recommended_plan": None,   # LLM 填
            "confidence": None,          # LLM 填
            "reasoning": None,           # LLM 填
            "cited_case_ids": [],        # LLM 填
        })

    result = {
        "schema_version": "cut-plan-diff-v1",
        "run_dir": str(run_dir.relative_to(PROJECT_ROOT)) if str(run_dir).startswith(str(PROJECT_ROOT)) else str(run_dir),
        "episode_id": ep_id,
        "edl_path": str(edl_path.relative_to(PROJECT_ROOT)) if str(edl_path).startswith(str(PROJECT_ROOT)) else str(edl_path),
        "gold_edl_path": str(gold_edl_path.relative_to(PROJECT_ROOT)) if gold_edl_path and str(gold_edl_path).startswith(str(PROJECT_ROOT)) else None,
        "cut_parameters_source": "feedback_engine.load_cut_parameters()",
        "mentor_metadata": mentor_meta,
        "candidates": out_cands,
        "next_step_for_llm": "填充 recommended_plan / confidence / reasoning / cited_case_ids · 引用 applied_rules 里的 kind 或 gold_reference.gold_cut_id · 遵守 §17/§19/§21 硬边界",
    }
    _dump(out_path, result)
    print(f"wrote {out_path} · {len(out_cands)} candidates analyzed", file=sys.stderr)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--gold-edl", type=Path, default=None,
                    help="mentor gold_edl.json (可选 · 缺省不做 gold 对比)")
    ap.add_argument("--ep-id", default="EP04")
    ap.add_argument("--transcript-dir", type=Path, default=None,
                    help="ASR analysis 目录 · 用于 gap_before / gap_after 计算")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    run_dir = args.run_dir.expanduser().resolve()
    gold = args.gold_edl.expanduser().resolve() if args.gold_edl else None
    tr = args.transcript_dir.expanduser().resolve() if args.transcript_dir else None
    out = args.out.expanduser().resolve() if args.out else (run_dir / "cut_plan_diff.json")
    analyze(run_dir, gold, args.ep_id, tr, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
