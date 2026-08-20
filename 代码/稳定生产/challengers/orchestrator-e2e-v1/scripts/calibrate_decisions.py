#!/usr/bin/env python3
"""EP04-v2 candidates → 自动校对 → human_decisions.calibrated.json

规则（v2：全部候选自动校对，不再一刀切 reject 串音）：
- transient.mic_bump_like       → accept（全轨同步剪切，30 ms crossfade）
- transient.cough_like / thump  → accept 仅当 peak_dbfs > -20 且 duration < 0.4s
- transient 时长 > 0.5s         → reject（避免误伤语音）
- crosstalk.confidence=high     → accept + action=gate_source_track（只对源轨静音，其它轨不动）
- crosstalk.confidence=medium   → reject（源轨还有主讲活动，容易误伤）
- self_correction               → reject（v1 未在 EP04 提名）

reviewer = CALIBRATED_v2_auto
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def calibrate(candidates: list[dict]) -> tuple[list[dict], dict]:
    decisions = []
    stats = {"accept": 0, "reject": 0}
    stats_by_family = {}
    for c in candidates:
        fam = c["reason_family"]
        rk = c["reason_key"]
        dur = float(c.get("end_seconds", 0) - c.get("start_seconds", 0))
        decision = "reject"
        reason = ""
        action = None
        if fam == "transient" and dur <= 0.5:
            if rk == "mic_bump_like":
                decision = "accept"
                action = "sync_cut_all_tracks"
                reason = "mic_bump_like: 明确碰麦"
            elif rk in ("cough_like", "thump_like"):
                peak = float(c.get("peak_dbfs", -60.0))
                if peak > -20.0 and dur < 0.4:
                    decision = "accept"
                    action = "sync_cut_all_tracks"
                    reason = f"{rk}: peak={peak:.1f} dBFS, dur={dur:.2f}s"
                else:
                    reason = f"{rk}: 不够明显 peak={peak:.1f} dur={dur:.2f}s"
        elif fam == "crosstalk":
            conf = c.get("confidence", "medium")
            if conf == "high":
                decision = "accept"
                action = "gate_source_track"
                reason = (
                    f"crosstalk high: 源轨无 primary 词 "
                    f"(bleed={c.get('bleed_words',0)}, "
                    f"other_prim={c.get('other_primary_words_total',0)})"
                )
            else:
                reason = (
                    f"crosstalk medium: 源轨仍有 primary，避免误伤 "
                    f"(prim_on_source={c.get('primary_words_on_source',0)})"
                )
        elif fam == "self_correction":
            reason = "self_correction v1 未提候选"
        decisions.append({
            "candidate_id": c["candidate_id"],
            "decision": decision,
            "action": action,
            "reviewer": "CALIBRATED_v2_auto",
            "decided_at": datetime.utcnow().isoformat() + "Z",
            "review_basis": "text_only",
            "listened_previews": {},
            "calibration_reason": reason,
        })
        stats[decision] += 1
        stats_by_family.setdefault(fam, {"accept": 0, "reject": 0})[decision] += 1
    return decisions, {"totals": stats, "by_family": stats_by_family}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged-candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--package-id", default="EP04-v2b-calibrated")
    ap.add_argument("--episode-id", default="EP04-v2b")
    args = ap.parse_args()

    merged = json.loads(Path(args.merged_candidates).read_text(encoding="utf-8"))
    decisions, stats = calibrate(merged["candidates"])
    doc = {
        "schema_version": "human-decisions-mvp-v1",
        "package_id": args.package_id,
        "episode_id": args.episode_id,
        "reviewer": "CALIBRATED_v2_auto",
        "review_mode": "rule_based_calibration_v2",
        "review_mode_explanation": (
            "本文件由 calibrate_decisions.py v2 自动打标：transient 走全轨同步剪切；"
            "crosstalk.high 走源轨 gate；crosstalk.medium 保守 reject。"
            "reviewer 前缀 CALIBRATED_ 与真人隔离。"
        ),
        "session_started_at": datetime.utcnow().isoformat() + "Z",
        "session_ended_at": datetime.utcnow().isoformat() + "Z",
        "stats": stats,
        "decisions": decisions,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total": len(decisions), **stats}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
