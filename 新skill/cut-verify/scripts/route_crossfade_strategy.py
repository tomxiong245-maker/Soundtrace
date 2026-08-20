#!/usr/bin/env python3
"""route_crossfade_strategy · 根据前 3 项 check 结果决定拼接策略.

**开源工具**：无（纯 policy 路由）
**规则**：
  优先级 P1 > P2 > P3 > P4:
  P1 · Check 1 REJECT_LOW_PROB_HALLUCINATION → strategy=REMOVE_FROM_EDL (不该剪)
  P2 · Check 3 RHYTHM_INVALID_NEGATIVE_GAP → strategy=REMOVE_FROM_EDL (会吃邻词)
  P3 · Check 3 RHYTHM_TOO_TIGHT → strategy=NEEDS_HUMAN_REVIEW (可能抢话)
  P4 · Check 2 CUT_IN_SILENCE_BUTT_SPLICE_OK → strategy=BUTT_SPLICE (crossfade=0 + 10ms room tone)
  P5 · Check 2 CUT_SPANS_BOUNDARY → strategy=CROSSFADE_50MS (短 xfade 平滑)
  P6 · Check 2 CUT_IN_CONTENT_ZONE → strategy=CROSSFADE_100MS_HUMAN_REVIEW (mask cut · 但需人审)
  P7 · fallback → strategy=CROSSFADE_50MS (默认)

**输入**：3 项 check 的完整结果 dict
**输出**：{strategy, recommended_crossfade_ms, recommended_room_tone_pad_ms, priority_level, why}

**注意**：
  - 输出仅是**建议** · 不改 EDL · 不改音频
  - HUMAN_REVIEW 类需要人耳听审 · 不能 auto-apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def route(check_hallucination: dict, check_silence: dict, check_rhythm: dict) -> dict:
    hallu_v = check_hallucination.get("verdict")
    silence_v = check_silence.get("verdict")
    rhythm_v = check_rhythm.get("verdict")

    # P1: 幻觉 → 移除
    if hallu_v == "REJECT_LOW_PROB_HALLUCINATION":
        return {
            "strategy": "REMOVE_FROM_EDL",
            "recommended_crossfade_ms": None,
            "recommended_room_tone_pad_ms": None,
            "priority_level": "P1",
            "why": f"Check 1 (幻觉): {check_hallucination.get('reason','')}",
        }

    # P2: 剪超过 raw gap → 会吃邻词
    if rhythm_v == "RHYTHM_INVALID_NEGATIVE_GAP":
        return {
            "strategy": "REMOVE_FROM_EDL",
            "recommended_crossfade_ms": None,
            "recommended_room_tone_pad_ms": None,
            "priority_level": "P2",
            "why": f"Check 3 (节奏): {check_rhythm.get('reason','')}",
        }

    # P3: 剪后 gap 抢话
    if rhythm_v == "RHYTHM_TOO_TIGHT":
        return {
            "strategy": "NEEDS_HUMAN_REVIEW",
            "recommended_crossfade_ms": 50,
            "recommended_room_tone_pad_ms": 0,
            "priority_level": "P3",
            "why": f"Check 3 (节奏): {check_rhythm.get('reason','')}",
        }

    # P4: 静音段 butt splice
    if silence_v == "CUT_IN_SILENCE_BUTT_SPLICE_OK":
        return {
            "strategy": "BUTT_SPLICE",
            "recommended_crossfade_ms": 0,
            "recommended_room_tone_pad_ms": 10,
            "priority_level": "P4",
            "why": f"Check 2 (静音): {check_silence.get('reason','')}",
        }

    # P5: 边界跨越
    if silence_v == "CUT_SPANS_BOUNDARY_NEEDS_CROSSFADE":
        return {
            "strategy": "CROSSFADE_50MS",
            "recommended_crossfade_ms": 50,
            "recommended_room_tone_pad_ms": 0,
            "priority_level": "P5",
            "why": f"Check 2 (边界): {check_silence.get('reason','')}",
        }

    # P6: 全在内容区 · 高风险
    if silence_v == "CUT_IN_CONTENT_ZONE":
        return {
            "strategy": "CROSSFADE_100MS_HUMAN_REVIEW",
            "recommended_crossfade_ms": 100,
            "recommended_room_tone_pad_ms": 0,
            "priority_level": "P6",
            "why": f"Check 2 (内容): {check_silence.get('reason','')} · 建议人审",
        }

    # P7: fallback
    return {
        "strategy": "CROSSFADE_50MS",
        "recommended_crossfade_ms": 50,
        "recommended_room_tone_pad_ms": 0,
        "priority_level": "P7",
        "why": f"fallback · check verdicts: hallu={hallu_v} silence={silence_v} rhythm={rhythm_v}",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hallucination-json", required=True, type=Path)
    ap.add_argument("--silence-json", required=True, type=Path)
    ap.add_argument("--rhythm-json", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    hd = json.loads(args.hallucination_json.read_text(encoding="utf-8"))
    sd = json.loads(args.silence_json.read_text(encoding="utf-8"))
    rd = json.loads(args.rhythm_json.read_text(encoding="utf-8"))
    h_by_id = {r["candidate_id"]: r for r in hd.get("results", [])}
    s_by_id = {r["candidate_id"]: r for r in sd.get("results", [])}
    r_by_id = {r["candidate_id"]: r for r in rd.get("results", [])}

    all_ids = set(h_by_id) | set(s_by_id) | set(r_by_id)
    results = []
    for cid in sorted(all_ids):
        r = route(
            h_by_id.get(cid, {"verdict": "NO_HALLU_CHECK"}),
            s_by_id.get(cid, {"verdict": "NO_SILENCE_CHECK"}),
            r_by_id.get(cid, {"verdict": "NO_RHYTHM_CHECK"}),
        )
        r["candidate_id"] = cid
        results.append(r)

    out = {
        "schema_version": "route-crossfade-strategy-v1",
        "candidate_count": len(results),
        "results": results,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
