#!/usr/bin/env python3
"""为 EP04-v4 178 段全部剪切生成 review_package.json（供 mvp.html 使用）。

结构对齐 稳定生产/challengers/review-product-v1/mvp.html：
- candidates[].text_tracks[<track_id>].words[]  ← 从 canonical.json 取该段时间±context 的词
- candidates[].previews.{original_path, proposed_cut_path, ...}  ← 复用已生成 mp3
- 词根据 in-cut 判定：word 时间与 [start_seconds, end_seconds] 有重叠 → 红色 strikethrough
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load_words(canonical_path: Path) -> list[dict]:
    d = json.loads(canonical_path.read_text(encoding="utf-8"))
    return d.get("words", [])


def slice_words(words: list[dict], t0: float, t1: float) -> list[dict]:
    out = []
    for w in words:
        we = float(w.get("end_seconds", 0))
        ws = float(w.get("start_seconds", 0))
        if we <= t0 or ws >= t1:
            continue
        out.append({
            "text": w.get("text", ""),
            "start_seconds": ws,
            "end_seconds": we,
            "classification": w.get("classification", "unknown"),
        })
    return out


def _sha_stub(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-changes-json", required=True)
    ap.add_argument("--canonical-dir", required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--context-seconds", type=float, default=6.0)
    ap.add_argument("--gate-context-seconds", type=float, default=3.0)
    ap.add_argument("--gate-clip-seconds", type=float, default=20.0)
    args = ap.parse_args()

    changes = json.loads(Path(args.all_changes_json).read_text(encoding="utf-8"))
    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))

    # 加载三轨词
    words_by_track = {}
    for label in ("track_01", "track_02", "track_03"):
        p = Path(args.canonical_dir) / f"{label}.canonical.json"
        if not p.exists():
            print(f"WARN missing {p}")
            continue
        words_by_track[label] = load_words(p)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = []

    # 建立每段 sync_cut 的 source 映射
    def find_source_for_sync(m):
        # 找覆盖该合并段的第一条 cut 的 source & reason
        for c in edl["cuts"]:
            if c["action"] != "sync_cut":
                continue
            if c["start_sample"] < m["end_sample"] and c["end_sample"] > m["start_sample"]:
                return c
        return None

    # sync cuts
    for i, m in enumerate(edl["sync_cuts_merged"], 1):
        cid = f"S{i:03d}"
        src = find_source_for_sync(m) or {}
        source_track = src.get("track_id") or "track_02"  # 默认 track_02（主讲多）
        # 但 sync cut 是全轨的，source_track_id 只用于"哪条轨的原转写做上文"
        # 对长停顿类无 track_id：选 words 最多的
        if not src.get("track_id"):
            # 挑该段时间窗内话最多的轨
            t0 = m["start_sample"] / edl["sample_rate_hz"] - args.context_seconds
            t1 = m["end_sample"] / edl["sample_rate_hz"] + args.context_seconds
            best_n = -1
            for tr, ws in words_by_track.items():
                n = len([w for w in ws if float(w.get("end_seconds", 0)) > t0 and float(w.get("start_seconds", 0)) < t1])
                if n > best_n:
                    best_n = n; source_track = tr
        s = m["start_sample"] / edl["sample_rate_hz"]
        e = m["end_sample"] / edl["sample_rate_hz"]
        # source_type 用于 reason_key 展示
        srclabel = src.get("source", "long_pause")
        if srclabel == "真人-filler":
            reason = "filler_immediate_repetition"
        elif srclabel == "LEARNED_v4_transient":
            reason = src.get("reason_key", "transient")
        elif srclabel == "LONG_PAUSE_v4":
            reason = "long_pause"
        else:
            reason = srclabel
        text_tracks = {}
        for tr, ws in words_by_track.items():
            picked = slice_words(ws, s - args.context_seconds, e + args.context_seconds)
            text_tracks[tr] = {"label": tr, "words": picked}
        candidates.append({
            "candidate_id": cid,
            "reason_key": reason,
            "action": "sync_cut",
            "source_track_id": source_track,
            "start_sample": m["start_sample"],
            "end_sample": m["end_sample"],
            "start_seconds": round(s, 3),
            "end_seconds": round(e, 3),
            "duration_seconds": round(e - s, 3),
            "silence_seconds": src.get("silence_seconds"),
            "cut_seconds": src.get("cut_seconds"),
            "reason_hint": src.get("reason_key"),
            "semantic_sha256": _sha_stub(cid),
            "global_cut": {
                "start_sample": m["start_sample"],
                "end_sample": m["end_sample"],
                "applies_to_tracks": list(words_by_track.keys()),
            },
            "text_tracks": text_tracks,
            "previews": {
                "original_path": f"previews/{cid}.original.mp3",
                "proposed_cut_path": f"previews/{cid}.edited.mp3",
                "original_sha256": _sha_stub(cid + ".original"),
                "proposed_cut_sha256": _sha_stub(cid + ".edited"),
            },
        })

    # gate segments
    g_index = 0
    for track, segs in edl["gates_by_track"].items():
        for g in segs:
            g_index += 1
            cid = f"G{g_index:03d}"
            s = g["start_sample"] / edl["sample_rate_hz"]
            # gate preview 时长
            t0 = s - args.gate_context_seconds
            t1 = s + args.gate_clip_seconds
            # 查置信度
            conf = "?"
            for c in edl["cuts"]:
                if c["action"] == "gate" and c.get("track_id") == track and \
                   c["start_sample"] <= g["start_sample"] < c["end_sample"]:
                    conf = c.get("confidence", "?"); break
            text_tracks = {}
            for tr, ws in words_by_track.items():
                text_tracks[tr] = {
                    "label": tr,
                    "words": slice_words(ws, t0, t1),
                }
            candidates.append({
                "candidate_id": cid,
                "reason_key": f"crosstalk_gate_{conf}",
                "action": "gate",
                "confidence": conf,
                "source_track_id": track,
                "start_sample": g["start_sample"],
                # gate preview 只取 clip；in-cut 展示：把该段所有 word 标红？
                # 用 gate 起点到 clip 长度作为视觉"覆盖区间"，让 mvp.html 划出源轨那部分文字
                "end_sample": g["start_sample"] + int(args.gate_clip_seconds * edl["sample_rate_hz"]),
                "start_seconds": round(s, 3),
                "end_seconds": round(s + args.gate_clip_seconds, 3),
                "duration_seconds": round(g["duration_seconds"], 3),
                "note": "源轨 gate（不改整片时长；只把该轨此段音量降到 0，其它轨主讲保留）",
                "semantic_sha256": _sha_stub(cid),
                "global_cut": {
                    "start_sample": g["start_sample"],
                    "end_sample": g["end_sample"],
                    "applies_to_tracks": [track],
                },
                "text_tracks": text_tracks,
                "previews": {
                    "original_path": f"previews/{cid}.original.mp3",
                    "proposed_cut_path": f"previews/{cid}.edited.mp3",
                    "original_sha256": _sha_stub(cid + ".original"),
                    "proposed_cut_sha256": _sha_stub(cid + ".edited"),
                },
            })

    pkg = {
        "schema_version": "review-product-mvp-v2-EP04-v4",
        "episode_id": "EP04-v4",
        "package_id": "EP04-v4-all-changes",
        "review_manifest_sha256": _sha_stub("EP04-v4-all-changes"),
        "sample_rate_hz": edl["sample_rate_hz"],
        "track_count": len(words_by_track),
        "tracks": [{"track_id": t, "label": t} for t in words_by_track],
        "mvp_limits": {
            "adjust_enabled": False,
            "adjust_reason": "本页只为浏览 EP04-v4 剪切，未启用 adjust。",
        },
        "candidates": candidates,
    }
    (out_dir / "review_package.json").write_text(
        json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidates": len(candidates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
