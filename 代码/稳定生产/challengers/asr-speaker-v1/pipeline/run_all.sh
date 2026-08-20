#!/usr/bin/env bash
# 一键跑完全流程。M3 上执行：
#   cd 稳定生产/challengers/asr-speaker-v1
#   bash pipeline/install.sh          # 一次性
#   bash pipeline/run_all.sh          # 出报告
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

REPO="$(cd "$HERE/../../.." && pwd)"
BENCH="$REPO/benchmark/EP03-ASR-mini-gold-v1"
GOLD="$BENCH/gold.json"
SEG="$BENCH/segments"
RUN="$REPO/main/runs/EP03-asr-speaker-v1"
SILVER="$RUN/silver"
RAW="$RUN/raw"
NORM="$RUN/normalized"
DIAR="$RUN/diar"

mkdir -p "$SILVER" "$RAW" "$NORM" "$DIAR"

# 用哪个 venv 就用哪个 python；缺失时提示。
VENV_SCORER="$HERE/environment/venv-scorer/bin/python"
VENV_SV="$HERE/environment/venv-sensevoice/bin/python"
VENV_MLX="$HERE/environment/venv-mlx/bin/python"
VENV_DIAR="$HERE/environment/venv-diar/bin/python"
[ -x "$VENV_SCORER" ] || { echo "缺 venv-scorer，先跑 pipeline/install.sh"; exit 1; }

echo "[1/6] 银标（双轨物理真值）"
"$VENV_SCORER" pipeline/silver_truth.py --gold "$GOLD" --segments-dir "$SEG" --out "$SILVER"

echo "[2/6] baseline 切片（复用 Champion faster-whisper）"
"$VENV_SCORER" pipeline/run_asr_baseline.py \
  --freshrun-asr-dir "$REPO/main/runs/EP03-freshrun-20260810-1730/05_asr" \
  --gold "$GOLD" --norm-out "$NORM/faster_whisper_small"

if [ -x "$VENV_SV" ]; then
  echo "[3/6] SenseVoice-Small 推理"
  "$VENV_SV" pipeline/run_asr_sensevoice.py \
    --gold "$GOLD" --segments-dir "$SEG" \
    --raw-out "$RAW/sensevoice_small" --norm-out "$NORM/sensevoice_small" || echo "  SenseVoice 失败，跳过"
else
  echo "[3/6] 跳过 SenseVoice（venv 缺失）"
fi

if [ -x "$VENV_MLX" ]; then
  echo "[4/6] MLX Whisper Turbo 推理"
  "$VENV_MLX" pipeline/run_asr_mlx.py \
    --gold "$GOLD" --segments-dir "$SEG" \
    --raw-out "$RAW/mlx_whisper_turbo" --norm-out "$NORM/mlx_whisper_turbo" || echo "  MLX 失败，跳过"
else
  echo "[4/6] 跳过 MLX Whisper（venv 缺失）"
fi

echo "[5/6] Diarization（自动选：pyannote → sherpa → dual-track）"
DIAR_PY="$VENV_DIAR"
[ -x "$DIAR_PY" ] || DIAR_PY="$VENV_SCORER"  # dual-track fallback 只需 numpy
"$DIAR_PY" pipeline/run_diar.py --gold "$GOLD" --segments-dir "$SEG" --out "$DIAR" --engine auto

echo "[6/6] 五指标评分 + 报告"
"$VENV_SCORER" pipeline/score.py \
  --gold "$GOLD" --silver "$SILVER" \
  --norm-root "$NORM" --diar-root "$DIAR" \
  --out "$RUN/metrics.json"

"$VENV_SCORER" pipeline/build_report.py \
  --metrics "$RUN/metrics.json" \
  --diar-used "$DIAR/USED_ENGINE.txt" \
  --out "$RUN/final_report.md"

echo
echo "=== 完成 ==="
echo "指标：$RUN/metrics.json"
echo "报告：$RUN/final_report.md"
