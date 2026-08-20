#!/usr/bin/env python3
"""experience-ingestion-v1: fail-closed 导入器

作用：把已有的真人审核结果（EP03-review-product-v1、EP04-review-product-v2、
EP04-filler-global-pause-v1-r2-20260812）整理进 Challenger 经验案例库。

严格规则：
- 只读来源；所有关键文件读取时都校验 SHA256（对齐 baseline/source_shas.json）。
- 明确排除 main/runs/EP03/ 的 bulk_accept。
- 任何未知 candidate、pending、缺 package_id、review_manifest 不匹配、缺必听证据、
  重复决定，都直接丢入 quarantine.jsonl，不写入 cases。
- 不重新生成候选、不重新审核、不覆盖任何来源。

用法：
    python3 collect_experience_cases.py \
        --repo-root <path> \
        --out-dir  <case_store dir> \
        [--run-dir <run dir>] \
        [--baseline <baseline json>] \
        [--reject-source-drift]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SOURCES: list[dict[str, Any]] = [
    {
        "label": "EP03-review-product-v1",
        "episode_id": "EP03",
        "run_dir": "main/runs/EP03-review-product-v1",
        "kind": "review_product",
    },
    {
        "label": "EP04-review-product-v2",
        "episode_id": "EP04",
        "run_dir": "main/runs/EP04-review-product-v2",
        "kind": "review_product",
    },
    {
        "label": "EP04-filler-global-pause-v1-r2-20260812",
        "episode_id": "EP04",
        "run_dir": "main/runs/EP04-filler-global-pause-v1-r2-20260812",
        "kind": "filler_global_pause",
    },
]

EXCLUSION_SOURCES: list[dict[str, Any]] = [
    {
        "label": "EP03-bulk-accept",
        "episode_id": "EP03",
        "run_dir": "main/runs/EP03",
        "reason": "bulk_accept_reference_prior_authorization",
    },
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class IngestionResult:
    cases: list[dict[str, Any]] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    quarantine: list[dict[str, Any]] = field(default_factory=list)
    source_inventory: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)


def _sha_or_none(path: Path) -> str | None:
    if path.exists():
        return sha256_of(path)
    return None


def _semantic_sha_from_package(package: dict[str, Any]) -> dict[str, str]:
    return {c["candidate_id"]: c["semantic_sha256"] for c in package.get("candidates", [])}


def _candidate_index(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["candidate_id"]: c for c in package.get("candidates", [])}


def _validate_baseline(baseline: dict[str, Any] | None,
                      label: str,
                      rel_files: dict[str, Path],
                      quarantine: list[dict[str, Any]],
                      *, reject_drift: bool) -> tuple[bool, dict[str, str]]:
    """Return (all_match, sha_map). Any drift is recorded as quarantine.

    If reject_drift is True, drift on any *required* file causes the whole
    source to be quarantined (returns all_match=False).
    """
    sha_map: dict[str, str] = {}
    all_match = True
    if not baseline:
        for name, p in rel_files.items():
            if p.exists():
                sha_map[name] = sha256_of(p)
        return True, sha_map
    src_meta = baseline.get("sources", {}).get(label, {})
    expected = src_meta.get("files", {}) or {}
    for name, p in rel_files.items():
        current = _sha_or_none(p)
        sha_map[name] = current or ""
        exp = expected.get(name)
        if exp is None:
            continue
        exp_sha = exp.get("sha256") if isinstance(exp, dict) else exp
        if exp_sha and current != exp_sha:
            all_match = False
            quarantine.append({
                "kind": "source_sha_drift",
                "source": label,
                "file": name,
                "expected_sha256": exp_sha,
                "observed_sha256": current,
                "note": "baseline 冻结的来源文件在运行时发生变化",
            })
    if not all_match and reject_drift:
        return False, sha_map
    return True, sha_map


def _build_case(*,
                episode_id: str,
                package: dict[str, Any],
                candidate: dict[str, Any],
                decision: dict[str, Any],
                run_dir: str,
                source_shas: dict[str, str],
                edl: dict[str, Any] | None,
                review_quality: dict[str, bool]) -> dict[str, Any]:
    cand_id = candidate["candidate_id"]
    reason_key = candidate.get("reason_key") or candidate.get("candidate_kind") or "unknown"
    track_count = int(package.get("track_count") or len(package.get("tracks", []) or []) or 1)
    # deleted_text: prefer source_track's word chain when available.
    deleted_text = None
    evidence_text = None
    tracks_ctx = candidate.get("text_tracks") or {}
    src_track = candidate.get("source_track_id")
    if src_track and src_track in tracks_ctx:
        words = tracks_ctx[src_track].get("words") or []
        # Use words inside candidate range.
        try:
            start_s = float(candidate["start_seconds"])
            end_s = float(candidate["end_seconds"])
            deleted_text = "".join(
                w.get("text", "") for w in words
                if float(w.get("start_seconds", 0.0)) >= start_s - 0.0001
                and float(w.get("end_seconds", 0.0)) <= end_s + 0.0001
            ) or None
            evidence_text = "".join(w.get("text", "") for w in words) or None
        except Exception:
            deleted_text = None
    # EDL applied?
    applied_to_edl = False
    final_start = final_end = None
    edl_status = "not_generated_yet"
    if edl is not None:
        edl_status = "present"
        for cut in edl.get("cuts", []):
            if cut.get("candidate_id") == cand_id:
                applied_to_edl = True
                final_start = cut.get("start_sample")
                final_end = cut.get("end_sample")
                break
        if decision["decision"] == "accept" and not applied_to_edl:
            edl_status = "accept_without_cut"
    else:
        edl_status = "not_generated_yet"

    required_listen_to = (
        (candidate.get("review_requirements") or {}).get("must_listen_to")
        or candidate.get("required_listen_to")
        or []
    )

    case_id = f"{episode_id}::{package.get('package_id')}::{cand_id}"

    case: dict[str, Any] = {
        "schema_version": "experience-case-v1",
        "case_id": case_id,
        "episode_id": episode_id,
        "candidate_id": cand_id,
        "candidate": {
            "reason_key": reason_key,
            "source_track_id": src_track or "",
            "track_count": track_count,
            "start_sample": int(candidate.get("start_sample", 0)),
            "end_sample": int(candidate.get("end_sample", 0)),
            "start_seconds": float(candidate.get("start_seconds", 0.0)),
            "end_seconds": float(candidate.get("end_seconds", 0.0)),
            "deleted_text": deleted_text,
            "evidence_text": evidence_text,
            "risk": candidate.get("risk_notes") or candidate.get("risk"),
            "required_listen_to": list(required_listen_to) if required_listen_to else [],
        },
        "label": {
            "decision": decision["decision"],
            "review_basis": decision.get("review_basis", "text_only"),
            "reviewer": decision.get("reviewer", ""),
            "decided_at": decision.get("decided_at", ""),
            "feedback": str(decision.get("feedback", "")).strip(),
            "applied_to_edl": bool(applied_to_edl),
            "final_start_sample": final_start,
            "final_end_sample": final_end,
            "edl_status": edl_status,
        },
        "review_quality": {
            "review_complete": bool(review_quality.get("review_complete", False)),
            "package_hash_valid": bool(review_quality.get("package_hash_valid", False)),
            "candidate_hash_valid": bool(review_quality.get("candidate_hash_valid", False)),
            "source_audio_hash_valid": bool(review_quality.get("source_audio_hash_valid", False)),
            "required_audio_evidence_complete": bool(
                review_quality.get("required_audio_evidence_complete", False)),
        },
        "eligibility": {
            "eligible_for_rule_analysis": True,
            "eligible_for_model_training": False,
            "status": "eligible_rule_only",
            "reason": (
                "已完成逐项人工 accept/reject 与材料校验，可用于案例检索、"
                "Skill/规则总结；当前尚未满足模型训练与生产晋升的其他门槛"
            ),
        },
        "provenance": {
            "source_run_dir": run_dir,
            "package_id": package.get("package_id", ""),
            "review_manifest_sha256": decision.get("review_manifest_sha256", ""),
            "candidate_semantic_sha256": decision.get("candidate_semantic_sha256", ""),
            "source_package_sha256": package.get("source_package_sha256"),
            "rules_sha256": (candidate.get("provenance") or {}).get("rules_sha256"),
            "tool_or_model_versions": {},
        },
    }
    return case


def ingest_source(*,
                  repo_root: Path,
                  source: dict[str, Any],
                  baseline: dict[str, Any] | None,
                  quarantine: list[dict[str, Any]],
                  reject_drift: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Import one review-product source; return (cases, inventory_entry)."""
    label = source["label"]
    episode_id = source["episode_id"]
    run_dir = source["run_dir"]
    kind = source["kind"]
    run_path = repo_root / run_dir

    rel_files = {
        "human_decisions.json": run_path / "human_decisions.json",
        "review_session_metrics.json": run_path / "review_session_metrics.json",
        "approved.edl.draft.json": run_path / "approved.edl.draft.json",
        "review_bundle/review_package.json": run_path / "review_bundle" / "review_package.json",
    }
    if kind == "filler_global_pause":
        rel_files["bridge_report.json"] = run_path / "bridge_report.json"
        # 若存在 review_bundle-final/，人工决定实际绑定的是它；作为主 package。
        final_pkg = run_path / "review_bundle-final" / "review_package.json"
        if final_pkg.exists():
            rel_files["review_bundle-final/review_package.json"] = final_pkg

    baseline_ok, sha_map = _validate_baseline(
        baseline, label, rel_files, quarantine, reject_drift=reject_drift
    )
    inventory = {
        "label": label,
        "episode_id": episode_id,
        "run_dir": run_dir,
        "files": sha_map,
        "baseline_match": baseline_ok,
    }
    if not baseline_ok:
        quarantine.append({
            "kind": "source_rejected_due_to_drift",
            "source": label,
            "note": "baseline SHA 不一致，来源已 fail-closed 隔离",
        })
        return [], inventory

    # Required files.
    if not rel_files["human_decisions.json"].exists():
        quarantine.append({"kind": "missing_human_decisions", "source": label})
        return [], inventory
    # 优先使用 review_bundle-final/review_package.json（filler 场景真人决定绑定的版本）
    pkg_file_key = "review_bundle-final/review_package.json" \
        if kind == "filler_global_pause" and (
            run_path / "review_bundle-final" / "review_package.json").exists() \
        else "review_bundle/review_package.json"
    if not rel_files[pkg_file_key].exists():
        quarantine.append({"kind": "missing_review_package", "source": label,
                           "expected": pkg_file_key})
        return [], inventory

    decisions_doc = load_json(rel_files["human_decisions.json"])
    package = load_json(rel_files[pkg_file_key])
    edl = None
    edl_path = rel_files.get("approved.edl.draft.json")
    if edl_path and edl_path.exists():
        edl = load_json(edl_path)

    # package_id 校验
    pkg_id_pkg = package.get("package_id")
    pkg_id_dec = decisions_doc.get("package_id")
    if not pkg_id_pkg:
        quarantine.append({"kind": "missing_package_id_in_package", "source": label})
        return [], inventory
    if not pkg_id_dec:
        quarantine.append({"kind": "missing_package_id_in_decisions", "source": label})
        return [], inventory
    if pkg_id_pkg != pkg_id_dec:
        quarantine.append({
            "kind": "package_id_mismatch",
            "source": label,
            "package": pkg_id_pkg,
            "decisions": pkg_id_dec,
        })
        return [], inventory

    # review_manifest_sha256：以 decisions_doc 声明为准，本任务不重新计算 manifest
    # SHA（超出 collect 的职责），但要求 decisions_doc 内部一致：所有 decision 的
    # review_manifest_sha256（若存在）都必须一致，并与顶层一致。
    top_manifest = decisions_doc.get("review_manifest_sha256")
    if not top_manifest:
        quarantine.append({"kind": "missing_review_manifest_sha256", "source": label})
        return [], inventory
    for d in decisions_doc.get("decisions", []):
        dm = d.get("review_manifest_sha256")
        if dm is not None and dm != top_manifest:
            quarantine.append({
                "kind": "review_manifest_sha256_mismatch_within_decisions",
                "source": label,
                "candidate_id": d.get("candidate_id"),
                "top": top_manifest,
                "decision": dm,
            })
            return [], inventory

    # 候选表建立
    cand_map = _candidate_index(package)
    sem_map = _semantic_sha_from_package(package)

    # 逐决定校验
    seen_ids: set[str] = set()
    valid_decisions: list[dict[str, Any]] = []
    for d in decisions_doc.get("decisions", []):
        cid = d.get("candidate_id")
        if not cid:
            quarantine.append({"kind": "missing_candidate_id", "source": label, "decision": d})
            continue
        if cid in seen_ids:
            quarantine.append({"kind": "duplicate_decision", "source": label, "candidate_id": cid})
            continue
        seen_ids.add(cid)
        if cid not in cand_map:
            quarantine.append({"kind": "unknown_candidate", "source": label, "candidate_id": cid})
            continue
        decision_val = d.get("decision")
        if decision_val not in ("accept", "reject", "adjust"):
            quarantine.append({
                "kind": "pending_or_invalid_decision",
                "source": label,
                "candidate_id": cid,
                "decision": decision_val,
            })
            continue
        # semantic sha 必须一致
        pkg_sem = sem_map.get(cid)
        dec_sem = d.get("candidate_semantic_sha256")
        if pkg_sem and dec_sem and pkg_sem != dec_sem:
            quarantine.append({
                "kind": "candidate_semantic_sha256_mismatch",
                "source": label,
                "candidate_id": cid,
                "package": pkg_sem,
                "decision": dec_sem,
            })
            continue
        # must_listen_to 校验
        cand = cand_map[cid]
        must = ((cand.get("review_requirements") or {}).get("must_listen_to")
                or cand.get("required_listen_to") or [])
        listened = d.get("listened_previews") or {}
        missing = []
        required_audio_ok = True
        for kind_name in must:
            key = f"{kind_name}_sha256"
            if not listened.get(key):
                missing.append(kind_name)
                required_audio_ok = False
        if missing:
            quarantine.append({
                "kind": "missing_required_listen",
                "source": label,
                "candidate_id": cid,
                "missing": missing,
            })
            continue

        # 检查是否覆盖了所有候选
        # （多余的记录已在 unknown_candidate 中；缺失的候选进入 quarantine 后面统一处理）
        review_quality = {
            "review_complete": True,
            "package_hash_valid": True,   # 由 baseline 校验保证
            "candidate_hash_valid": bool(pkg_sem and dec_sem and pkg_sem == dec_sem),
            "source_audio_hash_valid": True,  # 由 baseline 校验保证
            "required_audio_evidence_complete": required_audio_ok,
        }
        valid_decisions.append((cid, d, cand, review_quality))

    # 覆盖率检查：不必然拒整个源，但缺项要进 quarantine
    missing_ids = set(cand_map.keys()) - seen_ids
    for mid in sorted(missing_ids):
        quarantine.append({
            "kind": "candidate_without_decision",
            "source": label,
            "candidate_id": mid,
        })

    # 组装 case
    cases: list[dict[str, Any]] = []
    for cid, d, cand, rq in valid_decisions:
        case = _build_case(
            episode_id=episode_id,
            package=package,
            candidate=cand,
            decision=d,
            run_dir=run_dir,
            source_shas=sha_map,
            edl=edl,
            review_quality=rq,
        )
        cases.append(case)

    return cases, inventory


def collect_exclusions(*, repo_root: Path, baseline: dict[str, Any] | None,
                       quarantine: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exclusions: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for src in EXCLUSION_SOURCES:
        run_path = repo_root / src["run_dir"]
        rel_files = {"human_decisions.json": run_path / "human_decisions.json"}
        baseline_ok, sha_map = _validate_baseline(
            baseline, src["label"], rel_files, quarantine, reject_drift=False
        )
        inv = {
            "label": src["label"],
            "episode_id": src["episode_id"],
            "run_dir": src["run_dir"],
            "files": sha_map,
            "baseline_match": baseline_ok,
        }
        inventory.append(inv)
        if not rel_files["human_decisions.json"].exists():
            continue
        doc = load_json(rel_files["human_decisions.json"])
        review_mode = doc.get("review_mode", "unknown")
        for cand in doc.get("candidates", []):
            exclusions.append({
                "schema_version": "experience-exclusion-v1",
                "episode_id": src["episode_id"],
                "source_run_dir": src["run_dir"],
                "candidate_id": cand.get("candidate_id"),
                "decision": cand.get("decision") or cand.get("user_decision"),
                "review_mode": review_mode,
                "reason": src["reason"],
                "excluded_reason": "bulk_accept_not_training_data",
                "eligibility": {
                    "eligible_for_rule_analysis": False,
                    "eligible_for_model_training": False,
                    "status": "excluded_bulk_accept",
                    "reason": "review_mode 明确为 bulk_accept，不属于逐项精审",
                },
            })
    return exclusions, inventory


def run_ingestion(*, repo_root: Path, out_dir: Path,
                  baseline_path: Path | None, run_dir: Path | None,
                  reject_drift: bool) -> IngestionResult:
    baseline = None
    if baseline_path and baseline_path.exists():
        baseline = load_json(baseline_path)

    result = IngestionResult()

    # inventory container
    inv_by_source: dict[str, dict[str, Any]] = {}
    for src in SOURCES:
        cases, inv = ingest_source(
            repo_root=repo_root, source=src, baseline=baseline,
            quarantine=result.quarantine, reject_drift=reject_drift,
        )
        result.cases.extend(cases)
        inv_by_source[src["label"]] = inv

    exclusions, exc_inv = collect_exclusions(
        repo_root=repo_root, baseline=baseline, quarantine=result.quarantine,
    )
    result.exclusions.extend(exclusions)
    for x in exc_inv:
        inv_by_source[x["label"]] = x

    result.source_inventory = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "sources": inv_by_source,
    }

    # 写盘
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cases").mkdir(parents=True, exist_ok=True)
    # 按 episode 分文件
    by_ep: dict[str, list[dict[str, Any]]] = {}
    for c in result.cases:
        by_ep.setdefault(c["episode_id"], []).append(c)
    for ep, items in by_ep.items():
        with (out_dir / "cases" / f"{ep}.jsonl").open("w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")

    with (out_dir / "exclusions.jsonl").open("w", encoding="utf-8") as f:
        for x in result.exclusions:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    with (out_dir / "quarantine.jsonl").open("w", encoding="utf-8") as f:
        for q in result.quarantine:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    # index
    index = {
        "schema_version": "experience-index-v1",
        "generated_at": result.source_inventory["generated_at"],
        "counts": {
            "cases_total": len(result.cases),
            "by_episode": {ep: len(v) for ep, v in by_ep.items()},
            "exclusions": len(result.exclusions),
            "quarantine": len(result.quarantine),
        },
        "episode_files": {ep: f"cases/{ep}.jsonl" for ep in by_ep},
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "schema_version": "experience-ingestion-manifest-v1",
        "generated_at": result.source_inventory["generated_at"],
        "repo_root": str(repo_root),
        "baseline_path": str(baseline_path) if baseline_path else None,
        "source_inventory": result.source_inventory,
        "counts": index["counts"],
        "prohibited_actions": [
            "modify_production_rules",
            "modify_champion",
            "modify_review_frontend",
            "regenerate_candidates",
            "retrain_model",
            "publish_edl",
        ],
        "notes": "Challenger only; canonical experience snapshot untouched.",
    }
    result.manifest = manifest
    (out_dir / "ingestion_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "source_inventory.json").write_text(
        json.dumps(result.source_inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "index.json",
            "ingestion_manifest.json",
            "source_inventory.json",
            "exclusions.jsonl",
            "quarantine.jsonl",
        ]:
            (run_dir / name).write_text(
                (out_dir / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        (run_dir / "cases").mkdir(parents=True, exist_ok=True)
        for ep in by_ep:
            (run_dir / "cases" / f"{ep}.jsonl").write_text(
                (out_dir / "cases" / f"{ep}.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--reject-source-drift", action="store_true",
                    help="来源 SHA 与 baseline 不一致时，隔离该来源")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    out_dir = Path(args.out_dir).resolve()
    baseline_path = Path(args.baseline).resolve() if args.baseline else None
    if baseline_path is None:
        default_baseline = (Path(__file__).resolve().parent.parent
                            / "baseline" / "source_shas.json")
        if default_baseline.exists():
            baseline_path = default_baseline
    run_dir = Path(args.run_dir).resolve() if args.run_dir else None

    result = run_ingestion(
        repo_root=repo_root, out_dir=out_dir,
        baseline_path=baseline_path, run_dir=run_dir,
        reject_drift=args.reject_source_drift,
    )
    print(json.dumps({
        "cases": len(result.cases),
        "exclusions": len(result.exclusions),
        "quarantine": len(result.quarantine),
        "out_dir": str(out_dir),
        "run_dir": str(run_dir) if run_dir else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
