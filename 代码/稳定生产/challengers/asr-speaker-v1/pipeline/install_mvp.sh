#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

PYTHON_BIN="$(command -v python3.11 || command -v python3)"
VENV="environment/venv-mvp"

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet faster-whisper

echo "P0 MVP dependencies ready: $VENV"
