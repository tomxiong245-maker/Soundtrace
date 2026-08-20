#!/usr/bin/env python3
"""Apply a preference snapshot as review-priority evidence only.

The output is deliberately *not* a decision.  It annotates candidates with
historical vote counts and case IDs so the next reviewer can spend attention
where prior labels disagree or where a known reject pattern recurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any



def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def candidate_text(candidate: dict[str, Any], package_candidate: dict[str, Any] | None = None) -> str:
    merged = dict(package_candidate or {})
    merged.update({k: v for k, v in candidate.items() if v is not None})
    for key in ("proposed_delete_text", "filler_token", "proposed_text", "candidate_text", "text"):
        if merged.get(key):
            return str(merged[key])
    tracks = merged.get("text_tracks") or {}
    source = merged.get("source_track_id")
    words = (tracks.get(source) or {}).get("words") if source else None
    if words:
        start = float(merged.get("start_seconds") or 0)
        end = float(merged.get("end_seconds") or 0)
        return "".join(str(w.get("text") or "") for w in words
                       if float(w.get("start_seconds") or 0) < end
                       and float(w.get("end_seconds") or 0) > start)
    return ""


def load_records(snapshot_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((snapshot_dir / "snapshot_manifest.json").read_text(encoding="utf-8"))
    aggregate = json.loads((snapshot_dir / "aggregated.json").read_text(encoding="utf-8"))
    if manifest.get("artifacts", {}).get("aggregated.json") != sha256_file(snapshot_dir / "aggregated.json"):
        raise ValueError("preference snapshot aggregated.json SHA mismatch")
    return manifest, list(aggregate.get("records") or [])


def index_records(records: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not (record.get("quality") or {}).get("rule_analysis_eligible", False):
            continue
        candidate = record.get("candidate") or {}
        key = (
            str(candidate.get("reason_key") or "unknown"),
            norm(candidate.get("proposed_text")),
            str(candidate.get("clause_position") or ""),
        )
        index[key].append(record)
    return index


def annotate_candidates(candidates: list[dict[str, Any]], package_candidates: dict[str, dict[str, Any]],
                        records: list[dict[str, Any]], snapshot_sha: str,
                        *, episode_id: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = index_records(records)
    output: list[dict[str, Any]] = []
    counts = defaultdict(int)
    for original in candidates:
        candidate = dict(original)
        cid = str(candidate.get("candidate_id") or "")
        package_candidate = package_candidates.get(cid, {})
        merged = dict(package_candidate)
        merged.update({k: v for k, v in candidate.items() if v is not None})
        reason = str(merged.get("reason_key") or merged.get("candidate_kind") or "unknown")
        text = norm(candidate_text(candidate, package_candidate))
        clause = str(merged.get("clause_position") or "")
        rows = list(index.get((reason, text, clause), []))
        if not rows:
            rows = list(index.get((reason, text, ""), []))
        accepts = sum(1 for row in rows if (row.get("label") or {}).get("decision") == "accept")
        rejects = sum(1 for row in rows if (row.get("label") or {}).get("decision") == "reject")
        if accepts and rejects:
            signal = "mixed_history"
            priority = 2
        elif rejects:
            signal = "historical_reject"
            priority = 3
        elif accepts:
            signal = "historical_accept"
            priority = 1
        else:
            signal = "no_matching_history"
            priority = 0
        evidence = {
            "schema_version": "experience-signal-v1",
            "snapshot_sha256": snapshot_sha,
            "signal": signal,
            "review_priority": priority,
            "historical_accept_count": accepts,
            "historical_reject_count": rejects,
            "case_ids": [row.get("case_id") for row in rows],
            "policy": "review_priority_only; no decision, no auto-cut, no filtering",
        }
        candidate["experience_signal"] = evidence
        counts[signal] += 1
        output.append(candidate)
    return output, {
        "schema_version": "experience-application-report-v1",
        "snapshot_sha256": snapshot_sha,
        "candidate_count": len(output),
        "signal_counts": dict(counts),
        "policy": "review_priority_only; no decision, no auto-cut, no filtering",
        "candidates": [
            {"candidate_id": c.get("candidate_id"), "experience_signal": c.get("experience_signal")}
            for c in output
        ],
    }


def apply_to_run(run_dir: Path, snapshot_dir: Path, *, write_candidates: bool = False) -> dict[str, Any]:
    manifest, records = load_records(snapshot_dir)
    snapshot_sha = sha256_file(snapshot_dir / "snapshot_manifest.json")
    all_path = run_dir / "all_candidates.json"
    if all_path.is_file():
        all_doc = json.loads(all_path.read_text(encoding="utf-8"))
    else:
        # During a fresh orchestrator run the snapshot stage is intentionally
        # before all_candidates.json is frozen.  Read the immutable generator
        # source in that case; the caller will add the signals to all_rows.
        source_path = run_dir / "candidates" / "candidate_source.json"
        if not source_path.is_file():
            raise FileNotFoundError(f"missing candidate source: {source_path}")
        all_doc = json.loads(source_path.read_text(encoding="utf-8"))
        all_doc["run_id"] = all_doc.get("run_id") or run_dir.name
    package_path = run_dir / "review_bundle" / "review_package.json"
    package_candidates: dict[str, dict[str, Any]] = {}
    if package_path.is_file():
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package_candidates = {str(c.get("candidate_id")): c for c in package.get("candidates") or []}
    annotated, report = annotate_candidates(
        all_doc.get("candidates") or [], package_candidates, records, snapshot_sha,
        episode_id=str(all_doc.get("episode_id") or ""),
    )
    report["run_id"] = all_doc.get("run_id")
    report["snapshot_id"] = manifest.get("snapshot_id")
    if write_candidates:
        all_doc["candidates"] = annotated
        all_doc["experience_snapshot"] = {
            "snapshot_id": manifest.get("snapshot_id"),
            "snapshot_manifest_sha256": snapshot_sha,
            "scope": "review priority and ordering only",
        }
        all_path.write_text(json.dumps(all_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--write-candidates", action="store_true")
    args = parser.parse_args(argv)
    report = apply_to_run(args.run_dir.resolve(), args.snapshot_dir.resolve(), write_candidates=args.write_candidates)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_id": report.get("run_id"), "signal_counts": report.get("signal_counts"), "out": str(args.out.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
