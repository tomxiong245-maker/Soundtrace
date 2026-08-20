#!/usr/bin/env python3
"""tests/test_embedding_pipeline.py — 契约测试骨架 (SKELETON_CREATED · 2026-08-19)

只测「argparse 契约」与「函数存在性」，不触发真实推理。
真实数值测试等 EP05 实现期补齐（fake fixture 放在 tests/fixtures/）。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class SkeletonSyntaxTests(unittest.TestCase):
    """每个骨架脚本必须能被 ast.parse."""

    def test_build_case_embeddings_parses(self) -> None:
        src = (SCRIPTS_DIR / "build_case_embeddings.py").read_text(encoding="utf-8")
        ast.parse(src)

    def test_embed_candidate_parses(self) -> None:
        src = (SCRIPTS_DIR / "embed_candidate.py").read_text(encoding="utf-8")
        ast.parse(src)

    def test_retrieve_similar_cases_parses(self) -> None:
        src = (SCRIPTS_DIR / "retrieve_similar_cases.py").read_text(encoding="utf-8")
        ast.parse(src)


class ArgparseContractTests(unittest.TestCase):
    """骨架期固定 argparse 契约 — 参数名不得变。"""

    def _load_parser(self, module_name: str):
        import importlib.util
        path = SCRIPTS_DIR / f"{module_name}.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.build_parser()

    def test_build_case_embeddings_required_args(self) -> None:
        parser = self._load_parser("build_case_embeddings")
        required = {a.dest for a in parser._actions if getattr(a, "required", False)}
        self.assertEqual(required, {"case_memory", "audio_root", "out"})

    def test_embed_candidate_required_args(self) -> None:
        parser = self._load_parser("embed_candidate")
        required = {a.dest for a in parser._actions if getattr(a, "required", False)}
        self.assertEqual(required, {"wav", "start", "end"})

    def test_retrieve_similar_cases_required_args(self) -> None:
        parser = self._load_parser("retrieve_similar_cases")
        required = {a.dest for a in parser._actions if getattr(a, "required", False)}
        self.assertEqual(required, {"index", "meta", "query_wav", "start", "end"})


class SkeletonRaisesTests(unittest.TestCase):
    """骨架期 main() 必须抛 NotImplementedError (证明未偷偷跑真实逻辑)."""

    def _import(self, module_name: str):
        import importlib.util
        path = SCRIPTS_DIR / f"{module_name}.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_build_case_embeddings_main_raises(self) -> None:
        mod = self._import("build_case_embeddings")
        with self.assertRaises(NotImplementedError):
            mod.main([
                "--case-memory", str(FIXTURES_DIR / "fake_case_memory.json"),
                "--audio-root", str(FIXTURES_DIR),
                "--out", str(FIXTURES_DIR / "fake_out"),
            ])

    def test_embed_candidate_main_raises(self) -> None:
        mod = self._import("embed_candidate")
        with self.assertRaises(NotImplementedError):
            mod.main([
                "--wav", str(FIXTURES_DIR / "fake.wav"),
                "--start", "0.0",
                "--end", "1.0",
            ])

    def test_retrieve_similar_cases_main_raises(self) -> None:
        mod = self._import("retrieve_similar_cases")
        with self.assertRaises(NotImplementedError):
            mod.main([
                "--index", str(FIXTURES_DIR / "fake.index"),
                "--meta", str(FIXTURES_DIR / "fake_meta.jsonl"),
                "--query-wav", str(FIXTURES_DIR / "fake.wav"),
                "--start", "0.0",
                "--end", "1.0",
            ])


if __name__ == "__main__":
    unittest.main()
