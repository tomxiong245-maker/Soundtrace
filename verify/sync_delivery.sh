#!/usr/bin/env bash
# sync_delivery.sh · 剪辑项目 → 交付-2026-8-17 双向同步
# 用户 2026-08-18 明确 "两个文件夹里的规则都要更新" · CLAUDE.md §20
#
# 用法: bash sync_delivery.sh
# 每次改代码/规则/tool/skill 后必跑
set -e
PROJ="/Users/renting/Desktop/minglue/剪辑项目"
DELIV="/Users/renting/Desktop/minglue/交付-2026-8-17"

echo "=== 同步 剪辑项目 → 交付-2026-8-17"

# CLAUDE.md (2 处)
cp "$PROJ/CLAUDE.md" "$DELIV/docs/CLAUDE.md"
cp "$PROJ/CLAUDE.md" "$DELIV/src/CLAUDE.md"
echo "  ✓ CLAUDE.md × 2"

# tools.json
cp "$PROJ/main/tools/tools.json" "$DELIV/config/tools.json"
echo "  ✓ tools.json"

# labels_lake
cp "$PROJ/main/knowledge/labels_lake.json" "$DELIV/labels/labels_lake.json"
cp "$PROJ/main/knowledge/labels_lake.json" "$DELIV/config/labels_lake.json"
echo "  ✓ labels_lake.json × 2"

# cut_parameters (PARAMETER 知识 · CLAUDE.md §21)
cp "$PROJ/main/knowledge/cut_parameters.json" "$DELIV/config/cut_parameters.json" 2>/dev/null || true
cp "$PROJ/main/knowledge/cut_parameters.json" "$DELIV/src/main/knowledge/cut_parameters.json" 2>/dev/null || true
echo "  ✓ cut_parameters.json × 2"

# experience.md (单一经验 SOT · 用户 2026-08-18 明确 "我就要一个经验的文件")
cp "$PROJ/main/knowledge/experience.md" "$DELIV/config/experience.md" 2>/dev/null || true
cp "$PROJ/main/knowledge/experience.md" "$DELIV/src/main/knowledge/experience.md" 2>/dev/null || true
echo "  ✓ experience.md × 2"

# session_feedback (单一 SOT + archived)
mkdir -p "$DELIV/labels/session_feedback" "$DELIV/src/main/knowledge/session_feedback"
cp "$PROJ/main/knowledge/session_feedback/"*.jsonl* "$DELIV/labels/session_feedback/" 2>/dev/null
cp "$PROJ/main/knowledge/session_feedback/"*.jsonl* "$DELIV/src/main/knowledge/session_feedback/" 2>/dev/null
echo "  ✓ session_feedback × 2"

# speaker_maps
mkdir -p "$DELIV/src/main/knowledge/speaker_maps"
cp "$PROJ/main/knowledge/speaker_maps/"*.json "$DELIV/src/main/knowledge/speaker_maps/" 2>/dev/null || true
echo "  ✓ speaker_maps"

# orchestrator (主目录 python 全部)
mkdir -p "$DELIV/src/main/orchestrator/tests"
cp "$PROJ/main/orchestrator/"*.py "$DELIV/src/main/orchestrator/" 2>/dev/null
cp "$PROJ/main/orchestrator/tests/"*.py "$DELIV/src/main/orchestrator/tests/" 2>/dev/null
echo "  ✓ main/orchestrator/*.py"

# skills 全部 (含 flow_boundary + SKILL.md)
mkdir -p "$DELIV/src/skills"
for skill in "$PROJ/skills/"*/; do
    name=$(basename "$skill")
    mkdir -p "$DELIV/src/skills/$name"
    cp "$skill/"*.md "$DELIV/src/skills/$name/" 2>/dev/null || true
done
echo "  ✓ skills"

# 主流程 pipeline script
cp "$PROJ/稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py" "$DELIV/scripts/"
cp "$PROJ/稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py" \
   "$DELIV/src/稳定生产/challengers/e2e-auto-runner-v1/scripts/" 2>/dev/null
echo "  ✓ run_end_to_end.py × 2"

# filler_global_pause 关键 script
mkdir -p "$DELIV/src/稳定生产/challengers/filler-global-pause-v1/scripts"
mkdir -p "$DELIV/src/稳定生产/challengers/filler-global-pause-v1/tests"
cp "$PROJ/稳定生产/challengers/filler-global-pause-v1/scripts/build_filler_global_pause_review_source.py" \
   "$DELIV/src/稳定生产/challengers/filler-global-pause-v1/scripts/"
cp "$PROJ/稳定生产/challengers/filler-global-pause-v1/tests/"*.py \
   "$DELIV/src/稳定生产/challengers/filler-global-pause-v1/tests/" 2>/dev/null || true
echo "  ✓ filler-global-pause-v1"

# autocut_gate script
mkdir -p "$DELIV/src/稳定生产/challengers/autocut-gate-v1/scripts"
cp "$PROJ/稳定生产/challengers/autocut-gate-v1/scripts/apply_autocut_gate.py" \
   "$DELIV/src/稳定生产/challengers/autocut-gate-v1/scripts/"
echo "  ✓ autocut-gate-v1"

# docs
mkdir -p "$DELIV/docs"
cp "$PROJ/docs/"*.md "$DELIV/docs/" 2>/dev/null || true
echo "  ✓ docs"

echo ""
echo "=== 同步完成. 交付包体积:"
du -sh "$DELIV"