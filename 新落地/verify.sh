#!/usr/bin/env bash
# verify.sh · minglue 剪辑项目 静态校验器
# CLAUDE.md §15 承诺的分层扫描 · 本文件是骨架 · 补齐历史欠账
# 2026-08-18 初版 · 落地第 12/13/18 层 · 其他层作 backlog

set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[0;33m'
NC='\033[0m'

pass()  { echo -e "${GRN}[PASS]${NC} $1"; }
warn()  { echo -e "${YEL}[WARN]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo "=== verify.sh · minglue 剪辑项目 静态校验（骨架版 · 三层） ==="
echo ""

# ============================================================
# 第 12 层 · tools.json full_path 可达性
# ============================================================
echo "--- 第 12 层 · tools.json full_path 可达性 ---"
python3 <<'PY' || fail "第 12 层：tools.json full_path 有缺失"
import json, pathlib, sys
ROOT = pathlib.Path.cwd()
d = json.loads((ROOT / "main/tools/tools.json").read_text())
scripts_root = d.get("scripts_root", "端到端学习剪辑/代码")
missing = []
for t in d["tools"]:
    if "full_path" in t:
        p = ROOT / t["full_path"]
    elif "script" in t:
        p = ROOT / scripts_root / t["script"]
    else:
        missing.append((t["name"], "no full_path or script")); continue
    if not p.exists():
        missing.append((t["name"], str(p)))
if missing:
    print(f"[FAIL] {len(missing)} tool 文件不存在或路径缺失:")
    for n, path in missing: print(f"  - {n}: {path}")
    sys.exit(1)
print(f"[PASS] 全部 {len(d['tools'])} 项 tool 的 script/full_path 都可达")
PY
pass "tools.json full_path 可达性"

# ============================================================
# 第 13 层 · skills SKILL.md 存在 + status 合法
# ============================================================
echo ""
echo "--- 第 13 层 · skills SKILL.md 结构 ---"
python3 <<'PY' || fail "第 13 层：skills 结构异常"
import pathlib, sys, re
ROOT = pathlib.Path.cwd()
skills_dir = ROOT / "skills"
if not skills_dir.is_dir():
    print("[FAIL] skills/ 目录不存在"); sys.exit(1)
active = deprecated = index = 0
bad = []
for d in sorted(skills_dir.iterdir()):
    if not d.is_dir(): continue
    sk = d / "SKILL.md"
    if not sk.exists():
        bad.append((d.name, "no SKILL.md")); continue
    txt = sk.read_text()
    m = re.search(r'^status:\s*(\w+)', txt, re.M)
    if not m:
        bad.append((d.name, "no status frontmatter")); continue
    st = m.group(1)
    if st == "active": active += 1
    elif st == "deprecated": deprecated += 1
    elif st == "index": index += 1
    else:
        bad.append((d.name, f"unknown status: {st}"))
if bad:
    print(f"[FAIL] {len(bad)} skill 结构异常:")
    for n, r in bad: print(f"  - {n}: {r}")
    sys.exit(1)
print(f"[PASS] skills 结构 OK · active={active} deprecated={deprecated} index={index}")
PY
pass "skills SKILL.md 结构"

# ============================================================
# 第 18 层 · installed-vs-used 扫描（CLAUDE.md §15）
# ============================================================
echo ""
echo "--- 第 18 层 · installed vs used 扫描 ---"
python3 scripts/verify/layer_18_installed_vs_used.py || warn "第 18 层：有 installed-but-unused 或 used-but-undeclared"

echo ""
echo -e "${GRN}=== verify.sh 完成 ===${NC}"
echo ""
echo "未落地的层 (backlog):"
echo "  - 1-11 层：输入 QC / audio 门 / EDL identity 一致性 / render 门 / QC / delivery manifest 校验 / ..."
echo "  - 14 层：CLAUDE.md § 编号 grep 对齐"
echo "  - 15 层：session_feedback jsonl 单一 SOT 校验"
echo "  - 16 层：run_identity_sha256 三处一致（run_identity/plan/input_manifest）"
echo "  - 17 层：audit 覆盖率 · 目标 51/51 · 当前 2/51（见 main/tools/audits/BACKLOG.md）"
echo "  - 19 层：deprecated skill banner 存在校验"
echo "  - 20 层：交接点 postcondition 字段校验（顶层 SKILL.md §4b）"
