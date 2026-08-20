#!/usr/bin/env python3
"""FROZEN 2026-08-19 · LLM Takeover

用户 2026-08-19 evening 明确: LLM 完全主导 candidate 生成 + 判决 (Stage 3.5.5).
本文件冻结 · 保留代码作 fallback · 不再是主流水消费者.
详见: 交付/最终交付文档/统筹全局/DEPRECATED_LLM_TAKEOVER_2026-08-19.md

** 何时会被 pipeline 消费 **:
- 若 Stage 3.5.5 LLM 挂 (3 mode 全挂)
- 或 --no-auto-llm-full-pipeline 明确 opt-out
- 否则 pipeline 走 LLM 主导 · 本文件 idle

---

detect_self_correction_wordlevel — 词级 self_correction 检测器（v2 算法）。

**动机**：现有 detect_self_correction.py 的算法基于"停顿 ≥ 0.6s 切开的两个整句 A、B，
判 B 是不是 A 的改写"。在 EP04 三人对谈数据上 0 命中，因为 EP04 的说错重来大多是
**半句内快速自我修正**（gap < 100 ms，没触发切句）。

**新算法**：sliding word window（不切句）：
    for i in K..N-K:
        pre = words[i-K:i]   # i 之前 K 个词
        post = words[i:i+K]  # i 起 K 个词
        gap = words[i].start - words[i-1].end
        if gap ∈ [gap_min, gap_max]  AND  edit_ratio(pre, post) ≥ edit_min:
            emit candidate: delete pre, keep post

**边界**：候选边界 = pre 段的 ASR 词整体范围 [pre[0].start, pre[-1].end]。
输出 `boundary_lock=true` 让 snap_candidate_boundaries 跳过精修。

**用法**：
    python3 detect_self_correction_wordlevel.py \\
        --transcript track_01=/abs/track_01.transcript.json \\
        --transcript track_02=/abs/track_02.transcript.json \\
        --rules 稳定生产/challengers/self-correction-v1/rules/self-correction-wordlevel.v1.json \\
        --sample-rate-hz 48000 \\
        --out /path/to/candidates.json

**跟 detect_self_correction.py 关系**：并存不替换。老的走"句对"抽象，新的走"词滑窗"。
以后哪个准哪个用；也可以联合出候选后 dedup。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable


def _clean(text: Any) -> str:
    return str(text or "").strip().replace(",", "").replace(".", "").replace("?", "").replace("!", "")


def _text_of(words: Iterable[dict[str, Any]]) -> str:
    return "".join(_clean(w.get("text", "")) for w in words)


def sha256_of_rules(rules: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def detect_word_level_self_correction(
    words: list[dict[str, Any]],
    rules: dict[str, Any],
    track_id: str = "track_01",
    sample_rate_hz: int = 48000,
) -> list[dict[str, Any]]:
    """See module docstring for the algorithm."""
    K = int(rules.get("pre_window_words", 3))
    edit_min = float(rules.get("edit_ratio_min", 0.4))
    gap_min = float(rules.get("gap_min_seconds", 0.05))
    gap_max = float(rules.get("gap_max_seconds", 0.6))
    min_pre_chars = int(rules.get("min_pre_chars", 2))
    min_post_chars = int(rules.get("min_post_chars", 2))
    protected_starts = tuple(rules.get("protected_starts", []))
    high_confidence_ratio = float(rules.get("high_confidence_edit_ratio", 0.7))

    # 用户 2026-08-19 · 句号后新句是话轮转换 · 非自纠正
    # 例: '怎么保证。怎么确保' 不该判纠正
    _sbv = rules.get("sentence_boundary_veto", {}) or {}
    _sbv_on = bool(_sbv.get("value", False))
    _sbv_tokens = set(_sbv.get("boundary_tokens", []))

    valid = [w for w in words if w.get("start_seconds") is not None and w.get("text")]
    n = len(valid)
    if n < 2 * K:
        return []

    proposals: list[dict[str, Any]] = []
    seen_starts: set[int] = set()

    for i in range(K, n - K + 1):
        pre = valid[i - K : i]
        post = valid[i : i + K]
        gap = float(post[0]["start_seconds"]) - float(pre[-1]["end_seconds"])
        if not (gap_min <= gap <= gap_max):
            continue
        # sentence_boundary_veto: 若 pre 段末词文本尾部命中句末标点,
        # 视为句子/话轮边界,跳过候选提名 (与 protected_starts 互补,
        # 后者按开场语料词过滤,本检查按句法边界过滤)。
        if _sbv_on and _sbv_tokens:
            pre_last_text = pre[-1].get("text", "") if pre else ""
            if any(bt in pre_last_text for bt in _sbv_tokens):
                continue
        pre_text = _text_of(pre)
        post_text = _text_of(post)
        if len(pre_text) < min_pre_chars or len(post_text) < min_post_chars:
            continue
        if pre_text == post_text:
            # 逐字重复走 immediate_repetition，不走 self_correction
            continue
        if pre_text.startswith(protected_starts) or post_text.startswith(protected_starts):
            continue
        ratio = difflib.SequenceMatcher(None, pre_text, post_text).ratio()
        if ratio < edit_min:
            continue
        start_s = float(pre[0]["start_seconds"])
        end_s = float(pre[-1]["end_seconds"])
        # Dedup by pre start (keep first hit per timestamp; sliding window would
        # otherwise emit overlapping candidates for the same repair region).
        key = int(start_s * 1000)
        if key in seen_starts:
            continue
        seen_starts.add(key)
        proposals.append(
            {
                "reason_key": "self_correction",
                "kind": "self_correction",
                "track_id": track_id,
                "source_track_id": track_id,
                "start_seconds": start_s,
                "end_seconds": end_s,
                "start_sample": int(round(start_s * sample_rate_hz)),
                "end_sample": int(round(end_s * sample_rate_hz)),
                "abandoned_span": {
                    "text": pre_text,
                    "start_seconds": start_s,
                    "end_seconds": end_s,
                },
                "retry_span": {
                    "text": post_text,
                    "start_seconds": float(post[0]["start_seconds"]),
                    "end_seconds": float(post[-1]["end_seconds"]),
                },
                "edit_ratio": round(ratio, 3),
                "gap_seconds": round(gap, 3),
                "pre_window_words": K,
                "algorithm": "wordlevel_sliding_v1",
                "boundary_lock": True,
                "boundary_lock_reason": "self_correction wordlevel v1: abandoned span anchored to ASR word bounds",
                # v20 feedback (2026-08-17): SC005 case（怎么保证→怎么确保 ratio=0.75）
                # 用户明确"要么留一个要么都保留" —— rate 0.7-0.85 是"改写"而非
                # "重复口误"，剪了会破坏语义。gate 应把这类降级人审。
                # ratio ≥ 0.85 才认为是"重复口误"级别，可 auto。
                "algorithm_confidence": (
                    "high_repetition" if ratio >= 0.85
                    else "paraphrase_both_spans_cut"
                ),
                # v20.1 feedback: 用户要"全或无" —— 整对都剪（pre + retry 都删）
                # 而不是只删 pre 保 retry。此字段被 EDL 生成器消费。
                "cut_scope": "both_spans" if ratio < 0.85 else "pre_only",
                "post_cut_pause_ms": 200,
                "confidence_tier": "high" if ratio >= high_confidence_ratio else "mid",
                "policy": "review_only_no_automatic_accept",
            }
        )
    return proposals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--transcript",
        action="append",
        required=True,
        help="LABEL=/abs/path.transcript.json；可多次",
    )
    ap.add_argument("--rules", required=True)
    ap.add_argument("--sample-rate-hz", type=int, default=48000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    result = {
        "schema_version": "self-correction-wordlevel-run-v1",
        "algorithm": "wordlevel_sliding_v1",
        "rules_path": args.rules,
        "rules_sha256": sha256_of_rules(rules),
        "sample_rate_hz": args.sample_rate_hz,
        "tracks": [],
    }
    total = 0
    for spec in args.transcript:
        label, p = spec.split("=", 1)
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        words = data.get("words", [])
        cands = detect_word_level_self_correction(
            words, rules, track_id=label, sample_rate_hz=args.sample_rate_hz
        )
        total += len(cands)
        result["tracks"].append(
            {
                "label": label,
                "transcript_path": p,
                "candidate_count": len(cands),
                "candidates": cands,
            }
        )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"tracks": len(result["tracks"]), "candidates": total, "out": args.out},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
