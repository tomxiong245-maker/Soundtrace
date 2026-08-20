#!/usr/bin/env python3
"""embed_candidate.py — 骨架 (SKELETON_CREATED · 2026-08-19)

用途
----
对单个候选 wav 的 (start, end) 区间抽取一条 audio embedding，向 stdout 打印
JSON。用于 pipeline 中「查询侧」调用：先算 embedding，再交给
``retrieve_similar_cases.py`` 做 top-K 查询。

CLI 契约
--------
必填参数:
    --wav    PATH    候选 wav 文件绝对路径
    --start  FLOAT   起始秒
    --end    FLOAT   结束秒 (> start)

可选参数:
    --model-size  STR   faster-whisper 规格 (默认 base；须与建索引侧一致)
    --device      STR   cpu|cuda，默认 cpu

输出 schema (stdout, 单行 JSON)
------------------------------
{
  "embedding": [<float>, ...],   # 长度 D，L2 归一化
  "dim": <int>,                  # = len(embedding)
  "wav": "<abs path>",
  "start": <float>,
  "end": <float>,
  "model_size": "<str>"
}

退出码
------
0 成功 / 非 0 失败 (参数错误 / wav 不可读 / start>=end / encoder 失败)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    """返回本脚本的 argparse.ArgumentParser (骨架期即固定契约)."""
    p = argparse.ArgumentParser(
        prog="embed_candidate",
        description="Compute one audio embedding for a wav segment (SKELETON).",
    )
    p.add_argument("--wav", type=Path, required=True,
                   help="candidate wav absolute path")
    p.add_argument("--start", type=float, required=True,
                   help="segment start (seconds)")
    p.add_argument("--end", type=float, required=True,
                   help="segment end (seconds), must be > --start")
    p.add_argument("--model-size", type=str, default="base",
                   choices=["tiny", "base", "small", "medium", "large"],
                   help="faster-whisper model size (must match index side)")
    p.add_argument("--device", type=str, default="cpu",
                   choices=["cpu", "cuda"],
                   help="inference device")
    return p


def load_encoder(model_size: str, device: str) -> Any:
    """加载 faster-whisper，返回可调用 encoder 的 model 对象."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def compute_embedding(wav_path: Path, start: float, end: float,
                      model: Any) -> "Any":
    """对 (start, end) 区间：读 wav → log-mel → encoder → mean-pool → L2 norm.

    Returns
    -------
    numpy.ndarray of shape (D,), dtype float32, ||v||_2 = 1
    """
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def emit_json(embedding: "Any", wav: Path, start: float, end: float,
              model_size: str) -> None:
    """把结果按输出 schema 打印到 stdout (单行 JSON)."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def main(argv: list[str] | None = None) -> int:
    """入口 — 骨架期直接抛 NotImplementedError."""
    parser = build_parser()
    _args = parser.parse_args(argv)
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


if __name__ == "__main__":
    raise SystemExit(main())
