#!/usr/bin/env python3
"""Check that the human-facing current-state MD agrees with live run artifacts.

This is deliberately small and local.  It prevents a repeat of the situation
where a page, a run, and the Markdown hand-off tell different stories about the
active ASR, review UI, or version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUS_MD = PROJECT_ROOT / "统筹全局/当前项目进度.md"
MEMORY_MD = PROJECT_ROOT / "统筹全局/全局统筹记忆.md"
FLOW_MD = PROJECT_ROOT / "统筹全局/Agent交付流程-从音频到成片.md"
F05_MD = PROJECT_ROOT / "统筹全局/功能说明/F05-人工审核与训练标签.md"
F06_MD = PROJECT_ROOT / "统筹全局/功能说明/F06-EDL渲染混音与QC.md"
F07_MD = PROJECT_ROOT / "统筹全局/功能说明/F07-统筹Agent与Tool注册表.md"
F08_MD = PROJECT_ROOT / "统筹全局/功能说明/F08-外部知识与监督学习.md"
F10_MD = PROJECT_ROOT / "统筹全局/功能说明/F10-基准、能力目录与Skill路线.md"
FRONTEND = PROJECT_ROOT / "审核前端/challenger-review-product-v1/mvp.html"
REVIEW_SERVER = PROJECT_ROOT / "稳定生产/challengers/review-product-v1/scripts/server_episode.py"
LABEL_REFRESHER = PROJECT_ROOT / "main/orchestrator/refresh_label_learning_snapshot.py"
MUSIC_CONFIG = PROJECT_ROOT / "main/orchestrator/music_templates.json"
REQUIRED_REVIEW_UI_MARKERS = (
    "data-feedback",
    "/api/save",
    "semantic_context",
    'id="run"',
    'id="scope"',
    "review_scope",
    "不代表风险",
)
MARKER = re.compile(
    r"<!-- CURRENT_DELIVERY_FACTS:start -->\s*```json\s*(\{.*?\})\s*```\s*<!-- CURRENT_DELIVERY_FACTS:end -->",
    re.DOTALL,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def review_ui_capability_errors(content: str, *, label: str) -> list[str]:
    """Return missing core review capabilities for either UI generation."""

    return [
        f"{label} is missing required review capability: {needle}"
        for needle in REQUIRED_REVIEW_UI_MARKERS
        if needle not in content
    ]


def review_draft_errors(draft: dict, package: dict) -> list[str]:
    """Validate a saved, still-unsubmitted reviewer draft against its package.

    An empty reviewer and all-pending decisions are deliberately valid: opening
    the review page creates a protected draft before the reviewer has made a
    decision.  The draft must nevertheless be bound to the same package,
    manifest and visible candidate set, so a stale browser draft cannot be
    mistaken for current review work.
    """

    errors: list[str] = []
    if draft.get("package_id") != package.get("package_id"):
        errors.append("review draft package_id disagrees with frozen review package")
    if draft.get("review_manifest_sha256") != package.get("review_manifest_sha256"):
        errors.append("review draft review_manifest_sha256 disagrees with frozen review package")
    rows = draft.get("decisions")
    package_rows = package.get("candidates")
    if not isinstance(rows, list) or not isinstance(package_rows, list):
        return errors + ["review draft or frozen review package has no candidate array"]
    by_id = {
        str(row.get("candidate_id")): row
        for row in package_rows
        if isinstance(row, dict) and row.get("candidate_id") is not None
    }
    draft_ids = [
        str(row.get("candidate_id"))
        for row in rows
        if isinstance(row, dict) and row.get("candidate_id") is not None
    ]
    if len(draft_ids) != len(rows) or len(set(draft_ids)) != len(draft_ids) or set(draft_ids) != set(by_id):
        return errors + ["review draft candidate set disagrees with frozen review package"]
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id"))
        expected = by_id[candidate_id].get("semantic_sha256")
        actual = row.get("candidate_semantic_sha256")
        if expected is not None and actual != expected:
            errors.append(f"review draft {candidate_id} semantic SHA disagrees with frozen review package")
    return errors


def facts_from_status() -> dict:
    content = STATUS_MD.read_text(encoding="utf-8")
    match = MARKER.search(content)
    if not match:
        raise ValueError("当前项目进度.md is missing CURRENT_DELIVERY_FACTS block")
    facts = json.loads(match.group(1))
    if facts.get("schema_version") != "current-delivery-facts-v1":
        raise ValueError("CURRENT_DELIVERY_FACTS has wrong schema_version")
    return facts


def check() -> list[str]:
    errors: list[str] = []
    try:
        facts = facts_from_status()
    except Exception as exc:
        return [str(exc)]

    review = facts.get("current_review") or {}
    review_relpath = review.get("run_relpath")
    run_id = review.get("run_id")
    if not review_relpath or not run_id:
        return ["CURRENT_DELIVERY_FACTS.current_review is incomplete"]
    run_dir = PROJECT_ROOT / str(review_relpath)
    for required in ("run_identity.json", "state.json", "analysis_reuse_manifest.json", "all_candidates.json", "review_bundle/review_package.json"):
        if not (run_dir / required).is_file():
            errors.append(f"current review missing {required}")
    if errors:
        return errors

    identity = read_json(run_dir / "run_identity.json")
    state = read_json(run_dir / "state.json")
    reuse = read_json(run_dir / "analysis_reuse_manifest.json")
    all_candidates = read_json(run_dir / "all_candidates.json")
    package = read_json(run_dir / "review_bundle/review_package.json")
    plan = read_json(run_dir / "plan.json")
    if identity.get("run_id") != run_id:
        errors.append("current review run_id disagrees with run_identity.json")
    if state.get("state") != review.get("state"):
        errors.append("current review state disagrees with state.json")
    if reuse.get("asr", {}).get("engine") != facts.get("asr", {}).get("engine"):
        errors.append("current ASR engine disagrees with analysis_reuse_manifest.json")
    if reuse.get("reused_from_run") != facts.get("asr", {}).get("reused_from_run"):
        errors.append("current ASR reuse source disagrees with analysis_reuse_manifest.json")
    all_candidate_count = len(all_candidates.get("candidates") or [])
    if all_candidate_count != review.get("candidate_count"):
        errors.append("current review candidate count disagrees with all_candidates")
    package_candidate_count = len(package.get("candidates") or [])
    expected_package_count = review.get("review_package_candidate_count", review.get("candidate_count"))
    if package_candidate_count != expected_package_count:
        errors.append("current review review-package candidate count disagrees with review package")
    frontend_visible_count = review.get("frontend_visible_candidate_count")
    if frontend_visible_count is not None and frontend_visible_count != package_candidate_count:
        errors.append("current review frontend-visible count disagrees with review package")
    # A review package freezes a copy of the UI used by its reviewer.  Future
    # frontend improvements must not retroactively invalidate an in-progress
    # package (especially one with a saved draft).  Validate the run-local
    # frozen copy here; the canonical frontend is checked below for required
    # capabilities and will be bound only when a *new* package is built.
    frozen_frontend = run_dir / "review_bundle" / "index.html"
    if not frozen_frontend.is_file():
        errors.append("review package frozen frontend is missing")
    elif package.get("ui_sha256") != sha256_file(frozen_frontend):
        errors.append("review package UI SHA does not match its frozen frontend copy")
    else:
        frozen_frontend_text = frozen_frontend.read_text(encoding="utf-8")
        errors.extend(review_ui_capability_errors(frozen_frontend_text, label="frozen review frontend"))
    draft_path = run_dir / "review_draft.json"
    if draft_path.is_file():
        errors.extend(review_draft_errors(read_json(draft_path), package))
    if package.get("run_id") != run_id:
        errors.append("review package run_id disagrees with current review run")
    review_scope = package.get("review_scope")
    if not isinstance(review_scope, dict) or not review_scope.get("available"):
        errors.append("review package is missing frozen risk-routing metadata")
    else:
        selected = [
            row for row in all_candidates.get("candidates") or []
            if isinstance(row, dict) and row.get("selected_for_calibration")
        ]
        expected_high = {
            str(row.get("candidate_id")) for row in selected
            if row.get("risk_level") == "high"
        }
        expected_low = {
            str(row.get("candidate_id")) for row in selected
            if row.get("risk_level") == "low"
        }
        actual_high = {str(value) for value in review_scope.get("high_risk_candidate_ids") or []}
        actual_low = {str(value) for value in review_scope.get("low_risk_candidate_ids") or []}
        if actual_high != expected_high or actual_low != expected_low:
            errors.append("review package risk-routing metadata disagrees with frozen all_candidates")
        if review_scope.get("high_risk_count") != len(actual_high) or review_scope.get("low_risk_count") != len(actual_low):
            errors.append("review package risk-routing counts are invalid")
    if package.get("review_manifest_sha256") != review.get("review_manifest_sha256"):
        errors.append("current review manifest SHA disagrees with current facts")
    if review.get("package_revision_required") and not (run_dir / "review_package_revision.json").is_file():
        errors.append("current facts require a review-package revision record, but none exists")

    music_facts = facts.get("music") or {}
    expected_music = {
        "template_id": "reference-linear-v1",
        "voice_start_seconds": 5.0,
        "intro_music_only_end_seconds": 5.0,
        "intro_fade_out_start_seconds": 5.0,
        "intro_fade_out_end_seconds": 16.0,
        "outro_fade_in_lead_seconds": 22.0,
        "outro_music_tail_seconds": 37.976,
    }
    for key, expected in expected_music.items():
        if music_facts.get(key) != expected:
            errors.append(f"current music hard fact {key} must be {expected!r}")
    plan_music = plan.get("music") or {}
    if plan_music.get("music_template_id") != expected_music["template_id"]:
        errors.append("current review plan is not locked to reference-linear-v1")
    timing = plan_music.get("timing")
    checkpoint_relpath = plan.get("requirements_checkpoint_relpath") or "requirements_checkpoint.json"
    checkpoint_path = run_dir / str(checkpoint_relpath)
    if not checkpoint_path.is_file():
        errors.append(f"current review is missing requirements checkpoint: {checkpoint_relpath}")
    else:
        try:
            music_document = read_json(MUSIC_CONFIG)
            canonical_timing = (music_document.get("templates") or {}).get(expected_music["template_id"])
        except Exception as exc:
            canonical_timing = None
            errors.append(f"music template definitions cannot be read: {exc}")
        checkpoint = read_json(checkpoint_path)
        checkpoint_music = checkpoint.get("music") or {}
        checkpoint_timing = checkpoint_music.get("timing")
        if checkpoint_music.get("template_id") != expected_music["template_id"]:
            errors.append("requirements checkpoint music template is not reference-linear-v1")
        if not isinstance(checkpoint_timing, dict):
            errors.append("requirements checkpoint has no music timing object")
        else:
            if not isinstance(canonical_timing, dict) or checkpoint_timing != canonical_timing:
                errors.append("requirements checkpoint does not match canonical music template definitions")
            if checkpoint_music.get("timing_sha256") != canonical_sha(checkpoint_timing):
                errors.append("requirements checkpoint music timing SHA is invalid")
            for key, expected in expected_music.items():
                if key == "template_id":
                    continue
                if checkpoint_timing.get(key) != expected:
                    errors.append(f"requirements checkpoint music hard fact {key} disagrees")
            if isinstance(timing, dict) and timing != checkpoint_timing:
                errors.append("plan music timing disagrees with requirements checkpoint")
    facts_checkpoint_sha = (music_facts.get("requirements_checkpoint_sha256") or "")
    if checkpoint_path.is_file() and facts_checkpoint_sha != sha256_file(checkpoint_path):
        errors.append("current facts requirements_checkpoint_sha256 disagrees with the run checkpoint")

    benchmark_facts = facts.get("development_benchmark") or {}
    expected_benchmark = {
        "contract": "editing-e2e-v1",
        "status": "ACTIVE_DEVELOPMENT_EVIDENCE_ONLY",
        "scorecard_status": "INCOMPLETE_HUMAN_REVIEW_REQUIRED",
    }
    for key, expected in expected_benchmark.items():
        if benchmark_facts.get(key) != expected:
            errors.append(f"current development benchmark fact {key} must be {expected!r}")
    scorecard_relpath = benchmark_facts.get("scorecard_relpath")
    if not isinstance(scorecard_relpath, str) or not scorecard_relpath:
        errors.append("current development benchmark scorecard_relpath is missing")
    else:
        scorecard_md = PROJECT_ROOT / scorecard_relpath
        scorecard_json = scorecard_md.with_name("scorecard.json")
        if not scorecard_md.is_file() or not scorecard_json.is_file():
            errors.append("current development benchmark scorecard artifacts are missing")
        else:
            scorecard = read_json(scorecard_json)
            if (scorecard.get("run") or {}).get("run_id") != run_id:
                errors.append("current development benchmark scorecard run_id disagrees with current review")
            if (scorecard.get("scorecard_status") or {}).get("status") != benchmark_facts.get("scorecard_status"):
                errors.append("current development benchmark scorecard status disagrees with current facts")
    if checkpoint_path.is_file():
        checkpoint_document = read_json(checkpoint_path)
        checkpoint_benchmark = checkpoint_document.get("development_benchmark") or {}
        if checkpoint_benchmark.get("status") != "ACTIVE_EVIDENCE_LOOP_NOT_AUTOCUT_POLICY":
            errors.append("requirements checkpoint lacks the active benchmark evidence-loop boundary")
        if checkpoint_benchmark.get("missing_evidence_rule") != "NOT_MEASURED is not zero problems, a pass, or permission to reduce human review further":
            errors.append("requirements checkpoint benchmark missing-evidence rule disagrees")
        live_refresh = (checkpoint_document.get("experience_learning") or {}).get("live_label_refresh") or {}
        if live_refresh.get("live_source_filename") != "human_decisions_and_feedback.live.json":
            errors.append("requirements checkpoint lacks the live human-label source contract")
        if live_refresh.get("future_run_effect_only") is not True:
            errors.append("requirements checkpoint does not limit live-label refresh to future runs")
        if live_refresh.get("never_writes_edl_or_audio") is not True:
            errors.append("requirements checkpoint live-label refresh must not write EDL or audio")

    live_facts = ((facts.get("learning_loop") or {}).get("label_learning_driver") or {}).get("live_label_refresh") or {}
    if live_facts.get("status") != "IMPLEMENTED__CONTRACT_TESTED__AWAITING_NEXT_REAL_HUMAN_SAVE":
        errors.append("current facts do not declare the tested live-label refresh state")

    best = facts.get("best_local_delivery") or {}
    best_dir = PROJECT_ROOT / str(best.get("run_relpath", ""))
    if not (best_dir / "state.json").is_file():
        errors.append("best local delivery state.json is missing")
    elif read_json(best_dir / "state.json").get("state") != best.get("state"):
        errors.append("best local delivery state disagrees with state.json")

    # Do not force every long policy document to repeat the current run ID.
    # That duplication caused context bloat and made every new run rewrite four
    # unrelated documents.  Each document only needs a stable pointer to the
    # canonical facts/summary; the run manifest remains the source of truth.
    for path in (MEMORY_MD, FLOW_MD, F05_MD, F07_MD):
        content = path.read_text(encoding="utf-8")
        if "CURRENT_DELIVERY_FACTS" not in content and "当前状态摘要" not in content:
            errors.append(f"{path.relative_to(PROJECT_ROOT)} lacks a canonical current-state pointer")
    for path in (MEMORY_MD, FLOW_MD, F06_MD):
        content = path.read_text(encoding="utf-8")
        for needle in ("reference-linear-v1", "5.000"):
            if needle not in content:
                errors.append(f"{path.relative_to(PROJECT_ROOT)} does not mention music hard fact: {needle}")
    for path in (MEMORY_MD, FLOW_MD, F10_MD):
        content = path.read_text(encoding="utf-8")
        for needle in ("editing-e2e-v1", "NOT_MEASURED"):
            if needle not in content:
                errors.append(f"{path.relative_to(PROJECT_ROOT)} does not mention benchmark hard fact: {needle}")
    frontend_text = FRONTEND.read_text(encoding="utf-8")
    errors.extend(review_ui_capability_errors(frontend_text, label="canonical frontend"))
    for path in (MEMORY_MD, F05_MD, F08_MD):
        content = path.read_text(encoding="utf-8")
        for needle in ("/api/save", "human_decisions_and_feedback.live.json", "LABEL-LEARNING-AUTO"):
            if needle not in content:
                errors.append(f"{path.relative_to(PROJECT_ROOT)} lacks live-label refresh fact: {needle}")
    server_text = REVIEW_SERVER.read_text(encoding="utf-8")
    for needle in (
        "persist_draft_and_refresh_learning",
        "refresh_learning_after_label_save",
        "LIVE_HUMAN_DECISIONS_FILENAME",
    ):
        if needle not in server_text:
            errors.append(f"review server lacks live-label refresh implementation marker: {needle}")
    refresher_text = LABEL_REFRESHER.read_text(encoding="utf-8")
    for needle in ("refresh_after_human_label_save", "decision_content_sha256", "source_human_labels.json"):
        if needle not in refresher_text:
            errors.append(f"label refresher lacks immutable live-label marker: {needle}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate MD/run/frontend consistency")
    args = parser.parse_args()
    if not args.check:
        parser.error("pass --check")
    errors = check()
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
