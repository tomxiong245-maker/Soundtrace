#!/usr/bin/env python3
"""retrieve_similar_cases.py — 骨架 (SKELETON_CREATED · 2026-08-19)

用途
----
对单个候选 wav 的 (start, end) 区间：
  1. 用与建索引一致的 encoder 抽 embedding；
  2. 在 FAISS ``IndexFlatIP`` 中检索 top-K 最相似历史 case clip；
  3. 结合 ``meta.jsonl`` 回填 case_id / clip_id，打印 JSON 到 stdout。

CLI 契约
--------
必填参数:
    --index       PATH    FAISS index 文件路径 (由 build_case_embeddings.py 产出)
    --meta        PATH    meta.jsonl 路径 (与 index 同批次产出)
    --query-wav   PATH    候选 wav 文件绝对路径
    --start       FLOAT   起始秒
    --end         FLOAT   结束秒 (> start)

可选参数:
    --top-k       INT     返回条数，默认 5
    --model-size  STR     faster-whisper 规格，默认 base (须与建索引侧一致)
    --device      STR     cpu|cuda，默认 cpu
    --min-score   FLOAT   过滤阈值 (内积/余弦)，默认 0.0 (不过滤)

输出 schema (stdout, 单行 JSON)
------------------------------
{
  "query": {"wav":"<abs>", "start":<float>, "end":<float>},
  "matches": [
    {
      "rank": <int, 1-based>,
      "case_id": "<str>",
      "clip_id": "<str>",
      "similarity_score": <float>,
      "start": <float>,
      "end": <float>,
      "wav_path": "<str>"
    }, ...
  ],
  "model_size": "<str>",
  "top_k": <int>
}

退出码
------
0 成功 / 非 0 失败 (参数错误 / index 或 meta 缺失 / 维度不匹配 / IO 失败)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    """返回本脚本的 argparse.ArgumentParser (骨架期即固定契约)."""
    p = argparse.ArgumentParser(
        prog="retrieve_similar_cases",
        description="Retrieve top-K similar case clips via FAISS (SKELETON).",
    )
    p.add_argument("--index", type=Path, required=True,
                   help="FAISS index path")
    p.add_argument("--meta", type=Path, required=True,
                   help="meta.jsonl path (row-aligned with index)")
    p.add_argument("--query-wav", type=Path, required=True,
                   help="query wav absolute path")
    p.add_argument("--start", type=float, required=True,
                   help="query segment start (seconds)")
    p.add_argument("--end", type=float, required=True,
                   help="query segment end (seconds), must be > --start")
    p.add_argument("--top-k", type=int, default=5,
                   help="number of neighbors to return")
    p.add_argument("--model-size", type=str, default="base",
                   choices=["tiny", "base", "small", "medium", "large"],
                   help="faster-whisper model size (must match index side)")
    p.add_argument("--device", type=str, default="cpu",
                   choices=["cpu", "cuda"],
                   help="inference device")
    p.add_argument("--min-score", type=float, default=0.0,
                   help="filter matches below this cosine/IP score")
    return p


def load_index_and_meta(index_path: Path, meta_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    """faiss.read_index + 逐行读 meta.jsonl；校验行数与 index.ntotal 一致."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def compute_query_embedding(wav: Path, start: float, end: float,
                            model_size: str, device: str) -> "Any":
    """算查询侧 embedding；须与建索引侧同规格 / 同池化 / 同归一化."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def search(index: Any, query: "Any", top_k: int) -> tuple["Any", "Any"]:
    """faiss.search(query, top_k)，返回 (scores, indices)，均为 (1, top_k)."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def format_matches(scores: "Any", indices: "Any", meta: list[dict[str, Any]],
                   min_score: float) -> list[dict[str, Any]]:
    """按 output schema 装配 matches 列表 (rank 从 1 开始，score 降序)."""
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


def main(argv: list[str] | None = None) -> int:
    """入口 — 骨架期直接抛 NotImplementedError."""
    parser = build_parser()
    _args = parser.parse_args(argv)
    raise NotImplementedError("skeleton · 待 EP05 上线时实现")


if __name__ == "__main__":
    raise SystemExit(main())
