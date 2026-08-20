"""tool_lookup · single source of truth for orchestrator subprocess resolution.

Before this module existed, `delivery_orchestrator.py` had 12 hardcoded
`PROJECT_ROOT / "path/to/script.py"` constants scattered across a 4640-line file,
while `main/tools/tools.json` declared 18 tools that nobody imported. This module
closes the gap: **tools.json is the manifest, this file is the reader.**

Every subprocess call in orchestrator should go through:

    from tools.tool_lookup import script_for
    subprocess.run([sys.executable, str(script_for("label_learning_driver")), ...])

Adding a new capability:
    1. Add an entry to `main/tools/tools.json` with `name` + (`script` under
       scripts_root OR `full_path` relative to project root).
    2. Import script_for("new_name") in orchestrator. No more constants.

The `test_orchestrator_uses_tools_json.py` contract enforces this: any hardcoded
script path in orchestrator that is NOT in tools.json → test FAIL.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_JSON = PROJECT_ROOT / "main/tools/tools.json"


class ToolLookupError(RuntimeError):
    """Raised when a tool name is unknown or its script does not exist on disk."""


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    return json.loads(TOOLS_JSON.read_text(encoding="utf-8"))


def _tool_relpath(tool: dict[str, Any], scripts_root: str) -> str:
    """Return the project-root-relative path for a tool entry."""
    if fp := tool.get("full_path"):
        return fp
    script = tool.get("script")
    if not script:
        raise ToolLookupError(f"tool {tool.get('name')!r} has neither full_path nor script")
    return f"{scripts_root}/{script}" if scripts_root else script


def all_tools() -> list[dict[str, Any]]:
    """Return the full list of tool declarations from tools.json."""
    return list(_manifest().get("tools", []))


def tool(name: str) -> dict[str, Any]:
    """Return the tool declaration by name; raise if unknown."""
    for t in all_tools():
        if t.get("name") == name:
            return t
    known = sorted(t.get("name", "?") for t in all_tools())
    raise ToolLookupError(f"unknown tool {name!r}; known: {known}")


def script_for(name: str) -> Path:
    """Resolve a tool name to its absolute script path. Fail closed if missing."""
    t = tool(name)
    scripts_root = _manifest().get("scripts_root", "")
    relpath = _tool_relpath(t, scripts_root)
    absolute = (PROJECT_ROOT / relpath).resolve()
    if not absolute.exists():
        raise ToolLookupError(
            f"tool {name!r} declared at {relpath} but the file does not exist on disk"
        )
    return absolute


def tool_name_for_script(relpath: str) -> str | None:
    """Reverse lookup: given a project-root-relative script path, return the tool_name.
    Used by contract tests to prove orchestrator-hardcoded paths map back to a tool."""
    scripts_root = _manifest().get("scripts_root", "")
    for t in all_tools():
        if _tool_relpath(t, scripts_root) == relpath:
            return t.get("name")
    return None


def verify_manifest() -> list[str]:
    """Check every tool's declared script exists on disk. Return list of errors
    (empty list = healthy). Doesn't raise; caller decides whether to fail closed."""
    errors: list[str] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    scripts_root = _manifest().get("scripts_root", "")
    for t in all_tools():
        name = t.get("name")
        if not name:
            errors.append(f"tool with no name: {t}")
            continue
        if name in seen_names:
            errors.append(f"duplicate tool name: {name}")
        seen_names.add(name)
        try:
            relpath = _tool_relpath(t, scripts_root)
        except ToolLookupError as exc:
            errors.append(str(exc))
            continue
        if relpath in seen_paths:
            errors.append(f"duplicate script path (tool {name!r}): {relpath}")
        seen_paths.add(relpath)
        absolute = PROJECT_ROOT / relpath
        if not absolute.exists():
            errors.append(f"tool {name!r} script missing: {relpath}")
    return errors


def clear_cache() -> None:
    """For tests: drop the manifest cache so a fresh read picks up changes."""
    _manifest.cache_clear()
