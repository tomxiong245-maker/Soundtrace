#!/usr/bin/env python3
"""Build/reuse and serve one explicitly configured episode review package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from episode_config import EpisodeConfig, load_episode_config, package_identity_errors
from validate_mvp import approved_edl, validate_decisions, validate_package


BUILD_SCRIPT = Path(__file__).with_name("build_mvp_package.py")
PROJECT_ROOT = Path(__file__).resolve().parents[4]
ORCHESTRATOR_DIR = PROJECT_ROOT / "main" / "orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))
from refresh_label_learning_snapshot import (  # noqa: E402
    LIVE_HUMAN_DECISIONS_FILENAME,
    SnapshotRefreshError,
    decision_content_sha256,
    is_real_human_reviewer,
    refresh_after_human_label_save,
    refresh_after_human_label_withdrawal,
    refresh_after_human_submit,
)

WRITE_LOCK = threading.Lock()


def write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def build_or_reuse(config: EpisodeConfig) -> str:
    """Build once; on restart reuse only an exact, fully valid package."""
    package_path = config.review_package
    if package_path.is_file():
        package = json.loads(package_path.read_text(encoding="utf-8"))
        # The package was fully hash-validated when it was built.  Avoid
        # re-reading gigabytes of source WAV on every local server restart;
        # render_ntrack_edl.py performs the authoritative source-hash check
        # before any final output is written.
        errors = validate_package(package_path, verify_track_hashes=False) + package_identity_errors(config, package)
        if errors:
            raise RuntimeError(
                "existing review bundle does not match this config; use a new run_dir:\n- "
                + "\n- ".join(errors)
            )
        return "REUSED"

    if config.bundle_dir.exists() and any(config.bundle_dir.iterdir()):
        raise RuntimeError("review_bundle is partial/non-empty; use a new run_dir")

    config.run_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--source-package",
        str(config.source_package),
        "--previews-dir",
        str(config.previews_dir),
        "--tracks-manifest",
        str(config.tracks_manifest),
        "--frontend",
        str(config.frontend),
        "--out",
        str(config.bundle_dir),
        "--ffmpeg",
        str(config.ffmpeg),
    ]
    subprocess.run(command, check=True)
    errors = validate_package(package_path)
    package = json.loads(package_path.read_text(encoding="utf-8"))
    errors += package_identity_errors(config, package)
    if errors:
        raise RuntimeError("new review bundle failed validation:\n- " + "\n- ".join(errors))
    return "BUILT"


def feedback_count(decisions: dict) -> int:
    return sum(
        1
        for row in decisions.get("decisions") or []
        if isinstance(row, dict) and str(row.get("feedback", "")).strip()
    )


def live_human_label_document(decisions: dict) -> dict | None:
    """Extract only effective human labels from a reversible browser draft.

    ``review_draft.json`` can contain pending rows, typing noise and session
    timestamps.  This sidecar contains only labelled rows, so the learning
    snapshot builder never mistakes an unfinished draft for a final EDL source.
    """

    reviewer = str(decisions.get("reviewer") or "").strip()
    if not is_real_human_reviewer(reviewer):
        return None
    labelled: list[dict] = []
    for raw in decisions.get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        decision = str(raw.get("decision") or "").lower()
        if decision not in {"accept", "reject"}:
            continue
        labelled.append(
            {
                "candidate_id": raw.get("candidate_id"),
                "candidate_semantic_sha256": raw.get("candidate_semantic_sha256"),
                "decision": decision,
                "reviewer": reviewer,
                "decided_at": raw.get("decided_at"),
                "review_basis": raw.get("review_basis"),
                "listened_previews": raw.get("listened_previews") or {},
                "feedback": raw.get("feedback") or "",
            }
        )
    if not labelled:
        return None
    labelled.sort(key=lambda row: str(row.get("candidate_id") or ""))
    return {
        "schema_version": "human-decisions-mvp-v1",
        "package_id": decisions.get("package_id"),
        "review_manifest_sha256": decisions.get("review_manifest_sha256"),
        "reviewer": reviewer,
        "decisions": labelled,
        "live_label_source": True,
        "live_label_policy": "accept_reject_only; pending_rows_excluded; no_edl",
    }


def refresh_learning_after_label_save(target_run: Path, decisions: dict) -> dict:
    """Persist changed labels, then atomically activate fresh future-run evidence.

    Repeated auto-saves with only a new session timestamp do not rewrite the
    live source or create another immutable snapshot.  A failed refresh remains
    retryable because only the active pointer proves that a source is active.
    """

    live_document = live_human_label_document(decisions)
    if live_document is None:
        reviewer = str(decisions.get("reviewer") or "").strip()
        rows = decisions.get("decisions") or []
        all_rows_pending = bool(rows) and all(
            isinstance(row, dict) and str(row.get("decision") or "").lower() == "pending"
            for row in rows
        )
        if is_real_human_reviewer(reviewer) and all_rows_pending:
            return refresh_after_human_label_withdrawal(
                project_root=PROJECT_ROOT,
                review_run=target_run,
                draft_path=target_run / "review_draft.json",
            )
        return {
            "status": "SKIPPED_NO_EFFECTIVE_HUMAN_LABEL",
            "reason": "a real named reviewer and at least one accept/reject label are required",
        }
    live_path = target_run / LIVE_HUMAN_DECISIONS_FILENAME
    content_sha = decision_content_sha256(live_document)
    current_content_sha = None
    if live_path.is_file():
        try:
            current = json.loads(live_path.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                current_content_sha = decision_content_sha256(current)
        except (OSError, json.JSONDecodeError):
            current_content_sha = None
    if current_content_sha != content_sha:
        write_json_atomic(live_path, live_document)
    return refresh_after_human_label_save(
        project_root=PROJECT_ROOT,
        review_run=target_run,
        decision_path=live_path,
    )


def persist_draft_and_refresh_learning(
    target_run: Path,
    decisions: dict,
    metrics: object,
) -> dict:
    """Persist the browser draft and refresh future-run evidence when warranted.

    This is deliberately separate from HTTP handling so the exact `/api/save`
    transaction is testable without opening a network listener.  Callers must
    validate the draft and hold ``WRITE_LOCK`` before entering it.
    """

    write_json_atomic(target_run / "review_draft.json", decisions)
    write_json_atomic(target_run / "review_session_metrics.draft.json", metrics)
    try:
        learning_refresh = refresh_learning_after_label_save(target_run, decisions)
        write_json_atomic(
            target_run / "learning_snapshot_refresh.live.json",
            {
                "schema_version": "review-save-learning-refresh-v1",
                "status": learning_refresh.get("status"),
                "reviewer": str(decisions.get("reviewer") or "").strip(),
                "feedback_count": feedback_count(decisions),
                "result": learning_refresh,
            },
        )
        return learning_refresh
    except SnapshotRefreshError as exc:
        failure = {
            "schema_version": "review-save-learning-refresh-v1",
            "status": "FAILED",
            "reviewer": str(decisions.get("reviewer") or "").strip(),
            "draft_saved": True,
            "error": str(exc),
            "retry_rule": "the next save with the same valid label content retries because the active pointer was not updated",
        }
        write_json_atomic(target_run / "learning_snapshot_refresh.live.json", failure)
        raise


def refresh_learning_after_submit(target_run: Path, reviewer: str) -> dict:
    """Revalidate a full final review after its incremental live refreshes."""

    if reviewer.startswith("AUTOMATED_"):
        return {"status": "SKIPPED_AUTOMATED_TEST"}
    return refresh_after_human_submit(project_root=PROJECT_ROOT, review_run=target_run)


def validate_draft(package: dict, document: dict) -> list[str]:
    """Validate a resumable browser draft without treating it as a human decision.

    A draft may contain pending rows and an empty reviewer field.  It still has
    to be bound to exactly this package so a stale tab cannot write notes into a
    different episode/run.
    """

    errors: list[str] = []
    if document.get("schema_version") != "human-decisions-mvp-v1":
        errors.append("wrong decision schema")
    if document.get("package_id") != package.get("package_id"):
        errors.append("package mismatch")
    if document.get("review_manifest_sha256") != package.get("review_manifest_sha256"):
        errors.append("manifest mismatch")
    rows = document.get("decisions")
    candidates = {str(item.get("candidate_id")): item for item in package.get("candidates") or []}
    if not isinstance(rows, list) or len(rows) != len(candidates):
        return errors + ["draft must contain exactly one row per candidate"]
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("invalid draft row")
            continue
        candidate_id = str(row.get("candidate_id", ""))
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate_id in seen:
            errors.append(f"duplicate or unknown candidate: {candidate_id}")
            continue
        seen.add(candidate_id)
        if row.get("candidate_semantic_sha256") != candidate.get("semantic_sha256"):
            errors.append(f"{candidate_id}: candidate hash mismatch")
        if row.get("decision") not in {"pending", "accept", "reject"}:
            errors.append(f"{candidate_id}: invalid draft decision")
        feedback = row.get("feedback", "")
        if not isinstance(feedback, str) or len(feedback) > 500:
            errors.append(f"{candidate_id}: feedback must be a string of at most 500 characters")
    return errors


def make_handler(config: EpisodeConfig):
    class EpisodeReviewHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(config.bundle_dir), **kwargs)

        def do_POST(self):
            if self.path not in {"/api/save", "/api/submit"}:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 5 * 1024 * 1024:
                    self._json(400, {"ok": False, "errors": ["invalid request size"]})
                    return
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                package = json.loads(config.review_package.read_text(encoding="utf-8"))
                identity_errors = package_identity_errors(config, package)
                if identity_errors:
                    self._json(409, {"ok": False, "errors": identity_errors})
                    return
                decisions = payload.get("decisions")
                metrics = payload.get("metrics")
                if self.path == "/api/save":
                    errors = validate_draft(package, decisions or {})
                    if errors:
                        self._json(400, {"ok": False, "errors": errors})
                        return
                    with WRITE_LOCK:
                        try:
                            learning_refresh = persist_draft_and_refresh_learning(
                                config.run_dir,
                                decisions or {},
                                metrics,
                            )
                        except SnapshotRefreshError as exc:
                            failure = {
                                "schema_version": "review-save-learning-refresh-v1",
                                "status": "FAILED",
                                "reviewer": str((decisions or {}).get("reviewer") or "").strip(),
                                "draft_saved": True,
                                "error": str(exc),
                                "retry_rule": "the next save with the same valid label content retries because the active pointer was not updated",
                            }
                            self._json(
                                503,
                                {
                                    "ok": False,
                                    "draft_saved": True,
                                    "learning_snapshot": failure,
                                    "errors": ["draft was saved, but automatic learning snapshot refresh failed"],
                                },
                            )
                            return
                    self._json(
                        200,
                        {
                            "ok": True,
                            "draft_path": str(config.run_dir / "review_draft.json"),
                            "candidate_count": len((decisions or {}).get("decisions") or []),
                            "feedback_count": feedback_count(decisions or {}),
                            "learning_snapshot": learning_refresh,
                        },
                    )
                    return
                errors = validate_decisions(package, decisions or {})
                if errors:
                    self._json(400, {"ok": False, "errors": errors})
                    return
                reviewer = str((decisions or {}).get("reviewer", ""))
                target_run = (
                    config.run_dir / "e2e"
                    if reviewer.startswith("AUTOMATED_")
                    else config.run_dir
                )
                target_run.mkdir(parents=True, exist_ok=True)
                with WRITE_LOCK:
                    write_json_atomic(target_run / "human_decisions.json", decisions)
                    write_json_atomic(target_run / "review_session_metrics.json", metrics)
                    edl = approved_edl(package, decisions)
                    write_json_atomic(target_run / "approved.edl.draft.json", edl)
                    try:
                        learning_refresh = refresh_learning_after_submit(target_run, reviewer)
                        write_json_atomic(
                            target_run / "learning_snapshot_refresh.json",
                            {
                                "schema_version": "review-submit-learning-refresh-v1",
                                "status": learning_refresh.get("status"),
                                "reviewer": reviewer,
                                "human_decisions_sha256": hashlib.sha256(
                                    (target_run / "human_decisions.json").read_bytes()
                                ).hexdigest(),
                                "result": learning_refresh,
                            },
                        )
                    except SnapshotRefreshError as exc:
                        failure = {
                            "schema_version": "review-submit-learning-refresh-v1",
                            "status": "FAILED",
                            "reviewer": reviewer,
                            "decision_saved": True,
                            "error": str(exc),
                        }
                        write_json_atomic(target_run / "learning_snapshot_refresh.json", failure)
                        self._json(
                            503,
                            {
                                "ok": False,
                                "decision_saved": True,
                                "learning_snapshot": failure,
                                "errors": ["human decisions were saved, but automatic learning snapshot refresh failed"],
                            },
                        )
                        return
                self._json(
                    200,
                    {
                        "ok": True,
                        "episode_id": config.episode_id,
                        "automated_test": reviewer.startswith("AUTOMATED_"),
                        "decisions_path": str(target_run / "human_decisions.json"),
                        "edl_path": str(target_run / "approved.edl.draft.json"),
                        "feedback_count": feedback_count(decisions or {}),
                        "learning_snapshot": learning_refresh,
                    },
                )
            except Exception as exc:
                self._json(500, {"ok": False, "errors": [str(exc)]})

        def _json(self, status: int, value: dict) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return EpisodeReviewHandler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", type=int, help="Override the config port")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()

    config = load_episode_config(args.config)
    port = args.port if args.port is not None else config.port
    if not 1 <= port <= 65535:
        parser.error("port must be within 1..65535")
    status = build_or_reuse(config)
    print(
        json.dumps(
            {
                "status": status,
                "episode_id": config.episode_id,
                "bundle": str(config.bundle_dir),
                "run_dir": str(config.run_dir),
            },
            ensure_ascii=False,
        )
    )
    if args.build_only:
        return 0

    url = f"http://127.0.0.1:{port}/index.html"
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(config))
    print(f"P1 episode review ready: {url}")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
