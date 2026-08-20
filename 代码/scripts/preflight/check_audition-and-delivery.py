#!/usr/bin/env python3
"""check_audition-and-delivery — pre_flight_check 脚本 (skills/audition-and-delivery/SKILL.md §7).

用途
----
把 SKILL.md §7 里的 10 条 preflight 命令做成一个可跑脚本，替换原本纯注释的 bash 片段。
覆盖两个显式引用点：
  * SKILL.md line 170:  `--check ab_wav_sha_match`
  * SKILL.md line 182:  `--check edl_variant_fields`

CLI 契约
--------
  --check <name>   跑单条检查（可多次给）
  --all            跑全部检查（默认）
  --project-root PATH  改写项目根 (default: 从 __file__ 上溯两级)
  --json           以 JSON 输出汇总
  --verbose        打印每条 check 的完整原因

退出码
------
  0  全部 PASS
  1  至少一条 FAIL
  2  BLOCKED（依赖缺失：例如 release_specs.json 未冻结）

Check 覆盖
----------
release_specs_frozen        · #1  release_specs.json 存在
music_sha_stable            · #2  片头片尾 music.mp3 sha256 == 3f3a715...
session_feedback_sot_exists · #3  §20 单一 SOT current.session_feedback.jsonl 存在
no_v20x_dirs                · #4  run 目录下无 v20[0-9]_* 累积目录
manifest_tools_used_all     · #5  current_audit_clips/*.manifest.json.tools_used_all == true
ab_wav_sha_match            · #6  candidate manifest sha == render_<variant>/speech.mono.wav sha
no_source_track_gate_in_edl · #7  machine_assisted_draft.edl.json 不含 source_track_gate_only render_sync_cut
delivery_approval_chain     · #8  DELIVERY_MANIFEST.approval_chain 含 mentor + project_owner
splice_method_whitelist     · #9  manifest.tools_used 白名单命中
edl_variant_fields          · #10 双 EDL variant/decision/provenance/actions vs global_sync_actions 键位区分正确
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


MUSIC_EXPECTED_SHA = "3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83"
SPLICE_WHITELIST_RE = re.compile(r"pydub\.crossfade \+ append|ffmpeg acrossfade")


def _project_root_default() -> Path:
    """默认项目根 = 本脚本所在目录上溯两级."""
    return Path(__file__).resolve().parents[2]


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


# --- individual checks ------------------------------------------------------

def check_release_specs_frozen(root: Path) -> tuple[str, str]:
    p = root / "main/runs/RELEASE-SPEC-FROM-EP03-20260817-1204/release_specs.json"
    if not p.is_file():
        return "BLOCK", f"release_specs.json missing: {p}"
    return "PASS", str(p.relative_to(root))


def check_music_sha_stable(root: Path) -> tuple[str, str]:
    p = root / "音频参考库/raw material/第三集/片头片尾music.mp3"
    if not p.is_file():
        return "BLOCK", f"music mp3 missing: {p}"
    got = _sha256_of(p)
    if got != MUSIC_EXPECTED_SHA:
        return "FAIL", f"sha drift: got {got}, want {MUSIC_EXPECTED_SHA}"
    return "PASS", f"sha256={got[:12]}…"


def check_session_feedback_sot_exists(root: Path) -> tuple[str, str]:
    p = root / "main/knowledge/session_feedback/current.session_feedback.jsonl"
    if not p.is_file():
        return "FAIL", f"§20 单一 SOT missing: {p}"
    return "PASS", str(p.relative_to(root))


def check_no_v20x_dirs(root: Path) -> tuple[str, str]:
    """run 目录下不许有 v20[0-9]_* 累积目录（§7 line 162）."""
    hits: list[str] = []
    for p in glob.glob(str(root / "main/runs/*/v20[0-9]_*")):
        if Path(p).is_dir():
            hits.append(p)
    if hits:
        return "FAIL", f"v20X_* accumulation dirs: {hits[:5]}"
    return "PASS", "no v20X_* accumulation dirs under main/runs/*"


def check_manifest_tools_used_all(root: Path) -> tuple[str, str]:
    manifests = glob.glob(str(root / "main/runs/*/current_audit_clips/*.manifest.json"))
    missing: list[str] = []
    checked = 0
    for m in manifests:
        try:
            doc = _load_json(Path(m))
        except Exception:
            missing.append(f"{m} (unreadable)")
            continue
        # skipped candidate 用 skipped=... 字段 · 不强制 tools_used_all
        if doc.get("skipped"):
            continue
        checked += 1
        if doc.get("tools_used_all") is not True:
            missing.append(m)
    if missing:
        return "FAIL", f"{len(missing)} manifest missing tools_used_all=true (sample: {missing[:3]})"
    return "PASS", f"checked {checked} manifest.json (non-skipped)"


def _ab_wav_sha_match_one_run(run_dir: Path) -> list[str]:
    """对单个 run 检查每条 candidate manifest 的 automix wav sha == speech.mono.wav sha.

    manifest 里字段名可能是 render_variant / render_variant_speech_sha /
    render_<variant>_speech_mono_sha_256 / automix_wav_sha256 · 择一命中即可。
    """
    errors: list[str] = []
    render_dirs = list(run_dir.glob("render_*"))
    speech_sha_by_variant: dict[str, str] = {}
    for rd in render_dirs:
        wav = rd / "speech.mono.wav"
        if wav.is_file():
            speech_sha_by_variant[rd.name.split("render_", 1)[-1]] = _sha256_of(wav)
    if not speech_sha_by_variant:
        return errors  # 该 run 尚未渲染 · 跳过
    manifests = list((run_dir / "current_audit_clips").glob("*.manifest.json"))
    for m in manifests:
        try:
            doc = _load_json(m)
        except Exception:
            errors.append(f"{m.name} unreadable")
            continue
        if doc.get("skipped"):
            continue
        # 优先查 automix_wav_sha256 / render_variant_speech_sha
        recorded = doc.get("automix_wav_sha256") or doc.get("render_variant_speech_sha")
        variant = doc.get("render_variant") or doc.get("variant")
        if recorded is None and variant is None:
            # 兼容 · manifest 若无相关字段 · skip 该 candidate
            continue
        # variant → speech sha 比对
        if variant and variant in speech_sha_by_variant:
            expected = speech_sha_by_variant[variant]
            if recorded and recorded != expected:
                errors.append(f"{m.name} sha mismatch: manifest={recorded[:12]} vs speech.mono.wav={expected[:12]}")
        # 若 recorded 存在但 variant 缺 · 至少 recorded 必须命中某 render dir 的 speech sha
        elif recorded and recorded not in speech_sha_by_variant.values():
            errors.append(f"{m.name} recorded sha {recorded[:12]} not in any render_<variant>/speech.mono.wav sha")
    return errors


def check_ab_wav_sha_match(root: Path) -> tuple[str, str]:
    all_errors: list[str] = []
    run_dirs = [Path(p) for p in glob.glob(str(root / "main/runs/*"))]
    checked = 0
    for rd in run_dirs:
        if not rd.is_dir():
            continue
        if not (rd / "current_audit_clips").is_dir():
            continue
        errs = _ab_wav_sha_match_one_run(rd)
        if errs:
            all_errors.extend([f"{rd.name}: {e}" for e in errs])
        else:
            checked += 1
    if all_errors:
        return "FAIL", f"{len(all_errors)} sha mismatches (sample: {all_errors[:3]})"
    return "PASS", f"checked {checked} run(s)"


def check_no_source_track_gate_in_edl(root: Path) -> tuple[str, str]:
    """§13 · source_track_gate_only 候选不得进 render_sync_cuts.

    在 machine_assisted_draft.edl.json 里 render_sync_cuts / actions 数组
    每条 action 都不能 cross-reference cough_like/source_track_gate 候选。
    """
    edls = glob.glob(str(root / "main/runs/*/machine_assisted_draft.edl.json"))
    offenders: list[str] = []
    checked = 0
    for e in edls:
        try:
            doc = _load_json(Path(e))
        except Exception:
            continue
        checked += 1
        # 结构差异容错 · actions / render_sync_cuts / global_sync_actions
        acts = doc.get("actions") or doc.get("render_sync_cuts") or []
        for a in acts:
            if a.get("action_type") == "source_track_gate":
                offenders.append(f"{e} action_id={a.get('action_id')}")
            if a.get("cut_scope") == "source_track_gate_only":
                offenders.append(f"{e} action_id={a.get('action_id')} scope=source_track_gate_only")
    if offenders:
        return "FAIL", f"source_track_gate leaked into render (sample: {offenders[:3]})"
    return "PASS", f"checked {checked} EDL(s)"


def check_delivery_approval_chain(root: Path) -> tuple[str, str]:
    """DELIVERY_MANIFEST.approval_chain 必须齐 mentor + project_owner."""
    manifests = glob.glob(str(root / "main/runs/*-DELIVERY-*/DELIVERY_MANIFEST.json"))
    offenders: list[str] = []
    checked = 0
    for m in manifests:
        try:
            doc = _load_json(Path(m))
        except Exception:
            offenders.append(f"{m} unreadable")
            continue
        checked += 1
        roles = {r.get("role") for r in doc.get("approval_chain", [])}
        if not {"mentor", "project_owner"}.issubset(roles):
            offenders.append(f"{m} roles={sorted(r for r in roles if r)}")
    if offenders:
        return "FAIL", f"{len(offenders)} manifest missing mentor+project_owner (sample: {offenders[:3]})"
    return "PASS", f"checked {checked} DELIVERY_MANIFEST"


def check_splice_method_whitelist(root: Path) -> tuple[str, str]:
    """§16 · manifest 里 tools_used 必含 pydub.crossfade+append 或 ffmpeg acrossfade."""
    manifests = glob.glob(str(root / "main/runs/*/current_audit_clips/*.manifest.json"))
    offenders: list[str] = []
    checked = 0
    for m in manifests:
        try:
            raw = Path(m).read_text(encoding="utf-8")
        except Exception:
            continue
        if '"skipped"' in raw and '"true"' not in raw and re.search(r'"skipped"\s*:\s*"[^"]', raw):
            # 跳过 skipped candidate
            continue
        checked += 1
        if not SPLICE_WHITELIST_RE.search(raw):
            offenders.append(m)
    if offenders:
        return "FAIL", f"{len(offenders)} manifest without splice whitelist (sample: {offenders[:3]})"
    return "PASS", f"checked {checked} manifest"


def check_edl_variant_fields(root: Path) -> tuple[str, str]:
    """双 EDL 变体字段冲突自检.

    machine_assisted_draft.edl.json ↔ human_approved.edl.json 两份必须：
      * variant 字段区分（machine_assisted_draft vs human_approved）
      * decision_provenance 不能互串（autocut_gate_v* vs human_whole_episode_audition）
      * machine 版顶层键含 `actions` · human 版顶层键含 `global_sync_actions`
    """
    hits: list[str] = []
    ma_files = glob.glob(str(root / "main/runs/*/machine_assisted_draft.edl.json"))
    for m in ma_files:
        try:
            doc = _load_json(Path(m))
        except Exception:
            continue
        v = doc.get("variant")
        if v not in (None, "machine_assisted_draft"):
            hits.append(f"{m} variant={v} (should be machine_assisted_draft)")
        for a in doc.get("actions", []) or []:
            dp = a.get("decision_provenance", "")
            if "human_whole_episode_audition" in dp:
                hits.append(f"{m} action {a.get('action_id')} has human provenance in machine variant")
    ha_files = glob.glob(str(root / "main/runs/*/human_approved.edl.json"))
    for h in ha_files:
        try:
            doc = _load_json(Path(h))
        except Exception:
            continue
        v = doc.get("variant")
        if v not in (None, "human_approved"):
            hits.append(f"{h} variant={v} (should be human_approved)")
        if "actions" in doc and "global_sync_actions" not in doc:
            hits.append(f"{h} lacks global_sync_actions but has actions (machine schema)")
        for a in doc.get("global_sync_actions", []) or []:
            if "autocut_gate" in a.get("decision_provenance", ""):
                hits.append(f"{h} global action {a.get('action_id')} has autocut_gate provenance")
    if hits:
        return "FAIL", f"variant field conflicts (sample: {hits[:3]})"
    return "PASS", f"checked {len(ma_files)} machine + {len(ha_files)} human EDL(s)"


# --- registry ---------------------------------------------------------------

CHECKS: dict[str, Callable[[Path], tuple[str, str]]] = {
    "release_specs_frozen": check_release_specs_frozen,
    "music_sha_stable": check_music_sha_stable,
    "session_feedback_sot_exists": check_session_feedback_sot_exists,
    "no_v20x_dirs": check_no_v20x_dirs,
    "manifest_tools_used_all": check_manifest_tools_used_all,
    "ab_wav_sha_match": check_ab_wav_sha_match,
    "no_source_track_gate_in_edl": check_no_source_track_gate_in_edl,
    "delivery_approval_chain": check_delivery_approval_chain,
    "splice_method_whitelist": check_splice_method_whitelist,
    "edl_variant_fields": check_edl_variant_fields,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_audition-and-delivery",
        description="pre_flight_check for skills/audition-and-delivery/SKILL.md §7",
    )
    p.add_argument("--check", action="append", default=[],
                   help=f"check name to run (可多次); options: {sorted(CHECKS)}")
    p.add_argument("--all", action="store_true", help="run all checks (default)")
    p.add_argument("--project-root", type=Path, default=None,
                   help="override project root (default: 上溯脚本两级)")
    p.add_argument("--json", action="store_true", help="emit JSON summary")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.project_root or _project_root_default()).resolve()
    if not (root / "main" / "runs").is_dir() and not (root / "音频参考库").is_dir():
        print(f"[preflight] not a project root: {root}", file=sys.stderr)
        return 2

    if args.check:
        names = list(args.check)
        for n in names:
            if n not in CHECKS:
                print(f"[preflight] unknown check: {n}", file=sys.stderr)
                return 2
    else:
        names = list(CHECKS.keys())

    results: list[dict[str, str]] = []
    worst = "PASS"
    for name in names:
        try:
            status, detail = CHECKS[name](root)
        except Exception as exc:
            status, detail = "FAIL", f"exception: {exc!r}"
        results.append({"check": name, "status": status, "detail": detail})
        if status == "FAIL":
            worst = "FAIL"
        elif status == "BLOCK" and worst != "FAIL":
            worst = "BLOCK"
        if args.verbose or not args.json:
            print(f"[{status:5s}] {name} · {detail}")

    if args.json:
        print(json.dumps({"root": str(root), "results": results,
                          "overall": worst}, ensure_ascii=False, indent=2))
    if worst == "FAIL":
        return 1
    if worst == "BLOCK":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
