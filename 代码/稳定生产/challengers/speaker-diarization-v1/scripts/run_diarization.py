"""run_diarization · pyannote-audio 说话人 diarization 入口（Challenger）

用途
    对单轨 denoised 16 kHz mono WAV 跑 pyannote diarization pipeline，产出
    RTTM 格式 speaker turn 序列，替代 Champion `stage_speaker_role_filter`
    的能量启发式。

设计约束（不可越）
    - 强制 CPU：`pipeline.to(torch.device("cpu"))`；MPS wontfix，禁走。
    - 权重 SHA verify：加载后对 pipeline 内部 nn.Module 的 state_dict
      逐一算 SHA-256，与 `--models-dir/hashes.txt` 比对；任一漂移即
      fail-closed exit(2)。hashes.txt 为空/占位状态下仅 stderr 警告，
      不 fail —— 允许 bootstrap 首跑落 SHA。
    - 输入必须 denoised 单轨（不是混音 mono），否则 embedding 被污染。
    - 输出 RTTM 走 `pyannote.core.Annotation.write_rttm()`；不自造格式。

model_tag fallback 顺序
    1. --model-tag（默认 `pyannote/speaker-diarization-community-1`）
    2. `pyannote/speaker-diarization-3.1`（3 项 license 已 accept 的兜底）
    任一 raise / return None 都记 stderr 继续下一个；全失败 exit 非零。

网络策略
    首跑允许联网到 HF hub 拉权重（走 --auth-token 或 HF login 缓存）；
    权重落 `~/.cache/huggingface/hub/...` 后，后续 run 由 hub 库自动
    命中缓存。真正切离线时在外层设 `HF_HUB_OFFLINE=1` 环境变量，
    本脚本不强制。

用法
    python3 run_diarization.py \\
        --input-wav /abs/path/to/denoised_mono_16k.wav \\
        --output-rttm /abs/path/to/diarization.rttm

    可选：
        --models-dir /abs/path/to/hf/cache          # 默认 ~/.cache/huggingface
        --model-tag  pyannote/speaker-diarization-3.1
        --auth-token hf_xxx                          # 默认走 HF login 缓存

参考
    - pyannote-audio: https://github.com/pyannote/pyannote-audio
    - community-1: https://huggingface.co/pyannote/speaker-diarization-community-1
    - 3.1 fallback: https://huggingface.co/pyannote/speaker-diarization-3.1
    - 项目审计: audits/pyannote-audio-3.4.0.md
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

DEFAULT_MODEL_TAG = "pyannote/speaker-diarization-community-1"
FALLBACK_MODEL_TAG = "pyannote/speaker-diarization-3.1"
DEFAULT_MODELS_DIR = Path.home() / ".cache" / "huggingface"


# --------------------------------------------------------------------------- #
# 权重 SHA verify                                                              #
# --------------------------------------------------------------------------- #

def _read_hashes(models_dir: Path) -> dict[str, str]:
    """读 models_dir/hashes.txt → {relative_path: sha256_lower}。跳注释/空行。"""
    hashes_file = models_dir / "hashes.txt"
    entries: dict[str, str] = {}
    if not hashes_file.exists():
        return entries
    for raw in hashes_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        sha, rel = parts[0].strip().lower(), parts[1].strip()
        if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha) and sha != "0" * 64:
            entries[rel] = sha
    return entries


def _sha256_state_dict(state_dict) -> str:
    """对一份 torch state_dict 计算确定性 SHA-256（sorted keys + tensor bytes）。"""
    h = hashlib.sha256()
    for key in sorted(state_dict.keys()):
        h.update(key.encode("utf-8"))
        h.update(b"\0")
        tensor = state_dict[key]
        if hasattr(tensor, "detach"):
            arr = tensor.detach().cpu().contiguous().numpy()
            h.update(bytes(str(arr.dtype), "ascii"))
            h.update(b"\0")
            h.update(bytes(str(arr.shape), "ascii"))
            h.update(b"\0")
            h.update(arr.tobytes())
        else:
            h.update(repr(tensor).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _collect_submodels(pipeline) -> dict[str, object]:
    """从 pipeline 收集所有 torch.nn.Module 子成员（浅扫属性 + 一层子对象）。"""
    try:
        import torch.nn as nn
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"[verify_weight_hashes] torch import 失败: {e!r}")

    seen: set[int] = set()
    out: dict[str, object] = {}

    def _walk(prefix: str, obj) -> None:
        if id(obj) in seen:
            return
        seen.add(id(obj))
        if isinstance(obj, nn.Module):
            out[prefix] = obj
        for attr in list(vars(obj).keys()) if hasattr(obj, "__dict__") else []:
            if attr.startswith("_"):
                continue
            try:
                child = getattr(obj, attr)
            except Exception:
                continue
            if isinstance(child, nn.Module) and id(child) not in seen:
                out[f"{prefix}.{attr}" if prefix else attr] = child
                seen.add(id(child))

    _walk("", pipeline)
    return out


def verify_weight_hashes(pipeline, models_dir: Path, model_tag: str) -> None:
    """加载后核对 pipeline 各 nn.Module state_dict 的 SHA-256 与 hashes.txt。

    hashes.txt 无有效条目（占位状态）→ stderr 警告并返回；否则 hashes.txt
    里任一 SHA 若在实际加载的 submodel 集合中一个都匹配不到 → SystemExit(2)。
    这里比对方向是 "hashes.txt 要求的 SHA 必须至少落到一份 loaded state_dict
    上"；避免把 pipeline 内所有辅助 module（如激活层）也硬要求进 hashes.txt。
    """
    expected = _read_hashes(models_dir)
    if not expected:
        print(
            f"[verify_weight_hashes] models_dir={models_dir} hashes.txt "
            f"无有效条目 — 跳过校验（bootstrap 状态 · model_tag={model_tag}）",
            file=sys.stderr,
        )
        return

    submodels = _collect_submodels(pipeline)
    actual: dict[str, str] = {}
    for name, mod in submodels.items():
        try:
            actual[name] = _sha256_state_dict(mod.state_dict())
        except Exception as e:
            print(
                f"[verify_weight_hashes] {name} state_dict SHA 失败: {e!r}",
                file=sys.stderr,
            )

    actual_shas = set(actual.values())
    unmatched: list[tuple[str, str]] = []
    for rel, sha in expected.items():
        if sha not in actual_shas:
            unmatched.append((rel, sha))

    if unmatched:
        print(
            "[verify_weight_hashes] hashes.txt SHA 漂移，未在加载权重中匹配到:",
            file=sys.stderr,
        )
        for rel, sha in unmatched:
            print(f"    expected {sha}  {rel}", file=sys.stderr)
        print(
            f"[verify_weight_hashes] loaded submodels ({len(actual)}): "
            f"{sorted(actual.keys())}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print(
        f"[verify_weight_hashes] {len(expected)} 条 SHA 校验通过 "
        f"(model_tag={model_tag}, submodels={len(actual)})",
        file=sys.stderr,
    )


# --------------------------------------------------------------------------- #
# pipeline 加载                                                                #
# --------------------------------------------------------------------------- #

def load_pipeline(models_dir: Path, model_tag: str, auth_token: str | None):
    """按 fallback 顺序加载 pyannote Pipeline · 强制 CPU · 返回 (pipeline, tag)。

    顺序：--model-tag → pyannote/speaker-diarization-3.1（若与 model-tag 不同）
    任何异常 / return None（gated license 未 accept）都会走下一个候选。
    全失败 → SystemExit。
    """
    try:
        import torch
        from pyannote.audio import Pipeline as PyannotePipeline
    except Exception as e:
        raise SystemExit(f"[load_pipeline] 依赖 import 失败: {e!r}")

    # models_dir 作 HF cache 提示（不强制 offline）
    if models_dir and models_dir != DEFAULT_MODELS_DIR:
        os.environ.setdefault("HF_HOME", str(models_dir))

    candidates: list[str] = [model_tag]
    if FALLBACK_MODEL_TAG != model_tag:
        candidates.append(FALLBACK_MODEL_TAG)

    tried: list[tuple[str, str]] = []
    for tag in candidates:
        try:
            print(f"[load_pipeline] 尝试 from_pretrained({tag!r})", file=sys.stderr)
            kwargs: dict = {}
            if auth_token:
                kwargs["token"] = auth_token
            pipe = PyannotePipeline.from_pretrained(tag, **kwargs)
            if pipe is None:
                raise RuntimeError(
                    "Pipeline.from_pretrained 返回 None — 通常是 gated repo "
                    "license 未 accept 或 token 未配置"
                )
            pipe.to(torch.device("cpu"))
            print(f"[load_pipeline] 成功加载 {tag!r} → CPU", file=sys.stderr)
            return pipe, tag
        except Exception as e:
            tried.append((tag, repr(e)))
            print(f"[load_pipeline] {tag!r} 加载失败: {e!r}", file=sys.stderr)

    detail = "; ".join(f"{t} → {msg}" for t, msg in tried)
    raise SystemExit(f"[load_pipeline] 所有候选 model_tag 均失败: {detail}")


# --------------------------------------------------------------------------- #
# 推理 + RTTM 写入                                                             #
# --------------------------------------------------------------------------- #

def run(pipeline, input_wav: Path):
    """跑 pipeline 拿 pyannote.core.Annotation。"""
    if not input_wav.exists():
        raise SystemExit(f"[run] input_wav 不存在: {input_wav}")
    if not input_wav.is_file():
        raise SystemExit(f"[run] input_wav 不是文件: {input_wav}")
    return pipeline(str(input_wav))


def write_rttm(annotation, output_rttm: Path) -> None:
    """把 Annotation 落 RTTM，覆盖写；不自造格式，全部走 write_rttm()。

    pyannote-audio 4.x 的 SpeakerDiarization pipeline 默认返回 DiarizeOutput
    dataclass（含 speaker_diarization / exclusive_speaker_diarization /
    speaker_embeddings）；旧版和 legacy=True 直接返回 Annotation。这里两者
    都兼容 — 若 obj 没有 write_rttm，就取 .speaker_diarization。
    """
    output_rttm.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(annotation, "write_rttm"):
        target = annotation
    elif hasattr(annotation, "speaker_diarization"):
        target = annotation.speaker_diarization
    else:
        raise SystemExit(
            f"[write_rttm] pipeline 输出既非 Annotation 也无 speaker_diarization: "
            f"{type(annotation).__name__}"
        )
    with output_rttm.open("w", encoding="utf-8") as f:
        target.write_rttm(f)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="pyannote-audio 说话人 diarization · 单轨 WAV → RTTM",
    )
    ap.add_argument(
        "--input-wav",
        required=True,
        type=Path,
        help="输入 16 kHz mono denoised WAV 绝对路径",
    )
    ap.add_argument(
        "--output-rttm",
        required=True,
        type=Path,
        help="输出 RTTM 绝对路径（覆盖写）",
    )
    ap.add_argument(
        "--models-dir",
        default=DEFAULT_MODELS_DIR,
        type=Path,
        help=f"HF cache 目录（默认 {DEFAULT_MODELS_DIR}）；同时用于寻找 hashes.txt",
    )
    ap.add_argument(
        "--model-tag",
        default=DEFAULT_MODEL_TAG,
        type=str,
        help=(
            f"pyannote pipeline tag（默认 {DEFAULT_MODEL_TAG}）；"
            f"加载失败自动 fallback 到 {FALLBACK_MODEL_TAG}"
        ),
    )
    ap.add_argument(
        "--auth-token",
        default=None,
        type=str,
        help="HF token；默认 None（走 huggingface-cli login 缓存 ~/.cache/huggingface/token）",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    # 1. 加载 pipeline（CPU · fallback 顺序）
    pipeline, effective_tag = load_pipeline(
        args.models_dir, args.model_tag, args.auth_token
    )

    # 2. 校验权重 SHA（fail-closed；hashes.txt 空占位则跳过）
    verify_weight_hashes(pipeline, args.models_dir, effective_tag)

    # 3. 跑 diarization
    annotation = run(pipeline, args.input_wav)

    # 4. 落 RTTM
    write_rttm(annotation, args.output_rttm)

    print(
        f"[main] OK · model_tag={effective_tag} · RTTM → {args.output_rttm}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
