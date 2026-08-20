#!/usr/bin/env python3
"""check_hallucination · 用 faster-whisper word.probability 检测 filler 类候选的 ASR 幻觉.

**开源工具**：faster-whisper 自带 `word_timestamps=True` 时输出 word.probability (0-1).
**规则**：filler_hesitation / immediate_repetition 类候选 · 若匹配的 ASR word probability < 阈值 → 幻觉.
**阈值**：默认 0.6（EP04 实测 · 正常词 > 0.75 · 幻觉 < 0.5）· 可 CLI 覆盖.

**输入**：candidate (dict · 含 candidate_kind + filler_token + source_track_id + start_seconds)
       + ASR transcript dict (含 words[].{text, start_seconds, end_seconds, probability})
**输出**：{asr_word_probability, threshold, matched_word_text, verdict}

**verdict**:
  - REJECT_LOW_PROB_HALLUCINATION · prob < threshold 且是 filler 类
  - CLEAN_ASR · prob ≥ threshold
  - NO_ASR_MATCH · 该候选时间点找不到匹配的 ASR word (走 fallback)
  - NOT_APPLICABLE · 非 filler 类候选 · 本 check 不管

不改 EDL / 不改 candidate / 不改 audio / 只输出 verdict.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_PROB_THRESHOLD = 0.6
FILLER_KINDS = {"filler_hesitation", "immediate_repetition"}


def find_matching_word(words: list[dict], center_time: float,
                       expected_token: str = "",
                       tolerance_s: float = 0.05) -> dict | None:
    """在 ASR words 里找 center_time 附近的词.
    优先: token 精确匹配 · 其次时间最近."""
    best_token_match = None
    best_time_match = None
    best_time_diff = float("inf")
    for w in words:
        ws = float(w.get("start_seconds") or 0)
        we = float(w.get("end_seconds") or ws)
        if not (ws - tolerance_s <= center_time <= we + tolerance_s):
            continue
        text = str(w.get("text", "")).strip()
        # token 精确匹配 (处理繁简/空白差异宽松点)
        if expected_token and (expected_token == text or expected_token in text or text in expected_token):
            best_token_match = w
            break
        diff = abs(center_time - (ws + we) / 2)
        if diff < best_time_diff:
            best_time_diff = diff
            best_time_match = w
    return best_token_match or best_time_match


def check(candidate: dict, transcript: dict,
          prob_threshold: float = DEFAULT_PROB_THRESHOLD) -> dict:
    kind = str(candidate.get("candidate_kind") or candidate.get("kind") or "")
    if kind not in FILLER_KINDS:
        return {
            "verdict": "NOT_APPLICABLE",
            "reason": f"candidate_kind={kind!r} not in FILLER_KINDS",
            "threshold": prob_threshold,
        }

    token = str(candidate.get("filler_token") or candidate.get("proposed_delete_text") or "").strip()
    start_s = float(candidate.get("start_seconds") or 0)
    end_s = float(candidate.get("end_seconds") or start_s)
    center = (start_s + end_s) / 2

    words = transcript.get("words", [])
    match = find_matching_word(words, center, expected_token=token)
    if not match:
        return {
            "verdict": "NO_ASR_MATCH",
            "reason": f"no ASR word near t={center:.3f}s (token={token!r})",
            "threshold": prob_threshold,
            "asr_word_probability": None,
            "matched_word_text": None,
        }

    prob = match.get("probability")
    matched_text = match.get("text", "")
    if prob is None:
        return {
            "verdict": "NO_PROB_FIELD",
            "reason": "matched word has no `probability` field (ASR pipeline predates word_timestamps=True)",
            "threshold": prob_threshold,
            "matched_word_text": matched_text,
        }

    if prob < prob_threshold:
        verdict = "REJECT_LOW_PROB_HALLUCINATION"
        reason = (f"ASR word {matched_text!r} @ {match.get('start_seconds'):.3f}s "
                  f"probability {prob:.4f} < threshold {prob_threshold}. "
                  f"Whisper 在低能量段常幻觉出 filler 词 · 该候选大概率无真实语音.")
    else:
        verdict = "CLEAN_ASR"
        reason = f"probability {prob:.4f} ≥ threshold {prob_threshold} · 有真实语音支撑."

    return {
        "verdict": verdict,
        "reason": reason,
        "threshold": prob_threshold,
        "asr_word_probability": float(prob),
        "matched_word_text": matched_text,
        "matched_word_start_s": float(match.get("start_seconds") or 0),
        "matched_word_end_s": float(match.get("end_seconds") or 0),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate-json", required=True, type=Path,
                    help="candidate.json (single candidate) or candidate_source.json (with candidates[])")
    ap.add_argument("--transcript", required=True, type=Path,
                    help="ASR transcript.json for the candidate.source_track_id")
    ap.add_argument("--prob-threshold", type=float, default=DEFAULT_PROB_THRESHOLD)
    ap.add_argument("--out", type=Path, default=None,
                    help="write JSON output here (default: stdout)")
    args = ap.parse_args(argv)

    cd = json.loads(args.candidate_json.read_text(encoding="utf-8"))
    candidates = cd.get("candidates") if isinstance(cd, dict) and "candidates" in cd else [cd]
    tr = json.loads(args.transcript.read_text(encoding="utf-8"))

    results = []
    for c in candidates:
        r = check(c, tr, prob_threshold=args.prob_threshold)
        r["candidate_id"] = c.get("candidate_id")
        results.append(r)

    out = {
        "schema_version": "check-hallucination-v1",
        "prob_threshold": args.prob_threshold,
        "transcript_source": str(args.transcript),
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
