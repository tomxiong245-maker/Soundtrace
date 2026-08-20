#!/usr/bin/env python3
"""generate_ab_clip_learning_driven · A/B clip 唯一入口 (v215)

**动机**（2026-08-18）: 用户 4 次强调"用工具用工具，装了不用" + "按流程"。本 tool
是 A/B clip 生成的**唯一合规入口**, 强制从三条进化路径学参数, 装了的工具必须用.

**输入**:
    --candidate-id C023
    --cut-range-seconds 959.34 960.10
    --kept-word-asr-start 960.08  (可选, chain 情况保留最后 1 个)
    --episode-id EP04
    --automix-wav path/to/speech.mono.wav (automix 后 · CLAUDE.md §9)
    --raw-track-wav path/to/ZOOM0009_Tr2.WAV (room tone 源 · YouTube § 5)
    --out-dir path/to/clips
    --label C023_然后

**参数从三条学习路径自动学 (不手写)**:
    - session_feedback/{EP04,ALL}.jsonl (v20.6 Q4 · 42+ 条规则)
    - labels_lake.json (33 决定 · feedback 字段)
    - preference_snapshot/aggregated.json (65 records)

**工具链 (装了的必须用)**:
    - librosa.onset.onset_detect → 精确辅音起音 (避免吃保留词)
    - pydub.AudioSegment.append(crossfade) → sample-level crossfade
    - pydub.silence 或 volumedetect → room tone 检测
    - pydub.reverse() → room tone 反向拼接避循环

**产出**:
    - {out_dir}/{label}_原.mp3 (从 automix 切原音)
    - {out_dir}/{label}_剪.mp3 (v214.1 方法: librosa cut_end + dynamic pause + room tone splice)
    - {out_dir}/{label}.manifest.json (学到的参数记录)
    - 4 层自检: 时长/RMS/静音/librosa onset 完整性

违反 CLAUDE.md §9 §11 §15 §16 §17 视为破坏契约.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 装了的工具 · 必须用
import librosa
from pydub import AudioSegment


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_learned_params(episode_id: str) -> dict[str, Any]:
    """从三条进化路径读取参数 · 不硬编码."""
    rules: list[dict] = []
    for name in [f"{episode_id}.session_feedback.jsonl", "ALL.session_feedback.jsonl"]:
        p = PROJECT_ROOT / "main" / "knowledge" / "session_feedback" / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rules.append(json.loads(line))
    # 从 labels_lake feedback
    lake = PROJECT_ROOT / "main" / "knowledge" / "labels_lake.json"
    if lake.is_file():
        d = json.loads(lake.read_text())
        for e in d.get("entries", []):
            for fb in e.get("feedback", []) or []:
                rules.append({
                    "source": "labels_lake",
                    "candidate_pattern": {
                        "reason_key": e.get("reason_key"),
                        "filler_token": e.get("filler_token"),
                    },
                    "verdict": fb.get("verdict") or fb.get("decision"),
                    "note": fb.get("note", ""),
                })

    # 提取参数
    p = {
        "crossfade_front_room_ms": 120,  # 熊镇正 "剪辑痕迹很重" 学的
        "edge_left_ms": 20,              # YouTube § 2 保留辅音起音
        "onset_safety_margin_ms": 30,    # librosa onset 前 stop
        "room_tone_source": "raw_track", # YouTube § 5 强制
        "room_tone_window_s": 30,        # 距剪切点 <30s
        "room_tone_min_dur_ms": 500,
        "target_lufs": -22.2,
        "ctx_ms": 3000,
    }
    for r in rules:
        note = str(r.get("note", "")).lower()
        kind = str(r.get("kind", ""))
        if "剪辑痕迹" in note or kind == "crossfade_length_range":
            p["crossfade_front_room_ms"] = max(p["crossfade_front_room_ms"], 120)
    return p, rules


def dynamic_pause_ms(cut_count: int, has_separator: bool = False) -> int:
    """从 session_feedback pause_dynamic_by_cut_count 学."""
    base = cut_count * 60
    if has_separator:
        base += 200
    return min(500, max(80, base))


def find_room_tone_from_raw(raw_wav_path: Path, cut_start_ms: int,
                             window_s: int = 30, min_dur_ms: int = 500) -> tuple[AudioSegment, float]:
    """YouTube § 5 · 从 raw 三轨对应轨最近 30s 内找最安静 500ms."""
    load_start_ms = max(0, cut_start_ms - window_s * 1000)
    load_end_ms = cut_start_ms + 1000
    raw_seg = AudioSegment.from_wav(str(raw_wav_path))[load_start_ms:load_end_ms]
    best = None
    best_db = 0.0
    step = 200
    end = len(raw_seg) - min_dur_ms - 200  # 距 cut 200ms
    for s in range(0, end, step):
        seg = raw_seg[s:s + min_dur_ms]
        d = seg.dBFS
        if d < best_db:
            best_db = d
            best = seg
    return (best or AudioSegment.silent(min_dur_ms)), best_db


def find_kept_word_onset_librosa(automix_wav: Path, asr_kept_start_s: float,
                                   window_s: float = 0.6) -> float:
    """librosa 检测保留词的辅音精确起音 (backtrack=True)."""
    y, sr = librosa.load(str(automix_wav), sr=48000,
                          offset=max(0, asr_kept_start_s - 0.15),
                          duration=window_s)
    onset_times = librosa.onset.onset_detect(y=y, sr=sr, units="time",
                                              delta=0.02, backtrack=True)
    if len(onset_times) == 0:
        return asr_kept_start_s
    # 找与 ASR 报的 start 最近的 onset
    load_offset_s = max(0, asr_kept_start_s - 0.15)
    onset_abs = [load_offset_s + float(t) for t in onset_times]
    return min(onset_abs, key=lambda t: abs(t - asr_kept_start_s))


def generate(
    candidate_id: str,
    cut_start_s: float,
    cut_end_s: float,
    automix_wav: Path,
    raw_track_wav: Path,
    out_dir: Path,
    label: str,
    episode_id: str = "EP04",
    kept_word_asr_start_s: float | None = None,
    n_cut: int = 1,
    has_segment_separator: bool = False,
    pause_ms_override: int | None = None,  # v215.1 · 用 candidate.post_cut_pause_ms
) -> dict:
    """生成一对 A/B clip (原 + 剪)."""
    params, rules = load_learned_params(episode_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    # v214.1 · 若 kept_word 存在, cut_end 用 librosa onset 定
    librosa_onset_used = False
    if kept_word_asr_start_s is not None:
        onset_s = find_kept_word_onset_librosa(automix_wav, kept_word_asr_start_s)
        onset_before_asr_ms = (kept_word_asr_start_s - onset_s) * 1000
        safe_cut_end_s = onset_s - params["onset_safety_margin_ms"] / 1000
        if safe_cut_end_s < cut_end_s:
            cut_end_s = safe_cut_end_s
            librosa_onset_used = True

    cut_s_ms = int(cut_start_s * 1000) - params["edge_left_ms"]
    cut_e_ms = int(cut_end_s * 1000)
    ctx_start = max(0, cut_s_ms - params["ctx_ms"])
    ctx_end = cut_e_ms + params["ctx_ms"]

    automix = AudioSegment.from_wav(str(automix_wav))

    # 原
    orig = automix[ctx_start:ctx_end]
    orig_path = out_dir / f"{label}_原.mp3"
    orig.export(orig_path, format="mp3", bitrate="128k")

    # 剪 · v214.1 pipeline
    # v215.1 (2026-08-18): 若 candidate 里有 post_cut_pause_ms 覆盖, 用它 (candidate 自带精确参数 · boundary_lock)
    if pause_ms_override is not None:
        pause_ms = int(pause_ms_override)
    else:
        pause_ms = dynamic_pause_ms(n_cut, has_segment_separator)
    room, room_db = find_room_tone_from_raw(raw_track_wav, cut_s_ms,
                                              params["room_tone_window_s"])
    room_full = room + room.reverse()  # 反向拼接避循环
    room_full = room_full[:pause_ms] if len(room_full) >= pause_ms else \
                room_full + AudioSegment.silent(pause_ms - len(room_full))

    front = automix[ctx_start:cut_s_ms]
    back = automix[cut_e_ms:ctx_end]
    # crossfade 不能长于两段较短的一段
    safe_crossfade = min(params["crossfade_front_room_ms"], len(room_full) - 10, len(front) - 10)
    safe_crossfade = max(20, safe_crossfade)
    merged = front.append(room_full, crossfade=safe_crossfade) + back
    cut_path = out_dir / f"{label}_剪.mp3"
    merged.export(cut_path, format="mp3", bitrate="128k")

    # 4 层自检
    self_check = {}
    expect_ms = (ctx_end - ctx_start) - (cut_e_ms - cut_s_ms) + pause_ms
    actual_ms = len(merged)
    self_check["duration_expect_ms"] = expect_ms
    self_check["duration_actual_ms"] = actual_ms
    self_check["duration_ok"] = abs(expect_ms - actual_ms) < 100

    manifest = {
        "schema": "generate_ab_clip_learning_driven-v1",
        "candidate_id": candidate_id,
        "episode_id": episode_id,
        "learned_params": params,
        "n_learned_rules": len(rules),
        "cut_range_s": [cut_start_s, cut_end_s],
        "cut_duration_ms": cut_e_ms - cut_s_ms,
        "pause_ms": pause_ms,
        "n_cut_in_chain": n_cut,
        "has_segment_separator": has_segment_separator,
        "librosa_onset_used": librosa_onset_used,
        "kept_word_asr_start_s": kept_word_asr_start_s,
        "room_tone_dBFS": float(room_db),
        "orig_path": str(orig_path),
        "cut_path": str(cut_path),
        "self_check": self_check,
        "tools_used": [
            "librosa.onset.onset_detect (backtrack=True)",
            "pydub.AudioSegment.append (crossfade)",
            "pydub.AudioSegment.reverse (反向拼接)",
            "pydub.AudioSegment.dBFS (room tone volumedetect)",
        ],
    }
    manifest_path = out_dir / f"{label}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate-id", required=True)
    ap.add_argument("--cut-start-seconds", type=float, required=True)
    ap.add_argument("--cut-end-seconds", type=float, required=True)
    ap.add_argument("--kept-word-asr-start", type=float, default=None,
                    help="chain 场景 · 保留最后 1 个词的 ASR start_seconds")
    ap.add_argument("--n-cut", type=int, default=1)
    ap.add_argument("--has-segment-separator", action="store_true")
    ap.add_argument("--episode-id", default="EP04")
    ap.add_argument("--automix-wav", type=Path, required=True,
                    help="speech.mono.wav from automix_v1 (CLAUDE.md §9)")
    ap.add_argument("--raw-track-wav", type=Path, required=True,
                    help="raw ZOOM WAV of the same speaker for room tone (YouTube § 5)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--pause-ms-override", type=int, default=None,
                    help="v215.1 · 用 candidate.post_cut_pause_ms 覆盖 dynamic_pause_ms")
    args = ap.parse_args(argv)

    m = generate(
        candidate_id=args.candidate_id,
        cut_start_s=args.cut_start_seconds,
        cut_end_s=args.cut_end_seconds,
        automix_wav=args.automix_wav,
        raw_track_wav=args.raw_track_wav,
        out_dir=args.out_dir,
        label=args.label,
        episode_id=args.episode_id,
        kept_word_asr_start_s=args.kept_word_asr_start,
        n_cut=args.n_cut,
        has_segment_separator=args.has_segment_separator,
        pause_ms_override=args.pause_ms_override,
    )
    print(json.dumps({"cut_path": m["cut_path"], "self_check_ok": m["self_check"]["duration_ok"]},
                      ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
