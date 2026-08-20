#!/usr/bin/env python3
"""expand_to_asr_word_boundary · 把候选 cut 范围扩展到匹配的 ASR word 完整边界.

**学到的经验**（session_feedback 2026-08-19 · rule: filler_cut_use_full_asr_word_range_plus_50ms_xfade）:
  gold cut 中间段 (如 C007 354.23-354.62 · 385ms) 会留下 filler 头尾残留.
  改用完整 ASR word 边界 (如 C007 354.08-354.76 · 680ms · '呃' 整个词).
  加 50ms 短 crossfade · 呃彻底消失且接头平滑.

**开源工具**：无（纯 python · 读 ASR transcript）
**适用**：filler_hesitation / immediate_repetition · 候选中心时间点匹配到 ASR word
**规则**：
  - 若 candidate.candidate_kind ∈ {filler_hesitation, immediate_repetition}
  - 且找到匹配的 ASR word (token 相同或时间对齐)
  - 且 ASR word 比原 gold cut 更宽（gold 是 mentor 保守值）
  - → expand: new_cut_start = asr_word.start · new_cut_end = asr_word.end
  - → set recommended_crossfade_ms = 50

**输入**：candidate + ASR transcript (含 word.probability & word.start/end)
**输出**：{expanded, original_cut, expanded_cut, matched_asr_word, expansion_delta_ms}

不改 EDL · 不改 audio · 只输出 expansion 建议.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FILLER_KINDS = {"filler_hesitation", "immediate_repetition"}


def find_matching_word(words: list[dict], center_s: float, expected_token: str = "",
                       tolerance_s: float = 0.05) -> dict | None:
    best_token = None
    best_time = None
    best_diff = float("inf")
    for w in words:
        ws = float(w.get("start_seconds") or 0)
        we = float(w.get("end_seconds") or ws)
        if not (ws - tolerance_s <= center_s <= we + tolerance_s):
            continue
        text = str(w.get("text", "")).strip()
        if expected_token and (expected_token == text or expected_token in text or text in expected_token):
            best_token = w
            break
        diff = abs(center_s - (ws + we) / 2)
        if diff < best_diff:
            best_diff = diff
            best_time = w
    return best_token or best_time


def expand(candidate: dict, transcript: dict) -> dict:
    kind = str(candidate.get("candidate_kind") or candidate.get("kind") or "")
    if kind not in FILLER_KINDS:
        return {
            "expanded": False,
            "reason": f"candidate_kind={kind!r} not in FILLER_KINDS · skip",
        }

    cut_start_s = float(candidate.get("start_seconds") or 0)
    cut_end_s = float(candidate.get("end_seconds") or cut_start_s)
    center = (cut_start_s + cut_end_s) / 2
    token = str(candidate.get("filler_token") or candidate.get("proposed_delete_text") or "").strip()

    match = find_matching_word(transcript.get("words", []), center, expected_token=token)
    if not match:
        return {
            "expanded": False,
            "reason": f"no ASR word matched at center={center:.3f}s (token={token!r})",
            "original_cut": {"start_seconds": cut_start_s, "end_seconds": cut_end_s},
        }

    asr_start = float(match.get("start_seconds") or 0)
    asr_end = float(match.get("end_seconds") or asr_start)

    # 只在 ASR word 覆盖 gold cut 的情况下扩展 (避免向反方向"缩")
    if asr_start > cut_start_s + 0.02 or asr_end < cut_end_s - 0.02:
        # ASR window 在 gold 内部 (罕见) · 或与 gold 不匹配 · 走 fallback 不扩展
        return {
            "expanded": False,
            "reason": f"ASR word [{asr_start:.3f},{asr_end:.3f}] 不完全覆盖 gold cut [{cut_start_s:.3f},{cut_end_s:.3f}] · 不扩展",
            "original_cut": {"start_seconds": cut_start_s, "end_seconds": cut_end_s},
            "matched_asr_word": {"text": match.get("text"), "start_seconds": asr_start, "end_seconds": asr_end,
                                 "probability": match.get("probability")},
        }

    expansion_head_ms = (cut_start_s - asr_start) * 1000
    expansion_tail_ms = (asr_end - cut_end_s) * 1000
    total_expansion_ms = expansion_head_ms + expansion_tail_ms

    return {
        "expanded": True,
        "reason": (f"filler '{token}' 匹配 ASR word · 扩展到 word 完整边界: "
                   f"head +{expansion_head_ms:.0f}ms · tail +{expansion_tail_ms:.0f}ms · total +{total_expansion_ms:.0f}ms"),
        "original_cut": {"start_seconds": cut_start_s, "end_seconds": cut_end_s,
                         "duration_ms": (cut_end_s - cut_start_s) * 1000},
        "expanded_cut": {"start_seconds": asr_start, "end_seconds": asr_end,
                         "duration_ms": (asr_end - asr_start) * 1000},
        "matched_asr_word": {"text": match.get("text"), "start_seconds": asr_start, "end_seconds": asr_end,
                             "probability": match.get("probability")},
        "expansion_head_ms": round(expansion_head_ms, 1),
        "expansion_tail_ms": round(expansion_tail_ms, 1),
        "recommended_crossfade_ms": 50,   # 学到的经验
        "recommended_curve": "tri",
        "source_rule": "session_feedback:filler_cut_use_full_asr_word_range_plus_50ms_xfade (2026-08-19)",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate-json", required=True, type=Path)
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    cd = json.loads(args.candidate_json.read_text(encoding="utf-8"))
    candidates = cd.get("candidates") if isinstance(cd, dict) and "candidates" in cd else [cd]
    tr = json.loads(args.transcript.read_text(encoding="utf-8"))

    results = []
    for c in candidates:
        r = expand(c, tr)
        r["candidate_id"] = c.get("candidate_id")
        results.append(r)

    out = {
        "schema_version": "expand-to-asr-word-boundary-v1",
        "rule_source": "session_feedback:filler_cut_use_full_asr_word_range_plus_50ms_xfade",
        "candidate_count": len(candidates),
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
