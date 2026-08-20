#!/usr/bin/env python3
"""compute_after_metrics.py — 验证 SAFE 候选满足所有安全指标"""
from __future__ import annotations
import json, hashlib, shutil, subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "main/runs/EP03-cross-track-safety-v1"
CH = ROOT / "稳定生产/challengers/cross-track-safety-v1"
RUN = ROOT / "main/runs/EP03-freshrun-20260810-1730"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_words(p: Path):
    d = json.loads(p.read_text(encoding="utf-8"))
    return [{
        "word_id": w.get("word_id"),
        "text": w.get("text", ""),
        "s": float(w.get("start_seconds", 0)),
        "e": float(w.get("end_seconds", 0)),
        "cls": w.get("activity", {}).get("classification"),
    } for w in d.get("words", [])]


def main():
    safe = json.loads((OUT / "safe_candidates.json").read_text())
    blocked = json.loads((OUT / "blocked_candidates.json").read_text())
    pkg = json.loads((OUT / "review_package/review_package.json").read_text())

    female_words = load_words(RUN / "06_activity/female.classified.json")
    male_words = load_words(RUN / "06_activity/male.classified.json")

    def wiw(ws, s, e):
        return [w for w in ws if w["e"] > s and w["s"] < e]

    metrics = {
        "long_pause_candidate_count": 0,
        "source_without_primary_count": 0,
        "source_majority_bleed_count": 0,
        "other_track_primary_overlap_count": 0,
        "other_track_ambiguous_overlap_count": 0,
        "unsafe_global_cut_count": 0,
        "missing_provenance_count": 0,
        "ui_renderer_scope_mismatch_count": 0,
        "safe_count": len(safe["candidates"]),
        "blocked_count": len(blocked["candidates"]),
    }

    for c in safe["candidates"]:
        if c["reason_key"] == "long_pause":
            metrics["long_pause_candidate_count"] += 1

        src = c["source_track"]
        s, e = c["start_seconds"], c["end_seconds"]
        src_words = wiw(female_words if src == "female" else male_words, s, e)
        src_primary = sum(1 for w in src_words if w["cls"] == "primary")
        src_bleed = sum(1 for w in src_words if w["cls"] == "bleed")
        if src_primary == 0:
            metrics["source_without_primary_count"] += 1
        if src_bleed > src_primary:
            metrics["source_majority_bleed_count"] += 1

        other = "male" if src == "female" else "female"
        other_words = wiw(male_words if src == "female" else female_words, s, e)
        other_primary = sum(1 for w in other_words if w["cls"] == "primary")
        other_ambiguous = sum(1 for w in other_words if w["cls"] == "ambiguous")
        if other_primary > 0:
            metrics["other_track_primary_overlap_count"] += 1
        if other_ambiguous > 0:
            metrics["other_track_ambiguous_overlap_count"] += 1

        # provenance 检查
        prov = c.get("provenance") or {}
        if not (prov.get("female_transcript_sha256")
                and prov.get("male_transcript_sha256")
                and prov.get("rules_sha256")):
            metrics["missing_provenance_count"] += 1

    # UI/renderer scope 一致性：审核包里每条候选的 in_cut 词应该跟真实剪切范围一致
    for c in pkg["candidates"]:
        s, e = c["start_seconds"], c["end_seconds"]
        # 前端标注的 in_cut 词
        for track_words in c["context_words"].values():
            for w in track_words:
                should_be_in_cut = w["start_seconds"] < e and w["end_seconds"] > s
                if w["in_cut"] != should_be_in_cut or w["affected_by_global_cut"] != should_be_in_cut:
                    metrics["ui_renderer_scope_mismatch_count"] += 1

    # unsafe_global_cut：任何 SAFE 候选在剪切窗口内另一轨有 primary，都算不安全
    metrics["unsafe_global_cut_count"] = metrics["other_track_primary_overlap_count"]

    # A/B 试听存在性 + 可解码性 + 时长
    decoder = shutil.which("ffprobe") or shutil.which("ffmpeg")
    if not decoder:
        raise FileNotFoundError("ffprobe/ffmpeg not found; install FFmpeg or add it to PATH")
    ffprobe = Path(decoder)

    previews_dir = OUT / "review_package/previews"
    all_previews_ok = True
    preview_check = []
    for c in pkg["candidates"]:
        cid = c["candidate_id"]
        orig = previews_dir / f"{cid}.original.mp3"
        prop = previews_dir / f"{cid}.proposed-cut.mp3"
        for p in (orig, prop):
            if not p.exists():
                all_previews_ok = False
                preview_check.append({"file": str(p), "ok": False, "reason": "missing"})
                continue
            # 用 ffmpeg 尝试解码
            r = subprocess.run([str(ffprobe), "-i", str(p), "-f", "null", "-", "-hide_banner"],
                               capture_output=True)
            if r.returncode != 0:
                all_previews_ok = False
                preview_check.append({"file": p.name, "ok": False, "reason": "decode failed"})
            else:
                preview_check.append({"file": p.name, "ok": True})

    metrics["previews_all_decodable"] = all_previews_ok
    metrics["previews_expected_pairs"] = len(pkg["candidates"])
    metrics["previews_actual_originals"] = len([p for p in previews_dir.glob("*.original.mp3")])
    metrics["previews_actual_proposed"] = len([p for p in previews_dir.glob("*.proposed-cut.mp3")])

    # context_completeness 从 review_package 拿
    metrics["context_completeness"] = pkg["context_completeness"]

    # 决定 PASS/FAIL
    fail_conditions = {
        "long_pause_candidate_count == 0": metrics["long_pause_candidate_count"] == 0,
        "source_without_primary_count == 0": metrics["source_without_primary_count"] == 0,
        "source_majority_bleed_count == 0": metrics["source_majority_bleed_count"] == 0,
        "other_track_primary_overlap_count == 0": metrics["other_track_primary_overlap_count"] == 0,
        "other_track_ambiguous_overlap_count == 0": metrics["other_track_ambiguous_overlap_count"] == 0,
        "unsafe_global_cut_count == 0": metrics["unsafe_global_cut_count"] == 0,
        "missing_provenance_count == 0": metrics["missing_provenance_count"] == 0,
        "ui_renderer_scope_mismatch_count == 0": metrics["ui_renderer_scope_mismatch_count"] == 0,
        "previews_all_decodable": metrics["previews_all_decodable"],
        "previews_originals_match": metrics["previews_actual_originals"] == metrics["previews_expected_pairs"],
        "previews_proposed_match": metrics["previews_actual_proposed"] == metrics["previews_expected_pairs"],
        "context_word_coverage_100pct": metrics["context_completeness"]["context_word_coverage_all_100pct"],
    }
    all_pass = all(fail_conditions.values())

    result = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "verdict": "PASS" if all_pass else "FAIL",
        "metrics": metrics,
        "conditions": fail_conditions,
    }

    (OUT / "after_metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("=" * 60)
    print("AFTER METRICS VERIFICATION")
    print("=" * 60)
    for cond, ok in fail_conditions.items():
        mark = "✓" if ok else "✗"
        print(f"  {mark} {cond}")
    print()
    print("整体:", "PASS" if all_pass else "FAIL")
    print("SAFE:", metrics["safe_count"], "BLOCKED:", metrics["blocked_count"])
    if not all_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
