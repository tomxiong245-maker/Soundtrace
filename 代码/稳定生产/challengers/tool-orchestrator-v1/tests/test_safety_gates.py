#!/usr/bin/env python3
"""Phase 5 safety-gate tests. Together with test_runner.py they cover
the 11 failure/security scenarios listed in TASK_CONTRACT §Phase 5."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHALLENGER = HERE.parent
RUNNER = CHALLENGER / "runner" / "runner.py"
FIX = CHALLENGER / "tests" / "fixtures"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def make_fake_project(tmp: Path) -> Path:
    fake = tmp / "fake_project"
    (fake / "main" / "tools").mkdir(parents=True)
    (fake / "fixtures" / "mock_scripts").mkdir(parents=True)
    shutil.copy(FIX / "registry_valid.json", fake / "main" / "tools" / "tools.json")
    shutil.copy(FIX / "mock_scripts" / "mock_inspect.py", fake / "fixtures" / "mock_scripts" / "mock_inspect.py")
    shutil.copy(FIX / "mock_scripts" / "mock_transform.py", fake / "fixtures" / "mock_scripts" / "mock_transform.py")
    return fake


def make_track_and_config(tmp: Path, *, missing_input: bool = False, human_review_after: str | None = "01"):
    inp_dir = tmp / "inputs"
    inp_dir.mkdir(parents=True, exist_ok=True)
    p = inp_dir / "track_01.json"
    payload = {"hello": "world"}
    p.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    sha = sha256_of(p)
    if missing_input:
        p.unlink()
    cfg = {
        "episode_id": "T",
        "tracks": [
            {
                "track_id": "track_01",
                "label": "L",
                "input_path": str(p),
                "sample_rate": 48000,
                "channel_count": 1,
                "duration_seconds": 20.0,
                "sha256": sha,
            }
        ],
        "steps": [
            {"step_id": "01", "tool": "mock_inspect", "phase": "pre_review",
             "params": {"input_json": "@track:track_01.input_path", "output_json": "run:out.json"}},
        ],
        "human_review_after": human_review_after,
    }
    cfg_path = tmp / "episode.json"
    cfg_path.write_text(json.dumps(cfg))
    return cfg_path


def run_cli(*args: str, expect_code: int | None = None):
    proc = subprocess.run([sys.executable, str(RUNNER), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = proc.stdout.decode(); err = proc.stderr.decode()
    if expect_code is not None:
        assert proc.returncode == expect_code, (proc.returncode, out, err)
    return proc.returncode, out, err


# 1. Input file not found at plan-freeze time → fail closed
def test_input_missing_at_plan_time():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, missing_input=True)
        run_dir = tmp / "r"
        rc, out, err = run_cli("create", "--config", str(cfg_path),
                               "--run-dir", str(run_dir),
                               "--registry", str(fake / "main" / "tools" / "tools.json"),
                               expect_code=3)
        assert "input not found" in err, err


# 2. Non-zero returncode from a tool call → state=FAILED, no manifest overwrite
def test_tool_returns_nonzero():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        # Overwrite mock_inspect with a script that always exits 5
        bad = fake / "fixtures" / "mock_scripts" / "mock_inspect.py"
        bad.write_text(
            "import sys, argparse\n"
            "p=argparse.ArgumentParser();p.add_argument('--input-json');p.add_argument('--output-json')\n"
            "p.parse_args()\n"
            "sys.exit(5)\n"
        )
        cfg_path = make_track_and_config(tmp)
        run_dir = tmp / "r2"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        rc, out, err = run_cli("run", "--run-dir", str(run_dir), expect_code=3)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "FAILED", state
        assert state["failed_step_id"] == "01"
        # tool_calls records rc=5
        line = (run_dir / "tool_calls.jsonl").read_text().strip().splitlines()[-1]
        assert '"returncode": 5' in line


# 3. Script SHA drift between plan freeze and run → refuse to run
def test_script_sha_drift():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp)
        run_dir = tmp / "r3"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        # Mutate the script AFTER plan freeze
        script = fake / "fixtures" / "mock_scripts" / "mock_inspect.py"
        script.write_text(script.read_text() + "\n# drift\n")
        rc, out, err = run_cli("run", "--run-dir", str(run_dir), expect_code=3)
        assert "SHA changed" in err, err
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "FAILED"


# 4. Registry SHA drift between plan freeze and run → refuse
def test_registry_sha_drift():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp)
        run_dir = tmp / "r4"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        reg_path = fake / "main" / "tools" / "tools.json"
        reg = json.loads(reg_path.read_text())
        reg["description"] = "drift"
        reg_path.write_text(json.dumps(reg))
        rc, out, err = run_cli("run", "--run-dir", str(run_dir), expect_code=3)
        assert "registry SHA changed" in err, err


# 5. Attempt to run with unknown --stop-at step_id → refuse
def test_unknown_stop_at():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp)
        run_dir = tmp / "r5"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        rc, out, err = run_cli("run", "--run-dir", str(run_dir), "--stop-at", "not_a_step", expect_code=3)
        assert "stop-at" in err.lower() or "stop_at" in err.lower(), err


# 6. Run without prior create (no state.json) → refuse
def test_run_without_state():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        run_dir = tmp / "r6"
        run_dir.mkdir()
        rc, out, err = run_cli("run", "--run-dir", str(run_dir), expect_code=3)
        assert "no state.json" in err, err


# 7. HUMAN_REVIEW_REQUIRED must not auto-advance on resume without human decision
def test_resume_from_human_review_replays_final_manifest_only():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp)
        run_dir = tmp / "r7"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        run_cli("run", "--run-dir", str(run_dir), expect_code=0)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "HUMAN_REVIEW_REQUIRED"
        # Resume: should immediately re-enter HUMAN_REVIEW_REQUIRED because
        # all pre-review steps are already completed and human_review_after
        # is satisfied. Runner must NOT invent an approve/finalize step.
        run_cli("resume", "--run-dir", str(run_dir), expect_code=0)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "HUMAN_REVIEW_REQUIRED"
        # tool_calls.jsonl still 1 line only.
        lines = (run_dir / "tool_calls.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1


def main():
    tests = [
        test_input_missing_at_plan_time,
        test_tool_returns_nonzero,
        test_script_sha_drift,
        test_registry_sha_drift,
        test_unknown_stop_at,
        test_run_without_state,
        test_resume_from_human_review_replays_final_manifest_only,
    ]
    fails = []
    for t in tests:
        try:
            t(); print(f"PASS {t.__name__}")
        except AssertionError as exc:
            print(f"FAIL {t.__name__}: {exc}"); fails.append(t.__name__)
        except Exception as exc:
            print(f"ERROR {t.__name__}: {exc!r}"); fails.append(t.__name__)
    if fails:
        print(f"\n{len(fails)} failing tests: {fails}"); return 1
    print(f"\nall {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
