#!/usr/bin/env python3
"""Refresh the active, immutable label-learning snapshot after a real label save.

This is deliberately narrower than an online learning system.  Each saved
human ``accept``/``reject`` label can create a new immutable evidence run and
atomically point *future* delivery runs at it.  The pointer can affect review
ordering and protective preserve/review guards only; it can never create an
EDL action, a human decision, or an automatic semantic cut.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "main" / "runs"
ACTIVE_POINTER_RELPATH = Path("main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json")
LIVE_HUMAN_DECISIONS_FILENAME = "human_decisions_and_feedback.live.json"
FINAL_HUMAN_DECISIONS_FILENAME = "human_decisions.json"
HUMAN_LABEL_SOURCE_FILENAMES = {
    LIVE_HUMAN_DECISIONS_FILENAME,
    FINAL_HUMAN_DECISIONS_FILENAME,
}
NON_HUMAN_REVIEWER_PREFIXES = ("AUTOMATED_", "LEARNED_", "MACHINE_")
CANONICAL_CASE_STORE_RELPATH = Path(
    "稳定生产/challengers/experience-ingestion-v1/case_store/two-state-v1-20260812-1627"
)
EXPERIENCE_SCRIPTS = PROJECT_ROOT / "稳定生产" / "challengers" / "experience-ingestion-v1" / "scripts"
if str(EXPERIENCE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(EXPERIENCE_SCRIPTS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_preference_snapshot import build_snapshot  # noqa: E402
import label_learning_driver as learning_driver  # noqa: E402


class SnapshotRefreshError(RuntimeError):
    """A refresh failed before its new snapshot became active."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_run_id() -> str:
    return "LABEL-LEARNING-AUTO-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotRefreshError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotRefreshError(f"JSON object required: {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json_sha256(value: Any) -> str:
    """Hash the learning-relevant payload, independent of browser session noise."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def is_real_human_reviewer(value: Any) -> bool:
    reviewer = str(value or "").strip()
    return bool(reviewer) and not reviewer.upper().startswith(NON_HUMAN_REVIEWER_PREFIXES)


def decision_content_projection(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return only fields whose change should rebuild learning evidence.

    Browser auto-save changes timestamps and preview-listening metadata even
    when the reviewer has learned nothing new.  They remain in the immutable
    source document, but are intentionally excluded from this idempotency key.
    """

    rows: list[dict[str, Any]] = []
    for raw in document.get("decisions") or []:
        if not isinstance(raw, Mapping):
            continue
        decision = str(raw.get("decision") or "").lower()
        if decision not in {"accept", "reject"}:
            continue
        rows.append(
            {
                "candidate_id": str(raw.get("candidate_id") or ""),
                "candidate_semantic_sha256": str(raw.get("candidate_semantic_sha256") or ""),
                "decision": decision,
                "feedback": str(raw.get("feedback") or "").strip(),
            }
        )
    rows.sort(key=lambda row: row["candidate_id"])
    return {
        "schema_version": "label-learning-decision-content-v1",
        "package_id": str(document.get("package_id") or ""),
        "review_manifest_sha256": str(document.get("review_manifest_sha256") or ""),
        "reviewer": str(document.get("reviewer") or "").strip(),
        "decisions": rows,
    }


def decision_content_sha256(document: Mapping[str, Any]) -> str:
    return canonical_json_sha256(decision_content_projection(document))


def _relative_to_project(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError as exc:
        raise SnapshotRefreshError(f"path must stay inside project root: {path}") from exc


def _real_human_label_source(
    run_dir: Path,
    decision_path: Path,
    *,
    require_complete: bool,
) -> dict[str, Any]:
    """Validate either a live partial label source or a final complete review.

    Live saves may contain only the subset already labelled by the human.  A
    final submission must cover every candidate.  Both are bound to the same
    frozen review package and both reject machine/placeholder reviewers.
    """

    package_path = run_dir / "review_bundle" / "review_package.json"
    source = decision_path.expanduser().resolve()
    if source.name not in HUMAN_LABEL_SOURCE_FILENAMES:
        raise SnapshotRefreshError(f"unsupported human label source filename: {source.name}")
    try:
        source.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise SnapshotRefreshError("human label source must stay inside its review run") from exc
    if not source.is_file() or not package_path.is_file():
        raise SnapshotRefreshError("human label refresh requires a decision source and review_bundle/review_package.json")

    decisions = read_json(source)
    package = read_json(package_path)
    reviewer = str(decisions.get("reviewer") or "").strip()
    if not is_real_human_reviewer(reviewer):
        raise SnapshotRefreshError("active snapshot refresh accepts only a real named human reviewer")
    rows = decisions.get("decisions")
    if not isinstance(rows, list) or not rows:
        raise SnapshotRefreshError("human label refresh needs at least one itemized accept/reject decision")
    if decisions.get("package_id") != package.get("package_id"):
        raise SnapshotRefreshError("human decisions package_id does not match the review package")
    if decisions.get("review_manifest_sha256") != package.get("review_manifest_sha256"):
        raise SnapshotRefreshError("human decisions manifest does not match the review package")

    candidates = {
        str(candidate.get("candidate_id")): candidate
        for candidate in package.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    seen: set[str] = set()
    accepted = 0
    rejected = 0
    for row in rows:
        if not isinstance(row, dict):
            raise SnapshotRefreshError("human decisions contains an invalid row")
        candidate_id = str(row.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate_id in seen:
            raise SnapshotRefreshError(f"human decision has duplicate or unknown candidate: {candidate_id}")
        seen.add(candidate_id)
        if row.get("candidate_semantic_sha256") != candidate.get("semantic_sha256"):
            raise SnapshotRefreshError(f"{candidate_id}: candidate semantic SHA does not match the review package")
        if str(row.get("reviewer") or reviewer).strip() != reviewer:
            raise SnapshotRefreshError(f"{candidate_id}: decision reviewer does not match the named human reviewer")
        if not str(row.get("decided_at") or "").strip():
            raise SnapshotRefreshError(f"{candidate_id}: decision time is required")
        feedback = row.get("feedback", "")
        if not isinstance(feedback, str) or len(feedback) > 500:
            raise SnapshotRefreshError(f"{candidate_id}: feedback must be a string of at most 500 characters")
        decision = str(row.get("decision") or "").lower()
        if decision == "accept":
            accepted += 1
        elif decision == "reject":
            rejected += 1
        else:
            raise SnapshotRefreshError("active snapshot refresh accepts only explicit accept/reject labels")

    if require_complete and set(candidates) != seen:
        raise SnapshotRefreshError("final human review must contain exactly one accept/reject decision per package candidate")
    return {
        "reviewer": reviewer,
        "decision_count": len(rows),
        "accept": accepted,
        "reject": rejected,
        "decision_path": source,
        "decision_file_sha256": sha256_file(source),
        "decision_content_sha256": decision_content_sha256(decisions),
        "package_path": package_path,
        "require_complete": require_complete,
    }


def _insufficient_backtest(snapshot_dir: Path, error: Exception) -> dict[str, Any]:
    _, manifest, records = learning_driver.load_snapshot(snapshot_dir)
    eligible = [
        row for row in records
        if bool((row.get("quality") or {}).get("rule_analysis_eligible"))
        and str((row.get("label") or {}).get("decision")) in {"accept", "reject"}
    ]
    episodes = sorted({learning_driver._logical_episode_id(row) for row in eligible})
    reviewers = sorted(
        {
            learning_driver._record_reviewer(row)
            for row in eligible
            if learning_driver._record_reviewer(row) != "UNKNOWN_REVIEWER"
        }
    )
    audio_count = sum(bool(learning_driver._record_audio_identity(row)) for row in eligible)
    bundle_count = sum(bool(learning_driver._record_source_bundle_identity(row)) for row in eligible)
    return {
        "schema_version": learning_driver.BACKTEST_SCHEMA_VERSION,
        "driver_id": learning_driver.DRIVER_ID,
        "driver_source_sha256": learning_driver.driver_source_sha256(),
        "snapshot": {
            "snapshot_id": manifest.get("snapshot_id"),
            "snapshot_manifest_sha256": sha256_file(snapshot_dir / "snapshot_manifest.json"),
            "eligible_record_count": len(eligible),
        },
        "method": {
            "split": "leave_one_episode_out",
            "status": "NOT_RUN_TOO_FEW_EPISODES",
            "forbidden": ["machine labels as truth", "automatic deletion"],
        },
        "data_quality": {
            "status": "INSUFFICIENT_DATA_FOR_CROSS_EPISODE_GENERALIZATION",
            "episode_ids": episodes,
            "independent_reviewer_ids": reviewers,
            "source_audio_sha256_coverage": {"records_with_identity": audio_count, "eligible_records": len(eligible)},
            "source_bundle_sha256_coverage": {"records_with_identity": bundle_count, "eligible_records": len(eligible)},
            "blockers": [str(error)],
            "rule": "snapshot remains usable for review priority and protection; no cross-episode accuracy or autocut claim is allowed",
        },
        "summary": {
            "episode_fold_count": 0,
            "holdout_record_count": len(eligible),
            "machine_suggestion_count": 0,
            "human_review_required_count": len(eligible),
            "suggestion_coverage": 0.0,
            "suggestion_precision": None,
            "raw_diagnostic_suggestion_precision": None,
            "harmful_suggestion_count": 0,
            "autocut_policy": "NOT_APPROVED",
        },
    }


def _backtest(snapshot_dir: Path) -> dict[str, Any]:
    try:
        return learning_driver.backtest_document(snapshot_dir=snapshot_dir)
    except ValueError as exc:
        if "at least two episodes" not in str(exc):
            raise
        return _insufficient_backtest(snapshot_dir, exc)


def _backtest_markdown(document: Mapping[str, Any]) -> str:
    data_quality = document.get("data_quality") or {}
    summary = document.get("summary") or {}
    return "\n".join(
        [
            "# 标签学习自动刷新：防泄漏回测",
            "",
            f"- 状态：`{data_quality.get('status')}`",
            f"- 留出案例：{summary.get('holdout_record_count')}",
            f"- 机器建议：{summary.get('machine_suggestion_count')}",
            f"- 必须人审：{summary.get('human_review_required_count')}",
            "",
            "这份报告不会授权自动删剪；即使数据不足，新的不可变快照仍可供未来审核排序与保护规则读取。",
            "",
        ]
    )


def _validated_active_pointer(root: Path) -> dict[str, Any] | None:
    """Load the existing pointer only after verifying its target still exists."""

    pointer_path = root / ACTIVE_POINTER_RELPATH
    if not pointer_path.is_file():
        return None
    pointer = read_json(pointer_path)
    if pointer.get("schema_version") != "active-label-learning-snapshot-pointer-v1":
        raise SnapshotRefreshError("active label-learning pointer has an unsupported schema")
    # Do not silently replace a broken pointer: a bad active state must stay
    # visible until it is repaired instead of falling back to stale evidence.
    resolve_active_snapshot(root)
    return pointer


def _create_snapshot_and_activate(
    *,
    root: Path,
    run_relpath: str,
    submission: Mapping[str, Any],
    trigger_kind: str,
    run_id: str | None,
    source_files: list[tuple[str, Path, str]],
    excluded_decision_paths: tuple[Path, ...] = (),
    trigger_extra: Mapping[str, Any] | None = None,
    source_effect: str = "label_save",
    source_transition: Any = None,
) -> dict[str, Any]:
    """Build immutable evidence first, then atomically point future runs at it.

    A label withdrawal uses the same evidence path as a label save, while
    temporarily excluding the old mutable live sidecar from snapshot discovery.
    ``source_transition`` is delayed until the replacement snapshot and
    backtest exist; if pointer activation fails, its rollback restores the
    mutable source so the prior active evidence remains internally consistent.
    """

    new_run_id = run_id or default_run_id()
    if not new_run_id.startswith("LABEL-LEARNING-AUTO-"):
        raise SnapshotRefreshError("automatic snapshot run_id must start with LABEL-LEARNING-AUTO-")
    output = root / "main" / "runs" / new_run_id
    if output.exists():
        raise SnapshotRefreshError(f"refusing to overwrite immutable snapshot run: {output}")
    output.mkdir(parents=True, exist_ok=False)
    snapshot_dir = output / "preference_snapshot"
    canonical_store = root / CANONICAL_CASE_STORE_RELPATH
    pointer_path = root / ACTIVE_POINTER_RELPATH
    source_relpath = _relative_to_project(root, Path(submission["decision_path"]))
    source_hash = str(submission["decision_file_sha256"])
    frozen_artifacts: dict[str, str] = {}
    rollback_source = None
    try:
        # Freeze all mutable trigger files before building the derived snapshot.
        for relpath, source_path, expected_sha in source_files:
            source_bytes = source_path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != expected_sha:
                raise SnapshotRefreshError(
                    f"source changed before snapshot creation: {source_path}; save again to retry"
                )
            destination = output / relpath
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_bytes)
            frozen_artifacts[relpath] = sha256_file(destination)

        snapshot = build_snapshot(
            root,
            snapshot_dir,
            canonical_store if canonical_store.is_dir() else None,
            excluded_decision_paths=excluded_decision_paths,
        )
        for _, source_path, expected_sha in source_files:
            if sha256_file(source_path) != expected_sha:
                raise SnapshotRefreshError(
                    f"source changed during snapshot creation: {source_path}; active pointer was not updated"
                )
        # This verifies the aggregate hash before an active pointer can use it.
        learning_driver.load_snapshot(snapshot_dir)
        backtest = _backtest(snapshot_dir)
        write_json(output / "backtest_report.json", backtest)
        (output / "backtest_report.md").write_text(_backtest_markdown(backtest), encoding="utf-8")
        record_counts = Counter(
            str((row.get("label") or {}).get("decision"))
            for row in learning_driver.load_snapshot(snapshot_dir)[2]
        )
        trigger = {
            "kind": trigger_kind,
            "review_run_relpath": run_relpath,
            "human_labels_relpath": source_relpath,
            "human_labels_file_sha256": source_hash,
            "frozen_human_labels_relpath": next(iter(frozen_artifacts), None),
            "frozen_human_labels_sha256": frozen_artifacts.get(next(iter(frozen_artifacts), "")),
            "frozen_source_files": frozen_artifacts,
            "decision_content_sha256": submission["decision_content_sha256"],
            "review_package_relpath": _relative_to_project(root, Path(submission["package_path"])),
            "review_package_sha256": sha256_file(Path(submission["package_path"])),
            "reviewer": submission["reviewer"],
            "decision_count": submission["decision_count"],
            "accept_count": submission["accept"],
            "reject_count": submission["reject"],
            "require_complete": submission["require_complete"],
        }
        if trigger_extra:
            trigger.update(dict(trigger_extra))
        identity = {
            "schema_version": "label-learning-auto-refresh-run-v1",
            "run_id": new_run_id,
            "created_at": utc_now(),
            "trigger": trigger,
            "label_source_trust": "USER_CONFIRMED_HUMAN_LABELS",
            "snapshot_relpath": "preference_snapshot/snapshot_manifest.json",
            "snapshot_manifest_sha256": sha256_file(snapshot_dir / "snapshot_manifest.json"),
            "backtest_relpath": "backtest_report.json",
            "backtest_sha256": sha256_file(output / "backtest_report.json"),
            "scope": "future review ordering, historical reference and preserve/review guards only",
            "prohibited": ["write_human_decision", "write_edl", "render_audio", "autocut_permission", "modify_current_review_bundle"],
        }
        write_json(output / "run_identity.json", identity)
        report = {
            "schema_version": "label-learning-auto-refresh-manifest-v1",
            "run_id": new_run_id,
            "trigger": identity["trigger"],
            "snapshot_counts": snapshot.get("counts"),
            "observed_label_counts": dict(record_counts),
            "backtest_status": (backtest.get("data_quality") or {}).get("status"),
            "artifacts": {
                **frozen_artifacts,
                "run_identity.json": sha256_file(output / "run_identity.json"),
                "preference_snapshot/snapshot_manifest.json": sha256_file(snapshot_dir / "snapshot_manifest.json"),
                "backtest_report.json": sha256_file(output / "backtest_report.json"),
                "backtest_report.md": sha256_file(output / "backtest_report.md"),
            },
            "activation": "PENDING_ATOMIC_POINTER_UPDATE",
        }
        write_json(output / "refresh_manifest.json", report)
        pointer = {
            "schema_version": "active-label-learning-snapshot-pointer-v1",
            "updated_at": utc_now(),
            "active_refresh_run_relpath": _relative_to_project(root, output),
            "active_snapshot_manifest_relpath": _relative_to_project(root, snapshot_dir / "snapshot_manifest.json"),
            "active_snapshot_manifest_sha256": sha256_file(snapshot_dir / "snapshot_manifest.json"),
            "active_snapshot_id": snapshot.get("snapshot_id"),
            "refresh_manifest_relpath": _relative_to_project(root, output / "refresh_manifest.json"),
            "refresh_manifest_sha256": sha256_file(output / "refresh_manifest.json"),
            "source_review_run_relpath": run_relpath,
            "source_human_labels_relpath": source_relpath,
            "source_human_labels_file_sha256": source_hash,
            "source_decision_content_sha256": submission["decision_content_sha256"],
            "source_trigger_kind": trigger_kind,
            "source_effect": source_effect,
            "label_source_trust": "USER_CONFIRMED_HUMAN_LABELS",
            "scope": "future new runs: review priority, historical reference and preserve/review guards only",
            "prohibited": ["automatic semantic cut", "human decision", "EDL action", "audio render", "Champion promotion"],
        }
        try:
            if source_transition is not None:
                rollback_source = source_transition()
            write_json_atomic(pointer_path, pointer)
        except Exception:
            if callable(rollback_source):
                rollback_source()
            raise
        return {
            "status": "WITHDRAWN_AND_ACTIVE" if source_effect == "label_withdrawal" else "ACTIVE",
            "refresh_run": str(output),
            "snapshot_manifest": str(snapshot_dir / "snapshot_manifest.json"),
            "active_pointer": str(pointer_path),
            "snapshot_counts": snapshot.get("counts"),
            "backtest_status": (backtest.get("data_quality") or {}).get("status"),
            "decision_content_sha256": submission["decision_content_sha256"],
            "label_source_trust": pointer["label_source_trust"],
        }
    except Exception as exc:
        failure = {
            "schema_version": "label-learning-auto-refresh-failure-v1",
            "run_id": new_run_id,
            "failed_at": utc_now(),
            "review_run_relpath": run_relpath,
            "human_labels_relpath": source_relpath,
            "decision_content_sha256": submission["decision_content_sha256"],
            "trigger_kind": trigger_kind,
            "error": str(exc),
            "active_pointer_unchanged": True,
        }
        write_json(output / "REFRESH_FAILED.json", failure)
        if isinstance(exc, SnapshotRefreshError):
            raise
        raise SnapshotRefreshError(str(exc)) from exc


def _refresh_after_human_labels(
    *,
    project_root: Path,
    review_run: Path,
    decision_path: Path,
    require_complete: bool,
    trigger_kind: str,
    run_id: str | None,
) -> dict[str, Any]:
    """Create an immutable snapshot from one validated human-label source.

    A content hash makes browser auto-save idempotent.  The pointer changes
    only after the complete snapshot and its backtest have been written and
    re-read; it remains unchanged on every failure.
    """

    root = project_root.expanduser().resolve()
    run = review_run.expanduser().resolve()
    if not run.is_dir():
        raise SnapshotRefreshError(f"review run does not exist: {run}")
    run_relpath = _relative_to_project(root, run)
    submission = _real_human_label_source(
        run,
        decision_path,
        require_complete=require_complete,
    )
    decision_relpath = _relative_to_project(root, submission["decision_path"])
    pointer_path = root / ACTIVE_POINTER_RELPATH
    previous_pointer = _validated_active_pointer(root)
    if (
        previous_pointer
        and previous_pointer.get("source_review_run_relpath") == run_relpath
        and previous_pointer.get("source_human_labels_relpath") == decision_relpath
        and previous_pointer.get("source_decision_content_sha256") == submission["decision_content_sha256"]
        and previous_pointer.get("source_human_labels_file_sha256") == submission["decision_file_sha256"]
    ):
        active = resolve_active_snapshot(root)
        assert active is not None
        return {
            "status": "UNCHANGED",
            "refresh_run": str(root / str(previous_pointer["active_refresh_run_relpath"])),
            "snapshot_manifest": str(active),
            "active_pointer": str(pointer_path),
            "decision_content_sha256": submission["decision_content_sha256"],
            "label_source_trust": previous_pointer.get("label_source_trust"),
        }

    return _create_snapshot_and_activate(
        root=root,
        run_relpath=run_relpath,
        submission=submission,
        trigger_kind=trigger_kind,
        run_id=run_id,
        source_files=[
            (
                "source_human_labels.json",
                Path(submission["decision_path"]),
                str(submission["decision_file_sha256"]),
            )
        ],
    )


def _withdrawal_submission(
    *,
    root: Path,
    run: Path,
    draft_path: Path,
    previous_live: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a named reviewer's explicit all-pending label withdrawal."""

    package_path = run / "review_bundle" / "review_package.json"
    draft = read_json(draft_path)
    package = read_json(package_path)
    reviewer = str(draft.get("reviewer") or "").strip()
    if not is_real_human_reviewer(reviewer):
        raise SnapshotRefreshError("label withdrawal requires the same named human reviewer")
    if reviewer != str(previous_live["reviewer"]):
        raise SnapshotRefreshError("label withdrawal reviewer does not match the existing live label source")
    if draft.get("package_id") != package.get("package_id"):
        raise SnapshotRefreshError("label withdrawal draft package_id does not match the review package")
    if draft.get("review_manifest_sha256") != package.get("review_manifest_sha256"):
        raise SnapshotRefreshError("label withdrawal draft manifest does not match the review package")
    candidates = {
        str(candidate.get("candidate_id")): candidate
        for candidate in package.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    rows = draft.get("decisions")
    if not isinstance(rows, list) or len(rows) != len(candidates):
        raise SnapshotRefreshError("label withdrawal draft must contain every review-package candidate")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SnapshotRefreshError("label withdrawal draft has an invalid row")
        candidate_id = str(row.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate_id in seen:
            raise SnapshotRefreshError(f"label withdrawal has duplicate or unknown candidate: {candidate_id}")
        seen.add(candidate_id)
        if row.get("candidate_semantic_sha256") != candidate.get("semantic_sha256"):
            raise SnapshotRefreshError(f"{candidate_id}: withdrawal draft semantic SHA does not match the review package")
        if str(row.get("decision") or "").lower() != "pending":
            raise SnapshotRefreshError("label withdrawal requires every decision to be explicitly pending")
        feedback = row.get("feedback", "")
        if not isinstance(feedback, str) or len(feedback) > 500:
            raise SnapshotRefreshError(f"{candidate_id}: feedback must be a string of at most 500 characters")
    draft_sha = sha256_file(draft_path)
    withdrawal_content = {
        "schema_version": "label-learning-withdrawal-content-v1",
        "package_id": str(draft.get("package_id") or ""),
        "review_manifest_sha256": str(draft.get("review_manifest_sha256") or ""),
        "reviewer": reviewer,
        "decisions": [],
        "withdrawn_live_decision_content_sha256": previous_live["decision_content_sha256"],
    }
    return {
        "reviewer": reviewer,
        "decision_count": 0,
        "accept": 0,
        "reject": 0,
        "decision_path": draft_path,
        "decision_file_sha256": draft_sha,
        "decision_content_sha256": canonical_json_sha256(withdrawal_content),
        "package_path": package_path,
        "require_complete": False,
        "draft_relpath": _relative_to_project(root, draft_path),
    }


def refresh_after_human_label_withdrawal(
    *,
    project_root: Path = PROJECT_ROOT,
    review_run: Path,
    draft_path: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Remove a reviewer's last live label only after a replacement snapshot exists.

    This covers the normal UI action of changing every previously selected
    ``accept``/``reject`` back to ``pending``.  The old live source is excluded
    while the replacement snapshot is built, preserved inside its immutable
    evidence run, then removed only immediately before atomic pointer swap.
    """

    root = project_root.expanduser().resolve()
    run = review_run.expanduser().resolve()
    if not run.is_dir():
        raise SnapshotRefreshError(f"review run does not exist: {run}")
    run_relpath = _relative_to_project(root, run)
    live_path = run / LIVE_HUMAN_DECISIONS_FILENAME
    if not live_path.is_file():
        return {
            "status": "SKIPPED_NO_EFFECTIVE_HUMAN_LABEL",
            "reason": "there is no existing live label source to withdraw",
        }
    previous_live = _real_human_label_source(run, live_path, require_complete=False)
    current_draft = (draft_path or run / "review_draft.json").expanduser().resolve()
    try:
        current_draft.relative_to(run)
    except ValueError as exc:
        raise SnapshotRefreshError("label withdrawal draft must stay inside its review run") from exc
    if not current_draft.is_file():
        raise SnapshotRefreshError("label withdrawal requires the just-saved review draft")
    submission = _withdrawal_submission(
        root=root,
        run=run,
        draft_path=current_draft,
        previous_live=previous_live,
    )
    _validated_active_pointer(root)
    previous_live_sha = str(previous_live["decision_file_sha256"])
    previous_live_content_sha = str(previous_live["decision_content_sha256"])

    def remove_live_source_with_rollback():
        source_bytes = live_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != previous_live_sha:
            raise SnapshotRefreshError("live label source changed before withdrawal activation; save again to retry")
        live_path.unlink()

        def rollback() -> None:
            if not live_path.exists():
                live_path.write_bytes(source_bytes)

        return rollback

    return _create_snapshot_and_activate(
        root=root,
        run_relpath=run_relpath,
        submission=submission,
        trigger_kind="human_label_withdrawal",
        run_id=run_id,
        source_files=[
            (
                "source_review_draft.json",
                current_draft,
                str(submission["decision_file_sha256"]),
            ),
            (
                "withdrawn_human_labels.before.json",
                live_path,
                previous_live_sha,
            ),
        ],
        excluded_decision_paths=(live_path,),
        trigger_extra={
            "trigger_source_kind": "review_draft_all_pending",
            "withdrawn_human_labels_relpath": _relative_to_project(root, live_path),
            "withdrawn_human_labels_file_sha256": previous_live_sha,
            "withdrawn_human_labels_decision_content_sha256": previous_live_content_sha,
        },
        source_effect="label_withdrawal",
        source_transition=remove_live_source_with_rollback,
    )


def refresh_after_human_label_save(
    *,
    project_root: Path = PROJECT_ROOT,
    review_run: Path,
    decision_path: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Refresh after one or more valid labels were auto-saved from the review UI."""

    run = review_run.expanduser().resolve()
    return _refresh_after_human_labels(
        project_root=project_root,
        review_run=run,
        decision_path=decision_path or run / LIVE_HUMAN_DECISIONS_FILENAME,
        require_complete=False,
        trigger_kind="human_label_save",
        run_id=run_id,
    )


def refresh_after_human_submit(
    *,
    project_root: Path = PROJECT_ROOT,
    review_run: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Revalidate the complete final review after incremental live refreshes."""

    run = review_run.expanduser().resolve()
    return _refresh_after_human_labels(
        project_root=project_root,
        review_run=run,
        decision_path=run / FINAL_HUMAN_DECISIONS_FILENAME,
        require_complete=True,
        trigger_kind="final_human_review_submit",
        run_id=run_id,
    )


def resolve_active_snapshot(project_root: Path = PROJECT_ROOT) -> Path | None:
    """Return the active manifest only when the pointer and SHA are valid."""

    root = project_root.expanduser().resolve()
    pointer_path = root / ACTIVE_POINTER_RELPATH
    if not pointer_path.is_file():
        return None
    pointer = read_json(pointer_path)
    if pointer.get("schema_version") != "active-label-learning-snapshot-pointer-v1":
        raise SnapshotRefreshError("active label-learning pointer has an unsupported schema")
    relpath = str(pointer.get("active_snapshot_manifest_relpath") or "")
    if not relpath:
        raise SnapshotRefreshError("active label-learning pointer is missing snapshot path")
    snapshot = (root / relpath).resolve()
    _relative_to_project(root, snapshot)
    if not snapshot.is_file():
        raise SnapshotRefreshError("active label-learning snapshot manifest is missing")
    expected = str(pointer.get("active_snapshot_manifest_sha256") or "")
    if not expected or sha256_file(snapshot) != expected:
        raise SnapshotRefreshError("active label-learning snapshot manifest SHA mismatch")
    learning_driver.load_snapshot(snapshot)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-run", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--run-id", help="optional deterministic ID for a new immutable refresh run")
    args = parser.parse_args(argv)
    result = refresh_after_human_submit(
        project_root=args.project_root,
        review_run=args.review_run,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
