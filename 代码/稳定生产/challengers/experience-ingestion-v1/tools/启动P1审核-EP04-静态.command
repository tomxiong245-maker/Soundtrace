#!/usr/bin/env bash
# 静态旁路启动 P1 审核（EP04），跳过 build 与 ffmpeg。
# 双击即可；关闭本窗口结束。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUN_DIR="$PROJECT_ROOT/main/runs/EP04-review-product-v2"

echo "P1 审核（静态旁路）· EP04"
echo "  访问：http://127.0.0.1:8767/index.html"
echo "  关闭本窗口停止。"
exec python3 "$SCRIPT_DIR/serve_review_bundle_static.py" \
  --run-dir "$RUN_DIR" \
  --port 8767
