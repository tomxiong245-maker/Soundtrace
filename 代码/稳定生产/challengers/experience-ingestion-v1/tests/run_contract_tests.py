#!/usr/bin/env python3
"""不依赖 pytest 的 experience-ingestion-v1 契约测试入口。

当前项目的系统 Python 未必安装 pytest。所有契约测试仅使用 stdlib 的临时目录，
因此这个入口可直接复跑同一组 test_*.py，避免为了验证只读案例库而污染全局环境。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
TEST_FILE = HERE / "test_experience_ingestion.py"


def load_module():
    spec = importlib.util.spec_from_file_location("experience_contract_tests", TEST_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载测试文件：{TEST_FILE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    tests = sorted(
        (name, fn)
        for name, fn in vars(module).items()
        if name.startswith("test_") and callable(fn)
    )
    failures: list[str] = []
    for name, fn in tests:
        try:
            with tempfile.TemporaryDirectory(prefix="experience-ingestion-") as tmp:
                fn(Path(tmp))
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001 - report each independent contract failure
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"FAIL {failures[-1]}")

    print(f"RESULT {len(tests) - len(failures)}/{len(tests)} passed")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
