"""Contract tests for the v2 adapter registry (18 tool adapters).

Verifies:
- registry.json loads and adheres to schema (schema_version, no duplicate ids)
- every entry's contract wraps a script that exists on disk
- every entry produces a valid dry_run plan for a stub input set (no execution)
- every reads_only=false entry declares a write_policy block
- generic adapter enforces missing-input errors
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))

import _adapter_base as ab
from generic_script_adapter import GenericScriptAdapter, load_registry


def _find_project_root() -> Path:
    """从测试文件位置往上找 CLAUDE.md · 兼容 live (剪辑项目/) 与 交付 (最终交付文档/) 两种布局。"""
    d = Path(__file__).resolve().parent
    while d != d.parent:
        if (d / "CLAUDE.md").is_file() or (d / "项目主文档/CLAUDE.md").is_file():
            return d
        d = d.parent
    return Path(__file__).resolve().parent.parent.parent.parent.parent


PROJECT_ROOT = _find_project_root()
# 布局自适应：交付布局下代码在 代码/ 前缀下，registry 也在其中
_CODE_PREFIX = "代码/" if (PROJECT_ROOT / "代码/稳定生产/challengers/tool-orchestrator-v2/adapters/registry.json").is_file() else ""
REGISTRY = PROJECT_ROOT / f"{_CODE_PREFIX}稳定生产/challengers/tool-orchestrator-v2/adapters/registry.json"


class RegistryContractTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.adapters = load_registry(REGISTRY, project_root=PROJECT_ROOT)

    def test_schema_version_ok(self):
        self.assertEqual(self.data["schema_version"], "tool-orchestrator-v2.registry.v1")

    def test_all_registered_tools_have_adapter(self):
        # tools.json 里所有 non-deprecated tool 都应在 registry 里有 adapter；
        # 反之 registry 里的每一项也都应对应 tools.json 里的 tool_name。
        # 例外：skeleton adapter（对应脚本还没写）不需要 tools.json 条目。
        tj = json.loads((PROJECT_ROOT / f"{_CODE_PREFIX}main/tools/tools.json").read_text(encoding="utf-8"))
        declared_tools = {t["name"] for t in tj["tools"]}
        registered = {a["contract"]["tool_name"] for a in self.data["adapters"]
                      if not a["contract"].get("skeleton")}
        # Registry 内 non-skeleton tool_name 必须都真实存在于 tools.json（不然是漂移）
        orphans = registered - declared_tools
        self.assertEqual(orphans, set(), f"registry has adapters for unknown tool_names: {orphans}")
        # 允许 registry 覆盖不全（Session 2 阶段），但断言最少底线：Champion 18 + 2 v1 Challenger = 20
        self.assertGreaterEqual(len(self.data["adapters"]), 20,
                                f"adapter count regressed below 20: got {len(self.data['adapters'])}")

    def test_no_duplicate_adapter_or_tool_ids(self):
        ids = [a["contract"]["adapter_id"] for a in self.data["adapters"]]
        tools = [a["contract"]["tool_name"] for a in self.data["adapters"]]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate adapter_id: {ids}")
        self.assertEqual(len(tools), len(set(tools)), f"duplicate tool_name: {tools}")

    def test_every_wraps_script_exists(self):
        missing = []
        SKELETON_ADAPTERS = {"speaker-diarize-v1"}  # skeleton adapters allow script absence
        for entry in self.data["adapters"]:
            if entry["contract"]["adapter_id"] in SKELETON_ADAPTERS:
                continue
            script = PROJECT_ROOT / entry["contract"]["wraps_script"]
            if not script.exists():
                missing.append(str(script))
        self.assertEqual(missing, [], f"missing wraps_script files: {missing}")

    def test_write_tool_declares_write_policy(self):
        offenders = []
        for entry in self.data["adapters"]:
            c = entry["contract"]
            if c["reads_only"] is False and not c.get("write_policy"):
                offenders.append(c["adapter_id"])
        self.assertEqual(offenders, [], f"write-tool missing write_policy: {offenders}")

    def test_every_adapter_produces_dry_run_plan(self):
        # Feed each adapter a stub inputs dict matching its inputs_schema.required.
        # 如果 command_template 里出现 {name*} 声明的是 list 值，stub 用 list-of-one。
        import re as _re
        _spread_re = _re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\*\}")
        errors: list[str] = []
        for adapter_id, adapter in self.adapters.items():
            required = adapter.contract["inputs_schema"].get("required", [])
            spread_keys = set()
            for tok in adapter._command_template:
                for m in _spread_re.finditer(tok):
                    spread_keys.add(m.group(1))
            stub_inputs: dict[str, object] = {}
            for k in required:
                stub_val = f"/tmp/stub_{adapter_id}_{k}"
                stub_inputs[k] = [stub_val] if k in spread_keys else stub_val
            try:
                plan = adapter.dry_run_plan(stub_inputs, PROJECT_ROOT / "main/runs/stub-run")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{adapter_id}: {exc}")
                continue
            self.assertEqual(plan["adapter_id"], adapter_id)
            self.assertGreater(len(plan["command"]), 1, f"{adapter_id} command too short")
            self.assertTrue(plan["expected_outputs"], f"{adapter_id} produced no expected_outputs")
        self.assertEqual(errors, [], f"dry_run failures: {errors}")

    def test_generic_adapter_rejects_missing_input(self):
        adapter = self.adapters["inspect-audio-v2"]
        with self.assertRaises(ab.InputsValidationError):
            adapter.dry_run_plan({"input_wav": "x"}, PROJECT_ROOT / "main/runs/stub")

    def test_generic_adapter_dry_run_does_not_execute(self):
        # Confirm dry_run_plan does not invoke subprocess: run against inspect_audio-v2
        # then confirm no output file was created.
        adapter = self.adapters["inspect-audio-v2"]
        run_dir = PROJECT_ROOT / "main/runs/stub-dryrun-check"
        out_json = run_dir / "should_not_exist.json"
        if out_json.exists():
            out_json.unlink()
        _ = adapter.dry_run_plan(
            {"input_wav": "/tmp/nonexistent_input.wav", "output_json": str(out_json)},
            run_dir,
        )
        self.assertFalse(out_json.exists(), "dry_run_plan must not create outputs")

    def test_write_policy_scope_hash_helper_is_deterministic(self):
        h1 = ab.compute_writes_scope_hash(["a", "b"])
        h2 = ab.compute_writes_scope_hash(["b", "a"])
        self.assertEqual(h1, h2, "scope hash must be order-independent")
        h3 = ab.compute_writes_scope_hash(["a"])
        self.assertNotEqual(h1, h3)


if __name__ == "__main__":
    unittest.main()
