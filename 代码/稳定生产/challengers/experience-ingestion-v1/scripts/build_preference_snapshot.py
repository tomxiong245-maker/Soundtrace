#!/usr/bin/env python3
"""Build an immutable, provenance-preserving preference snapshot.

This is the offline Challenger half of the label-learning loop:

    human decisions + feedback -> validated records -> preference snapshot

It deliberately does not train a model, edit audio, create an EDL, or turn a
historical vote into a human decision.  The discovery mode finds future
``human_decisions*.json`` files instead of relying on a hard-coded list.  Old
or incomplete review schemas are retained with explicit warnings so a useful
Mentor feedback signal is not silently lost, while the snapshot still marks
them as ineligible for model training or automatic approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ORCHESTRATOR_DIR = PROJECT_ROOT / "main" / "orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from event_identity import TRADITIONAL_TO_SIMPLIFIED, normalize_event_text

from build_policy_cards import build_policy_cards, write_policy_outputs
from classify_feedback import classify_record


DECISION_RE = re.compile(r"^human_decisions(?:_and_feedback.*)?\.json$")
SKIP_NAMES = {
    "human_decisions.raw_review_ui.json",
    "review_decisions.template.json",
    "review_draft.json",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def display_text(value: Any) -> str:
    """A reading layer only; it never replaces raw ASR in the source package."""
    return unicodedata.normalize("NFKC", str(value or "")).translate(TRADITIONAL_TO_SIMPLIFIED)


def normalized_run_key(value: Any) -> str:
    """Make source-run identifiers comparable across discovery and case stores.

    Discovery records use a basename such as ``EP04-review-product-v2`` while
    the canonical store keeps an audit-relative path such as
    ``main/runs/EP04-review-product-v2``.  Those are the same historical
    decision source and must not be counted twice as independent evidence.
    """

    text = str(value or "").strip().replace("\\", "/").rstrip("/")
    return Path(text).name if text else "canonical"


def record_dedupe_key(run_id: Any, candidate_id: Any) -> tuple[str, str]:
    """Stable identity for one historical itemized decision source."""

    return normalized_run_key(run_id), str(candidate_id or "")


def source_track_audio_identity(
    run_dir: Path,
    track_id: Any,
    package: dict[str, Any] | None = None,
) -> tuple[str | None, int | None, str | None]:
    """Return an immutable source-track identity without guessing.

    Newer delivery runs expose it through ``input_manifest.json``.  Several
    early, otherwise valid human-review packages predate that manifest but do
    contain the same per-track SHA in ``review_package.json``.  Reading that
    packaged evidence is safe; inventing an SHA from a filename or episode is
    not.  The final value is the canonical physical track ID when the package
    can map an old alias (for example ``male``) to ``track_02``.
    """

    requested = str(track_id or "")
    manifest_path = run_dir / "input_manifest.json"
    if not manifest_path.is_file():
        manifest = {}
    else:
        try:
            manifest = read_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            manifest = {}

    def find_track(rows: Any) -> tuple[str | None, int | None, str | None]:
        if not isinstance(rows, list):
            return None, None, None
        for track in rows:
            if not isinstance(track, dict):
                continue
            canonical_track = str(track.get("track_id") or "") or None
            aliases = {
                str(value)
                for value in (track.get("track_id"), track.get("source_key"), track.get("label"))
                if value not in (None, "")
            }
            if requested and requested in aliases:
                return track.get("audio_sha256"), track.get("sample_rate_hz"), canonical_track
        return None, None, None

    audio_sha, sample_rate_hz, canonical_track = find_track(manifest.get("tracks"))
    if audio_sha:
        return audio_sha, sample_rate_hz or manifest.get("sample_rate_hz"), canonical_track
    package = package or {}
    audio_sha, sample_rate_hz, canonical_track = find_track(package.get("tracks"))
    if audio_sha:
        return audio_sha, sample_rate_hz or package.get("sample_rate_hz"), canonical_track
    return None, manifest.get("sample_rate_hz") or package.get("sample_rate_hz"), canonical_track


def episode_id_from_record_context(run_dir: Path, package: dict[str, Any], identity: dict[str, Any]) -> str:
    """Find the evidenced episode ID without treating the parent ``runs`` as one."""

    for value in (identity.get("episode_id"), package.get("episode_id"), run_dir.name):
        text = str(value or "").strip()
        if re.fullmatch(r"EP\d+[A-Za-z0-9_-]*", text, flags=re.IGNORECASE):
            return text.upper()
    return "UNKNOWN_EPISODE"


def normalize_decision(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"accept", "human_accept"}:
        return "accept"
    if text in {"reject", "human_reject"}:
        return "reject"
    if text == "adjust":
        return "adjust"
    return "unknown"


def candidate_text(candidate: dict[str, Any]) -> str:
    for key in ("proposed_delete_text", "filler_token", "proposed_text", "candidate_text", "text"):
        if candidate.get(key):
            return str(candidate[key])
    tracks = candidate.get("text_tracks") or {}
    source = candidate.get("source_track_id")
    words = (tracks.get(source) or {}).get("words") if source else None
    if words:
        start = float(candidate.get("start_seconds") or 0)
        end = float(candidate.get("end_seconds") or 0)
        return "".join(str(w.get("text") or "") for w in words
                       if float(w.get("start_seconds") or 0) < end
                       and float(w.get("end_seconds") or 0) > start)
    return ""


def candidate_index(package: dict[str, Any], run_dir: Path) -> dict[str, dict[str, Any]]:
    index = {str(c.get("candidate_id")): c for c in package.get("candidates") or []
             if c.get("candidate_id")}
    # Some legacy packages contain the reviewed candidates only in the source
    # file.  Joining it is still safe because we record both source hashes.
    source_path = run_dir / "candidates" / "candidate_source.json"
    if source_path.is_file():
        try:
            for c in (read_json(source_path).get("candidates") or []):
                cid = str(c.get("candidate_id") or "")
                if cid and cid not in index:
                    index[cid] = c
        except (OSError, json.JSONDecodeError):
            pass
    return index


def find_package(run_dir: Path) -> Path | None:
    for rel in (
        "review_bundle/review_package.json",
        "review_bundle-final/review_package.json",
    ):
        path = run_dir / rel
        if path.is_file():
            return path
    return None


def normalize_decision_document(raw: Any) -> dict[str, Any]:
    """Unwrap both direct decision docs and /api/save wrapper docs."""
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("decisions")
    if isinstance(nested, dict):
        doc = dict(nested)
        for key in ("_saved_at", "_saved_by_endpoint"):
            if key in raw:
                doc[key] = raw[key]
        return doc
    return raw


def discover_decision_files(repo_root: Path) -> list[Path]:
    found: list[Path] = []
    runs_root = repo_root / "main" / "runs"
    if not runs_root.is_dir():
        return found
    for path in runs_root.rglob("*.json"):
        if path.name in SKIP_NAMES or not DECISION_RE.match(path.name):
            continue
        if "review_bundle" in path.parts or "review_bundle_revisions" in path.parts:
            continue
        # The old EP03 root file is a bulk authorization, not itemized review.
        if path.parent == runs_root / "EP03":
            continue
        found.append(path)
    return sorted(found)


def _quality_warnings(decision_doc: dict[str, Any], package: dict[str, Any],
                     candidate: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not decision_doc.get("package_id") and not package.get("package_id"):
        warnings.append("missing_package_id")
    if not decision_doc.get("review_manifest_sha256") and not package.get("review_manifest_sha256"):
        warnings.append("missing_review_manifest_sha256")
    if not decision.get("candidate_semantic_sha256") and not candidate.get("semantic_sha256"):
        warnings.append("missing_candidate_semantic_sha256")
    if not decision.get("reviewer") and not decision_doc.get("reviewer"):
        warnings.append("missing_reviewer")
    if not (decision.get("decided_at") or decision.get("reviewed_at") or decision_doc.get("reviewed_at")):
        warnings.append("missing_decided_at")
    return warnings


def build_record(*, repo_root: Path, decision_path: Path, package_path: Path,
                 decision_doc: dict[str, Any], package: dict[str, Any],
                 candidate: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    run_dir = decision_path.parent
    identity_path = run_dir / "run_identity.json"
    source_path = run_dir / "candidates" / "candidate_source.json"
    identity = read_json(identity_path) if identity_path.is_file() else {}
    run_id = str(identity.get("run_id") or run_dir.name)
    episode_id = episode_id_from_record_context(run_dir, package, identity)
    cid = str(decision.get("candidate_id"))
    normalized = normalize_decision(decision.get("decision"))
    reviewer = str(decision.get("reviewer") or decision_doc.get("reviewer")
                   or decision_doc.get("reviewer_name") or "").strip()
    decided_at = decision.get("decided_at") or decision.get("reviewed_at") or decision_doc.get("reviewed_at") or ""
    package_id = package.get("package_id") or decision_doc.get("package_id") or f"legacy::{run_id}"
    manifest_sha = decision_doc.get("review_manifest_sha256") or package.get("review_manifest_sha256") or ""
    semantic_sha = decision.get("candidate_semantic_sha256") or candidate.get("semantic_sha256") or ""
    warnings = _quality_warnings(decision_doc, package, candidate, decision)
    decision_sha = sha256_file(decision_path)
    package_sha = sha256_file(package_path)
    identity_sha = sha256_file(identity_path) if identity_path.is_file() else ""
    source_sha = sha256_file(source_path) if source_path.is_file() else ""
    feedback = str(decision.get("feedback") or "").strip()
    if len(feedback) > 500:
        warnings.append("feedback_over_500_chars")
        feedback = feedback[:500]
    reason_key = str(candidate.get("reason_key") or candidate.get("candidate_kind") or "unknown")
    text = candidate_text(candidate)
    source_track_alias = candidate.get("source_track_id")
    source_audio_sha256, sample_rate_hz, canonical_track_id = source_track_audio_identity(
        run_dir, source_track_alias, package
    )
    source_track_id = canonical_track_id or source_track_alias
    source_bundle_rows = []
    input_manifest_path = run_dir / "input_manifest.json"
    if input_manifest_path.is_file():
        try:
            source_bundle_rows = read_json(input_manifest_path).get("tracks") or []
        except (OSError, json.JSONDecodeError):
            source_bundle_rows = []
    if not source_bundle_rows:
        source_bundle_rows = package.get("tracks") or []
    bundle_members = sorted(
        {
            (str(track.get("track_id") or track.get("source_key") or ""), str(track.get("audio_sha256") or ""))
            for track in source_bundle_rows
            if isinstance(track, dict) and track.get("audio_sha256")
        }
    )
    source_bundle_sha256 = canonical_sha(bundle_members) if bundle_members else None
    identity_complete = bool(
        episode_id != "UNKNOWN_EPISODE"
        and source_bundle_sha256
        and source_audio_sha256
        and source_track_id
        and candidate.get("start_sample") is not None
        and candidate.get("end_sample") is not None
        and sample_rate_hz
    )
    # A record can guide deterministic rule analysis when the itemized label,
    # candidate, reviewer and timestamp exist.  It can never authorize model
    # training or an automatic cut in this snapshot.
    rule_eligible = normalized in {"accept", "reject"} and bool(cid and reviewer and decided_at)
    return {
        "schema_version": "preference-record-v2",
        "case_id": f"{run_id}::{cid}::{decision_sha[:12]}",
        "episode_id": episode_id,
        "run_id": run_id,
        "candidate_id": cid,
        "candidate": {
            "reason_key": reason_key,
            "candidate_kind": candidate.get("candidate_kind") or candidate.get("candidate_family"),
            "source_track_id": source_track_id,
            "source_track_alias": source_track_alias,
            "start_sample": candidate.get("start_sample"),
            "end_sample": candidate.get("end_sample"),
            "start_seconds": candidate.get("start_seconds"),
            "end_seconds": candidate.get("end_seconds"),
            "raw_text": text,
            "match_text": normalize_event_text(text),
            "display_text": display_text(text),
            "proposed_text": text,
            "source_word_ids": candidate.get("source_word_ids") or [],
            "sample_rate_hz": sample_rate_hz,
            "clause_position": candidate.get("clause_position"),
            "confidence_tier": candidate.get("confidence_tier"),
            "filler_subtype": candidate.get("filler_subtype"),
            "repetition_signature": candidate.get("repetition_signature"),
            "duration_seconds": candidate.get("duration_seconds"),
            "lexical_context": candidate.get("lexical_context") or candidate.get("review_display"),
        },
        "label": {
            "decision": normalized,
            "reviewer": reviewer,
            "decided_at": decided_at,
            "review_basis": decision.get("review_basis") or "text_only",
            "feedback": feedback,
        },
        "quality": {
            "rule_analysis_eligible": rule_eligible,
            "generalization_eligible": rule_eligible and identity_complete,
            "identity_status": "COMPLETE" if identity_complete else "LEGACY_IDENTITY_INCOMPLETE",
            "model_training_eligible": False,
            "legacy_schema": not bool(decision.get("candidate_semantic_sha256")),
            "warnings": sorted(set(warnings)),
        },
        "provenance": {
            "decision_file_relpath": str(decision_path.relative_to(repo_root)),
            "decision_file_sha256": decision_sha,
            "package_file_relpath": str(package_path.relative_to(repo_root)),
            "package_file_sha256": package_sha,
            "run_identity_relpath": str(identity_path.relative_to(repo_root)) if identity_path.is_file() else None,
            "run_identity_sha256": identity_sha,
            "candidate_source_relpath": str(source_path.relative_to(repo_root)) if source_path.is_file() else None,
            "candidate_source_sha256": source_sha,
            "package_id": package_id,
            "review_manifest_sha256": manifest_sha,
            "candidate_semantic_sha256": semantic_sha,
            "source_audio_sha256": source_audio_sha256,
            "source_bundle_sha256": source_bundle_sha256,
        },
    }


def build_snapshot(
    repo_root: Path,
    out_dir: Path,
    canonical_store: Path | None = None,
    *,
    excluded_decision_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    """Build one immutable snapshot, optionally omitting mutable live sources.

    ``excluded_decision_paths`` is only for a reviewed label withdrawal: the
    old live sidecar remains on disk until the replacement snapshot has passed
    its checks, but must not be counted in that replacement snapshot.
    """
    if out_dir.exists():
        raise FileExistsError(f"refusing to overwrite immutable snapshot: {out_dir}")
    excluded = {path.expanduser().resolve() for path in excluded_decision_paths}
    records: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for decision_path in discover_decision_files(repo_root):
        if decision_path.resolve() in excluded:
            continue
        package_path = find_package(decision_path.parent)
        if package_path is None:
            quarantined.append({"source": str(decision_path.relative_to(repo_root)), "reason": "missing_review_package"})
            continue
        try:
            decision_doc = normalize_decision_document(read_json(decision_path))
            package = read_json(package_path)
        except (OSError, json.JSONDecodeError) as exc:
            quarantined.append({"source": str(decision_path.relative_to(repo_root)), "reason": f"invalid_json:{exc}"})
            continue
        candidates = candidate_index(package, decision_path.parent)
        file_sha = sha256_file(decision_path)
        source_meta = {
            "decision_file": str(decision_path.relative_to(repo_root)),
            "decision_file_sha256": file_sha,
            "package_file": str(package_path.relative_to(repo_root)),
            "package_file_sha256": sha256_file(package_path),
            "run_id": decision_path.parent.name,
        }
        sources.append(source_meta)
        rows = decision_doc.get("decisions") or decision_doc.get("candidates") or []
        if not isinstance(rows, list):
            quarantined.append({**source_meta, "reason": "decisions_not_list"})
            continue
        for decision in rows:
            cid = str(decision.get("candidate_id") or "")
            normalized = normalize_decision(decision.get("decision") or decision.get("user_decision"))
            if not cid or normalized not in {"accept", "reject"}:
                quarantined.append({**source_meta, "candidate_id": cid, "reason": "not_itemized_accept_reject"})
                continue
            candidate = candidates.get(cid)
            if candidate is None:
                quarantined.append({**source_meta, "candidate_id": cid, "reason": "unknown_candidate"})
                continue
            record = build_record(repo_root=repo_root, decision_path=decision_path,
                                  package_path=package_path, decision_doc=decision_doc,
                                  package=package, candidate=candidate, decision=decision)
            dedupe_key = record_dedupe_key(record["run_id"], cid)
            if dedupe_key in seen:
                quarantined.append({**source_meta, "candidate_id": cid, "reason": "duplicate_run_candidate"})
                continue
            seen.add(dedupe_key)
            records.append(record)

    # Include the previously verified canonical case store as a stable input,
    # but never duplicate a case already discovered from its source run.
    if canonical_store and canonical_store.is_dir():
        for path in sorted((canonical_store / "cases").glob("*.jsonl")):
            source_sha = sha256_file(path)
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    old = json.loads(line)
                except json.JSONDecodeError:
                    continue
                run_id = normalized_run_key((old.get("provenance") or {}).get("source_run_dir"))
                cid = str(old.get("candidate_id") or "")
                key = record_dedupe_key(run_id, cid)
                if key in seen:
                    continue
                label = old.get("label") or {}
                records.append({
                    "schema_version": "preference-record-v2",
                    "case_id": old.get("case_id") or f"canonical::{cid}::{source_sha[:12]}",
                    "episode_id": old.get("episode_id"),
                    "run_id": run_id,
                    "candidate_id": cid,
                    "candidate": {
                        "reason_key": (old.get("candidate") or {}).get("reason_key") or "unknown",
                        "candidate_kind": None,
                        "source_track_id": (old.get("candidate") or {}).get("source_track_id"),
                        "start_sample": (old.get("candidate") or {}).get("start_sample"),
                        "end_sample": (old.get("candidate") or {}).get("end_sample"),
                        "start_seconds": (old.get("candidate") or {}).get("start_seconds"),
                        "end_seconds": (old.get("candidate") or {}).get("end_seconds"),
                        "proposed_text": (old.get("candidate") or {}).get("deleted_text") or "",
                        "clause_position": None,
                        "confidence_tier": None,
                    },
                    "label": {
                        "decision": label.get("decision"),
                        "reviewer": label.get("reviewer") or "",
                        "decided_at": label.get("decided_at") or "",
                        "review_basis": label.get("review_basis") or "text_only",
                        "feedback": str(label.get("feedback") or "")[:500],
                    },
                    "quality": {
                        "rule_analysis_eligible": label.get("decision") in {"accept", "reject"},
                        "model_training_eligible": False,
                        "legacy_schema": True,
                        "warnings": ["imported_from_verified_canonical_case_store"],
                    },
                    "provenance": {
                        "canonical_case_file": str(path.relative_to(repo_root)),
                        "canonical_case_file_sha256": source_sha,
                        "original_case_provenance": old.get("provenance"),
                    },
                })
                seen.add(key)

    records.sort(key=lambda r: (str(r.get("run_id")), str(r.get("candidate_id")), str(r.get("case_id"))))
    # Feedback classes are derived immutable evidence.  Run-context event
    # identity is produced separately by main/orchestrator/event_identity.py,
    # because it must fail closed when input audio SHA is absent here.
    records = [classify_record(record) for record in records]
    snapshot_id = out_dir.name
    counts = Counter(str((r.get("label") or {}).get("decision")) for r in records)
    reason_counts = Counter(str((r.get("candidate") or {}).get("reason_key")) for r in records)
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "aggregated.json").write_text(json.dumps({
        "schema_version": "preference-aggregate-v2",
        "snapshot_id": snapshot_id,
        "total_records": len(records),
        "decisions": dict(counts),
        "learning_loop": {
            "event_identity_schema": "event-identity-v1 (run-context only; not inferred from snapshots)",
            "feedback_classification_schema": "feedback-classification-v1",
            "policy_status": "challenger_only",
            "never_creates_human_decision": True,
        },
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    classifications = out_dir / "feedback_classifications.jsonl"
    classifications.write_text(
        "".join(json.dumps({
            "case_id": record.get("case_id"),
            "event_identity": None,
            "feedback_classification": record.get("feedback_classification"),
            "source_case_sha256": canonical_sha(record),
        }, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    policy_cards = build_policy_cards(records, snapshot_id=snapshot_id)
    write_policy_outputs(policy_cards, out_dir, snapshot_id=snapshot_id)

    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        c = record.get("candidate") or {}
        key = (str(c.get("reason_key") or "unknown"), norm_text(c.get("proposed_text")), str(c.get("clause_position") or ""))
        by_key[key].append(record)
    rules: list[dict[str, Any]] = []
    for (reason, text, clause), rows in sorted(by_key.items()):
        accepts = sum(1 for r in rows if (r.get("label") or {}).get("decision") == "accept")
        rejects = sum(1 for r in rows if (r.get("label") or {}).get("decision") == "reject")
        if len(rows) < 2 or not text:
            continue
        signal = "historical_reject" if rejects > accepts else "historical_accept" if accepts > rejects else "mixed"
        rules.append({
            "rule_id": f"H-{len(rules)+1:03d}",
            "reason_key": reason,
            "proposed_text_normalized": text,
            "clause_position": clause or None,
            "accept_count": accepts,
            "reject_count": rejects,
            "signal": signal,
            "case_ids": [r.get("case_id") for r in rows],
            "policy": "review_priority_only; never creates human_accept or autocut permission",
        })
    (out_dir / "rules_suggestions.json").write_text(json.dumps({
        "schema_version": "preference-rules-suggestions-v1",
        "snapshot_id": snapshot_id,
        "rules": rules,
        "policy": "review_priority_only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    pref_lines = [
        f"# 标签偏好快照 {snapshot_id}",
        "",
        f"- 有效记录：**{len(records)}**（accept={counts.get('accept', 0)} / reject={counts.get('reject', 0)}）",
        f"- reason_key：{', '.join(sorted(reason_counts))}",
        "- 作用：只影响候选排序与审核提示；不生成 human_accept、不生成 EDL、不授权自动剪。",
        "- 每条规则都保留 case_id 和来源文件 SHA，可回溯。",
        "",
        "## 规则信号",
        "",
    ]
    for rule in rules:
        text = rule["proposed_text_normalized"] or "(空文本)"
        pref_lines.append(
            f"- `{rule['rule_id']}` `{rule['reason_key']}` `{text}` "
            f"[{rule.get('clause_position') or '-'}]：{rule['signal']} "
            f"(accept={rule['accept_count']}, reject={rule['reject_count']})；"
            f"cases={','.join(rule['case_ids'][:6])}"
        )
    (out_dir / "preferences.md").write_text("\n".join(pref_lines) + "\n", encoding="utf-8")
    agent_lines = [
        f"# Agent 标签偏好快照 {snapshot_id}",
        "",
        "> 这是审核排序建议，不是自动剪辑政策。遇到历史 reject 只能提高复听优先级；遇到历史 accept 只能降低重复提醒，不能自动批准。",
        "",
    ]
    for rule in rules:
        action = "提高人工复听优先级并展示历史 reject 依据" if rule["signal"] == "historical_reject" else "作为可比案例提示，但仍要求当前真人决定" if rule["signal"] == "historical_accept" else "标记为意见不一致，升级人工复听"
        agent_lines.append(
            f"- `{rule['rule_id']}`：`{rule['reason_key']}` / `{rule['proposed_text_normalized']}` / `{rule.get('clause_position') or '-'}` → {action}；"
            f"case_ids={','.join(rule['case_ids'])}"
        )
    (out_dir / "preferences_for_agent.md").write_text("\n".join(agent_lines) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": "preference-snapshot-manifest-v1",
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_policy": "discover itemized human decisions; skip bulk_accept, drafts and review UI raw copies",
        "source_files": sources,
        "quarantine": quarantined,
        "counts": {
            "records": len(records),
            "accept": counts.get("accept", 0),
            "reject": counts.get("reject", 0),
            "quarantine": len(quarantined),
            "rules": len(rules),
            "policy_cards": len(policy_cards),
        },
        "artifacts": {},
        "prohibited_actions": ["train_model", "write_model_weights", "modify_champion", "approve_edl", "render_audio"],
    }
    for name in (
        "aggregated.json", "rules_suggestions.json", "preferences.md", "preferences_for_agent.md",
        "feedback_classifications.jsonl", "policy_cards.json", "policies.md",
    ):
        manifest["artifacts"][name] = sha256_file(out_dir / name)
    (out_dir / "snapshot_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--canonical-case-store", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = build_snapshot(args.repo_root.resolve(), args.out_dir.resolve(),
                              args.canonical_case_store.resolve() if args.canonical_case_store else None)
    print(json.dumps({"snapshot_id": manifest["snapshot_id"], "counts": manifest["counts"], "out_dir": str(args.out_dir.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
