#!/usr/bin/env python3
"""orchestrator-e2e-v1: 合并三个 Challenger 的候选到统一 schema。

不复原候选内部结构，只加统一字段 reason_family/reason_key/track_ids 与稳定 ID，
供后续审核页面消费。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _cid(episode: str, family: str, reason: str, track: str,
         start: int, end: int) -> str:
    key = f"{episode}|{family}|{reason}|{track}|{start}|{end}"
    return "C-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def merge(episode_id: str, sr: int,
          crosstalk: dict | None, self_corr: dict | None,
          transient: dict | None) -> dict:
    out: list[dict[str, Any]] = []
    if crosstalk:
        for c in crosstalk.get("candidates", []):
            c2 = dict(c)
            c2["episode_id"] = episode_id
            c2["reason_family"] = "crosstalk"
            c2["applies_to_tracks"] = c.get("applies_to_tracks", [c["track_id"]])
            c2["candidate_id"] = _cid(
                episode_id, "crosstalk", c["reason_key"],
                c["track_id"], c["start_sample"], c["end_sample"])
            out.append(c2)
    if self_corr:
        for tr in self_corr.get("tracks", []):
            for c in tr.get("candidates", []):
                c2 = dict(c)
                c2["episode_id"] = episode_id
                c2["reason_family"] = "self_correction"
                c2["applies_to_tracks"] = [c["track_id"]]
                c2["candidate_id"] = _cid(
                    episode_id, "self_correction", c["reason_key"],
                    c["track_id"], c["start_sample"], c["end_sample"])
                out.append(c2)
    if transient:
        for tr in transient.get("tracks", []):
            for c in tr.get("candidates", []):
                c2 = dict(c)
                c2["episode_id"] = episode_id
                c2["reason_family"] = "transient"
                c2["applies_to_tracks"] = [c["track_id"]]
                c2["candidate_id"] = _cid(
                    episode_id, "transient", c["reason_key"],
                    c["track_id"], c["start_sample"], c["end_sample"])
                out.append(c2)
    out.sort(key=lambda x: (x["track_id"], x["start_sample"]))
    return {
        "schema_version": "merged-candidates-v1",
        "episode_id": episode_id,
        "sample_rate_hz": sr,
        "counts": {
            "total": len(out),
            "crosstalk": sum(1 for c in out if c["reason_family"] == "crosstalk"),
            "self_correction": sum(1 for c in out if c["reason_family"] == "self_correction"),
            "transient": sum(1 for c in out if c["reason_family"] == "transient"),
        },
        "candidates": out,
        "policy": "review_only_no_automatic_accept",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-id", required=True)
    ap.add_argument("--sample-rate-hz", type=int, required=True)
    ap.add_argument("--crosstalk", required=True)
    ap.add_argument("--self-correction", required=True)
    ap.add_argument("--transient", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    merged = merge(
        args.episode_id, args.sample_rate_hz,
        _load(Path(args.crosstalk)),
        _load(Path(args.self_correction)),
        _load(Path(args.transient)),
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total": merged["counts"]["total"], "out": args.out},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
