"""Contract: orchestrator hardcoded script constants MUST all be registered in tools.json.

This is the enforcement that fixes "反复做无用功": every subprocess-invoked script in
delivery_orchestrator.py must be discoverable via tools.json, not via PROJECT_ROOT /
"..." hardcoded constants scattered across a 4640-line file.

The test scans delivery_orchestrator.py for any `<NAME>_SCRIPT` or `<NAME>_SERVER`
module-level constant that assigns `PROJECT_ROOT / "<some path>.py"`, and asserts:
1. Each such script path appears in main/tools/tools.json under some tool entry's
   `script` (relative to scripts_root) or `full_path` (absolute-relative) field.
2. The tool_lookup module (once it exists) can resolve tool names to those paths.

Until tools.json + tool_lookup are wired up, this test WILL fail — which is the point.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ORCHESTRATOR = PROJECT_ROOT / "main/orchestrator/delivery_orchestrator.py"
TOOLS_JSON = PROJECT_ROOT / "main/tools/tools.json"


SCRIPT_CONST_RE = re.compile(
    r"^([A-Z][A-Z0-9_]*_(?:SCRIPT|SERVER))\s*=",
    re.MULTILINE,
)


def _extract_hardcoded_script_paths() -> dict[str, str]:
    """Parse delivery_orchestrator.py AST and pull constants that resolve to
    PROJECT_ROOT / "some/path.py". Returns {const_name: relpath}."""
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        if not (name.endswith("_SCRIPT") or name.endswith("_SERVER")):
            continue
        # Walk the RHS looking for a BinOp: PROJECT_ROOT / "some.py" (possibly nested)
        relpath = _resolve_project_root_relpath(node.value)
        if relpath and relpath.endswith(".py"):
            out[name] = relpath
    return out


def _resolve_project_root_relpath(expr) -> str | None:
    """Recursively resolve a `PROJECT_ROOT / "a" / "b"` expression to "a/b".
    Return None if not that shape."""
    if isinstance(expr, ast.Name) and expr.id == "PROJECT_ROOT":
        return ""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        left = _resolve_project_root_relpath(expr.left)
        right = _resolve_project_root_relpath(expr.right)
        if left is None or right is None:
            return None
        return right if left == "" else f"{left}/{right}"
    return None


def _tools_json_scripts() -> set[str]:
    """Extract all script paths (relative to project root) declared in tools.json.
    Supports both the flat `script` field (relative to scripts_root) and the new
    `full_path` field (relative to project root, for tools outside scripts_root)."""
    data = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    scripts_root = data.get("scripts_root", "")
    out: set[str] = set()
    for tool in data.get("tools", []):
        # Prefer explicit full_path (project-root-relative)
        if fp := tool.get("full_path"):
            out.add(fp)
        elif script := tool.get("script"):
            if scripts_root:
                out.add(f"{scripts_root}/{script}")
            else:
                out.add(script)
    return out


class OrchestratorToolsJsonContract(unittest.TestCase):
    def test_every_hardcoded_script_is_in_tools_json(self):
        hardcoded = _extract_hardcoded_script_paths()
        registered = _tools_json_scripts()
        missing = {}
        for name, path in hardcoded.items():
            if path not in registered:
                missing[name] = path
        self.assertEqual(
            missing,
            {},
            (
                "orchestrator hardcoded scripts NOT registered in tools.json:\n"
                + "\n".join(f"  {k} -> {v}" for k, v in missing.items())
                + "\n\n每加一个新脚本硬编码在 orchestrator 里就是一次'反复做无用功'。"
                "必须把每条都登记到 tools.json；orchestrator 通过 tool_lookup 解析。"
            ),
        )

    def test_tool_lookup_module_exists_and_resolves(self):
        """Once tool_lookup exists, verify it resolves every hardcoded script back
        to a tool_name. Skip if module not yet built."""
        try:
            import sys
            sys.path.insert(0, str(PROJECT_ROOT / "main"))
            import tools.tool_lookup as tl  # type: ignore
        except ImportError:
            self.skipTest("main/tools/tool_lookup.py not yet built")
        hardcoded = _extract_hardcoded_script_paths()
        for name, path in hardcoded.items():
            tool_name = tl.tool_name_for_script(path)
            self.assertIsNotNone(
                tool_name,
                f"tool_lookup cannot map hardcoded {name} ({path}) back to a tool_name",
            )
            resolved = tl.script_for(tool_name)
            self.assertEqual(
                str(resolved.relative_to(PROJECT_ROOT)),
                path,
                f"tool_lookup.script_for({tool_name!r}) resolves to {resolved} != {path}",
            )

    def test_no_duplicate_script_paths_in_tools_json(self):
        data = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
        scripts_root = data.get("scripts_root", "")
        paths: list[str] = []
        for tool in data.get("tools", []):
            if fp := tool.get("full_path"):
                paths.append(fp)
            elif script := tool.get("script"):
                paths.append(f"{scripts_root}/{script}" if scripts_root else script)
        dupes = [p for p in paths if paths.count(p) > 1]
        self.assertEqual(set(dupes), set(), f"duplicate script paths: {set(dupes)}")


if __name__ == "__main__":
    unittest.main()
