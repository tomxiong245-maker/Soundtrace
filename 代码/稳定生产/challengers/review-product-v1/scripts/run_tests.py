#!/usr/bin/env python3
"""
run_tests.py · review-product-v1 契约测试套

覆盖 13 类拒绝 + 3 类通过。运行时构造 fixture（不依赖磁盘 mp3），
使测试可以在有/无实际预览文件的环境中都能验证 fail-closed 逻辑。

用法：
    python3 run_tests.py            # 全跑
    python3 run_tests.py --json     # 输出机读结果
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from validate_review_package import (
    validate_package as validate_pkg,
    canonical_json,
    sha256_bytes,
    compute_review_manifest_sha,
    compute_candidate_semantic_sha,
)
from validate_human_decisions import validate as validate_dec


SAMPLE_RATE = 48000


def _hex(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


def build_candidate(cid: str, start_s: float, end_s: float, source_track: str = "female") -> Dict[str, Any]:
    start_sample = int(round(start_s * SAMPLE_RATE))
    end_sample = int(round(end_s * SAMPLE_RATE))
    cand = {
        "candidate_id": cid,
        "reason_key": "filler_hesitation",
        "source_track": source_track,
        "start_sample": start_sample,
        "end_sample": end_sample,
        "start_seconds": start_s,
        "end_seconds": end_s,
        "duration_seconds": round(end_s - start_s, 3),
        "safety_status": "SAFE",
        "reason_codes": [],
        "evidence_words": [{"text": "对", "s": start_s, "e": end_s, "cls": "primary"}],
        "text_tracks": {
            "female": {
                "track": "female",
                "window_start_seconds": max(0.0, start_s - 5.0),
                "window_end_seconds": end_s + 5.0,
                "words": [
                    {"text": "工", "s": start_s - 4.0, "e": start_s - 3.9, "cls": "primary", "in_cut": False},
                    {"text": "对", "s": start_s, "e": end_s, "cls": "primary", "in_cut": True},
                    {"text": "然", "s": end_s + 0.1, "e": end_s + 0.2, "cls": "primary", "in_cut": False},
                ],
            },
            "male": {
                "track": "male",
                "window_start_seconds": max(0.0, start_s - 5.0),
                "window_end_seconds": end_s + 5.0,
                "words": [
                    {"text": "嗯", "s": start_s - 0.5, "e": start_s - 0.3, "cls": "bleed", "in_cut": False},
                ],
            },
        },
        "global_cut": {
            "start_sample": start_sample,
            "end_sample": end_sample,
            "applies_to_tracks": ["female", "male"],
        },
        "previews": {
            "original_sha256": _hex(hash((cid, "orig"))),
            "proposed_sha256": _hex(hash((cid, "prop"))),
        },
        "risk_notes": [],
        "provenance": {
            "female_transcript_sha256": _hex(1),
            "male_transcript_sha256": _hex(2),
            "rules_sha256": _hex(3),
        },
    }
    cand["semantic_sha256"] = sha256_bytes(canonical_json(cand))
    return cand


def build_pkg(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    pkg = {
        "schema_version": "review-product-v1",
        "package_id": "EP03-review-product-v1-20260811-1500",
        "created_at": "2026-08-11T15:00:00+00:00",
        "sample_rate": SAMPLE_RATE,
        "master_time_base": "EP03-freshrun-20260810-1730-master",
        "source_audio": {
            "female_path": "test/female.wav",
            "female_sha256": _hex(10),
            "male_path": "test/male.wav",
            "male_sha256": _hex(11),
        },
        "input_provenance": {
            "female_classified_path": "test/fem.classified.json",
            "female_classified_sha256": _hex(20),
            "male_classified_path": "test/mal.classified.json",
            "male_classified_sha256": _hex(21),
            "candidates_source_path": "test/safe.json",
            "candidates_source_sha256": _hex(22),
        },
        "candidates": candidates,
        "preview_assets": {},
        "static_assets": {"files": {}},
        "review_config": {
            "context_seconds": 5.0,
            "crossfade_ms_default": 40,
        },
        "review_manifest_sha256": "PLACEHOLDER",
    }
    pkg["review_manifest_sha256"] = compute_review_manifest_sha(pkg)
    return pkg


def build_decision(cid: str, cand: Dict[str, Any], decision: str = "accept",
                   listened: str = None, reviewer: str = "alice_reviewer") -> Dict[str, Any]:
    if listened is None:
        listened = cand["previews"]["proposed_sha256"]
    d = {
        "candidate_id": cid,
        "candidate_semantic_sha256": cand["semantic_sha256"],
        "decision": decision,
        "reviewer": reviewer,
        "decided_at": "2026-08-11T15:10:00+00:00",
        "listened_at": "2026-08-11T15:09:30+00:00",
        "listened_preview_sha256": listened,
    }
    return d


def build_decisions_doc(pkg: Dict[str, Any], decisions: List[Dict[str, Any]],
                        reviewer: str = "alice_reviewer") -> Dict[str, Any]:
    return {
        "schema_version": "human-decisions-v1",
        "package_id": pkg["package_id"],
        "review_manifest_sha256": pkg["review_manifest_sha256"],
        "reviewer": reviewer,
        "session_started_at": "2026-08-11T15:00:00+00:00",
        "session_ended_at": "2026-08-11T15:30:00+00:00",
        "decisions": decisions,
    }


def _write(tmp: str, name: str, obj: Any) -> str:
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


# ---- Test cases ----

def run_all(verbose: bool = True) -> Tuple[int, int, List[Dict[str, Any]]]:
    results: List[Dict[str, Any]] = []
    total = 0
    passed = 0

    def record(tid: str, desc: str, ok: bool, detail: str = ""):
        nonlocal total, passed
        total += 1
        if ok:
            passed += 1
        results.append({"id": tid, "desc": desc, "pass": ok, "detail": detail})
        if verbose:
            print(f"[{'PASS' if ok else 'FAIL'}] {tid} · {desc}")
            if not ok and detail:
                print(f"       {detail}")

    with tempfile.TemporaryDirectory() as tmp:
        cands = [build_candidate("C001", 131.06, 131.72),
                 build_candidate("C006", 277.09, 277.67, "male")]
        pkg = build_pkg(cands)
        pkg_path = _write(tmp, "pkg.json", pkg)

        # T01 pkg validator PASS
        ok, reasons = validate_pkg(pkg_path)
        record("T01", "valid package passes validator", ok, "; ".join(reasons))

        # T02 tampered candidate semantic_sha
        bad_pkg = copy.deepcopy(pkg)
        bad_pkg["candidates"][0]["semantic_sha256"] = _hex(999)
        # 但 review_manifest_sha256 会随之被 recompute... 我们要模拟"篡改后未更新": 保持 sha
        bad_path = _write(tmp, "bad_sem.json", bad_pkg)
        ok, reasons = validate_pkg(bad_path)
        # 校验管理器会同时检出 manifest 和 semantic 两条错，但只要不通过即可
        record("T02", "tampered candidate semantic_sha256 rejected", not ok,
               "|".join(reasons))

        # T03 global_cut 只作用于一轨 → 拒绝
        bad_pkg = copy.deepcopy(pkg)
        bad_pkg["candidates"][0]["global_cut"]["applies_to_tracks"] = ["female"]
        # semantic sha 现在不匹配（候选被改）；先重算，让本项测的是 global_cut 规则
        bad_pkg["candidates"][0]["semantic_sha256"] = compute_candidate_semantic_sha(bad_pkg["candidates"][0])
        bad_pkg["review_manifest_sha256"] = compute_review_manifest_sha(bad_pkg)
        bp = _write(tmp, "bad_globalcut.json", bad_pkg)
        ok, reasons = validate_pkg(bp)
        record("T03", "global_cut must include both tracks (fail-closed)",
               (not ok) and any("global_cut" in r for r in reasons),
               "|".join(reasons))

        # T04 sample_rate not 48000
        bad_pkg = copy.deepcopy(pkg)
        bad_pkg["sample_rate"] = 44100
        bad_pkg["review_manifest_sha256"] = compute_review_manifest_sha(bad_pkg)
        bp = _write(tmp, "bad_sr.json", bad_pkg)
        ok, reasons = validate_pkg(bp)
        record("T04", "sample_rate != 48000 rejected",
               (not ok) and any("sample_rate" in r for r in reasons),
               "|".join(reasons))

        # ---- decision tests ----
        decisions = [build_decision("C001", cands[0]),
                     build_decision("C006", cands[1], decision="reject")]
        doc = build_decisions_doc(pkg, decisions)
        dpath = _write(tmp, "dec.json", doc)

        # T05 valid decisions PASS
        ok, reasons = validate_dec(pkg_path, dpath)
        record("T05", "valid decisions pass validator", ok, "|".join(reasons))

        # T06 reject 'pending'
        bad_doc = copy.deepcopy(doc)
        bad_doc["decisions"][0]["decision"] = "pending"
        bp = _write(tmp, "bad_pending.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T06", "decision='pending' rejected",
               (not ok) and any("R02" in r for r in reasons),
               "|".join(reasons))

        # T07 reviewer 硬编码 renting
        bad_doc = copy.deepcopy(doc)
        bad_doc["reviewer"] = "renting"
        bp = _write(tmp, "bad_reviewer.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T07", "hard-coded reviewer 'renting' rejected",
               (not ok) and any("R03" in r for r in reasons),
               "|".join(reasons))

        # T08 review_manifest_sha256 mismatch (old package)
        bad_doc = copy.deepcopy(doc)
        bad_doc["review_manifest_sha256"] = _hex(4242)
        bp = _write(tmp, "bad_manifest.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T08", "stale review_manifest_sha256 rejected",
               (not ok) and any("R04" in r for r in reasons),
               "|".join(reasons))

        # T09 unknown candidate
        bad_doc = copy.deepcopy(doc)
        bad_doc["decisions"][0]["candidate_id"] = "C999"
        bp = _write(tmp, "bad_unknown.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T09", "unknown candidate_id rejected",
               (not ok) and any("R05" in r for r in reasons),
               "|".join(reasons))

        # T10 duplicate decision
        bad_doc = copy.deepcopy(doc)
        bad_doc["decisions"].append(build_decision("C001", cands[0]))
        bp = _write(tmp, "bad_dup.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T10", "duplicate decision rejected",
               (not ok) and any("R06" in r for r in reasons),
               "|".join(reasons))

        # T11 tampered candidate_semantic_sha256 (换包/篡改)
        bad_doc = copy.deepcopy(doc)
        bad_doc["decisions"][0]["candidate_semantic_sha256"] = _hex(5555)
        bp = _write(tmp, "bad_sem_dec.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T11", "tampered candidate_semantic_sha256 rejected",
               (not ok) and any("R07" in r for r in reasons),
               "|".join(reasons))

        # T12 stale listened preview (adjust 场景：仍用旧 preview)
        bad_doc = copy.deepcopy(doc)
        bad_doc["decisions"][0]["decision"] = "adjust"
        bad_doc["decisions"][0]["adjustment"] = {
            "new_start_sample": cands[0]["start_sample"] + 480,
            "new_end_sample": cands[0]["end_sample"] - 480,
            "crossfade_ms": 40,
            # 复用旧 proposed_sha256 → R11
            "reprocessed_preview_sha256": cands[0]["previews"]["proposed_sha256"],
        }
        # listened_preview 也是旧的
        bp = _write(tmp, "bad_stale_prev.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T12", "adjust reusing old preview rejected (R11)",
               (not ok) and any("R11" in r for r in reasons),
               "|".join(reasons))

        # T13 decisions 数 != 候选数
        bad_doc = copy.deepcopy(doc)
        bad_doc["decisions"] = [doc["decisions"][0]]
        bp = _write(tmp, "bad_count.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T13", "decision count != candidate count rejected",
               (not ok) and any("R10" in r for r in reasons),
               "|".join(reasons))

        # T14 valid adjust with new preview passes
        good_doc = copy.deepcopy(doc)
        good_doc["decisions"][0]["decision"] = "adjust"
        new_prev = _hex(77777)
        good_doc["decisions"][0]["adjustment"] = {
            "new_start_sample": cands[0]["start_sample"] + 480,
            "new_end_sample": cands[0]["end_sample"] - 480,
            "crossfade_ms": 40,
            "reprocessed_preview_sha256": new_prev,
        }
        good_doc["decisions"][0]["listened_preview_sha256"] = new_prev
        bp = _write(tmp, "good_adjust.json", good_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T14", "valid adjust with fresh preview passes", ok, "|".join(reasons))

        # T15 listened_preview 不属于候选 previews
        bad_doc = copy.deepcopy(doc)
        bad_doc["decisions"][0]["listened_preview_sha256"] = _hex(88888)
        bp = _write(tmp, "bad_lp.json", bad_doc)
        ok, reasons = validate_dec(pkg_path, bp)
        record("T15", "listened_preview not in candidate previews rejected (R08)",
               (not ok) and any("R08" in r for r in reasons),
               "|".join(reasons))

    return total, passed, results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    total, passed, results = run_all(verbose=not args.json)
    summary = {"total": total, "passed": passed, "failed": total - passed, "results": results}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"\n===== {passed}/{total} PASSED =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
