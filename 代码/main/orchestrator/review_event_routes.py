#!/usr/bin/env python3
"""Attach read-only reviewed-event route metadata to a *new* review bundle.

This is deliberately a sidecar, not a rewrite of ``review_package.json``.
The review package is hash-bound evidence, while the sidecar records how each
candidate relates to prior human-reviewed audio events.  It never writes a
human decision, EDL, audio, candidate boundary, or auto-cut authorization.

The resulting ``review_bundle/event_routes.json`` is safe for the frontend to
fetch.  Its per-candidate ``route`` is one of:

* ``already_reviewed_exact``
* ``semantic_reuse_boundary_review``
* ``rejected_false_positive``
* ``rejected_execution_issue``
* ``new_event``

Every historical decision is explicitly a *reference*; it is never copied to
the current candidate as ``human_accept`` or ``human_reject``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import event_identity


SCHEMA_VERSION = "review-event-routes-v1"
ROUTE_LABELS = {
    "already_reviewed_exact": {
        "label": "已审核过（边界未变）",
        "reviewer_action": "可查看历史决定和备注；本轮仍需由真人明确确认。",
    },
    "semantic_reuse_boundary_review": {
        "label": "同一语义事件（需复听新剪口）",
        "reviewer_action": "旧语义判断仅供参考；边界已变，必须复听本轮原版/删后效果。",
    },
    "rejected_false_positive": {
        "label": "历史提示：疑似误报 / 应保留",
        "reviewer_action": "历史记录指向识别或语义误报；不要自动隐藏，真人确认后决定是否保留。",
    },
    "rejected_execution_issue": {
        "label": "历史提示：剪口执行需修复",
        "reviewer_action": "旧 reject 是边界/音量/剪辑痕迹问题，不等于语义上不该剪；请复听本轮剪口。",
    },
    "new_event": {
        "label": "新事件 / 无安全历史匹配",
        "reviewer_action": "没有可安全复用的历史事件；按本轮文本和试听独立审核。",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _run_identity(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run_identity.json"
    if path.is_file():
        return read_json(path)
    input_path = run_dir / "input_manifest.json"
    if input_path.is_file():
        document = read_json(input_path)
        return {
            "episode_id": document.get("episode_id"),
            "run_id": document.get("run_id") or run_dir.name,
        }
    raise FileNotFoundError(f"run identity/input manifest is missing: {run_dir}")


def _review_package_path(run_dir: Path) -> Path:
    path = run_dir / "review_bundle" / "review_package.json"
    if not path.is_file():
        raise FileNotFoundError(f"review package is missing: {path}")
    return path


def _decision_path(run_dir: Path) -> Path:
    path = run_dir / "human_decisions.json"
    if not path.is_file():
        raise FileNotFoundError(f"historical human decisions are missing: {path}")
    return path


def _candidate_ids(package: Mapping[str, Any]) -> list[str]:
    rows = package.get("candidates")
    if not isinstance(rows, list):
        raise ValueError("review package candidates must be an array")
    ids = [str(item.get("candidate_id", "")) for item in rows if isinstance(item, Mapping)]
    if not ids or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("review package candidate IDs must be present and unique")
    return ids


def _historical_events(history_runs: Iterable[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read validated itemized decisions only; preserve a deterministic case ID."""

    events: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen_runs: set[Path] = set()
    for raw_path in history_runs:
        run_dir = Path(raw_path).expanduser().resolve()
        if run_dir in seen_runs:
            continue
        seen_runs.add(run_dir)
        identity = _run_identity(run_dir)
        package_path = _review_package_path(run_dir)
        decision_path = _decision_path(run_dir)
        loaded = event_identity.load_run_events(run_dir, include_unreviewed=False)
        if not loaded:
            raise ValueError(f"historical run has no itemized human decisions: {run_dir}")
        source = {
            "run_dir": str(run_dir),
            "episode_id": identity.get("episode_id"),
            "run_id": identity.get("run_id") or run_dir.name,
            "input_manifest_sha256": sha256_file(run_dir / "input_manifest.json"),
            "review_package_sha256": sha256_file(package_path),
            "human_decisions_sha256": sha256_file(decision_path),
            "decision_count": len(loaded),
        }
        sources.append(source)
        for row in loaded:
            copied = dict(row)
            copied["matched_case_id"] = (
                f"{copied.get('episode_id') or source['episode_id']}::"
                f"{copied.get('run_id') or source['run_id']}::{copied.get('candidate_id')}"
            )
            copied["_event_route_source"] = source
            events.append(copied)
    return events, sources


def _current_events(run_dir: Path, package: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_id = {str(row.get("candidate_id")): row for row in event_identity.load_run_events(run_dir, include_unreviewed=True)}
    result: list[dict[str, Any]] = []
    for package_candidate in package.get("candidates") or []:
        if not isinstance(package_candidate, Mapping):
            continue
        candidate_id = str(package_candidate.get("candidate_id"))
        event = dict(by_id.get(candidate_id, {}))
        # The review package is the authoritative visible-candidate scope and
        # contains source_track_id and semantic context.  Do not add candidates
        # that are outside it.
        event.update(package_candidate)
        result.append(event)
    return result


def _case_id_for_match(match: event_identity.EventMatch, events: Iterable[Mapping[str, Any]]) -> str | None:
    if not match.historical:
        return None
    for event in events:
        identity = event_identity.canonical_event_identity(
            event,
            context=event.get("_identity_context") if isinstance(event.get("_identity_context"), Mapping) else None,
        )
        if (
            identity.run_id == match.historical.run_id
            and identity.candidate_id == match.historical.candidate_id
            and identity.event_key == match.historical.event_key
        ):
            return str(event.get("matched_case_id") or "") or None
    return None


def _route_row(
    match: event_identity.EventMatch,
    historical_events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    route = match.route.value
    display = ROUTE_LABELS[route]
    historical = match.historical
    return {
        "candidate_id": match.candidate.candidate_id,
        "event_key": match.candidate.event_key,
        "route": route,
        "route_label": display["label"],
        "reviewer_action": display["reviewer_action"],
        "category": match.category.value,
        "semantic_category": match.semantic_category.value,
        "reuse_class": match.reuse_class,
        "matched_case_id": _case_id_for_match(match, historical_events),
        "matched_historical_run_id": historical.run_id if historical else None,
        "matched_historical_candidate_id": historical.candidate_id if historical else None,
        "historical_decision_reference": match.historical_decision,
        "historical_feedback_reference": match.historical_feedback,
        "feedback_class": match.feedback_class.value,
        "semantic_decision_reference": match.semantic_decision,
        "boundary_review_required": match.boundary_review_required,
        "suppress_candidate": match.suppress_candidate,
        "boundary_reason": (
            "候选边界与历史边界的最大漂移超过 50ms；旧试听结论不能继承。"
            if match.boundary_review_required
            else "候选边界与历史边界一致或无额外边界复核要求。"
        ),
        "reasons": list(match.reasons),
        # Deliberately conspicuous: a historic decision is evidence only.  The
        # current run still starts with no human decision and no EDL action.
        "current_decision": None,
        "current_decision_authority": "NONE__HUMAN_REVIEW_REQUIRED",
        "creates_edl_action": False,
        "creates_autocut_permission": False,
    }


def build_event_routes(
    run_dir: str | Path,
    *,
    historical_runs: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Build sidecar metadata for one new review package; do not write it."""

    target = Path(run_dir).expanduser().resolve()
    identity = _run_identity(target)
    package_path = _review_package_path(target)
    package = read_json(package_path)
    candidate_ids = _candidate_ids(package)
    if package.get("run_id") not in (None, identity.get("run_id")):
        raise ValueError("review package run_id does not match target run")
    histories = [Path(value).expanduser().resolve() for value in historical_runs]
    if target in histories:
        raise ValueError("target run must not be used as its own historical source")
    historical_events, historical_sources = _historical_events(histories)
    route_rows: list[dict[str, Any]] = []
    for current in _current_events(target, package):
        match = event_identity.classify_candidate_against_history(current, historical_events)
        route_rows.append(_route_row(match, historical_events))
    routes = {str(row["candidate_id"]): row for row in route_rows}
    if set(routes) != set(candidate_ids):
        raise ValueError("event routes do not cover exactly the visible review candidates")
    summary: dict[str, int] = {}
    for row in route_rows:
        summary[row["route"]] = summary.get(row["route"], 0) + 1
    candidate_fingerprint = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "semantic_sha256": candidate.get("semantic_sha256"),
            "source_track_id": candidate.get("source_track_id"),
            "start_sample": candidate.get("start_sample"),
            "end_sample": candidate.get("end_sample"),
        }
        for candidate in package.get("candidates") or []
        if isinstance(candidate, Mapping)
    ]
    document = {
        "schema_version": SCHEMA_VERSION,
        "purpose": "frontend review metadata only; historical decisions never become current decisions",
        "episode_id": identity.get("episode_id"),
        "run_id": identity.get("run_id") or target.name,
        "run_identity_sha256": sha256_file(target / "run_identity.json") if (target / "run_identity.json").is_file() else None,
        "source_review_package_relpath": "review_bundle/review_package.json",
        "source_review_package_sha256": sha256_file(package_path),
        "source_review_manifest_sha256": package.get("review_manifest_sha256"),
        "candidate_fingerprint": candidate_fingerprint,
        "candidate_fingerprint_sha256": canonical_sha256(candidate_fingerprint),
        "historical_sources": historical_sources,
        "route_summary": summary,
        "routes": routes,
        "safety": {
            "mutates_review_package": False,
            "mutates_candidates": False,
            "mutates_historical_decisions": False,
            "creates_current_human_decision": False,
            "creates_edl": False,
            "creates_autocut_permission": False,
        },
    }
    validate_event_routes(document, package)
    return document


def validate_event_routes(document: Mapping[str, Any], package: Mapping[str, Any]) -> list[str]:
    """Validate a sidecar before a frontend consumes it."""

    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported event route schema")
    if document.get("source_review_manifest_sha256") != package.get("review_manifest_sha256"):
        errors.append("event routes are bound to a different review manifest")
    ids = set(_candidate_ids(package))
    routes = document.get("routes")
    if not isinstance(routes, Mapping) or set(map(str, routes)) != ids:
        errors.append("event routes do not cover exactly the package candidates")
        return errors
    for candidate_id, row in routes.items():
        if not isinstance(row, Mapping):
            errors.append(f"{candidate_id}: invalid route row")
            continue
        if row.get("candidate_id") != candidate_id:
            errors.append(f"{candidate_id}: candidate ID mismatch")
        if row.get("route") not in ROUTE_LABELS:
            errors.append(f"{candidate_id}: unknown route")
        if row.get("current_decision") is not None:
            errors.append(f"{candidate_id}: route metadata must not create a current decision")
        if row.get("current_decision_authority") != "NONE__HUMAN_REVIEW_REQUIRED":
            errors.append(f"{candidate_id}: current decision authority is invalid")
        if row.get("creates_edl_action") is not False or row.get("creates_autocut_permission") is not False:
            errors.append(f"{candidate_id}: route metadata must not authorize EDL/autocut")
    return errors


def write_event_routes(
    run_dir: str | Path,
    *,
    historical_runs: Iterable[str | Path] = (),
    out_path: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Write only the sidecar in the target bundle; never touch the package."""

    target = Path(run_dir).expanduser().resolve()
    # A saved reviewer draft is active work.  Do not even add a sidecar next
    # to it: future package generation must use a fresh bundle or the explicit
    # refresh path, which already refuses drafts and final decisions.
    if (target / "review_draft.json").exists():
        raise ValueError("refusing to enrich a review bundle with an active reviewer draft")
    if (target / "human_decisions.json").exists():
        raise ValueError("refusing to enrich a review bundle after human decisions exist")
    output = Path(out_path).expanduser().resolve() if out_path else target / "review_bundle" / "event_routes.json"
    bundle = (target / "review_bundle").resolve()
    try:
        output.relative_to(bundle)
    except ValueError as exc:
        raise ValueError("event route output must stay inside target review_bundle") from exc
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite event route sidecar: {output}")
    document = build_event_routes(target, historical_runs=historical_runs)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--history-run", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    path = write_event_routes(
        args.run_dir,
        historical_runs=args.history_run,
        out_path=args.out,
        overwrite=args.overwrite,
    )
    document = read_json(path)
    print(json.dumps({"status": "PASS", "out": str(path), "route_summary": document["route_summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
