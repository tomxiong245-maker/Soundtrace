#!/usr/bin/env python3
"""Smoke test · learning-pattern-from-case-v1.

契约:
- script 能跑 · exit 0
- --out 指定的文件被创建 · 非空
- 数据源全缺时也不 break (fail-closed)
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "extract_pattern_from_cases.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, check=False,
    )


def test_skeleton_runs_with_all_sources_missing() -> None:
    """所有数据源不存在 · 应 exit 0 · 输出仍写盘."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        out = tmpd / "output" / "pattern_summary.md"
        cp = _run([
            "--knowledge-dir", str(tmpd / "nonexistent_knowledge"),
            "--case-store-dir", str(tmpd / "nonexistent_cases"),
            "--human-decisions-json", str(tmpd / "nonexistent_human_decisions.json"),
            "--out", str(out),
        ])
        assert cp.returncode == 0, f"exit != 0: stderr={cp.stderr}"
        assert out.exists(), "输出文件未创建"
        text = out.read_text(encoding="utf-8")
        assert len(text) > 0, "输出为空"
        assert "SKELETON" in text, "缺 SKELETON 标记"
        assert "缺失" in text, "缺失路径未被标注"


def test_skeleton_creates_missing_out_dir() -> None:
    """--out 父目录不存在 · 应自动创建."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        out = tmpd / "deep" / "nested" / "dir" / "pattern_summary.md"
        cp = _run([
            "--knowledge-dir", str(tmpd),
            "--out", str(out),
        ])
        assert cp.returncode == 0, f"exit != 0: stderr={cp.stderr}"
        assert out.exists(), "深层输出目录未自动创建"


if __name__ == "__main__":
    test_skeleton_runs_with_all_sources_missing()
    test_skeleton_creates_missing_out_dir()
    print("[test_smoke] PASS")
