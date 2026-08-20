#!/usr/bin/env python3
"""auto_speaker_role · Q2 · 自动 speaker role 统计启发式 (v20.6, 2026-08-18)

**动机**: 用户明确 "尽量不要手动" · 但每轨录音已分开 (Zoom multitrack), 不需要
pyannote 那种说话人分辨. 用 faster-whisper 词级输出的统计特征判每轨角色
(host/guest_A/guest_B) 就够, 不需要外部工具或 HF token.

**信号**:
    backchannel_ratio = count(HOST_BACKCHANNEL_TOKENS) / total_words
    total_speaking_time = sum(word.end - word.start) / episode_duration
    question_density = count(QUESTION_TOKENS) / total_time
    avg_utterance_length = mean(连续 chain 秒数, gap>0.5s 切段)

**判决**:
    host: backchannel_ratio > 0.15 AND total_speaking_time < 0.40
    guest: 其他

**输出**: main/knowledge/speaker_maps/EP0X.speaker_map.auto.json (v1 schema)

**优先级** (`run_end_to_end.py` Stage 3.1 消费):
    人工 speaker_map.json 存在 → 用人工
    否则 → 用 auto (本 tool 输出)
    冲突时 → 保人工 + warn
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


HOST_BACKCHANNEL_TOKENS = {"嗯", "啊", "对", "对对", "是", "是的", "好", "好的", "唉"}
QUESTION_TOKENS = {"什么", "怎么", "怎样", "哪里", "哪个", "为什么", "谁", "多少"}

# v20.6.1 (2026-08-18): 用相对判决取代绝对阈值.
# 实测 EP04: 三轨 backchannel_ratio 都在 3-4% (不是 15%+), 但 track_03 说话
# 时间 44.9% vs 嘉宾 85-88% · **说话时间少**才是主持人的强信号.
HOST_TOTAL_SPEAKING_FRACTION_MAX = 0.55  # 主持人说话时间上限
HOST_BACKCHANNEL_RATIO_ABS_MIN = 0.02    # 至少 2% backchannel (绝对下限)


def analyze_track(words: list[dict[str, Any]], episode_duration_s: float) -> dict[str, Any]:
    """对单轨 ASR 词序列算统计特征."""
    total_words = len(words)
    if total_words == 0:
        return {
            "total_words": 0,
            "total_speaking_time_s": 0.0,
            "total_speaking_fraction": 0.0,
            "backchannel_ratio": 0.0,
            "question_density": 0.0,
            "avg_utterance_length_s": 0.0,
        }
    backchannel_count = 0
    question_count = 0
    total_speaking_s = 0.0
    for w in words:
        text = str(w.get("text", "")).strip()
        s = float(w.get("start_seconds") or 0)
        e = float(w.get("end_seconds") or s)
        total_speaking_s += max(0.0, e - s)
        if text in HOST_BACKCHANNEL_TOKENS:
            backchannel_count += 1
        if text in QUESTION_TOKENS:
            question_count += 1

    # avg utterance length: 连续 chain (gap>0.5s 切)
    chain_lengths = []
    chain_start = None
    prev_end = None
    for w in words:
        s = float(w.get("start_seconds") or 0)
        e = float(w.get("end_seconds") or s)
        if chain_start is None:
            chain_start = s
        elif prev_end is not None and s - prev_end > 0.5:
            chain_lengths.append(prev_end - chain_start)
            chain_start = s
        prev_end = e
    if chain_start is not None and prev_end is not None:
        chain_lengths.append(prev_end - chain_start)
    avg_utterance_s = sum(chain_lengths) / len(chain_lengths) if chain_lengths else 0.0

    return {
        "total_words": total_words,
        "total_speaking_time_s": round(total_speaking_s, 2),
        "total_speaking_fraction": round(total_speaking_s / max(1e-6, episode_duration_s), 4),
        "backchannel_ratio": round(backchannel_count / total_words, 4),
        "backchannel_count": backchannel_count,
        "question_density": round(question_count / max(1e-6, episode_duration_s) * 60, 4),  # per minute
        "avg_utterance_length_s": round(avg_utterance_s, 2),
        "n_utterance_chains": len(chain_lengths),
    }


def infer_role(stats: dict[str, Any], peer_stats: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    """v20.6.1 相对判决 · peer_stats 是其他 track 的 stats 用于相对比较.
    条件:
        host: total_speaking_fraction < 0.55 AND (backchannel_ratio >= 相对最高 OR 说话时间最少)
        guest: 其他
    """
    br = float(stats.get("backchannel_ratio", 0))
    frac = float(stats.get("total_speaking_fraction", 0))
    if peer_stats is None:
        peer_stats = []

    # 相对信号
    peer_fracs = [float(p.get("total_speaking_fraction", 0)) for p in peer_stats]
    peer_brs = [float(p.get("backchannel_ratio", 0)) for p in peer_stats]
    is_min_speaking = (not peer_fracs) or all(frac <= p for p in peer_fracs)
    is_max_backchannel = (not peer_brs) or all(br >= p for p in peer_brs)

    # 判决
    if frac < HOST_TOTAL_SPEAKING_FRACTION_MAX and (is_min_speaking or is_max_backchannel):
        return (
            "host",
            f"说话占比 {frac:.1%} < {HOST_TOTAL_SPEAKING_FRACTION_MAX:.0%} · "
            f"backchannel {br:.2%} · 相对{'最少说话' if is_min_speaking else '最多backchannel'}",
        )
    return ("guest", f"说话占比 {frac:.1%} · backchannel {br:.2%}")


def build_auto_speaker_map(
    episode_id: str,
    transcripts_by_track: dict[str, list[dict]],
    episode_duration_s: float,
) -> dict[str, Any]:
    """构建 speaker_map.auto.json v1 schema."""
    tracks: dict[str, Any] = {}
    all_stats: dict[str, dict] = {}
    for track_id in sorted(transcripts_by_track):
        all_stats[track_id] = analyze_track(transcripts_by_track[track_id], episode_duration_s)

    for track_id, stats in all_stats.items():
        peer_stats = [s for tid, s in all_stats.items() if tid != track_id]
        role, reason = infer_role(stats, peer_stats)
        tracks[track_id] = {
            "role": role,
            "reason": reason,
            "stats": stats,
        }
    # 若多轨都判 host, 取 说话时间最少的做 host, 其余降级 guest
    hosts = [tid for tid, info in tracks.items() if info["role"] == "host"]
    if len(hosts) > 1:
        best = min(hosts, key=lambda tid: tracks[tid]["stats"]["total_speaking_fraction"])
        for tid in hosts:
            if tid != best:
                tracks[tid]["role"] = "guest"
                tracks[tid]["reason"] += f" · 多轨 host 竞争, 降为 guest ({best} 说话最少)"

    return {
        "schema_version": "speaker-map-v1",
        "episode_id": episode_id,
        "generator": "auto_speaker_role.py (v20.6 · statistical heuristic)",
        "generator_version": "1.0",
        "map": {tid: {"role": info["role"], "note": info["reason"]} for tid, info in tracks.items()},
        "role_rules": {
            "host_backchannel_skip": {
                "roles": ["host"],
                "tokens_treated_as_backchannel": sorted(HOST_BACKCHANNEL_TOKENS),
                "skip_candidate_condition": "if token in tokens_treated_as_backchannel AND context has other-track speech within ±3s, treat as backchannel and SKIP filler_hesitation / immediate_repetition candidate generation",
            }
        },
        "detailed_stats": tracks,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-id", required=True)
    ap.add_argument("--analysis-dir", type=Path, required=True,
                    help="包含 track_XX.transcript.json 的目录")
    ap.add_argument("--episode-duration-seconds", type=float, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if not args.analysis_dir.is_dir():
        print(f"BLOCKED: analysis_dir 不存在: {args.analysis_dir}", file=sys.stderr)
        return 2

    tracks: dict[str, list[dict]] = {}
    for p in sorted(args.analysis_dir.glob("track_*.transcript.json")):
        tid = p.stem.replace(".transcript", "")
        d = json.loads(p.read_text(encoding="utf-8"))
        tracks[tid] = d.get("words", [])

    if not tracks:
        print(f"BLOCKED: no track_*.transcript.json in {args.analysis_dir}", file=sys.stderr)
        return 2

    result = build_auto_speaker_map(args.episode_id, tracks, args.episode_duration_seconds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "map": {tid: info["role"] for tid, info in result["map"].items()},
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
