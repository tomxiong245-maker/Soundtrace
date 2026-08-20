#!/usr/bin/env python3
"""跑 T01-T12 fixture 对 evaluate_candidate_safety 做单元测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from evaluate_candidate_safety import evaluate_candidate_safety  # noqa: E402


def main() -> int:
    fx = json.loads((ROOT / "tests/fixtures.json").read_text(encoding="utf-8"))
    ctx_s = float(fx["context_seconds"])
    passed = 0
    failed: list[str] = []
    lines: list[str] = []
    for case in fx["cases"]:
        result = evaluate_candidate_safety(case["candidate"], case["words"], ctx_s)
        exp = case["expect"]
        ok_decision = result["decision"] == exp["decision"]
        ok_reasons = sorted(result["reason_codes"]) == sorted(exp["reason_codes"])
        ok = ok_decision and ok_reasons
        mark = "PASS" if ok else "FAIL"
        lines.append(
            f"[{mark}] {case['id']}: got decision={result['decision']}"
            f" reasons={result['reason_codes']}"
            f" | expected decision={exp['decision']}"
            f" reasons={exp['reason_codes']}"
        )
        if ok:
            passed += 1
        else:
            failed.append(case["id"])
    lines.append(f"\n{passed}/{len(fx['cases'])} passed. failed={failed}")
    text = "\n".join(lines)
    print(text)
    (ROOT / "test_results.txt").write_text(text + "\n", encoding="utf-8")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
