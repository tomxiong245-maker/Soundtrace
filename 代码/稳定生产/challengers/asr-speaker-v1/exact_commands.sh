#!/usr/bin/env bash
# ============================================================================
# asr-speaker-v1 Challenger · 精确复现命令
#
# 分成两类：
#   [A] 在本会话（Linux 沙箱 VM 或任何 Python 3.10+）都能跑的：骨架构建、单测、
#       gold 重构、baseline 切片、scorer 合成自测。
#   [B] 必须在 Apple M3 本机跑：真实 FunASR / MLX 推理、SHA 校验、真人 gold 后的
#       正式评分。
#
# 使用方式：在项目根 (<HOME>/Desktop/minglue/剪辑项目) 打开终端，一段一段执行。
# 别一键 bash 整个脚本 —— 每步之间都要看输出、确认没有报错再走下一步。
# ============================================================================
set -euo pipefail
REPO="<HOME>/Desktop/minglue/剪辑项目"
CHAL="$REPO/稳定生产/challengers/asr-speaker-v1"
RUN="$REPO/main/runs/EP03-asr-speaker-v1"
BENCH="$REPO/benchmark/EP03-ASR-mini-gold-v1"
BASELINE_SHA="$REPO/稳定生产/challengers/cross-track-safety-v1/before_metrics.json"

# ---------- [A1] Champion SHA 校验（本会话 or M3 均可，只读） --------------
python3 "$CHAL/scripts/verify_champion_untouched.py" \
  --repo "$REPO" \
  --baseline-sha-file "$BASELINE_SHA"

# ---------- [A2] 合成 fixture 并跑单测（本会话 or M3 均可） -----------------
python3 "$CHAL/tests/build_fixtures.py" --out "$CHAL/tests/fixtures"
python3 "$CHAL/scripts/run_tests.py"

# ---------- [A3] gold.json 迁移到 gold.v2.json（本会话 or M3 均可） -------
python3 "$CHAL/scripts/rebuild_gold_v2.py" \
  --gold "$BENCH/gold.json" \
  --gold-v2 "$BENCH/gold.v2.json" \
  --hypotheses-out "$BENCH/hypotheses" \
  --backup "$BENCH/gold.json.backup" \
  --report "$BENCH/migration_report.md"

# ---------- [A4] baseline before_metrics + baseline_sha256 -----------------
python3 "$CHAL/scripts/build_baseline_metrics.py" \
  --repo "$REPO" \
  --gold "$BENCH/gold.json" \
  --baseline-sha-file "$BASELINE_SHA" \
  --segments-dir "$BENCH/segments" \
  --out "$RUN/before_metrics.json"

# ---------- [A5] Baseline 切片：从 freshrun ASR 输出切 12 段 --------------
# Uses only the frozen Champion transcript; does NOT re-run faster-whisper.
python3 "$CHAL/scripts/slice_baseline_from_freshrun.py" \
  --freshrun-asr-dir "$REPO/main/runs/EP03-freshrun-20260810-1730/05_asr" \
  --gold-json "$BENCH/gold.json" \
  --segments-dir "$BENCH/segments" \
  --raw-out "$RUN/raw/faster_whisper_small_vad_on" \
  --normalized-out "$RUN/normalized/faster_whisper_small_vad_on"

# ---------- [A6] 发布到 benchmark hypotheses 层 ---------------------------
python3 "$CHAL/scripts/build_hypotheses_layer.py" \
  --source "$RUN/normalized" \
  --dest "$BENCH/hypotheses" \
  --engine faster_whisper_small_vad_on

# ---------- [A7] scorer 自测（合成 gold；不算 EP03 分数） ----------------
python3 "$CHAL/scripts/score_asr_benchmark.py" \
  --gold "$CHAL/tests/fixtures/synthetic_gold.json" \
  --hypotheses-root "$CHAL/tests/fixtures/hypotheses_stub" \
  --engines faster_whisper_small_vad_on \
  --out "$CHAL/tests/fixtures/synthetic_metrics.json" \
  --allow-synthetic-only || echo "(expected: NO_HYPOTHESIS placeholders; see synthetic_metrics.json)"

# ============================================================================
# [B] 下面必须在 Apple M3 本机（arm64 / macOS 14+）执行
# ============================================================================

# ---------- [B1] 建立 venv & 下载模型（一次性） --------------------------
: '
# 每一步执行前先确认命令看着安全，再执行。所有下载都是官方源。
/opt/homebrew/bin/python3.11 -m venv "$CHAL/environment/venv-scorer"
"$CHAL/environment/venv-scorer/bin/pip" install jiwer numpy pyannote.metrics
"$CHAL/environment/venv-scorer/bin/pip" freeze > "$CHAL/environment/requirements.scorer.lock.txt"

/opt/homebrew/bin/python3.11 -m venv "$CHAL/environment/venv-funasr"
"$CHAL/environment/venv-funasr/bin/pip" install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
"$CHAL/environment/venv-funasr/bin/pip" install funasr modelscope soundfile librosa numpy
"$CHAL/environment/venv-funasr/bin/pip" freeze > "$CHAL/environment/requirements.funasr.lock.txt"

# 手动下载权重后，登记 SHA 到 audits/funasr-*.md；这里给出一次性下载命令：
"$CHAL/environment/venv-funasr/bin/python" -c "
from modelscope.hub.snapshot_download import snapshot_download
snapshot_download(\"iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch\",
                  cache_dir=\"$CHAL/environment/models\")
snapshot_download(\"iic/speech_fsmn_vad_zh-cn-16k-common-pytorch\",
                  cache_dir=\"$CHAL/environment/models\")
snapshot_download(\"iic/speech_campplus_speaker-diarization_common\",
                  cache_dir=\"$CHAL/environment/models\")
"

/opt/homebrew/bin/python3.11 -m venv "$CHAL/environment/venv-mlx"
"$CHAL/environment/venv-mlx/bin/pip" install mlx mlx-whisper numpy soundfile
"$CHAL/environment/venv-mlx/bin/pip" freeze > "$CHAL/environment/requirements.mlx-whisper.lock.txt"
"$CHAL/environment/venv-mlx/bin/python" -c "
import mlx_whisper, os
# 触发 hub 下载权重
mlx_whisper.transcribe(\"$CHAL/tests/fixtures/silence_10s.wav\",
    path_or_hf_repo=\"mlx-community/whisper-large-v3-turbo\",
    language=\"zh\", word_timestamps=True)
"
'

# ---------- [B2] FunASR：12×3 段推理（M3） -------------------------------
: '
"$CHAL/environment/venv-funasr/bin/python" "$CHAL/scripts/run_funasr.py" \
  --gold-json "$BENCH/gold.json" \
  --segments-dir "$BENCH/segments" \
  --raw-out "$RUN/raw" \
  --paraformer-dir "$CHAL/environment/models/iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch" \
  --vad-dir "$CHAL/environment/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch" \
  --campp-dir "$CHAL/environment/models/iic/speech_campplus_speaker-diarization_common"
'

# ---------- [B3] MLX Whisper：12×3 段推理（M3） --------------------------
: '
"$CHAL/environment/venv-mlx/bin/python" "$CHAL/scripts/run_mlx_whisper.py" \
  --gold-json "$BENCH/gold.json" \
  --segments-dir "$BENCH/segments" \
  --raw-out "$RUN/raw" \
  --model-repo "mlx-community/whisper-large-v3-turbo"
'

# ---------- [B4] Normalize + 发布到 hypotheses ---------------------------
: '
# 每个引擎调对应 adapter 遍历 raw/<engine>/S*/*.raw.json → normalized/<engine>/S*/*.words.json
# scripts/*_adapter.py 都可以 CLI 单文件调用（见 run_tests 用例）。
# 完成后：
python3 "$CHAL/scripts/build_hypotheses_layer.py" --source "$RUN/normalized" --dest "$BENCH/hypotheses" --engine funasr_paraformer
python3 "$CHAL/scripts/build_hypotheses_layer.py" --source "$RUN/normalized" --dest "$BENCH/hypotheses" --engine mlx_whisper_turbo
'

# ---------- [B5] 只有人工 gold 完成后才跑（脚本自身拒绝提前执行） -------
: '
"$CHAL/environment/venv-scorer/bin/python" "$CHAL/scripts/score_asr_benchmark.py" \
  --gold "$BENCH/gold.v2.json" \
  --hypotheses-root "$BENCH/hypotheses" \
  --engines faster_whisper_small_vad_on funasr_paraformer mlx_whisper_turbo \
  --out "$BENCH/metrics/benchmark_metrics.json"
'
