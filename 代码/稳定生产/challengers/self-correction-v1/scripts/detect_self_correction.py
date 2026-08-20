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

self-correction-v1: 说错重来候选器。

只依赖标准库：Python 3.10+ 自带 `difflib`。不联网、不下载模型。

规则：
- 同一 track 上，按大于 `sentence_split_gap_seconds` 的静音把词切成短句 A、B、C、...
- 相邻两句 A、B：
  1. gap ∈ [interrupt_min_gap, interrupt_max_gap] 或 A、B 中间存在打断词；
  2. A、B 共享前缀字数 ≥ min_shared_prefix_chars；
  3. difflib SequenceMatcher(A, B).ratio() ≥ min_edit_ratio；
  4. A 长度 ≤ max_abandoned_chars（真正的自我打断都是短句）；
  5. A 起始不属于受保护开场白（片头片尾）。

输出候选把 A 段整段删除，B 段保留。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable


def _clean_text(words: Iterable[dict[str, Any]]) -> str:
    return "".join((w.get("text", "") or "") for w in words)


def _shared_prefix_chars(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def split_sentences(words: list[dict[str, Any]], gap_s: float,
                    min_words: int, max_words: int) -> list[list[dict[str, Any]]]:
    if not words:
        return []
    words = [w for w in words if w.get("text")]
    sentences: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for i, w in enumerate(words):
        if not cur:
            cur.append(w)
            continue
        prev = cur[-1]
        gap = float(w.get("start_seconds", 0.0)) - float(prev.get("end_seconds", 0.0))
        if gap >= gap_s or len(cur) >= max_words:
            sentences.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        sentences.append(cur)
    # 过滤过短的句子
    return [s for s in sentences if len(s) >= min_words]


def detect_self_corrections(words: list[dict[str, Any]],
                            rules: dict[str, Any],
                            track_id: str = "track_01",
                            sample_rate_hz: int = 48000
                            ) -> list[dict[str, Any]]:
    sentences = split_sentences(
        words, gap_s=rules["sentence_split_gap_seconds"],
        min_words=rules["min_words_per_sentence"],
        max_words=rules["max_words_per_sentence"],
    )
    out: list[dict[str, Any]] = []
    interrupt_tokens = set(rules["interrupt_tokens"])
    protected = tuple(rules["protected_starts"])

    for i in range(len(sentences) - 1):
        a = sentences[i]
        b = sentences[i + 1]
        a_text = _clean_text(a)
        b_text = _clean_text(b)
        if not a_text or not b_text:
            continue
        if a_text.startswith(protected) or b_text.startswith(protected):
            continue
        if len(a_text) > rules["max_abandoned_chars"]:
            continue
        gap_s = float(b[0]["start_seconds"]) - float(a[-1]["end_seconds"])
        has_interrupt_word = False
        interrupt_hit = None
        # 在 a 尾部或 b 头部找打断词
        tail_text = "".join(w.get("text", "") for w in a[-3:] + b[:2])
        for tok in interrupt_tokens:
            if tok in tail_text:
                has_interrupt_word = True
                interrupt_hit = tok
                break
        gap_ok = (rules["interrupt_min_gap_seconds"] <= gap_s
                  <= rules["interrupt_max_gap_seconds"])
        if not gap_ok and not has_interrupt_word:
            continue
        shared = _shared_prefix_chars(a_text, b_text)
        if shared < rules["min_shared_prefix_chars"]:
            continue
        ratio = difflib.SequenceMatcher(None, a_text, b_text).ratio()
        if ratio < rules["min_edit_ratio"]:
            continue
        # 排除“完全相同”的直接重复（那不是自我更正，是重复口癖）
        if a_text == b_text:
            continue
        start_s = float(a[0]["start_seconds"])
        end_s = float(a[-1]["end_seconds"])
        start_sample = int(round(start_s * sample_rate_hz))
        end_sample = int(round(end_s * sample_rate_hz))
        out.append({
            "reason_key": "self_correction",
            "track_id": track_id,
            "source_track_id": track_id,
            "start_sample": start_sample,
            "end_sample": end_sample,
            "start_seconds": start_s,
            "end_seconds": end_s,
            "abandoned_span": {
                "text": a_text,
                "start_seconds": start_s,
                "end_seconds": end_s,
            },
            "retry_span": {
                "text": b_text,
                "start_seconds": float(b[0]["start_seconds"]),
                "end_seconds": float(b[-1]["end_seconds"]),
            },
            "shared_prefix_chars": shared,
            "shared_prefix": a_text[:shared],
            "edit_ratio": round(ratio, 4),
            "interrupt_gap_seconds": round(gap_s, 4),
            "interrupt_hit": interrupt_hit,
            "policy": "review_only_no_automatic_accept",
        })
    return out


def sha256_of_rules(rules: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", action="append", required=True,
                    help="LABEL=/abs/path.classified.json；可多次")
    ap.add_argument("--rules", required=True)
    ap.add_argument("--sample-rate-hz", type=int, default=48000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    result = {
        "schema_version": "self-correction-run-v1",
        "rules_path": args.rules,
        "rules_sha256": sha256_of_rules(rules),
        "sample_rate_hz": args.sample_rate_hz,
        "tracks": [],
    }
    for spec in args.transcript:
        label, p = spec.split("=", 1)
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        words = data.get("words", [])
        cands = detect_self_corrections(
            words, rules, track_id=label, sample_rate_hz=args.sample_rate_hz)
        result["tracks"].append({
            "label": label,
            "transcript_path": p,
            "candidate_count": len(cands),
            "candidates": cands,
        })
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "tracks": len(result["tracks"]),
        "candidates": sum(t["candidate_count"] for t in result["tracks"]),
        "out": args.out,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
