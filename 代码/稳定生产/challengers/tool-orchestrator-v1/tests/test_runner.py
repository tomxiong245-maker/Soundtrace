#!/usr/bin/env python3
"""Runner contract & integration tests using mock scripts and a synthetic fixture.

These tests do NOT touch the real registry, Champion scripts, or real audio.
They use the mock registry and mock scripts under tests/fixtures/.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHALLENGER = HERE.parent
RUNNER = CHALLENGER / "runner" / "runner.py"
FIX = CHALLENGER / "tests" / "fixtures"
MOCK_REG = FIX / "registry_valid.json"
sys.path.insert(0, str(CHALLENGER / "runner"))
import runner  # type: ignore


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_track_and_config(tmp: Path, *, tracks: int, human_review_after: str | None = "01_inspect_track_01"):
    input_dir = tmp / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    tconf = []
    steps = []
    for i in range(1, tracks + 1):
        p = input_dir / f"track_{i:02d}.json"
        payload = {"track": i, "hello": "world"}
        p.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tconf.append({
            "track_id": f"track_{i:02d}",
            "label": f"physical_mic_{i:02d}",
            "input_path": str(p),
            "sample_rate": 48000,
            "channel_count": 1,
            "duration_seconds": 20.0,
            "sha256": sha256_of(p),
        })
        steps.append({
            "step_id": f"01_inspect_track_{i:02d}",
            "tool": "mock_inspect",
            "phase": "pre_review",
            "params": {
                "input_json": f"@track:track_{i:02d}.input_path",
                "output_json": f"run:01_inspect/track_{i:02d}.inspection.json",
            },
        })
    cfg = {
        "episode_id": f"TOOL-ORCH-FIXTURE-N{tracks}",
        "tracks": tconf,
        "steps": steps,
        "human_review_after": human_review_after,
    }
    cfg_path = tmp / "episode.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg_path


def run_cli(*args: str, expect_code: int | None = None):
    proc = subprocess.run(
        [sys.executable, str(RUNNER), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out = proc.stdout.decode()
    err = proc.stderr.decode()
    if expect_code is not None:
        assert proc.returncode == expect_code, (proc.returncode, out, err)
    return proc.returncode, out, err


def project_root_of_mock_registry() -> Path:
    # Simulate a "project root" so the mock registry resolves under fixtures/.
    # The real runner infers project_root from registry_path.parents[2]; for the
    # mock registry to point at fixtures/mock_scripts we place it at
    # <fake_root>/main/tools/tools.json and copy scripts under
    # <fake_root>/fixtures/mock_scripts/. Do this via a temp dir per test.
    raise NotImplementedError("built inline by fixtures")


def make_fake_project(tmp: Path) -> Path:
    """Create a fake project layout so runner._project_root_from works."""
    fake = tmp / "fake_project"
    (fake / "main" / "tools").mkdir(parents=True)
    (fake / "fixtures" / "mock_scripts").mkdir(parents=True)
    reg_dst = fake / "main" / "tools" / "tools.json"
    shutil.copy(MOCK_REG, reg_dst)
    shutil.copy(FIX / "mock_scripts" / "mock_inspect.py", fake / "fixtures" / "mock_scripts" / "mock_inspect.py")
    shutil.copy(FIX / "mock_scripts" / "mock_transform.py", fake / "fixtures" / "mock_scripts" / "mock_transform.py")
    return fake


def test_ntrack_pipeline_stops_at_human_review():
    for n in (2, 3, 4):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake = make_fake_project(tmp)
            cfg_path = make_track_and_config(tmp, tracks=n, human_review_after=f"01_inspect_track_{n:02d}")
            run_dir = tmp / "run1"
            rc, out, err = run_cli(
                "create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"),
                expect_code=0,
            )
            assert (run_dir / "plan.json").exists()
            rc, out, err = run_cli("run", "--run-dir", str(run_dir), expect_code=0)
            state = json.loads((run_dir / "state.json").read_text())
            assert state["state"] == "HUMAN_REVIEW_REQUIRED", (state, out, err)
            manifest = json.loads((run_dir / "run_manifest.json").read_text())
            assert len(manifest["outputs"]) == n, manifest
            # tool_calls.jsonl has exactly n lines
            lines = (run_dir / "tool_calls.jsonl").read_text().strip().splitlines()
            assert len(lines) == n


def test_dry_run_writes_no_outputs():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2, human_review_after="01_inspect_track_02")
        run_dir = tmp / "run_dry"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        run_cli("run", "--run-dir", str(run_dir), "--dry-run", expect_code=0)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "PLAN_FROZEN"
        assert state["completed_step_ids"] == []
        # No output json files should exist under 01_inspect/
        outputs = list((run_dir / "01_inspect").glob("*.json")) if (run_dir / "01_inspect").exists() else []
        assert outputs == [], outputs
        assert not (run_dir / "tool_calls.jsonl").exists()
        assert (run_dir / "dry_run_tool_calls.jsonl").exists()
        assert not (run_dir / "logs").exists()
        # A dry run must not make a real run impossible.
        run_cli("run", "--run-dir", str(run_dir), expect_code=0)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "HUMAN_REVIEW_REQUIRED"


def test_run_refuses_to_overwrite_existing_run_dir():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        run_dir = tmp / "run_x"
        run_dir.mkdir()
        (run_dir / "some_pre_existing.txt").write_text("nope")
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(run_dir),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            expect_code=3,
        )
        assert "already exists" in err, err


def test_resume_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=3, human_review_after="01_inspect_track_03")
        run_dir = tmp / "run_r"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        run_cli("run", "--run-dir", str(run_dir), "--stop-at", "01_inspect_track_02", expect_code=0)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "STOPPED_AT_CHECKPOINT", state
        # Capture SHA of first output
        out1 = run_dir / "01_inspect" / "track_01.inspection.json"
        sha_before = sha256_of(out1)
        # Now resume; runner should skip completed steps and finish track_03.
        run_cli("resume", "--run-dir", str(run_dir), expect_code=0)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "HUMAN_REVIEW_REQUIRED"
        sha_after = sha256_of(out1)
        assert sha_before == sha_after, "resume must not re-execute completed steps"
        # 3 total calls (2 first, 1 second) in tool_calls.jsonl
        lines = (run_dir / "tool_calls.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3


def test_unknown_tool_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        cfg = json.loads(cfg_path.read_text())
        cfg["steps"][0]["tool"] = "does_not_exist"
        cfg_path.write_text(json.dumps(cfg))
        run_dir = tmp / "run_bad"
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(run_dir),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            expect_code=3,
        )
        assert "unknown tool" in err, err


def test_missing_param_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        cfg = json.loads(cfg_path.read_text())
        cfg["steps"][0]["params"].pop("output_json")
        cfg_path.write_text(json.dumps(cfg))
        run_dir = tmp / "run_bad2"
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(run_dir),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            expect_code=3,
        )
        assert "missing params" in err, err


def test_track_sha_mismatch_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        cfg = json.loads(cfg_path.read_text())
        cfg["tracks"][0]["sha256"] = "0" * 64
        cfg_path.write_text(json.dumps(cfg))
        run_dir = tmp / "run_bad3"
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(run_dir),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            expect_code=3,
        )
        assert "sha mismatch" in err, err


def test_post_review_phase_is_refused():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        cfg = json.loads(cfg_path.read_text())
        cfg["steps"][1]["phase"] = "post_review"
        cfg_path.write_text(json.dumps(cfg))
        run_dir = tmp / "run_bad4"
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(run_dir),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            expect_code=3,
        )
        assert "phase" in err and "not allowed" in err, err


def test_non_read_only_tool_is_rejected_at_plan_time():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        cfg = json.loads(cfg_path.read_text())
        cfg["steps"] = [{
            "step_id": "01_transform",
            "tool": "mock_transform",
            "phase": "pre_review",
            "params": {
                "input_json": "@track:track_01.input_path",
                "output_json": "run:out.json",
                "config": "safe-test-only",
            },
        }]
        cfg["human_review_after"] = "01_transform"
        cfg_path.write_text(json.dumps(cfg))
        run_dir = tmp / "run_non_read_only"
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(run_dir),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            expect_code=3,
        )
        assert "not reads_only" in err, err


def test_missing_human_review_gate_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        cfg = json.loads(cfg_path.read_text())
        cfg.pop("human_review_after")
        cfg_path.write_text(json.dumps(cfg))
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(tmp / "run_no_gate"),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            expect_code=3,
        )
        assert "human_review_after" in err, err


def test_direct_run_from_human_review_cannot_advance():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2, human_review_after="01_inspect_track_02")
        run_dir = tmp / "run_gate"
        run_cli("create", "--config", str(cfg_path), "--run-dir", str(run_dir),
                "--registry", str(fake / "main" / "tools" / "tools.json"), expect_code=0)
        run_cli("run", "--run-dir", str(run_dir), expect_code=0)
        before = (run_dir / "state.json").read_bytes()
        run_cli("run", "--run-dir", str(run_dir), expect_code=0)
        assert (run_dir / "state.json").read_bytes() == before
        state = json.loads(before)
        assert state["state"] == "HUMAN_REVIEW_REQUIRED"


def test_output_outside_run_dir_is_rejected_at_plan_time():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = make_fake_project(tmp)
        cfg_path = make_track_and_config(tmp, tracks=2)
        cfg = json.loads(cfg_path.read_text())
        cfg["steps"][0]["params"]["output_json"] = "abs:main/tools/not-allowed.json"
        cfg_path.write_text(json.dumps(cfg))
        rc, out, err = run_cli(
            "create", "--config", str(cfg_path), "--run-dir", str(tmp / "run_bad_output"),
            "--registry", str(fake / "main" / "tools" / "tools.json"),
            "--project-root", str(fake),
            expect_code=3,
        )
        assert "escapes required root" in err, err


def main():
    tests = [
        test_ntrack_pipeline_stops_at_human_review,
        test_dry_run_writes_no_outputs,
        test_run_refuses_to_overwrite_existing_run_dir,
        test_resume_is_idempotent,
        test_unknown_tool_is_rejected,
        test_missing_param_is_rejected,
        test_track_sha_mismatch_is_rejected,
        test_post_review_phase_is_refused,
        test_non_read_only_tool_is_rejected_at_plan_time,
        test_missing_human_review_gate_is_rejected,
        test_direct_run_from_human_review_cannot_advance,
        test_output_outside_run_dir_is_rejected_at_plan_time,
    ]
    fails = []
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            print(f"FAIL {t.__name__}: {exc}")
            fails.append(t.__name__)
        except Exception as exc:
            print(f"ERROR {t.__name__}: {exc!r}")
            fails.append(t.__name__)
    if fails:
        print(f"\n{len(fails)} failing tests: {fails}")
        return 1
    print(f"\nall {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
