#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))

from create_policy_promotion_evidence import build_evidence_run  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class PolicyPromotionEvidenceTests(unittest.TestCase):
    def test_evidence_run_is_separate_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            learning = root / "label-learning"
            artifact = learning / "event_routes.json"
            write_json(artifact, {"ok": True})
            write_json(
                learning / "manifest.json",
                {
                    "schema_version": "label-learning-challenger-manifest-v1",
                    "run_id": "label-learning",
                    "artifacts": {"event_routes.json": hashlib.sha256(artifact.read_bytes()).hexdigest()},
                },
            )
            cards = root / "cards.json"
            recommendations = root / "recommendations.json"
            readiness = root / "readiness.json"
            guards = root / "guards.json"
            write_json(cards, {"cards": []})
            write_json(recommendations, {"recommendations": []})
            write_json(readiness, {"status": "NOT_READY", "checks": {}})
            write_json(guards, {"guards": True})
            output = root / "promotion"
            result = build_evidence_run(
                out_dir=output,
                label_learning_run=learning,
                policy_cards=cards,
                recommendations=recommendations,
                readiness=readiness,
                active_guard_policy=guards,
            )
            self.assertEqual(result["status"], "NOT_APPROVED")
            self.assertTrue((output / "promotion_report.json").is_file())
            self.assertTrue((output / "manifest.json").is_file())
            manifest = json.loads((output / "manifest.json").read_text())
            for relpath, digest in manifest["artifacts"].items():
                self.assertEqual(hashlib.sha256((output / relpath).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
