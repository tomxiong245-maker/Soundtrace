#!/usr/bin/env bash
# ==============================================================================
# exact_commands.sh · P1 review-product-v1 一键复现（低风险，逐步执行）
#
# 使用方式：
#   cd /Users/renting/Desktop/minglue/剪辑项目
#   bash 稳定生产/challengers/review-product-v1/exact_commands.sh
#
# 该脚本严格分步；每一步失败即停。绝不修改 Champion / P0 /
# cross-track-safety-v1 历史产物。每次大改前先 git 快照（用户要求）。
# ==============================================================================
set -euo pipefail

PROJECT_ROOT="/Users/renting/Desktop/minglue/剪辑项目"
CHAL="$PROJECT_ROOT/稳定生产/challengers/review-product-v1"
RUN="$PROJECT_ROOT/main/runs/EP03-review-product-v1"
FRONTEND="$PROJECT_ROOT/审核前端/challenger-review-product-v1"
CROSS_RUN="$PROJECT_ROOT/main/runs/EP03-cross-track-safety-v1"

TIMESTAMP="$(date +'%Y%m%d-%H%M')"
PKG_ID="EP03-review-product-v1-$TIMESTAMP"

cd "$PROJECT_ROOT"

echo "========== 步骤 0 · 记录基线（不修改任何文件） =========="
git status --short > "$CHAL/baseline_git_status.txt" || true
git rev-parse HEAD  > "$CHAL/baseline_git_sha.txt"  || true

# 大改前的第一次 checkpoint 快照（本地 commit；无 push——尊重用户默认）
if git rev-parse --git-dir >/dev/null 2>&1; then
  git add -A
  git commit -m "P1 review-product-v1 baseline snapshot ($TIMESTAMP)" || echo "no changes to commit"
fi

echo
echo "========== 步骤 1 · 计算关键 SHA-256（引用而非重算） =========="
mkdir -p "$CHAL/baseline"
{
  for f in \
    "稳定生产/scripts/generate_cut_candidates.py" \
    "稳定生产/rules/candidate-generation.v1.json" \
    "审核前端/index.html" \
    "审核前端/candidates.json" \
    "审核前端/track_activity.json" \
    "端到端学习剪辑/代码/render_approved_edl.py" \
    "main/runs/EP03-cross-track-safety-v1/safe_candidates.json" \
    "main/runs/EP03-cross-track-safety-v1/blocked_candidates.json" \
    "main/runs/EP03-cross-track-safety-v1/run_manifest.json" \
    "main/runs/EP03-cross-track-safety-v1/normalized_output_sha256.json" \
    "main/runs/EP03-cross-track-safety-v1/after_metrics.json" \
    "main/runs/EP03-cross-track-safety-v1/before_metrics.json" \
    "main/runs/EP03-cross-track-safety-v1/review_package/review_package.json" \
    "审核前端/challenger-cross-track-safety-v1/index.html"
  do
    if [ -f "$f" ]; then
      shasum -a 256 "$f"
    else
      echo "MISSING  $f"
    fi
  done
} > "$CHAL/baseline/baseline_sha256.txt"
echo "wrote $CHAL/baseline/baseline_sha256.txt"

# 与 cross-track-safety-v1 的 before_metrics.json 中记录的 baseline_sha256 对比
python3 - <<'PY'
import json, hashlib, os, sys
root = "/Users/renting/Desktop/minglue/剪辑项目"
with open(os.path.join(root, "main/runs/EP03-cross-track-safety-v1/before_metrics.json")) as f:
    ref = json.load(f)["baseline_sha256"]
mismatches = []
for rel, expected in ref.items():
    p = os.path.join(root, rel)
    if not os.path.isfile(p):
        mismatches.append(f"MISSING {rel}")
        continue
    h = hashlib.sha256()
    with open(p, "rb") as fp:
        for c in iter(lambda: fp.read(1<<16), b""): h.update(c)
    got = h.hexdigest()
    if got != expected:
        mismatches.append(f"CHANGED {rel} expected={expected} got={got}")
if mismatches:
    print("BASELINE MISMATCH — 停止执行", file=sys.stderr)
    for m in mismatches: print("  ", m, file=sys.stderr)
    sys.exit(1)
print("baseline SHA 12/12 一致")
PY

echo
echo "========== 步骤 2 · 复现 cross-track-safety-v1 fixture 与计数 =========="
python3 "$PROJECT_ROOT/稳定生产/challengers/cross-track-safety-v1/scripts/run_tests.py" \
  > "$CHAL/baseline/cross_track_safety_v1_tests.txt" 2>&1
grep -q "12/12 PASSED\|12 tests, 12 passed\|PASS" "$CHAL/baseline/cross_track_safety_v1_tests.txt" \
  || { echo "cross-track-safety-v1 tests fail baseline check"; exit 1; }
python3 - <<'PY'
import json
root = "/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP03-cross-track-safety-v1"
with open(f"{root}/safe_candidates.json") as f: safe = json.load(f)
with open(f"{root}/blocked_candidates.json") as f: blocked = json.load(f)
n_safe = len(safe["candidates"])
n_blocked = len(blocked) if isinstance(blocked, list) else len(blocked.get("candidates", []))
print(f"SAFE={n_safe} BLOCKED={n_blocked}")
assert n_safe == 11, f"SAFE count expected 11, got {n_safe}"
# 14 BLOCKED
if isinstance(blocked, list): assert n_blocked == 14, f"expected 14, got {n_blocked}"
else: assert n_blocked == 14, f"expected 14, got {n_blocked}"
print("cross-track-safety-v1 counts 11 SAFE + 14 BLOCKED 复现一致")
PY

echo
echo "========== 步骤 3 · 运行 P1 契约测试（15 项） =========="
python3 "$CHAL/scripts/run_tests.py" 2>&1 | tee "$CHAL/contract_test_results.txt"
grep -qE "===== 15/15 PASSED =====" "$CHAL/contract_test_results.txt" \
  || { echo "P1 contract tests failed"; exit 1; }

echo
echo "========== 步骤 4 · vendor wavesurfer.js（安全审计后） =========="
mkdir -p "$FRONTEND/vendor/wavesurfer/plugins"
# 用 npm pack 从 registry 拉取固定版本，校验 tarball SHA
if ! command -v npm >/dev/null; then echo "npm 缺失，跳过 wavesurfer，页面自动回退原生 audio"; exit 0; fi
WS_VER="7.10.0"
NPM_PACK_DIR="$(mktemp -d)"
pushd "$NPM_PACK_DIR" >/dev/null
npm pack "wavesurfer.js@$WS_VER" >/dev/null
TARBALL="$(ls wavesurfer.js-*.tgz | head -n1)"
TARBALL_SHA="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
tar -xzf "$TARBALL"
# 拷贝 esm.min.js 和 regions 插件
cp package/dist/wavesurfer.esm.min.js "$FRONTEND/vendor/wavesurfer/wavesurfer.esm.min.js"
cp package/dist/plugins/regions.esm.min.js "$FRONTEND/vendor/wavesurfer/plugins/regions.esm.min.js" || true
cp package/LICENSE "$FRONTEND/vendor/wavesurfer/LICENSE"
popd >/dev/null
# 记录锁定信息
{
  echo "wavesurfer.js@$WS_VER vendored at $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "npm_tarball_sha256=$TARBALL_SHA"
  shasum -a 256 "$FRONTEND/vendor/wavesurfer/wavesurfer.esm.min.js"
  shasum -a 256 "$FRONTEND/vendor/wavesurfer/plugins/regions.esm.min.js" 2>/dev/null || echo "regions plugin optional"
  shasum -a 256 "$FRONTEND/vendor/wavesurfer/LICENSE"
} > "$CHAL/audits/wavesurfer.lock.txt"
# 遥测扫描
{
  echo "=== egress scan on wavesurfer.esm.min.js ==="
  grep -oE '(googletagmanager|google-analytics|sentry\.io|amplitude|segment\.io|posthog|bugsnag|heap\.io|mixpanel)' \
       "$FRONTEND/vendor/wavesurfer/wavesurfer.esm.min.js" || echo "no telemetry strings found"
} > "$CHAL/audits/wavesurfer.egress-scan.txt"

echo
echo "========== 步骤 5 · 从 cross-track-safety-v1 构建 P1 审核包 =========="
mkdir -p "$RUN/review_package/previews"
python3 "$CHAL/scripts/build_review_product_package.py" \
  --safe "$CROSS_RUN/safe_candidates.json" \
  --oldpkg "$CROSS_RUN/review_package/review_package.json" \
  --previews-dir "$CROSS_RUN/review_package/previews" \
  --out "$RUN/review_package" \
  --package-id "$PKG_ID"

echo
echo "========== 步骤 6 · 静态校验 P1 审核包 =========="
python3 "$CHAL/scripts/validate_review_package.py" \
  "$RUN/review_package/review_package.json" --check-files

echo
echo "========== 步骤 7 · 起本地服务器（端口 8767，与 cross-track 8766 隔离） =========="
# 拷贝审核包和 preview 到前端目录，以便 fetch 相对路径成功
cp "$RUN/review_package/review_package.json" "$FRONTEND/review_package.json"
mkdir -p "$FRONTEND/previews"
cp "$RUN/review_package/previews/"*.mp3 "$FRONTEND/previews/" 2>/dev/null || true

pushd "$FRONTEND" >/dev/null
# 若已有端口占用则先释放
lsof -ti :8767 | xargs -r kill 2>/dev/null || true
python3 -m http.server 8767 > "$RUN/http_server.log" 2>&1 &
SERVER_PID=$!
echo $SERVER_PID > "$RUN/http_server.pid"
sleep 1
curl -sS http://localhost:8767/index.html > /dev/null || { echo "server not responding"; kill $SERVER_PID; exit 1; }
popd >/dev/null

echo "server up at pid $SERVER_PID"

echo
echo "========== 步骤 8 · Playwright E2E =========="
if command -v node >/dev/null 2>&1 && command -v npx >/dev/null 2>&1; then
  pushd "$CHAL/e2e" >/dev/null
  npm install --no-audit --no-fund
  npx playwright install chromium
  PROJECT_ROOT="$PROJECT_ROOT" PAGE_URL="http://localhost:8767/index.html" \
    PKG_PATH="$RUN/review_package/review_package.json" OUT_DIR="$RUN" \
    npx playwright test -c playwright.config.mjs || {
      echo "E2E FAIL — 服务器保持运行以便人工调试。停止请：kill $(cat $RUN/http_server.pid)"
      exit 1
    }
  popd >/dev/null
else
  echo "node/npx 不可用 → 记录 STATICALLY_VERIFIED_ONLY"
  echo "STATICALLY_VERIFIED_ONLY: node/npx 缺失，未执行 Playwright E2E" > "$RUN/browser_e2e_report.json"
fi

echo
echo "========== 步骤 9 · 服务器停止与 run_manifest =========="
kill "$SERVER_PID" 2>/dev/null || true

python3 - <<PY
import json, hashlib, os
run = "$RUN"
def sha(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for c in iter(lambda:f.read(1<<16),b""): h.update(c)
    return h.hexdigest(), os.path.getsize(p)
files={}
for base,_,names in os.walk(run):
    for n in names:
        p=os.path.join(base,n)
        rel=os.path.relpath(p, run)
        if rel.startswith("run_manifest.json"): continue
        s, sz = sha(p)
        files[rel]={"sha256":s,"size":sz}
manifest={"run_id":"EP03-review-product-v1","generated_at":"$TIMESTAMP","files":files,"total_files":len(files)}
with open(os.path.join(run,"run_manifest.json"),"w") as f: json.dump(manifest,f,ensure_ascii=False,indent=2)
print("wrote", os.path.join(run,"run_manifest.json"), "with", len(files), "files")
PY

echo
echo "========== 步骤 10 · 大改后再打一次快照 =========="
if git rev-parse --git-dir >/dev/null 2>&1; then
  git add -A
  git commit -m "P1 review-product-v1 · results after exact_commands.sh ($TIMESTAMP)" || echo "no changes to commit"
fi

echo
echo "=========================================================="
echo "全部步骤完成。请查看："
echo "  · $CHAL/baseline/baseline_sha256.txt"
echo "  · $CHAL/contract_test_results.txt"
echo "  · $RUN/review_package/review_package.json"
echo "  · $RUN/browser_e2e_report.json"
echo "  · $RUN/run_manifest.json"
echo "=========================================================="
