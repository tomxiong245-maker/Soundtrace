#!/usr/bin/env python3
"""write_delivery_report — abstract of delivery_orchestrator.py::write_delivery_report.

Reads a run directory's identity/QC/EDL/prediction JSON and writes DELIVERY_REPORT.md.
Self-contained: no dependency on delivery_orchestrator's private helpers so it can
run as a standalone tool via subprocess or be imported by orchestrator.

Both invocation modes are supported:

    # As a library (backward-compatible with delivery_orchestrator caller sites)
    from write_delivery_report import write_delivery_report
    write_delivery_report(run_dir, final_status="human_approved_delivery",
                         special_scope=True)

    # As a CLI tool (registered in tools.json)
    python3 write_delivery_report.py --run-dir /path/to/run \\
        --final-status human_approved_delivery [--special-scope]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _require_identity(run_dir: Path) -> dict[str, Any]:
    ident_path = run_dir / "run_identity.json"
    if not ident_path.is_file():
        raise FileNotFoundError(f"missing run_identity.json in {run_dir}")
    return _read_json(ident_path)


def write_delivery_report(
    run_dir: Path, *, final_status: str, special_scope: bool = False
) -> None:
    identity = _require_identity(run_dir)
    input_manifest = _read_json(run_dir / "input_manifest.json")
    qc = (
        _read_json(run_dir / "qc_report.json")
        if (run_dir / "qc_report.json").is_file()
        else {}
    )
    all_candidates = (
        _read_json(run_dir / "all_candidates.json")
        if (run_dir / "all_candidates.json").is_file()
        else None
    )
    prediction = (
        _read_json(run_dir / "prediction_manifest.json")
        if (run_dir / "prediction_manifest.json").is_file()
        else None
    )
    human_edl = (
        _read_json(run_dir / "human_approved.edl.json")
        if (run_dir / "human_approved.edl.json").is_file()
        else None
    )
    machine_edl = (
        _read_json(run_dir / "machine_assisted_draft.edl.json")
        if (run_dir / "machine_assisted_draft.edl.json").is_file()
        else None
    )
    lines = [
        f"# {identity['episode_id']} 交付报告",
        "",
        f"- Run：`{identity['run_id']}`",
        f"- 状态：`{final_status}`",
        f"- 输入：{input_manifest['track_count']} 条对齐 mono WAV，"
        f"{input_manifest['sample_rate_hz']} Hz，"
        f"{input_manifest['frame_count']} samples。",
        f"- 自动身份/QC：`{qc.get('automatic_qc', 'NOT_RUN')}`。",
    ]
    if all_candidates and all_candidates.get("status") == (
        "SOURCE_CANDIDATE_DETAILS_NOT_RETROACTIVELY_REWRITTEN"
    ):
        counts = all_candidates.get("frozen_action_counts") or {}
        lines.append(
            f"- 历史动作范围：同步剪口 {counts.get('sync_cuts', 0)} 个，"
            f"source-track gate {counts.get('source_track_gates', 0)} 个；"
            "没有倒灌或伪造候选级真人标签。"
        )
    elif all_candidates:
        lines.append(
            f"- 候选：已冻结 {len(all_candidates.get('candidates') or [])} 条 reviewable，"
            f"另有 {all_candidates.get('blocked_count', 0)} 条安全阻断。"
        )
        gaps = all_candidates.get("candidate_coverage", {}).get("not_connected", [])
        if gaps:
            lines.append(
                "- 覆盖缺口：" + "、".join(gaps)
                + " 尚未接入本稳定入口；系统没有把它们静默当作已处理。"
            )
    if prediction and prediction.get("status") != (
        "LEGACY_MACHINE_PROVENANCE_PRESERVED_NOT_USED_AS_HUMAN_LABELS"
    ):
        rows = prediction.get("predictions") or []
        machine_accepts = sum(
            row.get("decision") == "machine_proposed_accept" for row in rows
        )
        human_accepts = sum(row.get("decision") == "human_accept" for row in rows)
        lines.append(
            f"- 剪口来源：逐项真人采用 {human_accepts} 条；"
            f"机器辅助采用 {machine_accepts} 条（仅低风险）。"
        )
    if human_edl and machine_edl:
        lines.append(
            f"- 双 EDL：人审版 {len(human_edl.get('global_sync_actions') or [])} 个全轨动作、"
            f"{len(human_edl.get('source_track_gates') or [])} 个源轨 gate；"
            f"机器辅助版 {len(machine_edl.get('global_sync_actions') or [])} 个全轨动作、"
            f"{len(machine_edl.get('source_track_gates') or [])} 个源轨 gate。"
        )
    transition_qc = qc.get("transition_qc") or {}
    if transition_qc.get("required"):
        reports = transition_qc.get("reports") or {}
        human_transition = reports.get("human_approved") or {}
        machine_transition = reports.get("machine_assisted_draft") or {}
        lines.append(
            "- 剪口复听排序：两份渲染后的 `transition_qc.json` 已按客观异常排序"
            f"（人审版 {human_transition.get('transition_count', '未知')} 个剪口、"
            f"重点复听 {human_transition.get('priority_relisten_count', '未知')} 个；"
            f"机器辅助版 {machine_transition.get('transition_count', '未知')} 个剪口、"
            f"重点复听 {machine_transition.get('priority_relisten_count', '未知')} 个）。"
            "它只安排重点试听，不会自动批准或否决剪辑。"
        )
    elif transition_qc.get("status") == "NOT_REQUIRED":
        lines.append(
            "- 剪口复听排序：本 run 是历史整片试听批准的窄恢复路径，"
            "未追溯补写新的 transition QC。"
        )
    if special_scope:
        lines.append(
            "- 本期采用"
            "“冻结整片试听批准范围”：它授权此 v12 动作集合，"
            "不生成可训练的逐项真人标签，也不改变未来自动剪辑政策。"
        )
    observed = (qc.get("loudness_observations") or {}).get("human_approved")
    if observed and observed.get("status") == (
        "OBSERVED_NOT_A_FROZEN_RELEASE_GATE"
    ):
        delta = observed.get("delta_from_working_target") or {}
        lines.append(
            "- 响度实测：人审版为 "
            f"{observed['observed_integrated_lufs']:.2f} LUFS / "
            f"{observed['observed_true_peak_dbtp']:.2f} dBTP；"
            f"相对 v12 工作目标的差值为 {delta.get('integrated_lu', '未知')} LU / "
            f"{delta.get('true_peak_db', '未知')} dB。"
            "该工作目标尚不是 Mentor 冻结的发布规格。"
        )
    lines.extend(
        [
            "- 音乐：固定授权素材 SHA 已校验；最终音乐模板和实验参数写在 "
            "`music_manifest.json` / render manifest。",
            "- 发布：外部发布未执行。本报告的状态只说明本地交付决定，"
            "不替代平台上架动作。",
            "",
            "## 输出",
            "",
            "两种版本的 WAV/MP3 位于各自 `render_human_approved/` 与 "
            "`render_machine_assisted_draft/` 目录；所有路径均相对于本 run 留痕。",
        ]
    )
    _write_text(run_dir / "DELIVERY_REPORT.md", "\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--final-status", required=True)
    ap.add_argument("--special-scope", action="store_true")
    args = ap.parse_args(argv)
    write_delivery_report(
        args.run_dir.resolve(),
        final_status=args.final_status,
        special_scope=args.special_scope,
    )
    print(f"wrote {args.run_dir / 'DELIVERY_REPORT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
