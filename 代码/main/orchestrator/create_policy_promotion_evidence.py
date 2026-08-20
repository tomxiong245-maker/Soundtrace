#!/usr/bin/env python3
"""Create one immutable evidence run for a policy-promotion decision.

The label-learning run and the promotion result deliberately live in different
directories.  A label-learning Challenger must stay immutable after its
manifest is written; a later promotion report therefore cannot be appended to
it without invalidating its evidence chain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ORCHESTRATOR_DIR = Path(__file__).resolve().parent
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from policy_promotion import evaluate_policy_promotion, write_report  # noqa: E402


SCHEMA_VERSION = "policy-promotion-evidence-run-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_label_learning_run(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("label-learning manifest is missing")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != "label-learning-challenger-manifest-v1":
        raise ValueError("label-learning source has an unsupported manifest schema")
    for relpath, expected in (manifest.get("artifacts") or {}).items():
        artifact = path / str(relpath)
        if not artifact.is_file() or sha256_file(artifact) != expected:
            raise ValueError(f"label-learning source manifest verification failed: {relpath}")
    return manifest


def _freeze(source: Path, destination: Path) -> dict[str, str]:
    if not source.is_file():
        raise ValueError(f"source is missing: {source}")
    shutil.copy2(source, destination)
    return {
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "frozen_relpath": str(destination.relative_to(destination.parents[1])),
        "frozen_sha256": sha256_file(destination),
    }


def build_evidence_run(
    *,
    out_dir: Path,
    label_learning_run: Path,
    policy_cards: Path,
    recommendations: Path,
    readiness: Path,
    active_guard_policy: Path,
    authorization: Path | None = None,
) -> dict[str, Any]:
    if out_dir.exists():
        raise FileExistsError(f"refusing to overwrite evidence run: {out_dir}")
    label_learning_run = label_learning_run.resolve()
    source_manifest = verify_label_learning_run(label_learning_run)
    out_dir.mkdir(parents=True, exist_ok=False)
    try:
        frozen = out_dir / "sources"
        frozen.mkdir()
        source_inventory = {
            "label_learning_manifest": _freeze(label_learning_run / "manifest.json", frozen / "label_learning_manifest.json"),
            "policy_cards": _freeze(policy_cards.resolve(), frozen / "policy_cards.json"),
            "recommendations": _freeze(recommendations.resolve(), frozen / "rule_recommendations.json"),
            "readiness": _freeze(readiness.resolve(), frozen / "training_readiness.json"),
            "active_guard_policy": _freeze(active_guard_policy.resolve(), frozen / "active_guard_policy.json"),
        }
        if authorization is not None:
            source_inventory["authorization"] = _freeze(authorization.resolve(), frozen / "authorization.json")

        policy_document = read_json(frozen / "policy_cards.json")
        recommendations_document = read_json(frozen / "rule_recommendations.json")
        readiness_document = read_json(frozen / "training_readiness.json")
        authorization_document = read_json(frozen / "authorization.json") if authorization is not None else None
        report = evaluate_policy_promotion(
            policy_document,
            recommendations_document,
            readiness_document,
            authorization=authorization_document,
        )
        write_report(
            out_dir,
            report,
            [
                frozen / "policy_cards.json",
                frozen / "rule_recommendations.json",
                frozen / "training_readiness.json",
                *([frozen / "authorization.json"] if authorization is not None else []),
            ],
        )
        identity = {
            "schema_version": SCHEMA_VERSION,
            "run_id": out_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": report["status"],
            "purpose": "separate evidence-only autocut-policy promotion evaluation",
            "label_learning_source": {
                "run_path": str(label_learning_run),
                "run_id": source_manifest.get("run_id"),
                "manifest_sha256": sha256_file(label_learning_run / "manifest.json"),
            },
            "safety": {
                "reads_audio": False,
                "changes_audio": False,
                "changes_existing_runs": False,
                "creates_human_decision": False,
                "creates_edl": False,
                "changes_champion": False,
            },
        }
        write_json(out_dir / "run_identity.json", identity)
        write_json(out_dir / "source_inventory.json", source_inventory)
        artifacts = {
            name: sha256_file(out_dir / name)
            for name in (
                "run_identity.json",
                "source_inventory.json",
                "promotion_report.json",
                "PROMOTION_REPORT.md",
            )
        }
        artifacts.update(
            {
                f"sources/{path.name}": sha256_file(path)
                for path in sorted(frozen.iterdir())
                if path.is_file()
            }
        )
        manifest = {
            "schema_version": "policy-promotion-evidence-manifest-v1",
            "run_id": out_dir.name,
            "status": report["status"],
            "autocut_policy": report["autocut_policy"],
            "artifacts": artifacts,
            "policy": "evidence-only; output never edits a production rule, review decision, EDL or audio",
        }
        write_json(out_dir / "manifest.json", manifest)
        return manifest
    except Exception:
        # Keep a diagnostic directory but never manufacture a manifest for a
        # partial run, so no later process can mistake it for valid evidence.
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--label-learning-run", type=Path, required=True)
    parser.add_argument("--policy-cards", type=Path, required=True)
    parser.add_argument("--recommendations", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--active-guard-policy", type=Path, required=True)
    parser.add_argument("--authorization", type=Path)
    args = parser.parse_args(argv)
    manifest = build_evidence_run(
        out_dir=args.out_dir.expanduser().resolve(),
        label_learning_run=args.label_learning_run.expanduser().resolve(),
        policy_cards=args.policy_cards.expanduser().resolve(),
        recommendations=args.recommendations.expanduser().resolve(),
        readiness=args.readiness.expanduser().resolve(),
        active_guard_policy=args.active_guard_policy.expanduser().resolve(),
        authorization=args.authorization.expanduser().resolve() if args.authorization else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
