#!/usr/bin/env python3
"""check_rhythm_gap · 剪后 gap_before/gap_after 是否落 cut_parameters.json target_range 内.

**开源工具**：无（纯 Python 计算 + 读 cut_parameters.json）
**规则**：
  1. 从 ASR transcript 找 candidate.source_track_id 上 cut_start 前一词 prev_word · cut_end 后一词 next_word
  2. raw_gap_ms = (next_word.start - prev_word.end) * 1000
  3. cut_duration_ms = (cut_end - cut_start) * 1000
  4. post_cut_gap_ms = raw_gap_ms - cut_duration_ms  (剪后剩余的自然停顿)
  5. 与 cut_parameters.json 的 gap_before/gap_after target_range 比对

**verdict**:
  - RHYTHM_OK        · target_min ≤ post_cut_gap ≤ target_max
  - RHYTHM_TOO_TIGHT · post_cut_gap < target_min · "抢话" · 用户会觉得不干净
  - RHYTHM_TOO_LOOSE · post_cut_gap > target_max · 太拖沓 · 建议 · 剪更多
  - RHYTHM_INSUFFICIENT_ASR · 找不到 prev/next word (静音段边缘) · 走 fallback

**默认阈值**（来自 main/knowledge/cut_parameters.json · 59 gold cut 反推）:
  - target_min = 120ms (gap_before.hard_reject_below · gap_after.target_range[0])
  - target_max = 450ms (gap_after.target_range[1])
  - median = 180-280ms (自然分布中位)

不改 EDL / 不改 audio / 只输出 verdict.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CUT_PARAMS_PATH = "main/knowledge/cut_parameters.json"
FALLBACK_TARGET_MIN_MS = 120
FALLBACK_TARGET_MAX_MS = 450
FALLBACK_TARGET_MEDIAN_MS = 200


def load_thresholds(cut_params_path: Path) -> dict:
    if cut_params_path.is_file():
        d = json.loads(cut_params_path.read_text(encoding="utf-8"))
        htc = d.get("how_to_cut_defaults", {})
        gb = htc.get("gap_before_ms", {})
        ga = htc.get("gap_after_ms", {})
        return {
            "target_min_ms": min(
                gb.get("target_range", [FALLBACK_TARGET_MIN_MS, 0])[0],
                ga.get("target_range", [FALLBACK_TARGET_MIN_MS, 0])[0],
            ),
            "target_max_ms": max(
                gb.get("target_range", [0, FALLBACK_TARGET_MAX_MS])[1],
                ga.get("target_range", [0, FALLBACK_TARGET_MAX_MS])[1],
            ),
            "median_before_ms": gb.get("median_observed", FALLBACK_TARGET_MEDIAN_MS),
            "median_after_ms": ga.get("median_observed", FALLBACK_TARGET_MEDIAN_MS),
            "hard_reject_below_ms": gb.get("hard_reject_below_and_not_in_silence",
                                            FALLBACK_TARGET_MIN_MS // 2),
            "source": str(cut_params_path),
        }
    return {
        "target_min_ms": FALLBACK_TARGET_MIN_MS,
        "target_max_ms": FALLBACK_TARGET_MAX_MS,
        "median_before_ms": FALLBACK_TARGET_MEDIAN_MS,
        "median_after_ms": FALLBACK_TARGET_MEDIAN_MS,
        "hard_reject_below_ms": FALLBACK_TARGET_MIN_MS // 2,
        "source": "FALLBACK_HARDCODED",
    }


def find_prev_next_word(words: list[dict], cut_start_s: float, cut_end_s: float,
                        exclude_range: tuple[float, float] | None = None) -> tuple[dict | None, dict | None]:
    """在 ASR words 里找 cut_start 之前最近的词 · cut_end 之后最近的词.
    exclude_range: 排除这个范围内的词 (用于 skip 剪口内部的词)."""
    prev_word = None
    next_word = None
    for w in words:
        ws = float(w.get("start_seconds") or 0)
        we = float(w.get("end_seconds") or ws)
        # 剪口内部的词跳过
        if exclude_range and exclude_range[0] - 0.01 <= ws and we <= exclude_range[1] + 0.01:
            continue
        if we <= cut_start_s + 0.001:
            prev_word = w
        elif ws >= cut_end_s - 0.001 and next_word is None:
            next_word = w
            break
    return prev_word, next_word


def check(candidate: dict, transcript: dict, thresholds: dict) -> dict:
    cut_start_s = float(candidate.get("start_seconds") or 0)
    cut_end_s = float(candidate.get("end_seconds") or cut_start_s)
    cut_duration_ms = (cut_end_s - cut_start_s) * 1000

    words = transcript.get("words", [])
    prev_word, next_word = find_prev_next_word(
        words, cut_start_s, cut_end_s, exclude_range=(cut_start_s, cut_end_s)
    )

    if not prev_word or not next_word:
        return {
            "verdict": "RHYTHM_INSUFFICIENT_ASR",
            "reason": "ASR 找不到 cut 前后邻词 (可能在音频首尾)",
            "cut_duration_ms": cut_duration_ms,
            "prev_word": prev_word,
            "next_word": next_word,
            "thresholds": thresholds,
        }

    prev_end_s = float(prev_word.get("end_seconds") or 0)
    next_start_s = float(next_word.get("start_seconds") or 0)
    raw_gap_ms = (next_start_s - prev_end_s) * 1000
    post_cut_gap_ms = raw_gap_ms - cut_duration_ms

    tgt_min = thresholds["target_min_ms"]
    tgt_max = thresholds["target_max_ms"]

    if post_cut_gap_ms < 0:
        verdict = "RHYTHM_INVALID_NEGATIVE_GAP"
        reason = f"cut_duration_ms={cut_duration_ms:.0f} > raw_gap_ms={raw_gap_ms:.0f} · 剪超过了自然停顿 · 会吃邻词"
    elif post_cut_gap_ms < tgt_min:
        verdict = "RHYTHM_TOO_TIGHT"
        reason = (f"剪后 gap={post_cut_gap_ms:.0f}ms < target_min={tgt_min}ms · "
                  f"'{prev_word.get('text','?')}' 和 '{next_word.get('text','?')}' 之间会有 '抢话' 感 · "
                  f"用户感知'不干净' 常来自此")
    elif post_cut_gap_ms > tgt_max:
        verdict = "RHYTHM_TOO_LOOSE"
        reason = (f"剪后 gap={post_cut_gap_ms:.0f}ms > target_max={tgt_max}ms · "
                  f"依然拖沓 · 建议 · 扩剪 {post_cut_gap_ms - thresholds['median_after_ms']:.0f}ms")
    else:
        verdict = "RHYTHM_OK"
        reason = (f"剪后 gap={post_cut_gap_ms:.0f}ms 在 target_range [{tgt_min}, {tgt_max}]ms 内 · "
                  f"节奏自然")

    return {
        "verdict": verdict,
        "reason": reason,
        "cut_duration_ms": round(cut_duration_ms, 1),
        "raw_gap_ms": round(raw_gap_ms, 1),
        "post_cut_gap_ms": round(post_cut_gap_ms, 1),
        "prev_word": {"text": prev_word.get("text"),
                      "end_seconds": round(prev_end_s, 4)},
        "next_word": {"text": next_word.get("text"),
                      "start_seconds": round(next_start_s, 4)},
        "thresholds": thresholds,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate-json", required=True, type=Path)
    ap.add_argument("--transcript", required=True, type=Path,
                    help="ASR transcript.json for the candidate's source_track_id")
    ap.add_argument("--cut-params-json", type=Path, default=Path(DEFAULT_CUT_PARAMS_PATH))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    cut_params_path = args.cut_params_json.resolve()
    if not cut_params_path.exists():
        # try common location
        alt = Path("<HOME>/Desktop/minglue/剪辑项目") / DEFAULT_CUT_PARAMS_PATH
        cut_params_path = alt if alt.exists() else cut_params_path
    thresholds = load_thresholds(cut_params_path)

    cd = json.loads(args.candidate_json.read_text(encoding="utf-8"))
    candidates = cd.get("candidates") if isinstance(cd, dict) and "candidates" in cd else [cd]
    tr = json.loads(args.transcript.read_text(encoding="utf-8"))

    results = []
    for c in candidates:
        r = check(c, tr, thresholds)
        r["candidate_id"] = c.get("candidate_id")
        results.append(r)

    out = {
        "schema_version": "check-rhythm-gap-v1",
        "cut_params_source": thresholds.get("source"),
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
