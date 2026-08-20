#!/usr/bin/env bash
# 一次性安装脚本（M3 arm64 / macOS 14+）
# 需要 python3.11。如果没有，先 brew install python@3.11。
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

echo "[1/4] scorer venv"
if [ ! -x environment/venv-scorer/bin/python ]; then
  /opt/homebrew/bin/python3.11 -m venv environment/venv-scorer
fi
environment/venv-scorer/bin/pip install --quiet --upgrade pip
environment/venv-scorer/bin/pip install --quiet numpy soundfile jiwer

echo "[2/4] sensevoice venv (FunASR + SenseVoice small + Silero VAD)"
if [ ! -x environment/venv-sensevoice/bin/python ]; then
  /opt/homebrew/bin/python3.11 -m venv environment/venv-sensevoice
fi
environment/venv-sensevoice/bin/pip install --quiet --upgrade pip
environment/venv-sensevoice/bin/pip install --quiet --index-url https://download.pytorch.org/whl/cpu torch torchaudio
environment/venv-sensevoice/bin/pip install --quiet funasr modelscope soundfile numpy librosa

echo "[3/4] mlx-whisper venv"
if [ ! -x environment/venv-mlx/bin/python ]; then
  /opt/homebrew/bin/python3.11 -m venv environment/venv-mlx
fi
environment/venv-mlx/bin/pip install --quiet --upgrade pip
environment/venv-mlx/bin/pip install --quiet mlx mlx-whisper numpy soundfile

echo "[4/4] diarization venv (pyannote if HF token, else sherpa-onnx)"
if [ ! -x environment/venv-diar/bin/python ]; then
  /opt/homebrew/bin/python3.11 -m venv environment/venv-diar
fi
environment/venv-diar/bin/pip install --quiet --upgrade pip
if [ -n "${HF_TOKEN:-}" ]; then
  echo "  HF_TOKEN found → installing pyannote.audio 3.x"
  environment/venv-diar/bin/pip install --quiet --index-url https://download.pytorch.org/whl/cpu torch torchaudio
  environment/venv-diar/bin/pip install --quiet pyannote.audio
else
  echo "  no HF_TOKEN → installing sherpa-onnx (no login needed)"
  environment/venv-diar/bin/pip install --quiet sherpa-onnx
fi

echo
echo "install ok. next: bash pipeline/run_all.sh"
