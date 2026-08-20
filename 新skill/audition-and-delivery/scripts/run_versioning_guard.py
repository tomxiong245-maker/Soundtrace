#!/usr/bin/env python3
"""run_versioning_guard — 拦 A/B clip 版本累积漂移 + manifest sha 不一致.

skill 承诺（skills/audition-and-delivery/SKILL.md line 130）:

    run_versioning_guard — 校验每候选 manifest.json 的 automix wav sha256 ==
    render_<variant>/speech.mono.wav sha256，并强制 current_audit_clips/ 单一目录
    （拦 v20X_* 累积）。

之前该 tool 只在 SKILL.md 承诺里出现 · **没有脚本、也未在 tools.json 注册** ·
被 orphan 扫描点名为 P1 "承诺零实现"。本脚本落地承诺，并可挂到主流水
audition-and-delivery skill 的出口。

CLI 契约
--------
  --run-dir  PATH        单个 run 目录（默认 · 遍历 --project-root/main/runs/*）
  --project-root PATH    项目根（默认 · 从 __file__ 上溯 4 级）
  --json                 JSON 输出
  --strict               任何 WARN 视为 FAIL
  --allow-empty          允许 candidate manifest 无 automix sha 字段
  --check <name>         单条 check（accumulated_dirs / manifest_sha_match / single_current）

Check 覆盖
----------
accumulated_dirs   · 目录名含 `v20[0-9]_*` 的累积目录不许出现（拦 v207→v217 12 版漂移）
manifest_sha_match · current_audit_clips/*.manifest.json 里的 automix_wav_sha256 ==
                     render_<variant>/speech.mono.wav 实际 sha256
single_current     · 只有一个 `current_audit_clips/`，不能有并列的 `current_audit_clips_v20X_*`

退出码
------
  0  全部 PASS（或允许的 WARN）
  1  至少一条 FAIL
  2  BLOCK（找不到 run 目录 / render_<variant>/speech.mono.wav 缺失）
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


V20X_DIR_RE = re.compile(r"v20[0-9]_")


def _project_root_default() -> Path:
    """默认项目根: 本脚本在 skills/audition-and-delivery/scripts/ · 上溯 3 级."""
    return Path(__file__).resolve().parents[3]


def _sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for buf in iter(lambda: fh.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()


def _load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


# --- checks -----------------------------------------------------------------

def check_accumulated_dirs(run_dir: Path) -> tuple[str, str]:
    """拦 v20X_* 累积目录 · 只允许最新一版 current_audit_clips."""
    hits: list[str] = []
    for entry in run_dir.iterdir() if run_dir.is_dir() else []:
        if not entry.is_dir():
            continue
        if V20X_DIR_RE.search(entry.name):
            hits.append(entry.name)
    if hits:
        return "FAIL", f"accumulated v20X_* dirs: {hits[:8]} (should be single current_audit_clips/)"
    return "PASS", f"{run_dir.name}: no v20X_* accumulation"


def check_single_current(run_dir: Path) -> tuple[str, str]:
    """只允许一个 current_audit_clips/ · 不允许 current_audit_clips_v20X_*."""
    variants: list[str] = []
    for entry in run_dir.iterdir() if run_dir.is_dir() else []:
        if not entry.is_dir():
            continue
        if entry.name.startswith("current_audit_clips") and entry.name != "current_audit_clips":
            variants.append(entry.name)
    if variants:
        return "FAIL", f"multiple audit_clips dirs: {variants} (should be single 'current_audit_clips')"
    return "PASS", f"{run_dir.name}: single current_audit_clips/ (or absent)"


def _speech_sha_by_variant(run_dir: Path) -> dict[str, str]:
    """返回 render_<variant> → sha256(speech.mono.wav)."""
    out: dict[str, str] = {}
    for rd in run_dir.glob("render_*"):
        wav = rd / "speech.mono.wav"
        if wav.is_file():
            variant = rd.name.split("render_", 1)[-1]
            out[variant] = _sha256_of(wav)
    return out


def check_manifest_sha_match(run_dir: Path, allow_empty: bool = False) -> tuple[str, str]:
    """current_audit_clips/*.manifest.json 里的 automix wav sha == speech.mono.wav sha."""
    speech = _speech_sha_by_variant(run_dir)
    if not speech:
        return "BLOCK", f"{run_dir.name}: no render_<variant>/speech.mono.wav present"
    manifests = list((run_dir / "current_audit_clips").glob("*.manifest.json"))
    if not manifests:
        return "BLOCK", f"{run_dir.name}: no current_audit_clips/*.manifest.json"
    mismatches: list[str] = []
    checked = 0
    missing_field = 0
    for m in manifests:
        try:
            doc = _load_json(m)
        except Exception as exc:
            mismatches.append(f"{m.name} unreadable ({exc!r})")
            continue
        if doc.get("skipped"):
            continue
        # 兼容多种字段名
        recorded = (doc.get("automix_wav_sha256")
                    or doc.get("render_variant_speech_sha")
                    or doc.get("speech_mono_wav_sha256"))
        variant = doc.get("render_variant") or doc.get("variant")
        if recorded is None:
            missing_field += 1
            continue
        # 优先 variant 明确比对
        if variant and variant in speech:
            if recorded != speech[variant]:
                mismatches.append(
                    f"{m.name} variant={variant} sha={recorded[:12]}… vs speech={speech[variant][:12]}…"
                )
        else:
            # 没显式 variant · 至少 recorded 必须命中某 render dir
            if recorded not in set(speech.values()):
                mismatches.append(
                    f"{m.name} sha={recorded[:12]}… not in any render_<variant>/speech.mono.wav"
                )
        checked += 1
    if mismatches:
        return "FAIL", f"{run_dir.name}: {len(mismatches)} manifest sha mismatch (sample: {mismatches[:3]})"
    if missing_field and not allow_empty:
        return "WARN", f"{run_dir.name}: {missing_field}/{len(manifests)} manifest without automix_wav_sha256 (pass --allow-empty to silence)"
    return "PASS", f"{run_dir.name}: {checked} manifest sha match"


CHECKS = {
    "accumulated_dirs": lambda rd, **kw: check_accumulated_dirs(rd),
    "single_current": lambda rd, **kw: check_single_current(rd),
    "manifest_sha_match": lambda rd, allow_empty=False, **kw: check_manifest_sha_match(rd, allow_empty=allow_empty),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_versioning_guard",
        description="Version guard for skills/audition-and-delivery A/B clip lifecycle.",
    )
    p.add_argument("--run-dir", type=Path, default=None,
                   help="单个 run 目录（默认 · 遍历 --project-root/main/runs/*）")
    p.add_argument("--project-root", type=Path, default=None,
                   help="项目根（默认 · 从脚本位置上溯）")
    p.add_argument("--check", action="append", default=[],
                   help=f"单条 check（可多次）; options: {sorted(CHECKS)}")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="WARN 视为 FAIL")
    p.add_argument("--allow-empty", action="store_true",
                   help="容忍 manifest 无 automix_wav_sha256 字段")
    p.add_argument("--verbose", action="store_true")
    return p


def _pick_run_dirs(args) -> list[Path]:
    if args.run_dir:
        return [args.run_dir.resolve()]
    root = (args.project_root or _project_root_default()).resolve()
    runs_root = root / "main" / "runs"
    if not runs_root.is_dir():
        return []
    return sorted(p for p in runs_root.iterdir() if p.is_dir())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dirs = _pick_run_dirs(args)
    if not run_dirs:
        print("[versioning_guard] no run directory found", file=sys.stderr)
        return 2

    names = args.check or list(CHECKS.keys())
    for n in names:
        if n not in CHECKS:
            print(f"[versioning_guard] unknown check: {n}", file=sys.stderr)
            return 2

    results: list[dict[str, Any]] = []
    worst = "PASS"
    for rd in run_dirs:
        # skip runs that don't participate in audition (no current_audit_clips and no render_*)
        has_audit = (rd / "current_audit_clips").is_dir()
        has_render = any(rd.glob("render_*"))
        if not has_audit and not has_render:
            continue
        for name in names:
            try:
                status, detail = CHECKS[name](rd, allow_empty=args.allow_empty)
            except Exception as exc:
                status, detail = "FAIL", f"exception: {exc!r}"
            if args.strict and status == "WARN":
                status = "FAIL"
            results.append({"run": rd.name, "check": name,
                            "status": status, "detail": detail})
            if status == "FAIL":
                worst = "FAIL"
            elif status == "BLOCK" and worst != "FAIL":
                worst = "BLOCK"
            elif status == "WARN" and worst == "PASS":
                worst = "WARN"
            if args.verbose or not args.json:
                print(f"[{status:5s}] {rd.name} · {name} · {detail}")

    if args.json:
        print(json.dumps({"results": results, "overall": worst,
                          "runs_scanned": len(run_dirs)},
                         ensure_ascii=False, indent=2))
    if worst == "FAIL":
        return 1
    if worst == "BLOCK":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
