#!/usr/bin/env python3
"""score_asr_benchmark.py — 等 gold 填好后算 CER 等指标；未填时不算数字"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path,
                    default=PROJECT_ROOT / "benchmark/EP03-ASR-mini-gold-v1/gold.json")
    args = ap.parse_args()

    d = json.loads(args.gold.read_text(encoding="utf-8"))

    if d.get("status") != "HUMAN_GOLD_FILLED":
        print("=" * 60)
        print("ASR benchmark 状态: WAITING_FOR_HUMAN_GOLD")
        print("=" * 60)
        print(f"gold.json 里有 {len(d['segments'])} 个片段，人工标注字段为空。")
        print()
        print("按任务书要求：")
        print("  - 不得计算或伪造 CER")
        print("  - 不得宣布 VAD 开/关谁更好")
        print()
        print("请人工在 label.html 里填 12 段的 transcript / speaker_attribution / missed_sentences")
        print("然后重新导出 gold.json，再跑本脚本即可拿到:")
        print("  - 中文 CER")
        print("  - substitution / deletion / insertion")
        print("  - 整句漏识别率")
        print("  - speaker attribution error")
        print("  - overlap recall")
        return 0

    # 未来 gold 填好后的评分实现
    print("Gold 已填。开始评分... (此路径当前 stub，真实实现待 gold 到位后开发)")
    # TODO: 用 jiwer 或类似库算中文 CER
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
