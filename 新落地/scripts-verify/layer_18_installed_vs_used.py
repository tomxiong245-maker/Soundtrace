#!/usr/bin/env python3
"""layer_18_installed_vs_used.py · CLAUDE.md §15 承诺的扫描器
用途：
  A. 装了但没用 (installed-but-unused): pip/conda list 里存在，但项目源码里从未 import → warn
  B. 用了但没登记 (used-but-undeclared): 项目源码 import，但没登记到 tools.json 顶层 runtime_dependencies 或某 tool 的 runtime_dependencies → fail

调用：`python3 scripts/verify/layer_18_installed_vs_used.py`
退出码：0 = 通过；1 = 有 undeclared；2 = 有 unused（warn 级）
"""
from __future__ import annotations
import json, pathlib, re, subprocess, sys
from collections import defaultdict

ROOT = pathlib.Path("/Users/renting/Desktop/minglue/剪辑项目")

# ---- 白名单：即使装了没用也不 warn（标准库、构建工具、编辑器需要的东西）
WHITELIST_UNUSED = {
    "pip", "setuptools", "wheel", "packaging", "typing_extensions",
    "certifi", "charset-normalizer", "idna", "urllib3", "requests",
    "click", "attrs", "iniconfig", "pluggy", "tomli", "pytest",
}

# ---- 项目实际扫描的目录（源代码所在处）
SCAN_DIRS = [
    "main/orchestrator",
    "main/tools",
    "稳定生产/challengers",
    "端到端学习剪辑/代码",
    "scripts",
]

# ---- 已知别名映射（pip 包名 vs import 名）
PIP_TO_IMPORT = {
    "faster-whisper": "faster_whisper",
    "python-dateutil": "dateutil",
    "pillow": "PIL",
    "scikit-learn": "sklearn",
    "beautifulsoup4": "bs4",
    "pyyaml": "yaml",
    "spacy-pkuseg": "pkuseg",
    "montreal-forced-aligner": "montreal_forced_aligner",
    "audioop-lts": "audioop",
    "opencv-python": "cv2",
    "audioread": "audioread",
    "typing-extensions": "typing_extensions",
}

def get_installed_packages() -> set[str]:
    """跑 pip list --format=json 拿装了的包名（lowercase）"""
    try:
        out = subprocess.check_output(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            stderr=subprocess.DEVNULL, timeout=30
        )
        pkgs = json.loads(out)
        return {p["name"].lower() for p in pkgs}
    except Exception as e:
        print(f"[SKIP] 无法跑 pip list: {e}")
        return set()

def scan_source_imports() -> set[str]:
    """扫源码里所有 import 语句，返回顶层模块名集合"""
    imports = set()
    pat = re.compile(r'^\s*(?:from\s+([a-zA-Z_][\w.]*)|import\s+([a-zA-Z_][\w.]*(?:\s*,\s*[a-zA-Z_][\w.]*)*))', re.M)
    for d in SCAN_DIRS:
        for py in (ROOT / d).rglob("*.py"):
            try:
                txt = py.read_text(errors="ignore")
            except Exception:
                continue
            for m in pat.finditer(txt):
                mod = m.group(1) or m.group(2)
                if mod:
                    # 拆逗号形式 `import a, b`
                    for x in re.split(r'[,\s]+', mod):
                        x = x.strip()
                        if not x: continue
                        top = x.split(".")[0]
                        imports.add(top.lower())
    return imports

def get_declared_deps() -> set[str]:
    """读 tools.json 顶层 runtime_dependencies + 每 tool 的 runtime_dependencies"""
    d = json.loads((ROOT / "main/tools/tools.json").read_text())
    declared = set()
    for r in d.get("runtime_dependencies", []):
        if isinstance(r, dict) and "name" in r:
            declared.add(r["name"].lower())
        elif isinstance(r, str):
            declared.add(r.lower())
    for t in d.get("tools", []):
        for r in t.get("runtime_dependencies", []) or []:
            # 依赖声明可能是 "conda pydub audioop-lts" 这种复合串 · split 取每个字
            for x in re.split(r'[\s:>=<,]+', r):
                x = x.strip().lower()
                if x and not x.isdigit() and x not in {"conda", "pip", "conda-forge", "channel"}:
                    declared.add(x)
    return declared

STDLIB = {
    "sys", "os", "json", "re", "pathlib", "typing", "collections", "itertools", "functools",
    "math", "time", "datetime", "argparse", "subprocess", "shutil", "tempfile", "hashlib",
    "logging", "textwrap", "io", "warnings", "csv", "unittest", "importlib", "inspect",
    "traceback", "contextlib", "urllib", "socket", "threading", "asyncio", "concurrent",
    "dataclasses", "abc", "enum", "copy", "pickle", "base64", "html", "xml", "email",
    "glob", "fnmatch", "random", "statistics", "uuid", "operator", "string", "struct",
    "queue", "heapq", "bisect", "multiprocessing", "signal", "__future__", "difflib",
    "codecs", "locale", "gzip", "zipfile", "tarfile", "sqlite3", "http", "wave",
}

def main():
    print("[layer 18] pip list ...")
    installed = get_installed_packages()
    print(f"  installed packages: {len(installed)}")

    print("[layer 18] 扫源码 import ...")
    imports = scan_source_imports()
    imports = {i for i in imports if i not in STDLIB}
    print(f"  distinct top-level imports (去掉 stdlib): {len(imports)}")

    print("[layer 18] 读 tools.json declared ...")
    declared = get_declared_deps()
    print(f"  declared in tools.json: {len(declared)}")

    # A · installed 但源码里没 import
    imports_normalized = set()
    for i in imports:
        imports_normalized.add(i)
        # pip 名的 dash 版本
        imports_normalized.add(i.replace("_", "-"))
    unused_installed = installed - imports_normalized - WHITELIST_UNUSED - STDLIB
    # 反向映射：如果 pip 名有别名，检查别名是否在 imports
    unused_installed = {p for p in unused_installed
                         if PIP_TO_IMPORT.get(p, p).lower() not in imports}

    # B · 源码 import 但没在 declared
    imports_pip = set()
    for i in imports:
        # 找是否是某个 pip 别名的目标
        found_alias = False
        for pip_name, import_name in PIP_TO_IMPORT.items():
            if import_name.lower() == i:
                imports_pip.add(pip_name.lower()); found_alias = True; break
        if not found_alias:
            imports_pip.add(i)

    undeclared = imports_pip - declared - STDLIB - {"__main__"}
    # 过滤：只报"确认已装"的 undeclared（否则是本地模块 import）
    undeclared_installed = undeclared & installed
    undeclared_installed |= {i for i in undeclared if any(
        PIP_TO_IMPORT.get(p, p).lower() == i for p in installed
    )}

    print()
    print(f"[layer 18] A · installed 但源码没 import: {len(unused_installed)}")
    for p in sorted(unused_installed)[:20]:
        print(f"    - {p}")
    if len(unused_installed) > 20:
        print(f"    ... 还有 {len(unused_installed)-20} 项")

    print()
    print(f"[layer 18] B · 源码 import 但 tools.json 没登记 (且确认已装): {len(undeclared_installed)}")
    for p in sorted(undeclared_installed):
        print(f"    - {p}")

    print()
    if undeclared_installed:
        print("[FAIL] 源码用了但 tools.json 没登记的包 → 违反 CLAUDE.md §11 & §15")
        sys.exit(1)
    if unused_installed:
        print("[WARN] 装了但没用的包 → 违反 CLAUDE.md §15 · 建议清理或补 import")
        sys.exit(2)
    print("[PASS] installed vs used 全对齐")
    sys.exit(0)

if __name__ == "__main__":
    main()
