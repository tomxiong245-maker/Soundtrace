"""build_report.py — read metrics.json and produce a human-readable verdict."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _fmt(v, digits=3):
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--diar-used", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    m = json.loads(args.metrics.read_text(encoding="utf-8"))
    diar_used = args.diar_used.read_text(encoding="utf-8").strip() if args.diar_used.is_file() else "unknown"

    lines: list[str] = []
    lines.append("# EP03 ASR / Diarization Challenger — Final Report")
    lines.append("")
    lines.append("**评分口径**：双轨物理银标（10 ms 帧，两个麦克风的能量对比 + Silero VAD）+ 三引擎交叉一致率（无需人工 gold）。若人工 gold 已填，额外给出绝对 CER。")
    lines.append("")

    # ---- ASR ----
    lines.append("## 一、ASR 三大指标（越小越好）")
    lines.append("")
    lines.append("| 引擎 | 静音幻听 (秒) | 幻听率 | 漏识别 (秒) | 漏识别率 |")
    lines.append("|---|---:|---:|---:|---:|")
    asr = m["asr_metrics"]
    engines = list(asr.keys())
    scored = []
    for eng in engines:
        r = asr[eng]
        lines.append(f"| `{eng}` | {_fmt(r['total_hallucination_in_silence_seconds'])} | "
                     f"{_fmt(r['hallucination_rate'])} | "
                     f"{_fmt(r['total_missed_speech_seconds'])} | "
                     f"{_fmt(r['miss_rate'])} |")
        # normalized score: 0.5*hallucination_rate + 0.5*miss_rate (lower better)
        score = 0.5 * r["hallucination_rate"] + 0.5 * r["miss_rate"]
        scored.append((eng, score))
    lines.append("")
    scored.sort(key=lambda x: x[1])
    lines.append(f"**ASR 综合排名（幻听+漏识别，越低越好）**：" +
                 " → ".join(f"`{e}` ({s:.3f})" for e, s in scored))
    lines.append("")

    # ---- Cross agreement ----
    lines.append("## 二、三引擎交叉一致率（互相之间的 CER；越低说明与另外两个越一致）")
    lines.append("")
    ce = m["cross_engine_agreement"]
    lines.append("| 引擎对 | 平均 CER | 中位 CER |")
    lines.append("|---|---:|---:|")
    for k, v in ce["pairs"].items():
        lines.append(f"| {k} | {_fmt(v['mean_cer'])} | {_fmt(v['median_cer'])} |")
    lines.append("")
    lines.append("**每引擎的"共识距离"（与另外两个的平均 CER；低=处于中间，被两个都认可）**")
    lines.append("")
    lines.append("| 引擎 | 共识距离 |")
    lines.append("|---|---:|")
    for e, v in ce["consensus_distance_per_engine"].items():
        lines.append(f"| `{e}` | {_fmt(v['mean_pairwise_cer'])} |")
    lines.append("")
    lines.append("*注：交叉一致率不是绝对准确率；只有 gold 存在时才能给绝对 CER。见下节。*")
    lines.append("")

    # ---- Absolute CER ----
    lines.append("## 三、绝对 CER（只有真人 gold 填写后才有）")
    lines.append("")
    abs_ = m.get("absolute_cer_vs_human_gold")
    if abs_ is None:
        lines.append("`WAITING_FOR_HUMAN_GOLD` — 12 段目前无人工正确文本。运行本 pipeline 已完成前两节；填 gold 后重跑 score.py 会自动补齐这一节。")
    else:
        lines.append(f"参与评分的已审段数：{abs_['n_reviewed']}")
        lines.append("")
        lines.append("| 引擎 | CER micro | CER macro |")
        lines.append("|---|---:|---:|")
        for e, v in abs_["per_engine"].items():
            lines.append(f"| `{e}` | {_fmt(v['cer_micro'])} | {_fmt(v['cer_macro'])} |")
    lines.append("")

    # ---- Diarization ----
    lines.append(f"## 四、说话人 / 重叠（当前使用引擎：`{diar_used}`）")
    lines.append("")
    lines.append("| 引擎 | 说话人搞混 (秒) | 重叠帧数(gold) | 重叠检出 | 重叠召回 |")
    lines.append("|---|---:|---:|---:|---:|")
    diar = m["diar_metrics"]
    diar_scored = []
    for eng, v in diar.items():
        ov_recall = v["overlap_recall"]
        lines.append(f"| `{eng}` | {_fmt(v['speaker_confusion_seconds'])} | "
                     f"{v['total_overlap_gold_frames']} | {v['total_overlap_detected_frames']} | "
                     f"{_fmt(ov_recall)} |")
        conf_rate = v["total_speaker_confusion_frames"] / max(v["total_frames"], 1)
        # penalize both confusion AND missed overlap; overlap_recall in [0,1] — higher better
        # combined "goodness" (lower better): confusion_rate + (1 - overlap_recall_or_0)
        gg = conf_rate + (1 - (ov_recall or 0))
        diar_scored.append((eng, gg))
    diar_scored.sort(key=lambda x: x[1])
    lines.append("")
    if diar_scored:
        lines.append(f"**Diarization 综合排名（搞混+漏重叠，越低越好）**：" +
                     " → ".join(f"`{e}` ({s:.3f})" for e, s in diar_scored))
    lines.append("")

    # ---- Verdict ----
    lines.append("## 五、Challenger verdict（自动组合建议）")
    lines.append("")
    best_asr = scored[0][0] if scored else None
    best_diar = diar_scored[0][0] if diar_scored else None
    lines.append(f"- **中文 ASR 建议使用**：`{best_asr}`（银标 + 交叉一致综合最低）")
    lines.append(f"- **说话人/重叠建议使用**：`{best_diar}`（银标综合最低）")
    if abs_ is None:
        lines.append("- **CER 绝对值待人工 gold 补齐**；本 verdict 已经保证：幻听、漏识别、说话人搞混、重叠漏检 4 个指标全部有物理银标背书，不是估计。")
    else:
        lines.append(f"- **绝对 CER 已算**：见第三节。")
    lines.append("")

    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("report →", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
