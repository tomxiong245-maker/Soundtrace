#!/usr/bin/env python3
"""verify_cut_plan · cut-verify skill 的整合入口.

一次调用 4 个 check · 输出 verified_edl.json 侧车:
  1. check_hallucination · 每个 filler 候选查 ASR word.probability
  2. check_silence_location · pydub.silence 判剪口位置
  3. check_rhythm_gap · 剪后 gap 与 cut_parameters 阈值比对
  4. route_crossfade_strategy · 决定 butt splice / crossfade / human_review / remove

**输入**：
  --candidate-json  candidate_source.json 或 EDL 里的 actions 列表
  --transcript-dir  三轨 ASR 目录 (含 track_XX.transcript.json)
  --raw-track-map   JSON: {"track_01": "path/to/Tr1.WAV", ...}
  --cut-params-json main/knowledge/cut_parameters.json (可选 · 默认路径查)
  --out             输出 verified_edl.json (默认 <run_dir>/verified_edl.json)

**输出 schema**：cut-verify-v1
  {
    schema_version: "cut-verify-v1",
    candidates: [{
      candidate_id, current_cut, checks: {hallucination, silence_location, rhythm_gap, crossfade_strategy},
      overall_verdict, recommended_params, reasoning
    }]
  }

**overall_verdict 融合**（先 hard reject · 再 soft warning · 再 OK）:
  - REJECT_HALLUCINATION    · P1 (幻觉)
  - REJECT_INVALID_CUT      · P2 (吃邻词)
  - NEEDS_HUMAN_REVIEW      · P3 (抢话) / P6 (内容区)
  - CLEAN_BUTT_SPLICE       · P4 (静音段 · butt splice)
  - CLEAN_SHORT_CROSSFADE   · P5 (边界 · 50ms xfade)

不改 EDL · 不改 audio · 不改 session_feedback · 只写侧车.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
SCRIPTS = SKILL_DIR
PROJECT_ROOT = SKILL_DIR.parents[2]

# 用 miniforge3 python (pydub / librosa 装在这)
MINIFORGE_PYTHON = Path.home() / "miniforge3" / "bin" / "python"


def _run_check(script_name: str, args: list[str], use_miniforge: bool = False) -> dict:
    py = str(MINIFORGE_PYTHON) if use_miniforge and MINIFORGE_PYTHON.exists() else sys.executable
    cmd = [py, str(SCRIPTS / script_name)] + args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{script_name} failed: {proc.stderr}")
    # if --out was used, stdout may be empty; caller should read from --out
    if proc.stdout.strip():
        return json.loads(proc.stdout)
    return {}


def verify(candidate_json: Path, transcript_dir: Path, raw_track_map: dict,
           cut_params_json: Path, tmp_dir: Path) -> dict:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cd = json.loads(candidate_json.read_text(encoding="utf-8"))
    candidates = cd.get("candidates") if isinstance(cd, dict) and "candidates" in cd else [cd]

    # 逐候选 · 按 source_track_id 分组跑
    per_cand_files: list[tuple[dict, dict]] = []  # (candidate, checks_dict)

    for c in candidates:
        cid = c.get("candidate_id") or "?"
        track = c.get("source_track_id") or c.get("track_id") or "track_01"
        transcript_path = transcript_dir / f"{track}.transcript.json"
        raw_wav = raw_track_map.get(track)

        # 单候选 wrapper
        single_json = tmp_dir / f"{cid}.candidate.json"
        single_json.write_text(json.dumps({"candidates": [c]}, ensure_ascii=False), encoding="utf-8")

        checks = {}

        # Check 1 · hallucination (sys python)
        h_out = tmp_dir / f"{cid}.hallucination.json"
        _run_check("check_hallucination.py", [
            "--candidate-json", str(single_json),
            "--transcript", str(transcript_path),
            "--out", str(h_out),
        ])
        checks["hallucination"] = json.loads(h_out.read_text(encoding="utf-8"))["results"][0]

        # Check 2 · silence_location (miniforge python for pydub)
        s_out = tmp_dir / f"{cid}.silence.json"
        if raw_wav and Path(raw_wav).is_file():
            _run_check("check_silence_location.py", [
                "--candidate-json", str(single_json),
                "--raw-wav", str(raw_wav),
                "--out", str(s_out),
            ], use_miniforge=True)
            checks["silence_location"] = json.loads(s_out.read_text(encoding="utf-8"))["results"][0]
        else:
            checks["silence_location"] = {"verdict": "SKIPPED_NO_RAW_WAV",
                                          "reason": f"raw wav for {track} not found"}

        # Check 3 · rhythm_gap
        r_out = tmp_dir / f"{cid}.rhythm.json"
        _run_check("check_rhythm_gap.py", [
            "--candidate-json", str(single_json),
            "--transcript", str(transcript_path),
            "--cut-params-json", str(cut_params_json),
            "--out", str(r_out),
        ])
        checks["rhythm_gap"] = json.loads(r_out.read_text(encoding="utf-8"))["results"][0]

        # Check 4 · crossfade_strategy (需要 3 项 check 的 wrapper 结构)
        for k, path in [("hallucination", h_out), ("silence", s_out), ("rhythm", r_out)]:
            pass  # 已经写好

        route_out = tmp_dir / f"{cid}.route.json"
        _run_check("route_crossfade_strategy.py", [
            "--hallucination-json", str(h_out),
            "--silence-json", str(s_out) if raw_wav else str(h_out),  # 缺 silence 时 fallback
            "--rhythm-json", str(r_out),
            "--out", str(route_out),
        ])
        if s_out.exists():
            checks["crossfade_strategy"] = json.loads(route_out.read_text(encoding="utf-8"))["results"][0]
        else:
            checks["crossfade_strategy"] = {"strategy": "CROSSFADE_50MS_FALLBACK",
                                            "recommended_crossfade_ms": 50,
                                            "priority_level": "P7"}

        per_cand_files.append((c, checks))

    # ---- 融合 overall_verdict ----
    def fuse(checks: dict) -> tuple[str, str]:
        strategy = checks.get("crossfade_strategy", {}).get("strategy", "")
        priority = checks.get("crossfade_strategy", {}).get("priority_level", "")
        why = checks.get("crossfade_strategy", {}).get("why", "")
        if strategy == "REMOVE_FROM_EDL":
            if priority == "P1":
                return "REJECT_HALLUCINATION", why
            elif priority == "P2":
                return "REJECT_INVALID_CUT", why
        if strategy == "NEEDS_HUMAN_REVIEW":
            return "NEEDS_HUMAN_REVIEW", why
        if strategy == "CROSSFADE_100MS_HUMAN_REVIEW":
            return "NEEDS_HUMAN_REVIEW", why
        if strategy == "BUTT_SPLICE":
            return "CLEAN_BUTT_SPLICE", why
        if strategy in ("CROSSFADE_50MS", "CROSSFADE_50MS_FALLBACK"):
            return "CLEAN_SHORT_CROSSFADE", why
        return "UNKNOWN", why

    result_candidates = []
    for c, checks in per_cand_files:
        overall, why = fuse(checks)
        rec = {
            "candidate_id": c.get("candidate_id"),
            "current_cut": {
                "start_seconds": c.get("start_seconds"),
                "end_seconds": c.get("end_seconds"),
                "track_id": c.get("source_track_id") or c.get("track_id"),
                "kind": c.get("candidate_kind") or c.get("kind"),
                "token": c.get("filler_token") or c.get("proposed_delete_text"),
            },
            "checks": checks,
            "overall_verdict": overall,
            "reasoning": why,
            "recommended_params": {
                "crossfade_ms": checks.get("crossfade_strategy", {}).get("recommended_crossfade_ms"),
                "room_tone_pad_ms": checks.get("crossfade_strategy", {}).get("recommended_room_tone_pad_ms"),
                "strategy": checks.get("crossfade_strategy", {}).get("strategy"),
            },
        }
        result_candidates.append(rec)

    return {
        "schema_version": "cut-verify-v1",
        "candidate_source": str(candidate_json),
        "transcript_dir": str(transcript_dir),
        "raw_track_map": {k: str(v) for k, v in raw_track_map.items()},
        "cut_parameters_source": str(cut_params_json),
        "candidates": result_candidates,
        "summary": {
            "total": len(result_candidates),
            "reject_hallucination": sum(1 for r in result_candidates if r["overall_verdict"] == "REJECT_HALLUCINATION"),
            "reject_invalid": sum(1 for r in result_candidates if r["overall_verdict"] == "REJECT_INVALID_CUT"),
            "human_review": sum(1 for r in result_candidates if r["overall_verdict"] == "NEEDS_HUMAN_REVIEW"),
            "clean_butt_splice": sum(1 for r in result_candidates if r["overall_verdict"] == "CLEAN_BUTT_SPLICE"),
            "clean_crossfade": sum(1 for r in result_candidates if r["overall_verdict"] == "CLEAN_SHORT_CROSSFADE"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate-json", required=True, type=Path)
    ap.add_argument("--transcript-dir", required=True, type=Path)
    ap.add_argument("--raw-track-map", required=True, type=str,
                    help='JSON string · e.g. \'{"track_01":"/abs/path/Tr1.WAV",...}\'')
    ap.add_argument("--cut-params-json", type=Path,
                    default=PROJECT_ROOT / "main/knowledge/cut_parameters.json")
    ap.add_argument("--tmp-dir", type=Path, default=Path("/tmp/cut_verify"))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    raw_track_map = json.loads(args.raw_track_map)

    result = verify(
        candidate_json=args.candidate_json,
        transcript_dir=args.transcript_dir,
        raw_track_map=raw_track_map,
        cut_params_json=args.cut_params_json,
        tmp_dir=args.tmp_dir,
    )

    out_path = args.out or args.candidate_json.parent / "verified_edl.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"summary: {json.dumps(result['summary'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
