from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ORCHESTRATOR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import build_label_learning_challenger as loop  # noqa: E402


class LabelLearningChallengerTests(unittest.TestCase):
    def test_refuses_to_overwrite_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                loop.build_package(
                    repo_root=Path(tmp),
                    out_dir=output,
                    current_run=Path(tmp) / "missing-current",
                    historical_run=Path(tmp) / "missing-history",
                    canonical_case_store=None,
                )


if __name__ == "__main__":
    unittest.main()
