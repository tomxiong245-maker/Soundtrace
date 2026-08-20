#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_nisqa_wrapper.py · Challenger nisqa-cutverify-v1 · unittest + mock.

覆盖 Check 5 判决路由的三条分支 —— PASS / HUMAN_REVIEW / REJECT。
骨架期：真实 NISQA 推理被 mock 掉，只验证判决表逻辑意图与脚本可执行性。

分支：
    1. absolute mos = 4.2  → PASS
    2. absolute mos = 2.5  → HUMAN_REVIEW（< 3.0）
    3. delta         = -0.8 → REJECT       （< -0.5）
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class AstParseSanityTest(unittest.TestCase):
    """ast.parse 每个骨架脚本，确保语法可解析。"""

    SCRIPTS = (
        "check_nisqa_mos.py",
        "compute_mos_delta.py",
        "route_by_mos.py",
    )

    def test_all_scripts_parseable(self) -> None:
        for name in self.SCRIPTS:
            path = SCRIPTS_DIR / name
            with self.subTest(script=name):
                self.assertTrue(path.exists(), f"missing {path}")
                src = path.read_text(encoding="utf-8")
                try:
                    ast.parse(src, filename=str(path))
                except SyntaxError as e:
                    self.fail(f"ast.parse failed for {name}: {e}")


class RouteByMosBranchesTest(unittest.TestCase):
    """mock route_by_mos 的三分支返回，验证判决表意图。

    骨架实现里 route_by_mos 是 NotImplementedError · 用 mock 替换成契约中
    应产出的 verdict dict · 用来固化 "输入 → 输出" 期望。
    """

    def _fake_route(self, mos_result: dict) -> dict:
        """按 audits/nisqa-2.0.md 的判决表复刻期望结果 · **仅测试期望 · 非真实实现**。"""
        if "delta" in mos_result and mos_result["delta"] is not None:
            if mos_result["delta"] < -0.5:
                return {
                    "verdict": "REJECT",
                    "reason": f"delta {mos_result['delta']} < -0.5",
                    "source": "delta",
                    "engine": "nisqa-2.0",
                    "status": "SKELETON",
                }
            return {
                "verdict": "PASS",
                "reason": None,
                "source": "delta",
                "engine": "nisqa-2.0",
                "status": "SKELETON",
            }
        mos = mos_result.get("mos")
        if mos is not None and mos < 3.0:
            return {
                "verdict": "HUMAN_REVIEW",
                "reason": f"mos {mos} < 3.0",
                "source": "absolute",
                "engine": "nisqa-2.0",
                "status": "SKELETON",
            }
        return {
            "verdict": "PASS",
            "reason": None,
            "source": "absolute",
            "engine": "nisqa-2.0",
            "status": "SKELETON",
        }

    def test_absolute_pass_branch(self) -> None:
        import route_by_mos
        with mock.patch.object(route_by_mos, "route_by_mos", side_effect=self._fake_route):
            out = route_by_mos.route_by_mos({"mos": 4.2, "engine": "nisqa-2.0"})
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["source"], "absolute")

    def test_absolute_human_review_branch(self) -> None:
        import route_by_mos
        with mock.patch.object(route_by_mos, "route_by_mos", side_effect=self._fake_route):
            out = route_by_mos.route_by_mos({"mos": 2.5, "engine": "nisqa-2.0"})
        self.assertEqual(out["verdict"], "HUMAN_REVIEW")
        self.assertEqual(out["source"], "absolute")
        self.assertIn("2.5", out["reason"])

    def test_delta_reject_branch(self) -> None:
        import route_by_mos
        with mock.patch.object(route_by_mos, "route_by_mos", side_effect=self._fake_route):
            out = route_by_mos.route_by_mos(
                {"before_mos": 4.1, "after_mos": 3.3, "delta": -0.8, "engine": "nisqa-2.0"}
            )
        self.assertEqual(out["verdict"], "REJECT")
        self.assertEqual(out["source"], "delta")


class SkeletonRaisesTest(unittest.TestCase):
    """确认骨架函数体保持 NotImplementedError · 避免误上线。"""

    def test_check_nisqa_mos_raises(self) -> None:
        import check_nisqa_mos
        with self.assertRaises(NotImplementedError):
            check_nisqa_mos.check_nisqa_mos(clip_path="x.wav", mode="overall")

    def test_compute_mos_delta_raises(self) -> None:
        import compute_mos_delta
        with self.assertRaises(NotImplementedError):
            compute_mos_delta.compute_mos_delta(before_clip="a.wav", after_clip="b.wav")

    def test_route_by_mos_raises(self) -> None:
        import route_by_mos
        with self.assertRaises(NotImplementedError):
            route_by_mos.route_by_mos({"mos": 3.9})


if __name__ == "__main__":
    unittest.main()
