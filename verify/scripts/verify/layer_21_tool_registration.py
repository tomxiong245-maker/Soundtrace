#!/usr/bin/env python3
"""verify.sh · 第 21 层 · §11 未登记即报错 (静态)

**规则**（对应 CLAUDE.md §11 · 禁自由发挥）：
`main/orchestrator/*.py` 里出现的每一个 .py 必须满足以下之一：
  1. 在 `main/tools/tools.json` 的 `tools[].full_path` 或 `tools[].script` 中登记
  2. 在 `main/tools/tool_registration_allowlist.json` 的某个 bucket 中列出
  3. 文件名以 `_`、`test_` 开头，或名为 `__init__.py`

**违反 = agent 自由发挥、在受管目录里加了没登记的脚本 → 触发 FAIL。**

用法：
    python3 scripts/verify/layer_21_tool_registration.py           # 严格校验，返回码非零即 FAIL
    python3 scripts/verify/layer_21_tool_registration.py --report  # 只报告，不失败（诊断用）

未来（Session 2 后）：把 `稳定生产/challengers/*/scripts/*.py` 也纳入校验，规则是 Challenger 晋升后其脚本必须登记或迁移。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_project_root() -> Path:
    """从脚本位置往上找，直到看到 CLAUDE.md。"""
    d = Path(__file__).resolve().parent
    while d != d.parent:
        if (d / "CLAUDE.md").is_file() or (d / "项目主文档/CLAUDE.md").is_file():
            return d
        d = d.parent
    # fallback：脚本所在目录往上两层
    return Path(__file__).resolve().parents[2]


ROOT = _find_project_root()

# 布局自适应：live 布局 tools.json 在 ROOT/main/... · 交付布局在 ROOT/代码/main/...
_CODE_PREFIX = "代码/" if (ROOT / "代码/main/tools/tools.json").is_file() else ""


def load_registered_scripts() -> tuple[set[str], set[str]]:
    """从 tools.json 收集全部登记脚本的 (相对路径, 文件名) 两套 key。"""
    tj = ROOT / f"{_CODE_PREFIX}main/tools/tools.json"
    if not tj.is_file():
        raise SystemExit(f"[FAIL] tools.json not found at {tj}")
    doc = json.loads(tj.read_text())
    tools = doc.get("tools", doc) if isinstance(doc, dict) else doc
    rel_paths: set[str] = set()
    basenames: set[str] = set()
    for t in tools:
        for k in ("full_path", "script"):
            v = t.get(k)
            if v:
                rel_paths.add(v)
                basenames.add(Path(v).name)
    return rel_paths, basenames


def load_allowlist() -> set[str]:
    """从 tool_registration_allowlist.json 收集全部白名单文件（相对路径）。"""
    aw = ROOT / f"{_CODE_PREFIX}main/tools/tool_registration_allowlist.json"
    if not aw.is_file():
        return set()
    doc = json.loads(aw.read_text())
    out: set[str] = set()
    for bucket in (doc.get("buckets") or {}).values():
        for f in bucket.get("files") or []:
            out.add(f)
    return out


def scan_directory(rel_dir: str) -> list[Path]:
    """列出目录下的 .py 文件（不递归，跳过 _/test_/__init__）。

    交付布局下 rel_dir 会自动加 `代码/` 前缀（因为 main/orchestrator 在 代码/ 下）。
    """
    d = ROOT / f"{_CODE_PREFIX}{rel_dir}"
    if not d.is_dir():
        return []
    result = []
    for pyf in sorted(d.glob("*.py")):
        n = pyf.name
        if n.startswith("_") or n.startswith("test_") or n == "__init__.py":
            continue
        result.append(pyf)
    return result


def check_orchestrator_dir() -> list[str]:
    """检查 main/orchestrator/*.py。返回未登记文件列表（相对路径）。

    路径归一化：无论 live 布局（`main/orchestrator/xxx.py`）还是交付布局（`代码/main/orchestrator/xxx.py`），
    与 tools.json / allowlist 比较时都剥离前导 `代码/` · 让 allowlist 只需声明 live 风格路径。
    """
    registered_paths, registered_basenames = load_registered_scripts()
    allowlist = load_allowlist()

    def _normalize(rel: str) -> str:
        return rel[3:] if rel.startswith("代码/") else rel

    unregistered: list[str] = []
    for pyf in scan_directory("main/orchestrator"):
        rel_raw = str(pyf.relative_to(ROOT))
        rel = _normalize(rel_raw)
        basename = pyf.name
        if rel in registered_paths or rel_raw in registered_paths:
            continue
        if basename in registered_basenames:
            continue
        if rel in allowlist or rel_raw in allowlist:
            continue
        unregistered.append(rel_raw)
    return unregistered


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只报告，不失败")
    args = ap.parse_args()

    unreg = check_orchestrator_dir()

    if not unreg:
        print("[PASS] 第 21 层 · main/orchestrator/*.py 全部登记或在白名单内")
        return 0

    print(f"[FAIL] 第 21 层 · main/orchestrator/ 下有 {len(unreg)} 个未登记且未在白名单的 .py：")
    for u in unreg:
        print(f"  - {u}")
    print()
    print("修复：")
    print("  1) 若这是新 tool → 登记到 main/tools/tools.json")
    print("  2) 若是共享库 / dev 脚本 / 编排入口 → 加到 main/tools/tool_registration_allowlist.json 对应 bucket")
    print("  3) 若是废弃文件 → 直接删除或改名为 _<name>.py.deprecated")
    return 1 if not args.report else 0


if __name__ == "__main__":
    sys.exit(main())
