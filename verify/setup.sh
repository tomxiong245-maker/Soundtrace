#!/usr/bin/env bash
# ==============================================================================
# 剪辑项目 · 依赖安装 setup.sh
# ==============================================================================
# 用法:
#   bash verify/setup.sh                 # 完整安装
#   bash verify/setup.sh --check         # 只检查现有环境 · 不装东西
#   bash verify/setup.sh --skip-mfa      # 跳过 MFA (约 500 MB 模型)
#
# 目标机器: macOS · arm64 (Apple Silicon)
# 依赖总量: 约 1.2 GB (含所有神经模型 + MFA 声学模型)
# 装完后可运行: 完整 pipeline · raw WAV → 交付 mp3
# ==============================================================================

set -euo pipefail

CHECK_ONLY=false
SKIP_MFA=false
for arg in "$@"; do
    case $arg in
        --check) CHECK_ONLY=true ;;
        --skip-mfa) SKIP_MFA=true ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "=== 剪辑项目依赖 setup · 目标: $PROJECT_ROOT ==="
echo ""

# ------------------------------------------------------------------------------
# 1. 系统级 CLI · Homebrew (ffmpeg / lame)
# ------------------------------------------------------------------------------
echo "[1/6] 系统 CLI · Homebrew"
if ! command -v brew &>/dev/null; then
    if [ "$CHECK_ONLY" = true ]; then
        echo "  ❌ brew 未装 · 需要装 Homebrew: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        exit 1
    fi
    echo "  Homebrew 未装 · 需要先手动装: https://brew.sh"
    exit 1
fi

for cli in ffmpeg lame; do
    if command -v $cli &>/dev/null; then
        echo "  ✓ $cli @ $(which $cli)"
    else
        if [ "$CHECK_ONLY" = true ]; then
            echo "  ❌ $cli 未装 · brew install $cli"
        else
            brew install $cli
        fi
    fi
done

# ------------------------------------------------------------------------------
# 2. 系统 Python 3.13 · pip 装 faster-whisper (--user)
# ------------------------------------------------------------------------------
echo ""
echo "[2/6] 系统 Python 3.13 · faster-whisper"
PY313=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
if [ ! -x "$PY313" ]; then
    if [ "$CHECK_ONLY" = true ]; then
        echo "  ❌ Python 3.13 未装于 /Library/Frameworks/... · 从 python.org 装"
    else
        echo "  ⚠️ Python 3.13 未在标准位置 · 从 python.org 手工装"
        echo "     https://www.python.org/downloads/macos/"
        exit 1
    fi
fi

if [ -x "$PY313" ]; then
    if "$PY313" -c "import faster_whisper" &>/dev/null; then
        echo "  ✓ faster-whisper 已在 py3.13 (--user 站点)"
    else
        if [ "$CHECK_ONLY" = true ]; then
            echo "  ❌ faster-whisper 需装: $PY313 -m pip install --user faster-whisper"
        else
            "$PY313" -m pip install --user faster-whisper
            echo "  ✓ faster-whisper installed"
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 3. Miniforge3 conda · MFA + pydub + librosa + spacy 全套 (剪 py3.11 环境)
# ------------------------------------------------------------------------------
echo ""
echo "[3/6] Miniforge3 conda · 音频处理 py3.11 环境"
MINIFORGE=$HOME/miniforge3
if [ ! -d "$MINIFORGE" ]; then
    if [ "$CHECK_ONLY" = true ]; then
        echo "  ❌ miniforge3 未装 · 需要从 https://github.com/conda-forge/miniforge 装 (arm64)"
    else
        echo "  miniforge3 未装 · 建议手工装:"
        echo "     curl -L -O https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh"
        echo "     bash Miniforge3-MacOSX-arm64.sh -b -p ~/miniforge3"
        echo "  (装完再重跑此 setup)"
        exit 1
    fi
fi

if [ -x "$MINIFORGE/bin/python" ]; then
    echo "  ✓ miniforge3 @ $MINIFORGE"

    # 检查关键包
    for pkg in pydub librosa noisereduce soundfile spacy dragonmapper hanziconv; do
        if "$MINIFORGE/bin/python" -c "import ${pkg//-/_}" &>/dev/null; then
            echo "    ✓ $pkg"
        else
            if [ "$CHECK_ONLY" = true ]; then
                echo "    ❌ $pkg 未装 · $MINIFORGE/bin/pip install $pkg"
            else
                "$MINIFORGE/bin/pip" install $pkg
                echo "    ✓ $pkg installed"
            fi
        fi
    done

    # spacy 中文模型
    if "$MINIFORGE/bin/python" -c "import spacy; spacy.load('zh_core_web_sm')" &>/dev/null; then
        echo "    ✓ spacy zh_core_web_sm"
    else
        if [ "$CHECK_ONLY" = true ]; then
            echo "    ❌ spacy zh_core_web_sm 未装 · $MINIFORGE/bin/python -m spacy download zh_core_web_sm"
        else
            "$MINIFORGE/bin/python" -m spacy download zh_core_web_sm
        fi
    fi

    # spacy-pkuseg
    if "$MINIFORGE/bin/python" -c "import spacy_pkuseg" &>/dev/null; then
        echo "    ✓ spacy-pkuseg"
    else
        if [ "$CHECK_ONLY" = true ]; then
            echo "    ❌ spacy-pkuseg 未装 · $MINIFORGE/bin/pip install spacy-pkuseg"
        else
            "$MINIFORGE/bin/pip" install spacy-pkuseg
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 4. MFA (Montreal Forced Aligner) · 音素级边界精修 (CLAUDE.md §8 硬边界)
# ------------------------------------------------------------------------------
echo ""
echo "[4/6] MFA · 音素级 alignment (CLAUDE.md §8)"
if [ "$SKIP_MFA" = true ]; then
    echo "  ⏭ 用户 --skip-mfa · 跳过"
elif [ -x "$MINIFORGE/bin/mfa" ]; then
    MFA_VERSION=$("$MINIFORGE/bin/mfa" version 2>&1 | head -1)
    echo "  ✓ MFA @ $MINIFORGE/bin/mfa (version: $MFA_VERSION)"

    # 检查中文 acoustic model + dictionary
    if "$MINIFORGE/bin/mfa" model inspect acoustic mandarin_mfa &>/dev/null; then
        echo "    ✓ acoustic model: mandarin_mfa"
    else
        if [ "$CHECK_ONLY" = true ]; then
            echo "    ❌ mandarin_mfa 未装 · $MINIFORGE/bin/mfa model download acoustic mandarin_mfa"
        else
            "$MINIFORGE/bin/mfa" model download acoustic mandarin_mfa
        fi
    fi

    if "$MINIFORGE/bin/mfa" model inspect dictionary mandarin_china_mfa &>/dev/null; then
        echo "    ✓ dictionary: mandarin_china_mfa"
    else
        if [ "$CHECK_ONLY" = true ]; then
            echo "    ❌ mandarin_china_mfa 未装 · $MINIFORGE/bin/mfa model download dictionary mandarin_china_mfa"
        else
            "$MINIFORGE/bin/mfa" model download dictionary mandarin_china_mfa
        fi
    fi
else
    if [ "$CHECK_ONLY" = true ]; then
        echo "  ❌ MFA 未装 · conda install -c conda-forge montreal-forced-aligner"
    else
        conda install -y -c conda-forge montreal-forced-aligner -p "$MINIFORGE"
    fi
fi

# ------------------------------------------------------------------------------
# 5. DeepFilterNet · 神经降噪 (本地 arm64 binary)
# ------------------------------------------------------------------------------
echo ""
echo "[5/6] DeepFilterNet 降噪 · arm64 binary"
DF_BINARY="$PROJECT_ROOT/.tools/deepfilternet-v0.5.6/deep-filter"
if [ -x "$DF_BINARY" ]; then
    echo "  ✓ DeepFilterNet @ $DF_BINARY (version: $($DF_BINARY --version 2>&1 | head -1))"
else
    if [ "$CHECK_ONLY" = true ]; then
        echo "  ❌ DeepFilterNet 未装于 $DF_BINARY"
        echo "     下载: https://github.com/Rikorose/DeepFilterNet/releases · macOS-aarch64-v0.5.6"
    else
        echo "  ⚠️ DeepFilterNet arm64 binary 需手工下载 · SHA 固定"
        echo "     mkdir -p $PROJECT_ROOT/.tools/deepfilternet-v0.5.6"
        echo "     curl -L -o $DF_BINARY https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-v0.5.6-aarch64-apple-darwin"
        echo "     chmod +x $DF_BINARY"
        echo "     期望 SHA-256: 4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081"
    fi
fi

# ------------------------------------------------------------------------------
# 6. cut-verify skill · py3.13 + miniforge3 双 python 依赖检查 (2026-08-19 新)
# ------------------------------------------------------------------------------
echo ""
echo "[6/6] cut-verify skill (2026-08-19 新 · CLAUDE.md §22)"
CUTVERIFY="$PROJECT_ROOT/skills/cut-verify/scripts"
if [ -d "$CUTVERIFY" ]; then
    echo "  ✓ cut-verify skill 目录: $CUTVERIFY"
    for script in check_hallucination.py check_silence_location.py check_rhythm_gap.py route_crossfade_strategy.py verify_cut_plan.py expand_to_asr_word_boundary.py; do
        if [ -f "$CUTVERIFY/$script" ]; then
            echo "    ✓ $script"
        else
            echo "    ❌ $script 缺失"
        fi
    done

    # 依赖交叉验证
    echo "  依赖交叉验证 (关键脚本):"
    if [ -x "$MINIFORGE/bin/python" ]; then
        "$MINIFORGE/bin/python" -c "from pydub import AudioSegment; from pydub.silence import detect_silence; print('    ✓ check_silence_location 依赖 pydub · miniforge3')" 2>&1 | tail -1
    fi
    if [ -x "$PY313" ]; then
        "$PY313" -c "import faster_whisper; print('    ✓ check_hallucination 依赖 faster-whisper · py3.13')" 2>&1 | tail -1
    fi
else
    echo "  ❌ cut-verify skill 未找到 · 期望路径: $CUTVERIFY"
fi

echo ""
echo "=== setup 完成 ==="
if [ "$CHECK_ONLY" = true ]; then
    echo "  · 上面所有 ❌ 都需要手工修复 · 修完再跑一次 --check"
else
    echo "  · 下一步跑 verify: bash verify/verify.sh"
    echo "  · 或直接跑一期新节目:"
    echo "      python3 $PROJECT_ROOT/稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py \\"
    echo "        --episode-id EP0X --from-raw-wav track_01.wav track_02.wav track_03.wav \\"
    echo "        --tracks-for-automix track_01.wav track_02.wav track_03.wav \\"
    echo "        --out-dir main/runs/EP0X-AUTO-$(date +%Y%m%d-%H%M)"
fi
