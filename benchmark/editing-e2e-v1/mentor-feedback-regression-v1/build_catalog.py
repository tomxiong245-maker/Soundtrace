#!/usr/bin/env python3
"""Build and strictly validate the development-only Mentor feedback regression.

This utility deliberately reads exactly four JSON files: the explicitly pinned
final decision snapshot and review package from each of two historical review
runs.  It never globs for a newer-looking file, opens no preview or source
media, and does not modify either run.  The result is useful for Challenger
regression analysis only; it is not a production rule, training gold, an
autocut policy, or an approval of any current delivery run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "mentor-feedback-regression-v1"
CATALOG_FILENAME = "catalog.json"
REPORT_FILENAME = "REPORT.md"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_SELECTION_TOKENS = ("partial", "auto_saved", "autosave", "draft")


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    run_relpath: str
    decisions_relpath: str
    review_package_relpath: str
    selection_note: str
    intentionally_excluded: tuple[str, ...]


# This is an allow-list, not a file-name discovery rule.  In particular, the
# round-2 autosave, timestamped non-final snapshot, and partial snapshot must
# never become input merely because they happen to exist beside the final file.
SOURCES = (
    SourceSpec(
        source_id="mixed-14-final",
        run_relpath="main/runs/EP04/EP04-review-mixed-14-20260814-043428",
        decisions_relpath="human_decisions_and_feedback__20260814-052319.json",
        review_package_relpath="review_bundle/review_package.json",
        selection_note=(
            "Exact completed 14-item decision file pinned by this development "
            "asset; it is the sole decision snapshot selected from this run."
        ),
        intentionally_excluded=(),
    ),
    SourceSpec(
        source_id="round2-final",
        run_relpath="main/runs/EP04/EP04-review-round2-20260814-1355",
        decisions_relpath="human_decisions_and_feedback__20260814-final.json",
        review_package_relpath="review_bundle/review_package.json",
        selection_note=(
            "Exact file explicitly named final.  No glob or timestamp-based "
            "selection is permitted."
        ),
        intentionally_excluded=(
            "auto_saved_reviews.json",
            "human_decisions_and_feedback__20260814-061242.json",
            "human_decisions_and_feedback__20260814-1348-partial.json",
        ),
    ),
)


class ValidationFailure(Exception):
    """Raised when a pinned source cannot form a safe regression catalog."""


def project_root() -> Path:
    # benchmark/editing-e2e-v1/mentor-feedback-regression-v1/build_catalog.py
    return Path(__file__).resolve().parents[3]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def read_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValidationFailure(f"{path}: root must be a JSON object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def safe_relpath(value: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationFailure(f"source path must stay project-relative: {value}")


def source_paths(root: Path, spec: SourceSpec) -> tuple[Path, Path]:
    for relative in (spec.run_relpath, spec.decisions_relpath, spec.review_package_relpath):
        safe_relpath(relative)
    decision_name = Path(spec.decisions_relpath).name.lower()
    if any(token in decision_name for token in FORBIDDEN_SELECTION_TOKENS):
        raise ValidationFailure(
            f"{spec.source_id}: forbidden autosave/partial/draft source selection: "
            f"{spec.decisions_relpath}"
        )
    run_dir = root / spec.run_relpath
    decision_path = run_dir / spec.decisions_relpath
    package_path = run_dir / spec.review_package_relpath
    if not decision_path.is_file():
        raise ValidationFailure(f"{spec.source_id}: missing pinned final decision JSON: {decision_path}")
    if not package_path.is_file():
        raise ValidationFailure(f"{spec.source_id}: missing pinned review package JSON: {package_path}")
    return decision_path, package_path


def preview_hash_state(
    package_previews: dict[str, Any],
    listened_previews: dict[str, Any],
    kind: str,
    errors: list[str],
    context: str,
) -> dict[str, Any]:
    field = f"{kind}_sha256"
    expected = package_previews.get(field)
    recorded = listened_previews.get(field)
    require(errors, is_sha256(expected), f"{context}: package previews.{field} must be a SHA-256")
    if recorded is not None:
        require(errors, is_sha256(recorded), f"{context}: listened_previews.{field} must be a SHA-256 when present")

    if recorded is None:
        status = "not_recorded"
    elif recorded == expected:
        status = "match"
    else:
        # This is an observed provenance difference, not a claim about audio
        # contents.  It remains visible in the catalog and report.
        status = "mismatch"
    return {
        "package_sha256": expected,
        "human_recorded_sha256": recorded,
        "derived_comparison": status,
    }


def load_source(root: Path, spec: SourceSpec) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    decision_path, package_path = source_paths(root, spec)
    decision_file = read_object(decision_path)
    review_package = read_object(package_path)
    entry_source_reference = {
        "run_relpath": spec.run_relpath,
        "final_decisions_relpath": f"{spec.run_relpath}/{spec.decisions_relpath}",
        "final_decisions_sha256": sha256_file(decision_path),
        "review_package_relpath": f"{spec.run_relpath}/{spec.review_package_relpath}",
        "review_package_sha256": sha256_file(package_path),
    }
    errors: list[str] = []

    decisions_container = decision_file.get("decisions")
    require(errors, isinstance(decisions_container, dict), f"{spec.source_id}: decisions must be an object")
    if not isinstance(decisions_container, dict):
        raise ValidationFailure("\n".join(errors))
    decisions = decisions_container.get("decisions")
    candidates = review_package.get("candidates")
    require(errors, isinstance(decisions, list), f"{spec.source_id}: decisions.decisions must be an array")
    require(errors, isinstance(candidates, list), f"{spec.source_id}: review package candidates must be an array")
    if not isinstance(decisions, list) or not isinstance(candidates, list):
        raise ValidationFailure("\n".join(errors))

    package_id = review_package.get("package_id")
    require(errors, isinstance(package_id, str) and package_id, f"{spec.source_id}: review package has no package_id")
    require(
        errors,
        decisions_container.get("package_id") == package_id,
        f"{spec.source_id}: decision package_id does not equal review package package_id",
    )

    package_manifest_binding = review_package.get("review_manifest_sha256")
    decision_manifest_binding = decisions_container.get("review_manifest_sha256")
    if package_manifest_binding is not None or decision_manifest_binding is not None:
        require(
            errors,
            package_manifest_binding == decision_manifest_binding,
            f"{spec.source_id}: decision/review-package review_manifest_sha256 differs",
        )

    candidate_by_id: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidates):
        context = f"{spec.source_id}: candidates[{index}]"
        require(errors, isinstance(candidate, dict), f"{context} must be an object")
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("candidate_id")
        require(errors, isinstance(candidate_id, str) and candidate_id, f"{context}: missing candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        require(errors, candidate_id not in candidate_by_id, f"{spec.source_id}: duplicate package candidate_id={candidate_id}")
        candidate_by_id[candidate_id] = candidate
        require(errors, is_sha256(candidate.get("semantic_sha256")), f"{context}: semantic_sha256 must be a SHA-256")
        previews = candidate.get("previews")
        require(errors, isinstance(previews, dict), f"{context}: previews must be an object")
        if isinstance(previews, dict):
            for kind in ("original", "proposed_cut"):
                require(
                    errors,
                    is_sha256(previews.get(f"{kind}_sha256")),
                    f"{context}: previews.{kind}_sha256 must be a SHA-256",
                )

    decision_by_id: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    decision_counts: Counter[str] = Counter()
    feedback_nonempty = 0
    preview_stats: Counter[str] = Counter()

    for index, decision in enumerate(decisions):
        context = f"{spec.source_id}: decisions[{index}]"
        require(errors, isinstance(decision, dict), f"{context} must be an object")
        if not isinstance(decision, dict):
            continue
        candidate_id = decision.get("candidate_id")
        require(errors, isinstance(candidate_id, str) and candidate_id, f"{context}: missing candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        require(errors, candidate_id not in decision_by_id, f"{spec.source_id}: duplicate decision candidate_id={candidate_id}")
        decision_by_id[candidate_id] = decision
        candidate = candidate_by_id.get(candidate_id)
        require(errors, candidate is not None, f"{context}: candidate_id={candidate_id} is absent from review package")
        if candidate is None:
            continue

        human_decision = decision.get("decision")
        require(errors, human_decision in {"accept", "reject"}, f"{context}: decision must be accept or reject")
        if human_decision in {"accept", "reject"}:
            decision_counts[human_decision] += 1
        require(errors, is_sha256(decision.get("candidate_semantic_sha256")), f"{context}: candidate_semantic_sha256 must be a SHA-256")
        require(
            errors,
            decision.get("candidate_semantic_sha256") == candidate.get("semantic_sha256"),
            f"{context}: candidate semantic SHA does not equal review package semantic SHA",
        )
        require(errors, "feedback" in decision, f"{context}: feedback is missing; refusing silent note loss")
        feedback = decision.get("feedback")
        require(errors, isinstance(feedback, str), f"{context}: feedback must be a string, including when empty")
        if isinstance(feedback, str) and feedback.strip():
            feedback_nonempty += 1
        listened_previews = decision.get("listened_previews")
        require(errors, isinstance(listened_previews, dict), f"{context}: listened_previews must be an object")
        if not isinstance(listened_previews, dict):
            listened_previews = {}
        previews = candidate.get("previews")
        if not isinstance(previews, dict):
            previews = {}
        normalized_previews = {
            kind: preview_hash_state(previews, listened_previews, kind, errors, context)
            for kind in ("original", "proposed_cut")
        }
        for normalized in normalized_previews.values():
            comparison = normalized["derived_comparison"]
            preview_stats[f"{comparison}_fields"] += 1

        entries.append(
            {
                "case_id": f"{spec.source_id}:{candidate_id}",
                "source_id": spec.source_id,
                "candidate_id": candidate_id,
                "source_reference": copy.deepcopy(entry_source_reference),
                "candidate_metadata": copy.deepcopy(candidate),
                "human_decision": copy.deepcopy(decision),
                "feedback": {
                    "verbatim": feedback,
                    "derived_nonempty": bool(isinstance(feedback, str) and feedback.strip()),
                    "derived_character_count": len(feedback) if isinstance(feedback, str) else None,
                },
                "preview_hashes": {
                    "package_and_human_record": normalized_previews,
                    "note": (
                        "Package hashes and hashes recorded during human listening are both preserved. "
                        "The comparison is derived evidence only and does not replace the human decision."
                    ),
                },
                "validation": {
                    "candidate_semantic_sha256_matches_review_package": (
                        decision.get("candidate_semantic_sha256") == candidate.get("semantic_sha256")
                    ),
                    "feedback_is_preserved_verbatim": True,
                },
            }
        )

    require(
        errors,
        set(candidate_by_id) == set(decision_by_id),
        f"{spec.source_id}: final decisions and review package candidate IDs are not an exact set match",
    )
    require(
        errors,
        len(entries) == len(candidates) == len(decisions),
        f"{spec.source_id}: final decision count must exactly equal review package candidate count",
    )
    if errors:
        raise ValidationFailure("\n".join(f"- {error}" for error in errors))

    source_summary = {
        "source_id": spec.source_id,
        "run_relpath": spec.run_relpath,
        "final_decisions_relpath": entry_source_reference["final_decisions_relpath"],
        "final_decisions_sha256": entry_source_reference["final_decisions_sha256"],
        "review_package_relpath": entry_source_reference["review_package_relpath"],
        "review_package_sha256": entry_source_reference["review_package_sha256"],
        "selection": {
            "method": "explicit_allowlist_only",
            "note": spec.selection_note,
            "intentionally_excluded_relpaths": [
                f"{spec.run_relpath}/{relative}" for relative in spec.intentionally_excluded
            ],
        },
        "decision_file_metadata": {
            "saved_at": decision_file.get("_saved_at"),
            "saved_by_endpoint": decision_file.get("_saved_by_endpoint"),
            "schema_version": decisions_container.get("schema_version"),
            "package_id": decisions_container.get("package_id"),
            "review_manifest_binding": decision_manifest_binding,
        },
        "review_package_metadata": {
            "schema_version": review_package.get("schema_version"),
            "episode_id": review_package.get("episode_id"),
            "package_id": package_id,
            "review_manifest_binding": package_manifest_binding,
        },
        "counts": {
            "candidates": len(candidates),
            "decisions": len(decisions),
            "accept": decision_counts["accept"],
            "reject": decision_counts["reject"],
            "feedback_nonempty": feedback_nonempty,
            "feedback_empty": len(decisions) - feedback_nonempty,
            "semantic_sha256_matches": len(entries),
            "preview_hash_fields_recorded": preview_stats["match_fields"] + preview_stats["mismatch_fields"],
            "preview_hash_fields_match": preview_stats["match_fields"],
            "preview_hash_fields_mismatch": preview_stats["mismatch_fields"],
            "preview_hash_fields_not_recorded": preview_stats["not_recorded_fields"],
        },
    }
    return source_summary, entries, dict(preview_stats)


def build_catalog(root: Path) -> dict[str, Any]:
    source_summaries: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    preview_totals: Counter[str] = Counter()
    for spec in SOURCES:
        source_summary, source_entries, source_preview_stats = load_source(root, spec)
        source_summaries.append(source_summary)
        entries.extend(source_entries)
        preview_totals.update(source_preview_stats)

    counts = Counter(entry["human_decision"]["decision"] for entry in entries)
    feedback_nonempty = sum(int(entry["feedback"]["derived_nonempty"]) for entry in entries)
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": "mentor-feedback-regression-v1",
        "benchmark_scope": {
            "split": "development",
            "frozen": False,
            "training_gold": False,
            "eligible_for_champion_comparison": False,
            "production_rule_change": False,
            "current_delivery_run_change": False,
            "canonical_experience_snapshot_change": False,
            "real_media_copied_or_opened": False,
        },
        "purpose": (
            "Regression evidence for future Challenger analysis of human accept/reject decisions and "
            "verbatim Mentor feedback. It cannot authorize deletion, replace human review, or modify v20."
        ),
        "source_policy": {
            "only_input_kinds": ["final human_decisions_and_feedback JSON", "review_bundle/review_package.json"],
            "selection": "explicit allow-list; no glob, autosave, partial, draft, or timestamp inference",
            "forbidden_selection_tokens": list(FORBIDDEN_SELECTION_TOKENS),
            "media_handling": "No preview/source audio is opened, decoded, copied, or hashed by this builder.",
        },
        "feedback_handling": {
            "storage": "verbatim per human decision, including empty strings",
            "keyword_classification": {
                "status": "not_generated",
                "reason": "No derived keyword category is allowed to stand in for a human label or feedback.",
            },
        },
        "sources": source_summaries,
        "entries": entries,
        "summary": {
            "total_decisions": len(entries),
            "accept": counts["accept"],
            "reject": counts["reject"],
            "feedback_nonempty": feedback_nonempty,
            "feedback_empty": len(entries) - feedback_nonempty,
            "candidate_semantic_sha256_matches_review_package": len(entries),
            "preview_hash_fields": {
                "recorded": preview_totals["match_fields"] + preview_totals["mismatch_fields"],
                "match": preview_totals["match_fields"],
                "mismatch": preview_totals["mismatch_fields"],
                "not_recorded": preview_totals["not_recorded_fields"],
            },
        },
        "limits": [
            "This is a development-only regression asset built from two historical review packets.",
            "It is not a frozen benchmark, human edit map, training dataset, autocut policy, or production approval.",
            "A preview-hash mismatch is retained as evidence and must not be silently treated as an A/B match.",
            "Semantic SHA equality is a strict metadata binding check; it does not prove audio quality or naturalness.",
        ],
    }


def render_report(catalog: dict[str, Any]) -> str:
    summary = catalog["summary"]
    lines = [
        "# Mentor feedback regression v1",
        "",
        "## Purpose and boundary",
        "",
        "This is a small **development-only** regression catalog of two completed Mentor review packets. "
        "It preserves the candidate metadata, final human decision, verbatim feedback, source paths/SHA, and both "
        "package and human-recorded preview hashes. It does not alter production rules, EP04 v20, the canonical "
        "experience snapshot, or real media.",
        "",
        "The builder reads only the four pinned JSON files below. It does not glob for decision files and does not "
        "open, decode, copy, or hash WAV/MP3 previews.",
        "",
        "## Pinned sources",
        "",
        "| Source | Final decision JSON | Decision SHA-256 | Review package SHA-256 | Decisions | Accept | Reject | Non-empty feedback |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for source in catalog["sources"]:
        counts = source["counts"]
        lines.append(
            "| {source_id} | `{decision}` | `{decision_sha}` | `{package_sha}` | {decisions} | {accept} | {reject} | {feedback} |".format(
                source_id=source["source_id"],
                decision=source["final_decisions_relpath"],
                decision_sha=source["final_decisions_sha256"],
                package_sha=source["review_package_sha256"],
                decisions=counts["decisions"],
                accept=counts["accept"],
                reject=counts["reject"],
                feedback=counts["feedback_nonempty"],
            )
        )
    lines.extend(
        [
            "",
            "Round 2 explicitly excludes `auto_saved_reviews.json`, the non-final timestamped snapshot, and the "
            "`-partial.json` snapshot. The mixed-14 source is also selected by exact path, never by a directory scan.",
            "",
            "## Actual result",
            "",
            f"- Final human decisions: **{summary['total_decisions']}** total — **{summary['accept']} accept**, "
            f"**{summary['reject']} reject**.",
            f"- Verbatim feedback: **{summary['feedback_nonempty']}** non-empty fields; "
            f"**{summary['feedback_empty']}** intentionally empty fields are retained as empty strings.",
            f"- Strict semantic binding: **{summary['candidate_semantic_sha256_matches_review_package']}/"
            f"{summary['total_decisions']}** decision `candidate_semantic_sha256` values exactly match their "
            "review-package candidate.",
            "",
            "## Preview-hash observation",
            "",
            f"The catalog retains **{summary['preview_hash_fields']['recorded']}** human-recorded preview-hash fields: "
            f"**{summary['preview_hash_fields']['match']}** match the current package hash and "
            f"**{summary['preview_hash_fields']['mismatch']}** differ; "
            f"**{summary['preview_hash_fields']['not_recorded']}** were not recorded. A mismatch is not erased or "
            "treated as a pass. It is a provenance question to resolve before using these records for any claim that a "
            "specific current A/B preview was heard.",
            "",
            "No feedback keyword classification is generated. Any future keyword grouping must be explicitly marked "
            "derived and can never replace the human accept/reject or verbatim feedback.",
            "",
            "## Rebuild and verify",
            "",
            "```bash",
            "python3 benchmark/editing-e2e-v1/mentor-feedback-regression-v1/build_catalog.py --build",
            "python3 benchmark/editing-e2e-v1/mentor-feedback-regression-v1/build_catalog.py --check",
            "```",
            "",
            "`--check` reconstructs the catalog in memory, validates the exact candidate-set and semantic-SHA bindings, "
            "requires every feedback field to survive verbatim, and rejects stale generated artifacts. It reads only the "
            "pinned JSON source files and the two generated text/JSON artifacts.",
            "",
        ]
    )
    return "\n".join(lines)


def expected_artifacts(root: Path) -> tuple[str, str]:
    catalog = build_catalog(root)
    return canonical_json(catalog), render_report(catalog)


def build(root: Path, output_dir: Path) -> None:
    catalog_text, report_text = expected_artifacts(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / CATALOG_FILENAME).write_text(catalog_text, encoding="utf-8")
    (output_dir / REPORT_FILENAME).write_text(report_text, encoding="utf-8")
    print(
        f"BUILT: {output_dir / CATALOG_FILENAME} and {output_dir / REPORT_FILENAME} "
        "from pinned JSON only; no media opened."
    )


def check(root: Path, output_dir: Path) -> int:
    expected_catalog, expected_report = expected_artifacts(root)
    errors: list[str] = []
    for filename, expected in ((CATALOG_FILENAME, expected_catalog), (REPORT_FILENAME, expected_report)):
        path = output_dir / filename
        if not path.is_file():
            errors.append(f"missing generated artifact: {path}")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            errors.append(f"stale or modified generated artifact: {path}; run --build")
    if errors:
        print("FAIL: mentor-feedback regression validation failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    catalog = json.loads(expected_catalog)
    summary = catalog["summary"]
    print(
        "PASS: pinned final-decision JSON only; "
        f"{summary['total_decisions']} decisions, {summary['accept']} accept, {summary['reject']} reject, "
        f"{summary['feedback_nonempty']} non-empty feedback fields; "
        f"{summary['candidate_semantic_sha256_matches_review_package']} semantic SHA bindings match. "
        "No media opened."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true", help="rebuild catalog.json and REPORT.md from pinned JSON")
    mode.add_argument("--check", action="store_true", help="strictly validate source bindings and generated artifacts")
    args = parser.parse_args()
    root = project_root()
    output_dir = Path(__file__).resolve().parent
    try:
        if args.build:
            build(root, output_dir)
            return 0
        return check(root, output_dir)
    except (OSError, ValueError, json.JSONDecodeError, ValidationFailure) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
