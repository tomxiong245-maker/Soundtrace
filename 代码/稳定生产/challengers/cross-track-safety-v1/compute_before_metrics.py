#!/usr/bin/env python3
"""
compute_before_metrics.py — 独立复现观察到的 8 项数字，并计算所有基线 SHA-256

不写死任何数字。跑一遍，输出到 before_metrics.json 供后续比对。
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "main/runs/EP03-freshrun-20260810-1730"
CH = ROOT / "稳定生产/challengers/cross-track-safety-v1"

CANDS_PATH = RUN / "09_review/package/edit_candidates.json"
ASR_CANDS_PATH = RUN / "09_review/asr_candidates_report.json"
FE_CANDS_PATH = ROOT / "审核前端/candidates.json"

FEMALE_CLASSIFIED = RUN / "06_activity/female.classified.json"
MALE_CLASSIFIED = RUN / "06_activity/male.classified.json"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    # ============================================================
    # 1. 计算基线 SHA-256
    # ============================================================
    files_to_hash = [
        ROOT / "稳定生产/scripts/generate_cut_candidates.py",
        ROOT / "稳定生产/rules/candidate-generation.v1.json",
        ROOT / "审核前端/index.html",
        ROOT / "审核前端/candidates.json",
        ROOT / "端到端学习剪辑/代码/render_approved_edl.py",
        FEMALE_CLASSIFIED,
        MALE_CLASSIFIED,
        CANDS_PATH,
        ASR_CANDS_PATH,
        RUN / "05_asr/female.transcript.json",
        RUN / "05_asr/male.transcript.json",
    ]
    # track_activity.json 可能不存在
    ta_path = ROOT / "审核前端/track_activity.json"
    if ta_path.exists():
        files_to_hash.append(ta_path)

    baseline_sha = {}
    for f in files_to_hash:
        if f.exists():
            baseline_sha[str(f.relative_to(ROOT))] = sha256_file(f)
        else:
            baseline_sha[str(f.relative_to(ROOT))] = None

    # ============================================================
    # 2. 加载数据
    # ============================================================
    fe_data = load(FE_CANDS_PATH)
    fe_cands = fe_data["candidates"]

    female_cls = load(FEMALE_CLASSIFIED)
    male_cls = load(MALE_CLASSIFIED)

    female_words = female_cls["words"]
    male_words = male_cls["words"]

    def get_words(track: str):
        return female_words if track == "female" else male_words

    def words_in_interval(track: str, start_s: float, end_s: float):
        return [w for w in get_words(track) if w["end_seconds"] > start_s and w["start_seconds"] < end_s]

    # ============================================================
    # 3. 独立复现 8 项数字
    # ============================================================
    from collections import Counter

    total = len(fe_cands)
    by_category = Counter(c["category"] for c in fe_cands)

    source_no_primary = 0
    source_majority_bleed = 0
    long_pause_other_has_primary = 0
    long_pause_other_has_3plus_primary = 0

    for c in fe_cands:
        src = c.get("source_track") or c.get("asr_source_track", "?")
        s = c["start_seconds"]
        e = c["end_seconds"]

        # source 轨在候选区间内的词
        source_words_in_cut = words_in_interval(src, s, e)
        source_primary = sum(1 for w in source_words_in_cut if w["activity"]["classification"] == "primary")
        source_bleed = sum(1 for w in source_words_in_cut if w["activity"]["classification"] == "bleed")

        if source_primary == 0:
            source_no_primary += 1
        if source_bleed > source_primary:
            source_majority_bleed += 1

        # long_pause 独立统计
        if c["category"] == "long_pause":
            other = "male" if src == "female" else "female"
            other_words_in_cut = words_in_interval(other, s, e)
            other_primary = sum(1 for w in other_words_in_cut if w["activity"]["classification"] == "primary")
            if other_primary >= 1:
                long_pause_other_has_primary += 1
            if other_primary >= 3:
                long_pause_other_has_3plus_primary += 1

    metrics = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "candidates_source": str(FE_CANDS_PATH.relative_to(ROOT)),
        "total_candidates": total,
        "by_category": dict(by_category),
        "source_track_no_primary_count": source_no_primary,
        "source_track_majority_bleed_count": source_majority_bleed,
        "long_pause_other_track_has_primary_count": long_pause_other_has_primary,
        "long_pause_other_track_has_3plus_primary_count": long_pause_other_has_3plus_primary,
    }

    expected = {
        "total_candidates": 56,
        "long_pause": 34,
        "immediate_repetition": 15,
        "filler_hesitation": 7,
        "source_track_no_primary_count": 27,
        "source_track_majority_bleed_count": 28,
        "long_pause_other_track_has_primary_count": 27,
        "long_pause_other_track_has_3plus_primary_count": 21,
    }

    diffs = []
    def check(name, actual, expect):
        ok = actual == expect
        diffs.append({"metric": name, "expected": expect, "actual": actual, "match": ok})
        return ok

    all_ok = True
    all_ok &= check("total_candidates", total, expected["total_candidates"])
    all_ok &= check("long_pause", by_category.get("long_pause", 0), expected["long_pause"])
    all_ok &= check("immediate_repetition", by_category.get("immediate_repetition", 0), expected["immediate_repetition"])
    all_ok &= check("filler_hesitation", by_category.get("filler_hesitation", 0), expected["filler_hesitation"])
    all_ok &= check("source_track_no_primary_count", source_no_primary, expected["source_track_no_primary_count"])
    all_ok &= check("source_track_majority_bleed_count", source_majority_bleed, expected["source_track_majority_bleed_count"])
    all_ok &= check("long_pause_other_track_has_primary_count", long_pause_other_has_primary, expected["long_pause_other_track_has_primary_count"])
    all_ok &= check("long_pause_other_track_has_3plus_primary_count", long_pause_other_has_3plus_primary, expected["long_pause_other_track_has_3plus_primary_count"])

    print("=" * 60)
    print("BASELINE REPRODUCTION")
    print("=" * 60)
    for d in diffs:
        mark = "✓" if d["match"] else "✗"
        print(f"  {mark} {d['metric']:60} expected={d['expected']:>5}  actual={d['actual']}")
    print()
    print("整体:", "PASS" if all_ok else "FAIL — 停止施工")

    # ============================================================
    # 4. 写产物
    # ============================================================
    output = {
        "task": "EP03 跨轨误删安全修复 Challenger v1 · Before Metrics",
        "generated_at": metrics["generated_at"],
        "baseline_reproduction_result": "PASS" if all_ok else "FAIL",
        "reproduction_details": diffs,
        "metrics": metrics,
        "baseline_sha256": baseline_sha,
    }

    CH.mkdir(parents=True, exist_ok=True)
    (CH / "before_metrics.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写入 {CH / 'before_metrics.json'}")

    if not all_ok:
        raise SystemExit("基线复现失败——按任务书要求停止施工")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
