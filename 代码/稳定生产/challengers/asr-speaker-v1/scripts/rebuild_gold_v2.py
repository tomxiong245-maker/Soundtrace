"""Rebuild gold.json into gold.v2.json.

Purpose:
- Strip inlined ASR predictions ("asr_predictions" per segment) out of gold.json
  into hypotheses/faster_whisper_small_vad_on/S*/{female,male}.words.json.
- Preserve every human-authored field. In this repo they are all currently empty
  (see migration_report.md), but the script is written to migrate non-empty ones
  intact and to REFUSE to run if it detects human-authored content it can't map.
- Never delete gold.json. Copy it to gold.json.backup and emit gold.v2.json.

Usage (from repo root):
    python 稳定生产/challengers/asr-speaker-v1/scripts/rebuild_gold_v2.py \
        --gold benchmark/EP03-ASR-mini-gold-v1/gold.json \
        --hypotheses-out benchmark/EP03-ASR-mini-gold-v1/hypotheses \
        --gold-v2 benchmark/EP03-ASR-mini-gold-v1/gold.v2.json \
        --backup benchmark/EP03-ASR-mini-gold-v1/gold.json.backup \
        --report benchmark/EP03-ASR-mini-gold-v1/migration_report.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_GOLD_FIELDS = ("transcript", "speaker_attribution", "missed_sentences",
                        "reviewer", "reviewed_at")


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as h:
        while chunk := h.read(1024 * 1024):
            d.update(chunk)
    return d.hexdigest()


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _has_human_content(gold_obj: dict) -> list[str]:
    """Return a list of human-authored non-empty fields discovered."""
    hits = []
    for seg in gold_obj.get("segments", []):
        g = seg.get("gold", {})
        if g.get("transcript"):
            hits.append(f"{seg['id']}.gold.transcript")
        if g.get("speaker_attribution"):
            hits.append(f"{seg['id']}.gold.speaker_attribution")
        if g.get("missed_sentences"):
            hits.append(f"{seg['id']}.gold.missed_sentences")
        if g.get("reviewer"):
            hits.append(f"{seg['id']}.gold.reviewer")
        if g.get("reviewed_at"):
            hits.append(f"{seg['id']}.gold.reviewed_at")
    return hits


def build_v2(gold_obj: dict, hypotheses_dir: Path, gold_original_sha: str) -> tuple[dict, list[dict]]:
    """Return (gold_v2, hypothesis_writes)."""
    segments_v2 = []
    hyp_writes: list[dict] = []
    for seg in gold_obj["segments"]:
        seg_v2 = {
            "id": seg["id"],
            "kind": seg["kind"],
            "start_seconds_in_ep03": seg["start_seconds_in_ep03"],
            "duration_seconds": seg["duration_seconds"],
            "note": seg.get("note", ""),
            "linked_candidate": seg.get("linked_candidate"),
            "files": seg["files"],
            "gold": {
                "transcript": seg.get("gold", {}).get("transcript", ""),
                "speaker_attribution": seg.get("gold", {}).get("speaker_attribution", []),
                "missed_sentences": seg.get("gold", {}).get("missed_sentences", []),
                "reviewer": seg.get("gold", {}).get("reviewer", ""),
                "reviewed_at": seg.get("gold", {}).get("reviewed_at", ""),
                "note": seg.get("gold", {}).get("note", ""),
                "reviewed": bool(seg.get("gold", {}).get("reviewer") and
                                 seg.get("gold", {}).get("transcript")),
            },
        }
        # Move asr_predictions -> hypotheses layer
        preds = seg.get("asr_predictions", {})
        for engine_key, pred in preds.items():
            if engine_key != "faster_whisper_small_vad_on":
                # unrecognized inlined engine; keep as raw for auditing
                hyp_writes.append({
                    "path": hypotheses_dir / engine_key / seg["id"] / "unknown_track.raw.json",
                    "obj": pred,
                })
                continue
            for track_key in ("female_words", "male_words"):
                words = pred.get(track_key)
                if words is None:
                    continue
                track = "female" if track_key == "female_words" else "male"
                # These are stripped preview words (fields text, s, e, cls) as in gold.json.
                # We store them as a preview raw layer, NOT as a fully-normalized transcript,
                # because they lack SHA/model_id/sample_rate. A separate slice step from the
                # freshrun transcript writes the *proper* normalized layer.
                hyp_writes.append({
                    "path": hypotheses_dir / engine_key / seg["id"] / f"{track}.preview.json",
                    "obj": {
                        "layer": "preview_from_gold_json_v1",
                        "engine": engine_key,
                        "segment_id": seg["id"],
                        "source_track": track,
                        "words": words,
                        "sourced_from_gold_json_sha256": gold_original_sha,
                        "note": ("Preview only; canonical normalized layer must be produced by "
                                 "slice_baseline_from_freshrun.py against the Champion transcript."),
                    },
                })
        segments_v2.append(seg_v2)

    gold_v2 = {
        "schema_version": 2,
        "status": gold_obj.get("status", "WAITING_FOR_HUMAN_GOLD"),
        "generated_at": gold_obj.get("generated_at"),
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "note": ("gold.v2 只保留 12 段元信息与人工字段；ASR 预测已剥离到 hypotheses/。"
                 " 严禁用任何模型输出自动填写 gold.transcript / speaker_attribution / missed_sentences。"),
        "original_gold_json_sha256": gold_original_sha,
        "segments": segments_v2,
    }
    return gold_v2, hyp_writes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--gold-v2", type=Path, required=True)
    ap.add_argument("--hypotheses-out", type=Path, required=True)
    ap.add_argument("--backup", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    gold_obj = json.loads(args.gold.read_text(encoding="utf-8"))
    hits = _has_human_content(gold_obj)
    gold_sha = sha256_file(args.gold)

    if hits and not args.force:
        report = [
            "# gold.json 迁移前置报告",
            "",
            f"原文件 SHA-256：`{gold_sha}`",
            "",
            "在原 gold.json 中发现下列**已有非空的人工字段**，迁移前必须显式确认：",
            "",
        ] + [f"- {h}" for h in hits] + [
            "",
            "为避免丢失，本命令未生成 gold.v2.json。请人工确认这些字段是否真为人工填写，",
            "如果是，重新运行时加 `--force`；本脚本会把它们原样搬入 gold.v2.json。",
        ]
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
        print("REFUSED: human-authored fields detected. See migration_report.md.", file=sys.stderr)
        return 2

    # Backup first
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.gold, args.backup)

    gold_v2, hyp_writes = build_v2(gold_obj, args.hypotheses_out, gold_sha)
    _write(args.gold_v2, gold_v2)
    for hw in hyp_writes:
        _write(hw["path"], hw["obj"])

    report = [
        "# gold.json → gold.v2.json 迁移报告",
        "",
        f"迁移时间：{datetime.now(timezone.utc).isoformat()}",
        f"原文件 SHA-256：`{gold_sha}`",
        f"备份：`{args.backup}`",
        f"新文件：`{args.gold_v2}`",
        "",
        "## 人工字段状态",
        "",
        ("发现的人工字段：" + (", ".join(hits) if hits else "**无**（全为空）。")),
        "",
        "## 迁移内容",
        "",
        f"- 剥离到 hypotheses 的引擎数：{len({hw['path'].parent.parent.name for hw in hyp_writes})}",
        f"- 剥离到 hypotheses 的文件数：{len(hyp_writes)}",
        "",
        "## 严禁事项",
        "",
        "- 不得用任何模型输出自动填 gold.transcript / speaker_attribution / missed_sentences。",
        "- 不得删除 gold.json；本迁移**只增不删**。",
        "- 迁移后 gold.v2.json 的 `status` 仍为 WAITING_FOR_HUMAN_GOLD。",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("gold.v2 written:", args.gold_v2)
    print("report:", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
