#!/usr/bin/env python3
"""
generate_safe_candidates.py — Challenger cross-track-safety-v1 的候选生成器

不修改 Champion。基于 06_activity 的 classified transcripts（带 activity.classification）：
    - 只启 filler_hesitation + immediate_repetition
    - long_pause 明确禁用（无跨轨感知的纯 ASR 规则不安全）
    - 每条候选立即调 evaluate_candidate_safety，SAFE/NEEDS_HUMAN 进 safe_candidates；
      BLOCK/FAIL_CLOSED 进 blocked_candidates
    - 输出确定性（按 candidate_id 排序），忽略 created_at 后语义哈希稳定

用法：
  python3 generate_safe_candidates.py \\
    --female-classified <path> \\
    --male-classified   <path> \\
    --rules             <path safety-v1.json> \\
    --output-dir        <path>
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 导入同目录 evaluator
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_candidate_safety import evaluate_candidate_safety


# -----------------------------------------------------------
# 工具
# -----------------------------------------------------------
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _clean_word(text: str) -> str:
    return re.sub(r"[^一-鿿\w]", "", (text or "").strip().lower())


# -----------------------------------------------------------
# 从 classified transcript 加载简化 word 结构
# -----------------------------------------------------------
def load_words(classified_path: Path) -> list[dict]:
    d = json.loads(classified_path.read_text(encoding="utf-8"))
    words = []
    for w in d.get("words", []):
        act = w.get("activity", {})
        cls = act.get("classification")
        words.append({
            "word_id": w.get("word_id"),
            "text": w.get("text", ""),
            "s": float(w.get("start_seconds", 0)),
            "e": float(w.get("end_seconds", 0)),
            "start_sample": w.get("start_sample"),
            "end_sample": w.get("end_sample"),
            "cls": cls,
        })
    return words


# -----------------------------------------------------------
# 候选生成规则（filler_hesitation + immediate_repetition，忠于 Champion 逻辑）
# -----------------------------------------------------------
def find_filler_runs(words: list[dict], rules: dict, track_label: str) -> list[dict]:
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
                    "start_seconds": words[i]["s"],
                    "end_seconds": words[j]["e"],
                    "source_track": track_label,
                    "evidence_word_ids": [w["word_id"] for w in words[i:j + 1]],
                    "evidence_words": [{"text": w["text"], "s": w["s"], "e": w["e"], "cls": w["cls"]}
                                       for w in words[i:j + 1]],
                })
            i = j + 1
        else:
            i += 1
    return out


def find_immediate_repetitions(words: list[dict], rules: dict, track_label: str) -> list[dict]:
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
                    "start_seconds": words[i]["s"],
                    "end_seconds": words[i + L - 1]["e"],
                    "source_track": track_label,
                    "evidence_word_ids": [words[i + k]["word_id"] for k in range(2 * L)],
                    "evidence_words": [{"text": words[i + k]["text"], "s": words[i + k]["s"],
                                        "e": words[i + k]["e"], "cls": words[i + k]["cls"]}
                                       for k in range(2 * L)],
                    "repeated_phrase": first,
                })
                break
    return out


def apply_guards(cands: list[dict], total_duration_s: float, guards: dict) -> tuple[list[dict], dict]:
    kept = []
    stats = {"dropped_by_opening_guard": 0, "dropped_by_closing_guard": 0, "dropped_by_protected_phrase": 0}
    protected = guards.get("protected_phrases_substrings", [])
    opening = guards.get("opening_seconds", 0.0)
    closing = guards.get("closing_seconds", 0.0)
    for c in cands:
        if c["start_seconds"] < opening:
            stats["dropped_by_opening_guard"] += 1
            continue
        if total_duration_s and c["end_seconds"] > total_duration_s - closing:
            stats["dropped_by_closing_guard"] += 1
            continue
        evidence_text = "".join(_clean_word(w["text"]) for w in c.get("evidence_words", []))
        if any(p in evidence_text for p in protected):
            stats["dropped_by_protected_phrase"] += 1
            continue
        kept.append(c)
    return kept, stats


def apply_boundary_padding(cands: list[dict], padding_ms: int) -> list[dict]:
    pad_s = padding_ms / 1000.0
    for c in cands:
        c["start_seconds"] = max(0.0, c["start_seconds"] - pad_s)
        c["end_seconds"] = c["end_seconds"] + pad_s
    return cands


def apply_min_duration(cands: list[dict], min_s: float) -> list[dict]:
    return [c for c in cands if c["end_seconds"] - c["start_seconds"] >= min_s]


def dedup_overlap(cands: list[dict], min_gap: float) -> list[dict]:
    if not cands:
        return []
    sorted_c = sorted(cands, key=lambda c: (c["start_seconds"], c["source_track"]))
    kept = [sorted_c[0]]
    for c in sorted_c[1:]:
        prev = kept[-1]
        if c["start_seconds"] < prev["end_seconds"] - min_gap:
            continue
        kept.append(c)
    return kept


# -----------------------------------------------------------
# 主流程
# -----------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--female-classified", type=Path, required=True)
    ap.add_argument("--male-classified", type=Path, required=True)
    ap.add_argument("--rules", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    for p in (args.female_classified, args.male_classified, args.rules):
        if not p.is_absolute():
            ap.error(f"path must be absolute: {p}")
    if not args.output_dir.is_absolute():
        ap.error("--output-dir must be absolute")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    rules_sha = sha256_file(args.rules)

    female_sha = sha256_file(args.female_classified)
    male_sha = sha256_file(args.male_classified)

    female_words = load_words(args.female_classified)
    male_words = load_words(args.male_classified)

    female_dur = female_words[-1]["e"] if female_words else 0
    male_dur = male_words[-1]["e"] if male_words else 0

    # 生成候选：每轨独立走 filler + repetition
    raw = []
    raw += find_filler_runs(female_words, rules["filler_hesitation"], "female")
    raw += find_filler_runs(male_words, rules["filler_hesitation"], "male")
    raw += find_immediate_repetitions(female_words, rules["immediate_repetition"], "female")
    raw += find_immediate_repetitions(male_words, rules["immediate_repetition"], "male")

    raw_counts = {
        "filler_hesitation_raw": sum(1 for c in raw if c["reason_key"] == "filler_hesitation"),
        "immediate_repetition_raw": sum(1 for c in raw if c["reason_key"] == "immediate_repetition"),
    }

    # 保护和边界
    min_dur = rules.get("min_candidate_seconds", {}).get("value_seconds", 0.15)
    min_gap = rules.get("min_gap_between_candidates_seconds", {}).get("value_seconds", 0.05)
    pad_ms = rules.get("boundary_padding", {}).get("padding_ms", 40)
    total_dur = max(female_dur, male_dur)

    filtered, guard_stats = apply_guards(raw, total_dur, rules.get("guards", {}))
    filtered = apply_min_duration(filtered, min_dur)
    filtered = dedup_overlap(filtered, min_gap)
    filtered = apply_boundary_padding(filtered, pad_ms)

    # 稳定排序：按 (start_seconds, source_track, reason_key)
    filtered.sort(key=lambda c: (round(c["start_seconds"], 3), c["source_track"], c["reason_key"]))

    # 分配 candidate_id
    for idx, c in enumerate(filtered, start=1):
        c["candidate_id"] = f"C{idx:03d}"

    # 用 evaluate_candidate_safety 分流
    words_by_track = {"female": female_words, "male": male_words}

    safe = []
    blocked = []
    for c in filtered:
        cand_input = {
            "reason_key": c["reason_key"],
            "source_track": c["source_track"],
            "start_seconds": c["start_seconds"],
            "end_seconds": c["end_seconds"],
        }
        result = evaluate_candidate_safety(cand_input, words_by_track, context_seconds=5.0)

        # 附加 sample 边界（用 48kHz 反推，跟主项目对齐）
        sr = 48000
        c["start_sample"] = round(c["start_seconds"] * sr)
        c["end_sample"] = round(c["end_seconds"] * sr)
        c["duration_seconds"] = round(c["end_seconds"] - c["start_seconds"], 3)
        c["safety_status"] = result["decision"]
        c["reason_codes"] = result["reason_codes"]
        c["source_activity_stats"] = result["cut_stats"].get(c["source_track"], {})
        c["other_activity_stats"] = result["cut_stats"].get(
            "male" if c["source_track"] == "female" else "female", {}
        )
        c["provenance"] = {
            "female_transcript_sha256": female_sha,
            "male_transcript_sha256": male_sha,
            "rules_sha256": rules_sha,
        }

        if result["decision"] == "SAFE":
            safe.append(c)
        else:
            blocked.append(c)

    # 稳定输出（用于幂等哈希，忽略 created_at）
    def canonical(cs):
        return sorted(cs, key=lambda x: x["candidate_id"])
    safe = canonical(safe)
    blocked = canonical(blocked)

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    from collections import Counter
    safe_cat = Counter(c["reason_key"] for c in safe)
    blocked_cat = Counter(c["reason_key"] for c in blocked)
    blocked_reasons = Counter()
    for c in blocked:
        for r in c["reason_codes"]:
            blocked_reasons[r] += 1

    safe_payload = {
        "schema_version": 1,
        "generated_at": now,
        "source_policy": "no_reference_to_mentor_master; no_reuse_of_prior_approved_edl; cross_track_safety_enforced",
        "rules_used": {
            "rules_version": rules["rules_version"],
            "rules_path": str(args.rules),
            "rules_sha256": rules_sha,
        },
        "input_provenance": {
            "female_classified_path": str(args.female_classified),
            "female_classified_sha256": female_sha,
            "male_classified_path": str(args.male_classified),
            "male_classified_sha256": male_sha,
        },
        "counts": {
            "raw_by_reason": raw_counts,
            "after_guards": len(filtered),
            "safe": len(safe),
            "blocked": len(blocked),
            "safe_by_reason": dict(safe_cat),
        },
        "guard_stats": guard_stats,
        "candidates": safe,
    }
    blocked_payload = {
        "schema_version": 1,
        "generated_at": now,
        "counts": {
            "total": len(blocked),
            "by_reason_key": dict(blocked_cat),
            "by_safety_reason_code": dict(blocked_reasons),
        },
        "candidates": blocked,
    }

    safe_out = args.output_dir / "safe_candidates.json"
    blocked_out = args.output_dir / "blocked_candidates.json"
    safe_out.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    blocked_out.write_text(json.dumps(blocked_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 幂等哈希：仅基于 candidates 内容（无 created_at）
    def semantic_hash(cs):
        canonical_json = json.dumps(cs, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return sha256_bytes(canonical_json)

    normalized_sha = {
        "safe_candidates_semantic_sha256": semantic_hash(safe),
        "blocked_candidates_semantic_sha256": semantic_hash(blocked),
        "note": "语义哈希基于 candidates 数组内容（排序 + 忽略 created_at），用于验证幂等性",
    }
    (args.output_dir / "normalized_output_sha256.json").write_text(
        json.dumps(normalized_sha, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"=== 候选生成完成 ===")
    print(f"  raw: {raw_counts}")
    print(f"  after guards: {len(filtered)}")
    print(f"  SAFE: {len(safe)}  {dict(safe_cat)}")
    print(f"  BLOCKED: {len(blocked)}  by_reason_code={dict(blocked_reasons)}")
    print(f"  safe_out: {safe_out}")
    print(f"  blocked_out: {blocked_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
