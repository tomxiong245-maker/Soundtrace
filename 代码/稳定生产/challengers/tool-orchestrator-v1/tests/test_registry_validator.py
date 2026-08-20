#!/usr/bin/env python3
"""Contract tests for registry_validator.

These assert both the happy path (mock registry + Champion registry) and the
failure modes described in Phase 1 of TASK_CONTRACT.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHALLENGER = HERE.parent
PROJECT_ROOT = CHALLENGER.parents[2]
sys.path.insert(0, str(CHALLENGER / "runner"))

from registry_validator import validate_registry  # type: ignore


def load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_valid_mock_registry():
    reg = load(CHALLENGER / "tests/fixtures/registry_valid.json")
    report = validate_registry(reg, project_root=CHALLENGER / "tests")
    assert report["ok"], report
    assert report["tool_count"] == 2
    names = [t["name"] for t in report["tools"]]
    assert names == ["mock_inspect", "mock_transform"]
    for t in report["tools"]:
        assert t["ok"], t
        assert t["script_exists"], t


def test_invalid_many_reports_every_issue():
    reg = load(CHALLENGER / "tests/fixtures/registry_invalid_many.json")
    report = validate_registry(reg, project_root=CHALLENGER / "tests")
    assert not report["ok"]
    blob = " ".join(report["errors"])
    assert "unsupported schema_version" in blob
    assert "scripts_root must be relative" in blob
    tool0 = report["tools"][0]
    joined = " | ".join(tool0["issues"])
    assert "does not match" in joined
    assert "description missing" in joined
    assert "params[1]" in joined
    assert "script path may not contain" in joined
    assert "reads_only" in joined
    tool1 = report["tools"][1]
    joined1 = " | ".join(tool1["issues"])
    assert "duplicate name" in joined1
    assert "duplicate script path" in joined1


def test_missing_script_defaults_to_warning():
    reg = load(CHALLENGER / "tests/fixtures/registry_missing_script.json")
    report = validate_registry(reg, project_root=CHALLENGER / "tests")
    assert report["ok"], report
    assert report["warnings"], "missing script must yield a warning by default"
    assert report["tools"][0]["script_exists"] is False


def test_missing_script_with_require_flag_fails():
    reg = load(CHALLENGER / "tests/fixtures/registry_missing_script.json")
    report = validate_registry(
        reg, project_root=CHALLENGER / "tests", require_scripts=True
    )
    assert not report["ok"]
    tool0 = report["tools"][0]
    assert any("script not found" in i for i in tool0["issues"])


def test_scripts_root_parent_escape_is_rejected():
    reg = load(CHALLENGER / "tests/fixtures/registry_valid.json")
    reg["scripts_root"] = "../escape"
    report = validate_registry(reg, project_root=CHALLENGER / "tests")
    assert not report["ok"], report
    assert "scripts_root may not contain '..'" in " ".join(report["errors"])


def test_champion_registry_passes_with_require_scripts():
    reg = load(PROJECT_ROOT / "main/tools/tools.json")
    report = validate_registry(
        reg, project_root=PROJECT_ROOT, require_scripts=True
    )
    assert report["ok"], report["errors"]
    assert report["tool_count"] == 18
    names = {t["name"] for t in report["tools"]}
    assert "inspect_audio" in names
    assert "denoise_tracks" in names
    assert "render_approved_edl" in names
    denoise = next(item for item in report["tools"] if item["name"] == "denoise_tracks")
    assert denoise["script"] == "deepfilternet_denoise_tracks.py"
    for t in report["tools"]:
        assert t["script_exists"], t


def test_absolute_project_root_and_no_write():
    """Validator must never write to project_root during static checks."""
    reg = load(PROJECT_ROOT / "main/tools/tools.json")
    # Take a lightweight snapshot of tools.json bytes before/after.
    tools_path = PROJECT_ROOT / "main/tools/tools.json"
    before = tools_path.read_bytes()
    validate_registry(reg, project_root=PROJECT_ROOT, require_scripts=True)
    after = tools_path.read_bytes()
    assert before == after


def main():
    tests = [
        test_valid_mock_registry,
        test_invalid_many_reports_every_issue,
        test_missing_script_defaults_to_warning,
        test_missing_script_with_require_flag_fails,
        test_scripts_root_parent_escape_is_rejected,
        test_champion_registry_passes_with_require_scripts,
        test_absolute_project_root_and_no_write,
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
