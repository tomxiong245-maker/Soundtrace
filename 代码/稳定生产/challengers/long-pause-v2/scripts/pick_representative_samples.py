#!/usr/bin/env python3
"""long-pause-v2 · 从 EP04 三轨挑代表性全轨共同静音候选，生成审核 A/B。

- 用 primary/bleed/ambiguous 词级时间戳求"三轨共同无词区间"；
- 按时长分档：[1.0-1.5s, 1.5-2.5s, 2.5-4.0s, 4.0-8.0s, 8.0+s]，每档挑 2-3 条；
- 生成 original.mp3（原音频那段）与 proposed-cut.mp3（保留 0.75s 中间压缩后）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _no_word_intervals(words_by_track: dict[str, list[dict]],
                       episode_seconds: float,
                       merge_gap: float = 0.1) -> list[tuple[float, float]]:
    """三轨的联合词覆盖区间的补集，即三轨都没词的区间。"""
    covers: list[tuple[float, float]] = []
    for ws in words_by_track.values():
        for w in ws:
            covers.append((float(w["start_seconds"]), float(w["end_seconds"])))
    covers.sort()
    merged: list[list[float]] = []
    for s, e in covers:
        if merged and s <= merged[-1][1] + merge_gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    silences: list[tuple[float, float]] = []
    prev = 0.0
    for s, e in merged:
        if s > prev:
            silences.append((prev, s))
        prev = e
    if prev < episode_seconds:
        silences.append((prev, episode_seconds))
    return silences


def _pick_samples(silences: list[tuple[float, float]], bins: list[tuple[float, float]],
                  per_bin: int = 3) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for lo, hi in bins:
        label = f"{lo:g}-{hi:g}s"
        band = [(s, e) for s, e in silences if lo <= (e - s) < hi]
        # 尽量取跨节目位置：等距选 per_bin 个
        if not band:
            continue
        if len(band) <= per_bin:
            for s, e in band:
                out.append((s, e, label))
            continue
        idxs = [int(round(i * (len(band) - 1) / (per_bin - 1))) for i in range(per_bin)]
        for i in idxs:
            s, e = band[i]
            out.append((s, e, label))
    return out


def _mp3(src: Path, out: Path, s: float, dur: float, extra: list[str] | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-ss", f"{s:.3f}", "-t", f"{dur:.3f}",
           "-i", str(src)]
    if extra:
        cmd += extra
    cmd += ["-c:a", "libmp3lame", "-b:a", "128k", "-ac", "1", str(out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity-dir", required=True,
                    help="含 track_*.classified.json 的目录")
    ap.add_argument("--mix-source", required=True,
                    help="用于生成 preview 的 speech mix 或原始三轨之一")
    ap.add_argument("--episode-seconds", type=float, required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--retain-seconds", type=float, default=0.75)
    ap.add_argument("--context-seconds", type=float, default=2.0)
    args = ap.parse_args()

    activity_dir = Path(args.activity_dir)
    words_by_track: dict[str, list[dict]] = {}
    for p in sorted(activity_dir.glob("track_*.classified.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        label = p.stem.replace(".classified", "")
        words_by_track[label] = d.get("words", [])

    silences = _no_word_intervals(words_by_track, args.episode_seconds)
    # 只挑 ≥ 1.0s 的静音
    silences = [(s, e) for s, e in silences if (e - s) >= 1.0]
    bins = [(1.0, 1.5), (1.5, 2.5), (2.5, 4.0), (4.0, 8.0), (8.0, 999.0)]
    samples = _pick_samples(silences, bins, per_bin=3)

    out_dir = Path(args.out_dir)
    (out_dir / "previews").mkdir(parents=True, exist_ok=True)

    candidates = []
    skipped = []
    for i, (s, e, band) in enumerate(samples, 1):
        cid = f"LP{i:03d}"
        try:
            dur = e - s
            # original：s-context 到 e+context
            pre = args.context_seconds
            post = args.context_seconds
            orig_start = max(0.0, s - pre)
            orig_dur = min(args.episode_seconds - orig_start, (e - s) + pre + post)
            _mp3(Path(args.mix_source),
                 out_dir / "previews" / f"{cid}.original.mp3",
                 orig_start, orig_dur)
            keep_before_end = min(pre, s)
            keep_after_start = e
            keep_before_start = orig_start
            pre_len = max(0.0, s - keep_before_start)
            post_len = max(0.0, min(args.episode_seconds - keep_after_start, post))
            gap_len = min(args.retain_seconds, dur)
            gap_start = s + max(0.0, (dur - args.retain_seconds) / 2)
            if pre_len < 0.1 or post_len < 0.1 or gap_len < 0.1:
                skipped.append({"cid": cid, "band": band,
                                 "reason": "context too small"})
                continue
            pre_part = out_dir / "previews" / f"{cid}._pre.mp3"
            gap_part = out_dir / "previews" / f"{cid}._gap.mp3"
            post_part = out_dir / "previews" / f"{cid}._post.mp3"
            _mp3(Path(args.mix_source), pre_part, keep_before_start, pre_len)
            _mp3(Path(args.mix_source), gap_part, gap_start, gap_len)
            _mp3(Path(args.mix_source), post_part, keep_after_start, post_len)
            concat_txt = out_dir / "previews" / f"{cid}._concat.txt"
            concat_txt.write_text(
                f"file '{pre_part.name}'\nfile '{gap_part.name}'\nfile '{post_part.name}'\n",
                encoding="utf-8")
            out_cut = out_dir / "previews" / f"{cid}.proposed-cut.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", concat_txt.name, "-c:a", "libmp3lame", "-b:a", "128k",
                 "-ac", "1", out_cut.name],
                check=True, cwd=str(out_dir / "previews"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for f in (pre_part, gap_part, post_part, concat_txt):
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass
            candidates.append({
                "candidate_id": cid,
                "time_bin": band,
                "start_seconds": round(s, 3),
                "end_seconds": round(e, 3),
                "duration_seconds": round(dur, 3),
                "context_seconds": args.context_seconds,
                "retain_seconds": args.retain_seconds,
                "previews": {
                    "original": f"previews/{cid}.original.mp3",
                    "proposed_cut": f"previews/{cid}.proposed-cut.mp3",
                },
                "policy": "review_only_no_automatic_accept",
            })
        except subprocess.CalledProcessError as e0:
            skipped.append({"cid": cid, "band": band, "reason": str(e0)})
    pkg = {
        "schema_version": "long-pause-samples-v1",
        "episode_id": "EP04",
        "sample_rate_hz": 48000,
        "candidates": candidates,
        "counts_by_bin": {},
    }
    for c in candidates:
        pkg["counts_by_bin"][c["time_bin"]] = pkg["counts_by_bin"].get(c["time_bin"], 0) + 1
    (out_dir / "samples.json").write_text(
        json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total": len(candidates),
                      "by_bin": pkg["counts_by_bin"],
                      "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
