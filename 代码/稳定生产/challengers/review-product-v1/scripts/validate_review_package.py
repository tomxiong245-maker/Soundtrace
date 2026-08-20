#!/usr/bin/env python3
"""
validate_review_package.py · review-product-v1

用法：
    python3 validate_review_package.py <review_package.json>

行为：
    - 加载 schema 校验；
    - 重新计算 review_manifest_sha256（对除本字段外的全部键做 canonical JSON）；
    - 逐个候选重新计算 semantic_sha256 并对比；
    - 校验 preview_assets 每个文件磁盘 SHA-256（若 --check-files）；
    - 校验 global_cut.applies_to_tracks 必须同时包含 female/male；
    - 校验 sample_rate 与 sample 边界一致性（start_sample/end_sample 在 24h*sample_rate 内）。

Exit code：
    0 = pass；非 0 = 具体拒绝原因写入 stderr。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, Iterable, Tuple

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "schemas",
    "review_package.schema.json",
)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_review_manifest_sha(pkg: Dict[str, Any]) -> str:
    copy = {k: v for k, v in pkg.items() if k != "review_manifest_sha256"}
    return sha256_bytes(canonical_json(copy))


def compute_candidate_semantic_sha(c: Dict[str, Any]) -> str:
    copy = {k: v for k, v in c.items() if k != "semantic_sha256"}
    return sha256_bytes(canonical_json(copy))


def _load_schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _minimal_schema_check(pkg: Dict[str, Any], schema: Dict[str, Any]) -> Iterable[str]:
    """极简 schema 校验：required 字段与 enum；避免额外依赖。"""
    def check(instance: Any, sch: Dict[str, Any], path: str) -> Iterable[str]:
        if "required" in sch and isinstance(instance, dict):
            for k in sch["required"]:
                if k not in instance:
                    yield f"{path}: missing required field '{k}'"
        if "enum" in sch:
            if instance not in sch["enum"]:
                yield f"{path}: value {instance!r} not in enum {sch['enum']!r}"
        if "type" in sch and sch["type"] == "object" and "properties" in sch:
            if isinstance(instance, dict):
                for k, sub in sch["properties"].items():
                    if k in instance:
                        yield from check(instance[k], sub, f"{path}.{k}")

    yield from check(pkg, schema, "$")


def validate_package(pkg_path: str, check_files: bool = False) -> Tuple[bool, list]:
    reasons: list = []
    try:
        with open(pkg_path, "r", encoding="utf-8") as f:
            pkg = json.load(f)
    except Exception as e:
        return False, [f"JSON parse failed: {e}"]

    schema = _load_schema()
    reasons.extend(list(_minimal_schema_check(pkg, schema)))

    # 1. review_manifest_sha256 一致
    declared = pkg.get("review_manifest_sha256", "")
    computed = compute_review_manifest_sha(pkg)
    if declared != computed:
        reasons.append(
            f"review_manifest_sha256 mismatch: declared={declared} computed={computed}"
        )

    # 2. sample_rate 只允许 48000（当前系统事实）
    sr = pkg.get("sample_rate")
    if sr != 48000:
        reasons.append(f"sample_rate must be 48000 (got {sr!r})")

    # 3. 候选逐个校验
    cids = set()
    for i, c in enumerate(pkg.get("candidates", [])):
        cid = c.get("candidate_id", f"<idx-{i}>")
        if cid in cids:
            reasons.append(f"duplicate candidate_id {cid}")
        cids.add(cid)

        declared_sem = c.get("semantic_sha256", "")
        computed_sem = compute_candidate_semantic_sha(c)
        if declared_sem != computed_sem:
            reasons.append(
                f"{cid} semantic_sha256 mismatch: "
                f"declared={declared_sem} computed={computed_sem}"
            )

        # global_cut 必须同时含 female/male
        gc = c.get("global_cut", {})
        tracks = set(gc.get("applies_to_tracks", []))
        if tracks != {"female", "male"}:
            reasons.append(
                f"{cid} global_cut.applies_to_tracks must be exactly "
                f"{{'female','male'}}, got {tracks!r}"
            )

        # sample 一致性
        if c.get("start_sample", 0) >= c.get("end_sample", 0):
            reasons.append(f"{cid} start_sample >= end_sample")
        if sr and c.get("end_sample", 0) > sr * 24 * 3600:
            reasons.append(f"{cid} end_sample out of 24h bound")

        # global_cut ↔ candidate sample 一致
        if gc.get("start_sample") != c.get("start_sample"):
            reasons.append(f"{cid} global_cut.start_sample != candidate.start_sample")
        if gc.get("end_sample") != c.get("end_sample"):
            reasons.append(f"{cid} global_cut.end_sample != candidate.end_sample")

    # 4. preview_assets 文件哈希（可选）
    if check_files:
        for k, meta in pkg.get("preview_assets", {}).items():
            path = meta.get("path")
            declared_h = meta.get("sha256", "")
            if not path or not os.path.isfile(path):
                reasons.append(f"preview_assets[{k}].path missing on disk: {path}")
                continue
            actual = sha256_file(path)
            if actual != declared_h:
                reasons.append(
                    f"preview_assets[{k}] sha mismatch: "
                    f"declared={declared_h} actual={actual}"
                )

    return len(reasons) == 0, reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", help="path to review_package.json")
    ap.add_argument("--check-files", action="store_true")
    args = ap.parse_args()
    ok, reasons = validate_package(args.package, check_files=args.check_files)
    if ok:
        print("PASS")
        return 0
    print("FAIL", file=sys.stderr)
    for r in reasons:
        print(f"  - {r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
