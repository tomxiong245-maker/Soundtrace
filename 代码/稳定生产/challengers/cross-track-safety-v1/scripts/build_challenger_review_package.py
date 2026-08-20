#!/usr/bin/env python3
"""build_challenger_review_package.py — 为 Challenger safe_candidates 打独立审核包

每条候选直接嵌入完整上下文两轨词表 + safety 状态。前端不再拼两份 JSON。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


CONTEXT_SECONDS = 5.0
SAMPLE_RATE = 48000


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_words(classified_path: Path) -> list[dict]:
    d = json.loads(classified_path.read_text(encoding="utf-8"))
    words = []
    for w in d.get("words", []):
        act = w.get("activity", {})
        words.append({
            "word_id": w.get("word_id"),
            "text": w.get("text", ""),
            "start_seconds": float(w.get("start_seconds", 0)),
            "end_seconds": float(w.get("end_seconds", 0)),
            "start_sample": w.get("start_sample"),
            "end_sample": w.get("end_sample"),
            "classification": act.get("classification"),
        })
    return words


def words_in_window(words, s, e):
    return [w for w in words if w["end_seconds"] > s and w["start_seconds"] < e]


def run(cmd, description=""):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed [{description}]: {r.stderr.decode('utf-8', errors='ignore')[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--safe-candidates", type=Path, required=True)
    ap.add_argument("--blocked-candidates", type=Path, required=True)
    ap.add_argument("--female-classified", type=Path, required=True)
    ap.add_argument("--male-classified", type=Path, required=True)
    ap.add_argument("--speech-mix", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--ffmpeg", type=Path,
                    default=PROJECT_ROOT / ".tools/bin/ffmpeg")
    args = ap.parse_args()

    for p in (args.safe_candidates, args.blocked_candidates, args.female_classified,
              args.male_classified, args.speech_mix, args.ffmpeg):
        if not p.is_absolute():
            ap.error(f"path must be absolute: {p}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    previews_dir = args.output_dir / "previews"
    previews_dir.mkdir(exist_ok=True)

    safe_data = json.loads(args.safe_candidates.read_text(encoding="utf-8"))
    blocked_data = json.loads(args.blocked_candidates.read_text(encoding="utf-8"))
    female_words = load_words(args.female_classified)
    male_words = load_words(args.male_classified)

    mix_sha = sha256_file(args.speech_mix)

    enriched = []
    context_stats = {
        "candidates_checked": 0,
        "context_word_coverage_all_100pct": True,
        "missing_context_words": 0,
        "unexpected_context_words": 0,
    }

    for c in safe_data["candidates"]:
        cid = c["candidate_id"]
        s, e = c["start_seconds"], c["end_seconds"]
        ctx_s = max(0.0, s - CONTEXT_SECONDS)
        ctx_e = e + CONTEXT_SECONDS

        f_ctx = words_in_window(female_words, ctx_s, ctx_e)
        m_ctx = words_in_window(male_words, ctx_s, ctx_e)

        def annotate(ws):
            annotated = []
            for w in ws:
                in_cut = w["start_seconds"] < e and w["end_seconds"] > s
                annotated.append({**w, "in_cut": in_cut, "affected_by_global_cut": in_cut})
            return annotated

        f_ctx_ann = annotate(f_ctx)
        m_ctx_ann = annotate(m_ctx)

        original_mp3 = previews_dir / f"{cid}.original.mp3"
        proposed_mp3 = previews_dir / f"{cid}.proposed-cut.mp3"

        original_start_sample = round(ctx_s * SAMPLE_RATE)
        original_end_sample = round(ctx_e * SAMPLE_RATE)
        run([
            str(args.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(args.speech_mix),
            "-af", f"atrim=start_sample={original_start_sample}:end_sample={original_end_sample},asetpts=PTS-STARTPTS",
            "-c:a", "libmp3lame", "-b:a", "128k", str(original_mp3),
        ], description=f"original {cid}")

        crossfade_samples = round(0.05 * SAMPLE_RATE)
        cut_start_sample = round(s * SAMPLE_RATE)
        cut_end_sample = round(e * SAMPLE_RATE)
        filter_graph = (
            f"[0:a]atrim=start_sample={original_start_sample}:end_sample={cut_start_sample},asetpts=PTS-STARTPTS[left];"
            f"[0:a]atrim=start_sample={cut_end_sample}:end_sample={original_end_sample},asetpts=PTS-STARTPTS[right];"
            f"[left][right]acrossfade=ns={crossfade_samples}:c1=tri:c2=tri[out]"
        )
        run([
            str(args.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(args.speech_mix),
            "-filter_complex", filter_graph,
            "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k", str(proposed_mp3),
        ], description=f"proposed-cut {cid}")

        context_stats["candidates_checked"] += 1
        expected_f = {w["word_id"] for w in f_ctx}
        actual_f = {w["word_id"] for w in f_ctx_ann}
        if expected_f != actual_f:
            context_stats["context_word_coverage_all_100pct"] = False
            context_stats["missing_context_words"] += len(expected_f - actual_f)
            context_stats["unexpected_context_words"] += len(actual_f - expected_f)
        expected_m = {w["word_id"] for w in m_ctx}
        actual_m = {w["word_id"] for w in m_ctx_ann}
        if expected_m != actual_m:
            context_stats["context_word_coverage_all_100pct"] = False
            context_stats["missing_context_words"] += len(expected_m - actual_m)
            context_stats["unexpected_context_words"] += len(actual_m - expected_m)

        enriched.append({
            **c,
            "context_seconds": CONTEXT_SECONDS,
            "context_start_seconds": ctx_s,
            "context_end_seconds": ctx_e,
            "context_words": {"female": f_ctx_ann, "male": m_ctx_ann},
            "previews": {
                "original": f"previews/{cid}.original.mp3",
                "proposed_cut": f"previews/{cid}.proposed-cut.mp3",
                "original_sha256": sha256_file(original_mp3),
                "proposed_cut_sha256": sha256_file(proposed_mp3),
            },
            "speech_mix_sha256": mix_sha,
        })

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "cross-track-safety-v1 Challenger",
        "global_cut_notice": "此 EDL 为全局时间线剪切，批准后相同时间段会从所有轨道删除。source_track 只表明候选来源轨。",
        "candidates": enriched,
        "blocked_reference": {
            "path": str(args.blocked_candidates),
            "count": len(blocked_data["candidates"]),
            "note": "被安全门拦截的候选，只做只读审阅，不进本审核包",
        },
        "provenance": {
            "safe_candidates_sha256": sha256_file(args.safe_candidates),
            "blocked_candidates_sha256": sha256_file(args.blocked_candidates),
            "female_classified_sha256": sha256_file(args.female_classified),
            "male_classified_sha256": sha256_file(args.male_classified),
            "speech_mix_sha256": mix_sha,
        },
        "context_completeness": context_stats,
    }

    (args.output_dir / "review_package.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"=== 审核包生成完成 ===")
    print(f"  SAFE 候选: {len(enriched)}")
    print(f"  previews: {len(list(previews_dir.glob('*.mp3')))} 个 mp3")
    print(f"  context_completeness: {context_stats}")
    print(f"  输出: {args.output_dir / 'review_package.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
