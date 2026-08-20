#!/usr/bin/env python3
"""
generate_cut_candidates.py

从 ASR 词级转写生成剪辑候选。
    - 规则参数从外部 JSON 读，不硬编码（--rules 参数）
    - 每条候选都带来源标签（filler/pause/repetition）
    - 边界带缓冲 padding（YT-02 证据："filler deletion must not crush adjacent speech"）

输出格式对齐 build_review_package.py 的 --reference-report，可直接喂给它。

用法：
  python3 generate_cut_candidates.py \\
    --transcript female=xxx.transcript.json \\
    --transcript male=yyy.transcript.json \\
    --rules /path/to/candidate-generation.v1.json \\
    --output out/asr_candidates.json
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def parse_label_path(s: str) -> tuple[str, Path]:
    if "=" not in s:
        raise argparse.ArgumentTypeError("must be LABEL=/abs/path")
    label, p = s.split("=", 1)
    return label, Path(p).expanduser().resolve()


def _clean_word(text: str) -> str:
    return re.sub(r"[^一-鿿\w]", "", (text or "").strip().lower())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_filler_runs(words: list[dict], rules: dict) -> list[dict]:
    if not rules.get("enabled"):
        return []
    tokens = set(rules["tokens"])
    min_run = rules["min_consecutive"]
    out = []
    i = 0
    while i < len(words):
        if _clean_word(words[i]["text"]) in tokens:
            j = i
            while j + 1 < len(words) and _clean_word(words[j + 1]["text"]) in tokens:
                j += 1
            if j - i + 1 >= min_run:
                out.append({
                    "reason_key": "filler_hesitation",
                    "start_seconds": words[i]["start_seconds"],
                    "end_seconds": words[j]["end_seconds"],
                    "confidence": "medium",
                    "evidence_words": [
                        {"text": w["text"], "s": w["start_seconds"], "e": w["end_seconds"]}
                        for w in words[i:j + 1]
                    ],
                })
            i = j + 1
        else:
            i += 1
    return out


def find_long_pauses(words: list[dict], rules: dict) -> list[dict]:
    if not rules.get("enabled"):
        return []
    lo = rules["min_seconds"]
    hi = rules["max_seconds"]
    out = []
    for a, b in zip(words, words[1:]):
        gap = b["start_seconds"] - a["end_seconds"]
        if lo <= gap <= hi:
            out.append({
                "reason_key": "long_pause",
                "start_seconds": a["end_seconds"],
                "end_seconds": b["start_seconds"],
                "confidence": "medium",
                "evidence_words": [
                    {"text": a["text"], "s": a["start_seconds"], "e": a["end_seconds"]},
                    {"text": b["text"], "s": b["start_seconds"], "e": b["end_seconds"]},
                ],
            })
    return out


def find_immediate_repetitions(words: list[dict], rules: dict) -> list[dict]:
    if not rules.get("enabled"):
        return []
    min_L = rules["min_phrase_chars"]
    max_L = rules["max_phrase_chars"]
    out = []
    n = len(words)
    for i in range(n):
        for L in range(min_L, max_L + 1):
            if i + 2 * L > n:
                break
            first = "".join(_clean_word(words[i + k]["text"]) for k in range(L))
            second = "".join(_clean_word(words[i + L + k]["text"]) for k in range(L))
            if first and first == second and len(first) >= min_L:
                out.append({
                    "reason_key": "immediate_repetition",
                    "start_seconds": words[i]["start_seconds"],
                    "end_seconds": words[i + L - 1]["end_seconds"],
                    "confidence": "medium",
                    "evidence_words": [
                        {"text": words[i + k]["text"], "s": words[i + k]["start_seconds"],
                         "e": words[i + k]["end_seconds"]}
                        for k in range(2 * L)
                    ],
                    "repeated_phrase": first,
                })
                break
    return out


def dedup_overlap(cands: list[dict], min_gap: float) -> list[dict]:
    if not cands:
        return []
    sorted_c = sorted(cands, key=lambda c: c["start_seconds"])
    kept = [sorted_c[0]]
    for c in sorted_c[1:]:
        prev = kept[-1]
        if c["start_seconds"] < prev["end_seconds"] - min_gap:
            continue
        kept.append(c)
    return kept


def apply_guards(cands: list[dict], total_duration_seconds: float, guards: dict) -> tuple[list[dict], dict]:
    kept = []
    stats = {"dropped_by_opening_guard": 0, "dropped_by_closing_guard": 0, "dropped_by_protected_phrase": 0}
    for c in cands:
        if c["start_seconds"] < guards["opening_seconds"]:
            stats["dropped_by_opening_guard"] += 1
            continue
        if total_duration_seconds and c["end_seconds"] > total_duration_seconds - guards["closing_seconds"]:
            stats["dropped_by_closing_guard"] += 1
            continue
        evidence_text = "".join(_clean_word(w["text"]) for w in c.get("evidence_words", []))
        if any(p in evidence_text for p in guards["protected_phrases_substrings"]):
            stats["dropped_by_protected_phrase"] += 1
            continue
        kept.append(c)
    return kept, stats


def apply_boundary_padding(cands: list[dict], padding_ms: int) -> list[dict]:
    pad_s = padding_ms / 1000.0
    out = []
    for c in cands:
        c2 = dict(c)
        c2["start_seconds"] = max(0.0, c["start_seconds"] - pad_s)
        c2["end_seconds"] = c["end_seconds"] + pad_s
        out.append(c2)
    return out


def apply_min_duration(cands: list[dict], min_seconds: float) -> list[dict]:
    return [c for c in cands if c["end_seconds"] - c["start_seconds"] >= min_seconds]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate cut candidates from ASR transcripts + external rules.")
    ap.add_argument("--transcript", action="append", type=parse_label_path, required=True)
    ap.add_argument("--rules", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    if not args.output.is_absolute():
        ap.error("--output must be absolute")
    if not args.rules.is_absolute():
        ap.error("--rules must be absolute")

    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    rules_sha = sha256_file(args.rules)

    per_track_reports = []
    all_candidates = []

    min_gap = rules["min_gap_between_candidates_seconds"]["value_seconds"]
    min_dur = rules["min_candidate_seconds"]["value_seconds"]
    pad_ms = rules["boundary_padding"]["padding_ms"]

    for label, path in args.transcript:
        data = json.loads(path.read_text(encoding="utf-8"))
        words = data.get("words", [])
        if not words:
            raise SystemExit(f"transcript {path} has no word-level entries")
        total_duration = words[-1]["end_seconds"] if words else 0

        f = find_filler_runs(words, rules["filler_hesitation"])
        p = find_long_pauses(words, rules["long_pause"])
        r = find_immediate_repetitions(words, rules["immediate_repetition"])
        combined = dedup_overlap(f + p + r, min_gap)
        combined = apply_min_duration(combined, min_dur)
        guarded, guard_stats = apply_guards(combined, total_duration, rules["guards"])
        padded = apply_boundary_padding(guarded, pad_ms)

        for c in padded:
            c["track"] = label
        all_candidates.extend(padded)

        per_track_reports.append({
            "track": label,
            "transcript_path": str(path),
            "total_duration_seconds": round(total_duration, 3),
            "counts": {
                "raw_filler_hesitation": len(f),
                "raw_long_pause": len(p),
                "raw_immediate_repetition": len(r),
                "after_overlap_dedup": len(combined),
                "after_guards": len(guarded),
                **guard_stats,
            },
        })

    all_candidates = dedup_overlap(all_candidates, min_gap)

    inferred = []
    for c in all_candidates:
        inferred.append({
            "status": "inferred_not_approved",
            "reference_boundary_range_seconds": [round(c["start_seconds"], 3), round(c["end_seconds"], 3)],
            "approximate_raw_interval_seconds": [round(c["start_seconds"], 3), round(c["end_seconds"], 3)],
            "estimated_removed_seconds": round(c["end_seconds"] - c["start_seconds"], 3),
            "confidence": c["confidence"],
            "note": f"asr-derived: {c['reason_key']}",
            "asr_reason_key": c["reason_key"],
            "asr_evidence_words": [
                {"text": w["text"], "s": round(w["s"], 3), "e": round(w["e"], 3)}
                for w in c.get("evidence_words", [])
            ],
            "asr_repeated_phrase": c.get("repeated_phrase"),
            "asr_source_track": c["track"],
        })

    output_payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "status": "generated_from_asr_only",
        "source_policy": "no_reference_to_mentor_master; no_reuse_of_prior_approved_edl",
        "rules_used": {
            "rules_version": rules["rules_version"],
            "rules_path": str(args.rules),
            "rules_sha256": rules_sha,
            "rules_source_mix": rules["source_types"],
        },
        "per_track_reports": per_track_reports,
        "candidate_count": len(inferred),
        "inferred_cut_candidates": inferred,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(inferred)} candidates -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
