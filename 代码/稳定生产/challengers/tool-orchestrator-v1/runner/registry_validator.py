#!/usr/bin/env python3
"""registry_validator: 静态校验 tool 注册表。

契约（本 Challenger 使用的字段，与 main/tools/tools.json 兼容）:

- schema_version: int (== 1)
- scripts_root_base: "project_root" (受支持) 或省略
- scripts_root: 相对路径字符串
- tools: [] 每个元素含
    - name: str，非空，`^[a-z][a-z0-9_]*$`，全表内唯一
    - description: str (非强制内容，但必须存在且非空)
    - script: str，非空，不得为绝对路径，不得包含 `..`，全表内唯一
    - params: [str, ...] 每个参数是非空字符串，参数名唯一
    - reads_only: bool

校验行为:

- 只做静态校验（读取 JSON、检查字符串、判断脚本文件是否存在），从不执行任何 tool。
- 若 `scripts_root` 是绝对路径，直接报错。
- 若某个 script 在 project_root/scripts_root 下不存在，标为 `missing`；
  但只有 `--require-scripts` 模式下才把 missing 判成 fatal，默认返回诊断即可。

输出: dict，包含
    ok: bool
    errors: [str]
    warnings: [str]
    tools: [{name, ok, script_path, script_exists, params, reads_only, issues:[str]}]

CLI:
    python3 registry_validator.py <registry_json> [--project-root <path>] [--require-scripts]
    退出码：ok=0，出现 errors=1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"registry not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_registry(
    registry: dict[str, Any],
    project_root: Path,
    require_scripts: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    tools_out: list[dict[str, Any]] = []

    if not isinstance(registry, dict):
        return {
            "ok": False,
            "errors": ["registry root is not an object"],
            "warnings": [],
            "tools": [],
        }

    schema_version = registry.get("schema_version")
    if schema_version != 1:
        errors.append(f"unsupported schema_version: {schema_version!r} (expected 1)")

    scripts_root = registry.get("scripts_root")
    scripts_base_path: Path | None = None
    if not isinstance(scripts_root, str) or not scripts_root:
        errors.append("scripts_root missing or not a non-empty string")
        scripts_root = ""
    elif Path(scripts_root).is_absolute():
        errors.append(f"scripts_root must be relative to project_root, got {scripts_root!r}")
    elif ".." in Path(scripts_root).parts:
        errors.append("scripts_root may not contain '..'")
    else:
        # A syntactically relative path can still escape via a symlink. Resolve
        # it once here and require the target to remain under project_root.
        candidate = (project_root / scripts_root).resolve()
        try:
            candidate.relative_to(project_root.resolve())
        except ValueError:
            errors.append(f"scripts_root escapes project_root after resolve: {scripts_root!r}")
        else:
            scripts_base_path = candidate

    scripts_root_base = registry.get("scripts_root_base", "project_root")
    if scripts_root_base not in {"project_root"}:
        errors.append(f"unsupported scripts_root_base: {scripts_root_base!r}")

    tools = registry.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tools must be a non-empty list")
        tools = []

    seen_names: dict[str, int] = {}
    seen_scripts: dict[str, int] = {}

    for idx, t in enumerate(tools):
        issues: list[str] = []
        name = t.get("name") if isinstance(t, dict) else None
        script = t.get("script") if isinstance(t, dict) else None
        params = t.get("params") if isinstance(t, dict) else None
        reads_only = t.get("reads_only") if isinstance(t, dict) else None
        description = t.get("description") if isinstance(t, dict) else None

        if not isinstance(name, str) or not name:
            issues.append("name missing or not a non-empty string")
        else:
            if not NAME_RE.match(name):
                issues.append(f"name {name!r} does not match {NAME_RE.pattern}")
            # Duplicate detection is independent of naming rules: we still want
            # to flag two identical spellings even if both are illegal.
            if name in seen_names:
                issues.append(f"duplicate name (first seen at index {seen_names[name]})")
            else:
                seen_names[name] = idx

        if not isinstance(description, str) or not description.strip():
            issues.append("description missing or empty")

        if not isinstance(script, str) or not script:
            issues.append("script missing or empty")
            script_path = None
            script_exists = False
        else:
            p = Path(script)
            if p.is_absolute():
                issues.append(f"script must be relative, got absolute path {script!r}")
                script_path = None
                script_exists = False
            elif ".." in p.parts:
                issues.append("script path may not contain '..'")
                script_path = None
                script_exists = False
            elif scripts_base_path is None:
                script_path = None
                script_exists = False
            else:
                candidate_script_path = scripts_base_path / script
                resolved_script_path = candidate_script_path.resolve()
                try:
                    resolved_script_path.relative_to(scripts_base_path)
                except ValueError:
                    issues.append(f"script escapes scripts_root after resolve: {script!r}")
                    script_path = None
                    script_exists = False
                else:
                    script_path = resolved_script_path
                    script_exists = script_path.is_file()
                    if not script_exists:
                        if require_scripts:
                            issues.append(f"script not found: {script_path}")
                        else:
                            warnings.append(f"[{name or f'#{idx}'}] script not found: {script_path}")
            # Duplicate script detection regardless of validity.
            if script in seen_scripts:
                issues.append(
                    f"duplicate script path (first seen at index {seen_scripts[script]})"
                )
            else:
                seen_scripts[script] = idx

        if not isinstance(params, list):
            issues.append("params must be a list of strings")
            params_norm: list[str] = []
        else:
            params_norm = []
            seen_p: set[str] = set()
            for pi, pname in enumerate(params):
                if not isinstance(pname, str) or not pname:
                    issues.append(f"params[{pi}] is not a non-empty string")
                elif pname in seen_p:
                    issues.append(f"params[{pi}] {pname!r} duplicated")
                else:
                    seen_p.add(pname)
                    params_norm.append(pname)

        if not isinstance(reads_only, bool):
            issues.append("reads_only must be a bool")

        tool_ok = not issues
        tools_out.append(
            {
                "name": name if isinstance(name, str) else None,
                "index": idx,
                "ok": tool_ok,
                "script": script if isinstance(script, str) else None,
                "script_path": str(script_path) if script_path else None,
                "script_exists": script_exists,
                "params": params_norm,
                "reads_only": reads_only if isinstance(reads_only, bool) else None,
                "issues": issues,
            }
        )
        if not tool_ok:
            errors.append(f"tool[{idx}] {name!r} invalid: {issues}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "tool_count": len(tools),
        "tools": tools_out,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static validator for tool registry")
    parser.add_argument("registry", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--require-scripts", action="store_true")
    args = parser.parse_args(argv)

    try:
        reg = _load_registry(args.registry)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: {args.registry} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    report = validate_registry(reg, project_root=args.project_root, require_scripts=args.require_scripts)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
