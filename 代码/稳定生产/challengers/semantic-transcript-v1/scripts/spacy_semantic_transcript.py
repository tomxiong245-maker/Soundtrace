#!/usr/bin/env python3
"""spacy_semantic_transcript · Q1 · 中文语义分句 (v20.6, 2026-08-18)

**动机**: 用户明确 "转到 spaCy" · 现有 semantic-transcript-v1 用 timing_text_heuristic
是自己写的启发式, F03 明确 "非源标点边界多为低置信度". spaCy `zh_core_web_sm`
用统计模型分句 + 依存句法, 精度更高.

**输入**: track_XX.transcript.json (faster-whisper 词级)
**输出**: track_XX.spacy_semantic.json (每句 sentence_id + word 范围 + 意图分类)

**Schema**:
    {
      "schema_version": "spacy-semantic-v1",
      "spacy_model": "zh_core_web_sm-3.x",
      "sentences": [
        {
          "sentence_id": "s001",
          "text": "然后 我们 继续",
          "start_word_idx": 0,
          "end_word_idx": 3,
          "start_seconds": 0.3,
          "end_seconds": 1.8,
          "category": "declarative"  // or interrogative / exclamation
        }
      ]
    }

**下游消费**: filler_global_pause 加载后, 对候选 word 找所在 sentence category.
若 interrogative 且 filler_token in 疑问词 → skip (Q1 + Q5 双保险).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


INTERROGATIVE_TOKENS = {"什么", "怎么", "怎样", "哪里", "哪个", "哪些", "为什么", "谁", "多少", "几", "吗", "呢"}
QUESTION_MARKS = {"?", "？"}


def _init_spacy(model: str = "zh_core_web_sm"):
    """加载 spaCy 中文模型. 若失败提示装模型."""
    try:
        import spacy
    except ImportError:
        print("ERROR: spaCy 未装. 跑: pip install spacy && python -m spacy download zh_core_web_sm", file=sys.stderr)
        return None
    try:
        return spacy.load(model)
    except OSError:
        print(f"ERROR: spaCy 模型 {model} 未装. 跑: python -m spacy download {model}", file=sys.stderr)
        return None


def classify_sentence(text: str) -> str:
    """判句子意图: interrogative / exclamation / declarative."""
    if any(qm in text for qm in QUESTION_MARKS):
        return "interrogative"
    # 中文疑问词末尾 (无标点) 也判疑问句
    # e.g. "你说什么" (末尾) or "怎么办呢"
    if any(text.endswith(tok) for tok in ("吗", "呢", "吧", "么")):
        return "interrogative"
    # 前 3 字符含疑问词 (开头) 也算 (如 "什么意思")
    prefix = text[:3]
    if any(tok in prefix for tok in INTERROGATIVE_TOKENS):
        # 只保留至少 2 字的疑问词
        if any(len(tok) >= 2 and tok in prefix for tok in INTERROGATIVE_TOKENS):
            return "interrogative"
    if "!" in text or "！" in text:
        return "exclamation"
    return "declarative"


def segment_with_spacy(words: list[dict[str, Any]], nlp) -> list[dict[str, Any]]:
    """用 spaCy 对 word 序列做 sentence segmentation.

    faster-whisper words 是 tokenized (无标点), 拼接后用 spaCy 分句. 我们再把
    分句 back-map 回原 word 索引.
    """
    if not words:
        return []
    # 拼词 (无空格 · 中文) + 用 sentencizer 分句
    concat_text = "".join(str(w.get("text", "")).strip() for w in words)
    doc = nlp(concat_text)

    sentences: list[dict[str, Any]] = []
    char_idx = 0  # 当前 char 位置
    word_idx = 0
    # 构建 word char range: 每 word 覆盖 [char_start, char_end)
    word_char_ranges: list[tuple[int, int, int]] = []  # (word_idx, char_start, char_end)
    for i, w in enumerate(words):
        text = str(w.get("text", "")).strip()
        word_char_ranges.append((i, char_idx, char_idx + len(text)))
        char_idx += len(text)

    for sid, sent in enumerate(doc.sents):
        s_start = sent.start_char
        s_end = sent.end_char
        # 找覆盖 [s_start, s_end) 的 word 范围
        word_start = None
        word_end = None
        for (wi, cs, ce) in word_char_ranges:
            if ce <= s_start:
                continue
            if cs >= s_end:
                break
            if word_start is None:
                word_start = wi
            word_end = wi
        if word_start is None:
            continue
        # 时间戳
        st_s = float(words[word_start].get("start_seconds") or 0)
        et_s = float(words[word_end].get("end_seconds") or st_s)
        text = str(sent.text).strip()
        cat = classify_sentence(text)
        sentences.append({
            "sentence_id": f"s{sid+1:04d}",
            "text": text,
            "start_word_idx": word_start,
            "end_word_idx": word_end,
            "start_seconds": round(st_s, 3),
            "end_seconds": round(et_s, 3),
            "category": cat,
        })
    return sentences


def sentence_for_word(sentences: list[dict], word_idx: int) -> dict | None:
    """快速找一个 word 属于哪个 sentence."""
    for s in sentences:
        if s["start_word_idx"] <= word_idx <= s["end_word_idx"]:
            return s
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transcript", type=Path, required=True,
                    help="faster-whisper 词级 transcript.json")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--model", default="zh_core_web_sm",
                    help="spaCy 中文模型 (sm/md/lg)")
    args = ap.parse_args(argv)

    nlp = _init_spacy(args.model)
    if nlp is None:
        return 2

    doc = json.loads(args.transcript.read_text(encoding="utf-8"))
    words = doc.get("words") if isinstance(doc, dict) else doc

    sentences = segment_with_spacy(words or [], nlp)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "schema_version": "spacy-semantic-v1",
        "spacy_model": f"{args.model}-{nlp.meta.get('version', '?')}",
        "source_transcript": str(args.transcript),
        "n_words": len(words or []),
        "n_sentences": len(sentences),
        "sentences": sentences,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "n_words": len(words or []),
        "n_sentences": len(sentences),
        "interrogative": sum(1 for s in sentences if s["category"] == "interrogative"),
        "declarative": sum(1 for s in sentences if s["category"] == "declarative"),
        "out": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
