#!/usr/bin/env python3
"""Render one explicitly authorized, machine-assisted EP04 audition draft.

This is deliberately *not* a normal delivery path.  The normal orchestrator
requires fresh itemized human decisions before it writes either EDL.  This
helper exists only when the project owner explicitly authorizes a bounded
local audition that may reuse named historical EP04 cases despite incomplete
source-identity metadata.

It never writes ``human_decisions.json`` or ``human_approved.edl.json`` and
never changes an autocut policy.  Its only media output is
``machine_assisted_draft`` and every action carries the local authorization
and its historic evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import delivery_orchestrator as delivery  # noqa: E402
import transition_qc  # noqa: E402


SCHEMA_VERSION = "user-authorized-machine-draft-v1"


def fail(message: str) -> None:
    raise delivery.DeliveryError(message)


def read_json_object(path: Path) -> dict[str, Any]:
    document = delivery.read_json(path)
    if not isinstance(document, dict):
        fail(f"JSON object required: {path}")
    return document


def relpath(run_dir: Path, path: Path) -> str:
    return delivery.relative_to_run(run_dir, path)


def require_current_safe_boundary(candidate: dict[str, Any], candidate_id: str) -> None:
    artifact = candidate.get("artifact_risk") or {}
    if artifact.get("verdict") != "OK":
        fail(
            f"{candidate_id} is not safe on its current snapped boundary "
            f"(artifact verdict={artifact.get('verdict')!r}); it must stay out of this draft"
        )


def historic_boundary(
    *, run_dir: Path, spec: dict[str, Any], candidate_id: str
) -> tuple[int, int, dict[str, Any]]:
    source_relpath = spec.get("historical_candidate_source_relpath")
    source_id = spec.get("historical_candidate_id")
    expected_sha = spec.get("historical_candidate_source_sha256")
    if not all(isinstance(value, str) and value for value in (source_relpath, source_id, expected_sha)):
        fail(f"{candidate_id} historic-boundary specification is incomplete")
    source = (PROJECT_ROOT / source_relpath).resolve()
    if not source.is_file() or delivery.sha256_file(source) != expected_sha:
        fail(f"{candidate_id} historic candidate source is missing or changed")
    document = read_json_object(source)
    historical = next(
        (
            row
            for row in document.get("candidates") or []
            if isinstance(row, dict) and row.get("candidate_id") == source_id
        ),
        None,
    )
    if not historical:
        fail(f"{candidate_id} historic candidate {source_id!r} is absent")
    artifact = historical.get("artifact_risk") or {}
    if artifact.get("verdict") != "OK":
        fail(f"{candidate_id} historic boundary is not backed by an OK artifact check")
    start = int(historical["start_sample"])
    end = int(historical["end_sample"])
    if start >= end:
        fail(f"{candidate_id} historic boundary is invalid")
    return start, end, {
        "boundary_mode": "historical_human_accepted_boundary",
        "historical_candidate_source_relpath": source_relpath,
        "historical_candidate_source_sha256": expected_sha,
        "historical_candidate_id": source_id,
        "historical_artifact_verdict": artifact.get("verdict"),
    }


def build_actions(
    *, run_dir: Path, authorization: dict[str, Any], authorization_sha: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    all_candidates = read_json_object(run_dir / "all_candidates.json")
    candidates = {
        str(row.get("candidate_id")): row
        for row in all_candidates.get("candidates") or []
        if isinstance(row, dict) and row.get("candidate_id")
    }
    selections = authorization.get("selections")
    if not isinstance(selections, list) or not selections:
        fail("authorization has no selections")

    actions: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for spec in selections:
        if not isinstance(spec, dict):
            fail("authorization selection must be an object")
        candidate_id = str(spec.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        if not candidate:
            fail(f"authorization refers to unknown candidate {candidate_id!r}")
        if candidate_id in selected_ids:
            fail(f"authorization selects {candidate_id} more than once")
        selected_ids.add(candidate_id)
        decision = spec.get("decision")
        reason = str(spec.get("reason") or "").strip()
        evidence = spec.get("evidence")
        if decision not in {"machine_proposed_accept", "machine_proposed_reject", "human_review_required"}:
            fail(f"{candidate_id} has an unsupported machine decision")
        if not reason or not isinstance(evidence, list) or not evidence:
            fail(f"{candidate_id} needs a reason and one or more historic evidence records")
        row: dict[str, Any] = {
            "candidate_id": candidate_id,
            "candidate_sha256": candidate.get("candidate_sha256") or delivery.sha256_bytes(candidate),
            "decision": decision,
            "decision_provenance": "user_authorized_same_episode_machine_draft",
            "risk_level": candidate.get("risk_level"),
            "reason": reason,
            "evidence": evidence,
            "authorization_relpath": "machine_draft_authorization.json",
            "authorization_sha256": authorization_sha,
        }
        if decision != "machine_proposed_accept":
            prediction_rows.append(row)
            continue
        if candidate.get("risk_level") != "low":
            fail(f"{candidate_id} is not low risk and cannot enter this machine-only draft")
        boundary_mode = spec.get("boundary_mode", "current_snapped_boundary")
        if boundary_mode == "current_snapped_boundary":
            require_current_safe_boundary(candidate, candidate_id)
            start, end = int(candidate["start_sample"]), int(candidate["end_sample"])
            boundary_provenance = {
                "boundary_mode": boundary_mode,
                "artifact_risk": candidate.get("artifact_risk"),
            }
        elif boundary_mode == "historical_human_accepted_boundary":
            start, end, boundary_provenance = historic_boundary(
                run_dir=run_dir, spec=spec, candidate_id=candidate_id
            )
        else:
            fail(f"{candidate_id} has unsupported boundary mode {boundary_mode!r}")
        row.update(
            {
                "start_sample": start,
                "end_sample": end,
                "boundary_provenance": boundary_provenance,
            }
        )
        prediction_rows.append(row)
        actions.append(
            {
                "action_id": f"machine-cut-{candidate_id}",
                "action_type": "global_sync_cut",
                "candidate_id": candidate_id,
                "candidate_sha256": row["candidate_sha256"],
                "start_sample": start,
                "end_sample": end,
                "applies_to_all_tracks": True,
                "decision": "machine_proposed_accept",
                "decision_provenance": "user_authorized_same_episode_machine_draft",
                "risk_level": "low",
                "authorization_relpath": "machine_draft_authorization.json",
                "authorization_sha256": authorization_sha,
                "historical_evidence": evidence,
                "boundary_provenance": boundary_provenance,
                "policy_status": "NOT_APPROVED__RUN_LOCAL_USER_AUTHORIZED_AUDITION_ONLY",
            }
        )

    # Anything not explicitly selected remains visible in the report as
    # preserve/review-required.  This prevents a small action list from being
    # misread as full episode coverage.
    already_reported = {row["candidate_id"] for row in prediction_rows}
    for candidate_id, candidate in sorted(candidates.items()):
        if candidate_id in already_reported:
            continue
        prediction_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_sha256": candidate.get("candidate_sha256") or delivery.sha256_bytes(candidate),
                "decision": "human_review_required",
                "decision_provenance": "not_selected_by_run_local_authorization",
                "risk_level": candidate.get("risk_level"),
                "reason": "not covered by the narrow user-authorized machine-draft policy",
                "authorization_relpath": "machine_draft_authorization.json",
                "authorization_sha256": authorization_sha,
            }
        )
    return actions, sorted(prediction_rows, key=lambda item: item["candidate_id"]), list(candidates.values())


def write_machine_qc(run_dir: Path, ffmpeg: str, render: dict[str, Any], transition: dict[str, Any]) -> dict[str, Any]:
    output = render.get("outputs") or {}
    master_wav = run_dir / str(output.get("master_wav"))
    master_mp3 = run_dir / str(output.get("master_mp3"))
    if not master_wav.is_file() or not master_mp3.is_file():
        fail("machine draft renderer did not produce both master files")
    if delivery.sha256_file(master_wav) != output.get("master_wav_sha256"):
        fail("machine draft WAV hash does not match its render manifest")
    if delivery.sha256_file(master_mp3) != output.get("master_mp3_sha256"):
        fail("machine draft MP3 hash does not match its render manifest")
    report = {
        "schema_version": "machine-draft-qc-v1",
        "episode_id": delivery.require_identity(run_dir)["episode_id"],
        "run_id": delivery.require_identity(run_dir)["run_id"],
        "run_identity_sha256": delivery.sha256_file(run_dir / "run_identity.json"),
        "variant": "machine_assisted_draft",
        "automatic_qc": "PASS",
        "scope": "single machine-assisted audition variant; it is not normal dual-variant delivery QC",
        "master_wav": {
            "relpath": str(output["master_wav"]),
            "sha256": output["master_wav_sha256"],
            "probe": delivery.audio_probe(master_wav),
            "loudness": delivery.loudnorm_measure(master_wav, ffmpeg),
        },
        "master_mp3": {
            "relpath": str(output["master_mp3"]),
            "sha256": output["master_mp3_sha256"],
            "probe": delivery.audio_probe(master_mp3),
        },
        "transition_qc": {
            "relpath": relpath(run_dir, run_dir / "render_machine_assisted_draft/transition_qc.json"),
            "sha256": delivery.sha256_file(run_dir / "render_machine_assisted_draft/transition_qc.json"),
            "transition_count": transition.get("transition_count"),
            "priority_relisten_count": transition.get("priority_relisten_count"),
            "status": transition.get("status"),
        },
        "manual_next_gate": "project owner listens to the complete machine-assisted draft; any issue returns to candidate/boundary selection",
    }
    delivery.write_json(run_dir / "machine_draft_qc.json", report)
    return report


def write_report(run_dir: Path, actions: list[dict[str, Any]], qc: dict[str, Any]) -> None:
    identity = delivery.require_identity(run_dir)
    action_lines = [
        f"- `{action['candidate_id']}`: {action['start_sample']}–{action['end_sample']} samples; "
        f"{action['decision_provenance']}."
        for action in actions
    ]
    text = "\n".join(
        [
            f"# {identity['episode_id']} 本次用户授权机器试听草稿",
            "",
            f"- Run：`{identity['run_id']}`",
            "- 状态：`MACHINE_ASSISTED_DRAFT_RENDERED`",
            "- 这不是 `human_approved`，没有写入真人决定、自动剪辑政策或 Champion。",
            "- 本次例外仅允许使用指定历史 EP04 人审证据，即使旧来源身份记录不完整；不得外推到其它节目。",
            f"- 机器剪口：{len(actions)} 条。",
            "",
            "## 已渲染剪口",
            "",
            *(action_lines or ["- 无；授权选择均未通过当前安全条件，因此未渲染。"]),
            "",
            "## 输出",
            "",
            "- `render_machine_assisted_draft/`：三轨同步剪切后的 stems、speech mix、WAV、MP3。",
            "- `machine_assisted_draft.edl.json`：所有动作和历史依据。",
            "- `machine_draft_prediction_manifest.json`：包括未采用候选及理由。",
            "- `machine_draft_qc.json`：单变体自动 QC 与重点复听排序。",
            "",
            "## 下一道门",
            "",
            "项目负责人整片试听；通过只能说明本地机器试听草稿可继续迭代，不能改称人审版或发布版。",
        ]
    )
    delivery.write_text(run_dir / "MACHINE_DRAFT_REPORT.md", text + "\n")


def validate_authorization(*, identity: dict[str, Any], authorization: dict[str, Any]) -> None:
    """Validate the narrow owner authorization shared by fresh and recovery paths."""

    if authorization.get("schema_version") != SCHEMA_VERSION:
        fail("machine-draft authorization schema mismatch")
    if authorization.get("scope") != "EP04_RUN_LOCAL_MACHINE_ASSISTED_AUDITION_ONLY":
        fail("authorization scope is not the narrow EP04 local-audition scope")
    if authorization.get("episode_id") != identity["episode_id"]:
        fail("authorization episode does not match the target run")
    if authorization.get("prohibited") != [
        "human_decision",
        "human_approved_edl",
        "autocut_policy_change",
        "champion_change",
        "external_publish",
    ]:
        fail("authorization prohibited-actions contract is incomplete")


def resume_existing_edl(
    *, run_dir: Path, identity: dict[str, Any], authorization: dict[str, Any], ffmpeg: str
) -> dict[str, Any]:
    """Render a valid pre-existing machine EDL after an interrupted media run.

    An earlier invocation may have safely frozen an EDL and transitioned the run
    to ``CALIBRATED`` before disk cleanup removed its partially-rendered media.
    This recovery path deliberately does *not* rebuild the EDL, rewrite the
    authorization, or alter any machine decision.  It only permits rendering
    when the submitted authorization is byte-for-byte identical to the one
    already bound into that EDL.
    """

    delivery.require_state(run_dir, "CALIBRATED")
    edl_path = run_dir / "machine_assisted_draft.edl.json"
    run_authorization_path = run_dir / "machine_draft_authorization.json"
    prediction_path = run_dir / "machine_draft_prediction_manifest.json"
    render_dir = run_dir / "render_machine_assisted_draft"
    if not edl_path.is_file() or not run_authorization_path.is_file() or not prediction_path.is_file():
        fail("CALIBRATED recovery requires existing EDL, authorization and prediction records")
    if render_dir.exists():
        fail("partial machine-draft render directory exists; refusing to overwrite it")
    stored_authorization = read_json_object(run_authorization_path)
    if stored_authorization != authorization:
        fail("provided authorization differs from the authorization frozen with this EDL")
    authorization_sha = delivery.sha256_file(run_authorization_path)
    edl = read_json_object(edl_path)
    if (
        edl.get("variant") != "machine_assisted_draft"
        or edl.get("episode_id") != identity["episode_id"]
        or edl.get("run_id") != identity["run_id"]
        or edl.get("run_identity_sha256") != delivery.sha256_file(run_dir / "run_identity.json")
        or (edl.get("authorization") or {}).get("sha256") != authorization_sha
        or edl.get("status") != "MACHINE_ASSISTED_DRAFT__NOT_HUMAN_APPROVED"
    ):
        fail("existing machine EDL is not a valid recovery target")
    prediction = read_json_object(prediction_path)
    if (
        prediction.get("episode_id") != identity["episode_id"]
        or prediction.get("run_id") != identity["run_id"]
        or prediction.get("run_identity_sha256") != delivery.sha256_file(run_dir / "run_identity.json")
        or prediction.get("authorization_sha256") != authorization_sha
        or prediction.get("candidate_source_sha256") != delivery.sha256_file(run_dir / "all_candidates.json")
    ):
        fail("existing machine prediction record is not a valid recovery target")

    actions = edl.get("global_sync_actions")
    if not isinstance(actions, list):
        fail("existing machine EDL has invalid global actions")
    render = delivery.render_one_variant(run_dir, "machine_assisted_draft", ffmpeg)
    delivery.transition(
        run_dir,
        "MACHINE_ASSISTED_DRAFT_RENDERED",
        "recovered machine-only audition render from existing EDL after media restoration",
    )
    transition = transition_qc.generate_transition_qc(run_dir, "machine_assisted_draft")
    qc = write_machine_qc(run_dir, ffmpeg, render, transition)
    write_report(run_dir, actions, qc)
    return {
        "status": "MACHINE_ASSISTED_DRAFT_RENDERED__NOT_HUMAN_APPROVED",
        "run_dir": str(run_dir),
        "machine_cut_count": len(actions),
        "master_mp3": str(run_dir / render["outputs"]["master_mp3"]),
        "master_wav": str(run_dir / render["outputs"]["master_wav"]),
        "qc": qc.get("automatic_qc"),
        "recovery_mode": "existing_edl_only",
    }


def run(run_dir: Path, policy_path: Path, ffmpeg: str) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    policy_path = policy_path.expanduser().resolve()
    identity = delivery.require_identity(run_dir)
    if not policy_path.is_file():
        fail("machine-draft authorization policy is missing")
    authorization = read_json_object(policy_path)
    validate_authorization(identity=identity, authorization=authorization)
    current_state = delivery.read_json(run_dir / "state.json").get("state")
    if current_state == "CALIBRATED":
        return resume_existing_edl(
            run_dir=run_dir, identity=identity, authorization=authorization, ffmpeg=ffmpeg
        )
    delivery.require_state(run_dir, "CALIBRATION_REVIEW_REQUIRED")
    if (run_dir / "machine_assisted_draft.edl.json").exists() or (run_dir / "render_machine_assisted_draft").exists():
        fail("machine-draft output already exists; refusing to overwrite it")
    run_authorization = run_dir / "machine_draft_authorization.json"
    delivery.write_json(run_authorization, authorization)
    authorization_sha = delivery.sha256_file(run_authorization)
    actions, prediction_rows, candidates = build_actions(
        run_dir=run_dir, authorization=authorization, authorization_sha=authorization_sha
    )
    edl = delivery.edl_document(
        run_dir=run_dir,
        variant="machine_assisted_draft",
        actions=actions,
        decision_summary={"human_accept": 0, "machine_proposed_accept": len(actions)},
    )
    edl["authorization"] = {
        "relpath": "machine_draft_authorization.json",
        "sha256": authorization_sha,
        "scope": authorization["scope"],
    }
    edl["status"] = "MACHINE_ASSISTED_DRAFT__NOT_HUMAN_APPROVED"
    delivery.write_json(run_dir / "machine_assisted_draft.edl.json", edl)
    prediction = {
        "schema_version": "machine-draft-prediction-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": delivery.sha256_file(run_dir / "run_identity.json"),
        "authorization_relpath": "machine_draft_authorization.json",
        "authorization_sha256": authorization_sha,
        "candidate_source_relpath": "all_candidates.json",
        "candidate_source_sha256": delivery.sha256_file(run_dir / "all_candidates.json"),
        "candidate_count": len(candidates),
        "machine_cut_count": len(actions),
        "machine_preserve_count": sum(row["decision"] == "machine_proposed_reject" for row in prediction_rows),
        "human_review_required_count": sum(row["decision"] == "human_review_required" for row in prediction_rows),
        "policy": {
            "autocut_policy": "NOT_APPROVED",
            "scope": "user-authorized same-episode EP04 audition only",
            "never_creates_human_decision": True,
            "never_creates_human_approved_edl": True,
            "never_changes_champion": True,
        },
        "predictions": prediction_rows,
    }
    delivery.write_json(run_dir / "machine_draft_prediction_manifest.json", prediction)
    delivery.transition(run_dir, "CALIBRATED", "run-local user authorization created machine-only EDL; no human decision exists")
    render = delivery.render_one_variant(run_dir, "machine_assisted_draft", ffmpeg)
    delivery.transition(run_dir, "MACHINE_ASSISTED_DRAFT_RENDERED", "user-authorized machine-only audition render completed")
    transition = transition_qc.generate_transition_qc(run_dir, "machine_assisted_draft")
    qc = write_machine_qc(run_dir, ffmpeg, render, transition)
    write_report(run_dir, actions, qc)
    return {
        "status": "MACHINE_ASSISTED_DRAFT_RENDERED__NOT_HUMAN_APPROVED",
        "run_dir": str(run_dir),
        "machine_cut_count": len(actions),
        "master_mp3": str(run_dir / render["outputs"]["master_mp3"]),
        "master_wav": str(run_dir / render["outputs"]["master_wav"]),
        "qc": qc.get("automatic_qc"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--ffmpeg")
    args = parser.parse_args()
    try:
        result = run(args.run_dir, args.authorization, delivery.resolve_ffmpeg(args.ffmpeg))
    except delivery.DeliveryError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
