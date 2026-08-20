#!/usr/bin/env python3
"""EP04-v4 · 全量剪辑对比：全部 86 段 sync cut + 92 段 gate。

每段：
- sync_cut：原音 ±6s + 剪后 ±6s
- gate：原音前 3s + gate 段前 20s + 剪后同段
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _mp3(src: Path, out: Path, start: float, dur: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}",
         "-i", str(src), "-c:a", "libmp3lame", "-b:a", "96k",
         "-ac", "1", "-ar", "48000", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def hms(s: float) -> str:
    m, ss = divmod(s, 60); h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{ss:05.2f}" if h else f"{int(m):02d}:{ss:05.2f}"


def original_to_edited_seconds(orig_s: float,
                               cuts_sorted: list[tuple[int, int]],
                               sr: int) -> float:
    removed = 0
    x = int(round(orig_s * sr))
    for s, e in cuts_sorted:
        if x >= e:
            removed += (e - s)
        elif x >= s:
            removed += (x - s); break
        else:
            break
    return (x - removed) / sr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", required=True)
    ap.add_argument("--original-mix", required=True)
    ap.add_argument("--speech-only-wav", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sync-context-seconds", type=float, default=6.0)
    ap.add_argument("--gate-pre-seconds", type=float, default=3.0)
    ap.add_argument("--gate-clip-seconds", type=float, default=20.0)
    args = ap.parse_args()

    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))
    sr = edl["sample_rate_hz"]
    sync_cuts_sorted = sorted([(c["start_sample"], c["end_sample"])
                               for c in edl["sync_cuts_merged"]])

    out_dir = Path(args.out_dir)
    (out_dir / "previews").mkdir(parents=True, exist_ok=True)

    entries_sync = []
    entries_gate = []

    # 记录 cuts 里每段来源（按 start_sample 匹配到 merged 段）
    src_index = {}
    for c in edl["cuts"]:
        if c["action"] == "sync_cut":
            src_index.setdefault(c["start_sample"], []).append(c)

    # 1) 86 段 sync_cut
    for i, m in enumerate(edl["sync_cuts_merged"], 1):
        s_sec = m["start_sample"] / sr
        e_sec = m["end_sample"] / sr
        dur = m["duration_seconds"]
        # 找源信息（最接近的 cut）
        srcs = []
        for c in edl["cuts"]:
            if c["action"] != "sync_cut": continue
            # overlap 判断
            if c["start_sample"] < m["end_sample"] and c["end_sample"] > m["start_sample"]:
                srcs.append(c)
        source_kinds = []
        for c in srcs:
            if c["source"] == "真人-filler":
                source_kinds.append("filler(真人)")
            elif c["source"] == "LEARNED_v4_transient":
                source_kinds.append(f"transient/{c.get('reason_key','?')}")
            elif c["source"] == "LONG_PAUSE_v4":
                source_kinds.append(f"long_pause(silence={c.get('silence_seconds','?')}s)")
        cid = f"S{i:03d}"
        # original ±context
        pre = args.sync_context_seconds; post = args.sync_context_seconds
        o_start = max(0.0, s_sec - pre)
        o_dur = (e_sec - o_start) + post
        _mp3(Path(args.original_mix),
             out_dir / "previews" / f"{cid}.original.mp3", o_start, o_dur)
        # edited：映射到剪后时间
        e_anchor = original_to_edited_seconds(s_sec, sync_cuts_sorted, sr)
        e_start = max(0.0, e_anchor - pre)
        e_dur = pre + post
        _mp3(Path(args.speech_only_wav),
             out_dir / "previews" / f"{cid}.edited.mp3", e_start, e_dur)
        entries_sync.append({
            "cid": cid,
            "position": hms(s_sec),
            "orig_start_seconds": round(s_sec, 3),
            "orig_end_seconds": round(e_sec, 3),
            "duration_seconds": round(dur, 3),
            "sources": source_kinds,
            "previews": {"original": f"previews/{cid}.original.mp3",
                         "edited":   f"previews/{cid}.edited.mp3"},
        })

    # 2) 92 段 gate（按 track 各段）
    idx = 0
    for track, segs in edl["gates_by_track"].items():
        for g in segs:
            idx += 1
            cid = f"G{idx:03d}"
            s_sec = g["start_sample"] / sr
            dur = g["duration_seconds"]
            pre = args.gate_pre_seconds
            clip = args.gate_clip_seconds
            start = max(0.0, s_sec - pre)
            total = pre + clip
            _mp3(Path(args.original_mix),
                 out_dir / "previews" / f"{cid}.original.mp3", start, total)
            _mp3(Path(args.speech_only_wav),
                 out_dir / "previews" / f"{cid}.edited.mp3", start, total)
            # 找源置信度
            conf = "?"
            for c in edl["cuts"]:
                if c["action"] == "gate" and c.get("track_id") == track and \
                   c["start_sample"] <= g["start_sample"] < c["end_sample"]:
                    conf = c.get("confidence", "?"); break
            entries_gate.append({
                "cid": cid,
                "position": hms(s_sec),
                "orig_start_seconds": round(s_sec, 3),
                "duration_seconds": round(dur, 3),
                "track_id": track,
                "confidence": conf,
                "previews": {"original": f"previews/{cid}.original.mp3",
                             "edited":   f"previews/{cid}.edited.mp3"},
            })

    doc = {
        "schema_version": "v4-all-changes-v1",
        "sync_context_seconds": args.sync_context_seconds,
        "gate_pre_seconds": args.gate_pre_seconds,
        "gate_clip_seconds": args.gate_clip_seconds,
        "sync_count": len(entries_sync),
        "gate_count": len(entries_gate),
        "sync_entries": entries_sync,
        "gate_entries": entries_gate,
    }
    (out_dir / "all_changes.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sync": len(entries_sync), "gate": len(entries_gate),
                      "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
