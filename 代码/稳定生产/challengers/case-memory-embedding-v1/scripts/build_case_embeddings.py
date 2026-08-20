#!/usr/bin/env python3
"""build_case_embeddings.py — 骨架 (SKELETON_CREATED · 2026-08-19)

用途
----
遍历 case memory 中所有历史 case clip，用 faster-whisper 内置 encoder 抽取
音频 embedding，L2 归一化后写入 FAISS ``IndexFlatIP``，另附 ``meta.jsonl`` 记录
每行索引对应的 (case_id, clip_id, start, end, wav_path)。

CLI 契约
--------
必填参数:
    --case-memory  PATH   case memory JSON 文件路径 (schema 见 Champion)
    --audio-root   PATH   case wav 存放根目录 (case memory 内路径相对此根)
    --out          PATH   输出目录 (会写入 faiss.index + meta.jsonl)

可选参数:
    --model-size   STR    faster-whisper 规格 (tiny|base|small|medium|large)
                          默认 base；须与查询侧一致
    --device       STR    cpu|cuda，默认 cpu
    --overwrite           若输出目录已存在，允许覆盖 (默认拒绝覆盖)

输入 schema
-----------
case_memory.json:
    {
      "cases": [
        {
          "case_id": "<str>",
          "clips": [
            {"clip_id": "<str>", "wav": "<relpath>", "start": <float>, "end": <float>}
          ]
        }
      ]
    }

输出 schema
-----------
<out>/faiss.index    FAISS IndexFlatIP 序列化
<out>/meta.jsonl     每行: {"case_id","clip_id","start","end","wav_path","row"}

退出码
------
0 成功 / 非 0 失败 (含拒绝覆盖 / 参数错误 / IO 错误)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    """返回本脚本的 argparse.ArgumentParser (骨架期即固定契约)."""
    p = argparse.ArgumentParser(
        prog="build_case_embeddings",
        description="Build FAISS index of case-clip audio embeddings (SKELETON).",
    )
    p.add_argument("--case-memory", type=Path, required=True,
                   help="case memory JSON path")
    p.add_argument("--audio-root", type=Path, required=True,
                   help="root dir of case wavs")
    p.add_argument("--out", type=Path, required=True,
                   help="output dir for faiss.index + meta.jsonl")
    p.add_argument("--model-size", type=str, default="base",
                   choices=["tiny", "base", "small", "medium", "large"],
                   help="faster-whisper model size (must match query side)")
    p.add_argument("--device", type=str, default="cpu",
                   choices=["cpu", "cuda"],
                   help="inference device")
    p.add_argument("--overwrite", action="store_true",
                   help="allow overwriting existing --out (default: refuse)")
    return p


def load_case_memory(path: Path) -> dict[str, Any]:
    """读 case memory JSON 并做最小 schema 校验."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def encode_clip(wav_path: Path, start: float, end: float,
                model: Any) -> "Any":
    """对单段 (start, end) 抽取 encoder 隐状态并 mean-pool + L2 归一化.

    Returns
    -------
    numpy.ndarray of shape (D,), dtype float32, ||v||_2 = 1
    """
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def build_index(vectors: "Any", dim: int) -> "Any":
    """把 (N, D) float32 向量装入 faiss.IndexFlatIP."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def write_outputs(out_dir: Path, index: "Any", meta_rows: list[dict[str, Any]],
                  overwrite: bool) -> None:
    """写 faiss.index + meta.jsonl；overwrite=False 时若目录已存在则报错."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def main(argv: list[str] | None = None) -> int:
    """入口 — 骨架期直接抛 NotImplementedError."""
    parser = build_parser()
    _args = parser.parse_args(argv)
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


if __name__ == "__main__":
    raise SystemExit(main())
