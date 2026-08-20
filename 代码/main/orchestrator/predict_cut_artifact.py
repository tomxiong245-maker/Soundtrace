#!/usr/bin/env python3
"""predict_cut_artifact.py — 每条候选打"剪口自然度"分数 0-1（1 = 剪辑痕迹严重）。

依据来自 65 条真人历史决定中 8+ 条"剪辑痕迹"reject 的边界特征分析：
1) 边界处 RMS 高 = 有语音尾音 = 剪出来会咔哒
2) 时长 300-700ms 是"剪辑痕迹"reject 高发区（8/10）
3) clause-mid 位置候选边界更难切干净（3/8 剪辑痕迹在此位置）
4) REAUDIT_* 家族只要 reject 过一次就永久标记高风险
5) filler_immediate_repetition / cough_like / mic_bump_like 已 disabled，标最高风险

用法：
  python3 predict_cut_artifact.py --run-dir main/runs/EP04/EP04-v23-...

依赖：纯 stdlib。必须在 snap_candidate_boundaries.py 之后运行（用 start_rms_at_snap / end_rms_at_snap）。
"""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path


# RMS 到 dB 的粗略映射（24-bit 满刻度约 8388608）
RMS_FULL_SCALE_24BIT = 8388608
RMS_FULL_SCALE_16BIT = 32768


def rms_to_dbfs(rms: float, full_scale: float = RMS_FULL_SCALE_24BIT) -> float:
    """RMS → dBFS，最低夹到 -80."""
    if rms <= 0:
        return -80.0
    import math
    return max(-80.0, 20 * math.log10(rms / full_scale))


def duration_seconds(c: dict) -> float:
    if "duration_seconds" in c and c["duration_seconds"] is not None:
        return float(c["duration_seconds"])
    if "start_seconds" in c and "end_seconds" in c:
        return float(c["end_seconds"]) - float(c["start_seconds"])
    return 0.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# 繁简同字映射（P-12 规则：ASR 把同一个字识别成繁体+简体两次 → 不算重复）
# 依据：EP04 v23b C036「什麼」+ C037「什么」相邻，mentor 反馈"没有剪任何有意义的地方"
TRADITIONAL_TO_SIMPLIFIED = {
    "麼": "么", "什麼": "什么",
    "個": "个", "這個": "这个", "那個": "那个",
    "對": "对", "們": "们", "我們": "我们", "你們": "你们",
    "為": "为", "說": "说", "時": "时", "現": "现",
    "應": "应", "還": "还", "會": "会", "後": "后",
    "過": "过", "問": "问", "題": "题", "來": "来",
    "萬": "万", "們": "们", "點": "点", "當": "当",
    "業": "业", "產": "产", "廠": "厂", "動": "动",
    "務": "务", "務": "务", "義": "义", "習": "习",
    "學": "学", "識": "识", "話": "话", "號": "号",
}

def _normalize_ts(s: str) -> str:
    """繁体转简体，用于 immediate_repetition 判等价."""
    return ''.join(TRADITIONAL_TO_SIMPLIFIED.get(c, c) for c in (s or ''))


def predict_one(c: dict) -> dict:
    """返回 {score, reasons: [...], boundary_dbfs, verdict}."""
    reasons = []
    score = 0.0

    # === 硬性阻断：已知假警报家族 / 已 reject 家族 ===
    kind = c.get("candidate_kind") or ""
    reason_key = c.get("reason_key") or ""
    if kind in ("cough_like", "mic_bump_like"):
        return {"score": 1.0, "reasons": ["家族已 disabled: 历史 3/3 假警报"], "verdict": "BLOCK", "boundary_dbfs": None}
    if kind == "filler_immediate_repetition":
        return {"score": 1.0, "reasons": ["家族已 disabled: 历史 2/2 reject (S038 报告报告/S056 我自己我自己)"], "verdict": "BLOCK", "boundary_dbfs": None}
    if reason_key.startswith("REAUDIT_"):
        return {"score": 0.85, "reasons": ["REAUDIT 家族: 已被历史 reject 过的边界样本；除非确认修好否则不再提名"], "verdict": "BLOCK", "boundary_dbfs": None}

    # P-12: 繁简同字 immediate_repetition（EP04 v23b C036/C037 mentor 反馈"没剪到有意义的东西"）
    if reason_key == "immediate_repetition" or kind == "immediate_repetition":
        # 从 text_tracks 拿相邻两次拼写
        tt = c.get("text_tracks") or {}
        src = c.get("source_track_id")
        span_text = ""
        if src and isinstance(tt.get(src), dict):
            words = tt[src].get("words") or []
            span_text = "".join(w.get("text","") for w in words if float(w.get("start_seconds",0)) < float(c.get("end_seconds",0)) and float(w.get("end_seconds",0)) > float(c.get("start_seconds",0)))
        if span_text:
            # 如果整段候选文本繁简化后是一样重复（比如"什麼什么" → "什么什么"），认为是同字重复
            norm = _normalize_ts(span_text)
            n = len(norm)
            if n >= 2 and n % 2 == 0 and norm[:n//2] == norm[n//2:]:
                return {"score": 0.9, "reasons": [f"繁简同字重复「{span_text}」→ 规范化后「{norm}」实际是同一个词的两种写法（ASR 识别副产物），不算真重复"], "verdict": "BLOCK", "boundary_dbfs": None}

    # === 边界 RMS 特征（需要 snap_candidate_boundaries 已跑）===
    snap = c.get("boundary_snap") or {}
    start_rms = snap.get("start_rms_at_snap")
    end_rms = snap.get("end_rms_at_snap")
    boundary_dbfs = None
    if start_rms is not None and end_rms is not None:
        max_rms = max(start_rms, end_rms)
        boundary_dbfs = rms_to_dbfs(max_rms)
        # 阈值：-30dBFS 是历史 mentor "剪辑痕迹" reject 的经验分水岭
        # < -50 dBFS: 静音区，剪口干净，score += 0.0
        # -50 ~ -35 dBFS: 边界过渡区，中等风险，score += 0.2
        # -35 ~ -25 dBFS: 有语音尾音，剪口会有咔哒，score += 0.5
        # > -25 dBFS: 直接剪在语音上，剪辑痕迹重，score += 0.7
        if boundary_dbfs > -25:
            score += 0.7
            reasons.append(f"边界处能量 {boundary_dbfs:.1f} dBFS 高（>-25，剪在语音上）→ 剪辑痕迹重")
        elif boundary_dbfs > -35:
            score += 0.5
            reasons.append(f"边界处能量 {boundary_dbfs:.1f} dBFS 偏高（-35~-25，有语音尾音）→ 剪口可能咔哒")
        elif boundary_dbfs > -50:
            score += 0.2
            reasons.append(f"边界处能量 {boundary_dbfs:.1f} dBFS 中等（-50~-35，过渡区）→ 剪口需 crossfade 掩饰")
        else:
            reasons.append(f"边界处能量 {boundary_dbfs:.1f} dBFS 低（<-50，静音区）→ 剪口干净")

    # === 时长特征（历史 8/10 剪辑痕迹 reject 集中在 300-700ms）===
    dur = duration_seconds(c)
    if 0.3 <= dur <= 0.7:
        score += 0.15
        reasons.append(f"时长 {dur*1000:.0f}ms 落在历史剪辑痕迹高发区间 (300-700ms)")

    # === 位置特征（历史 3/8 剪辑痕迹在 clause-mid）===
    clause = c.get("clause_position") or ""
    if clause in ("clause-mid", "cross-clause", "edge-of-track"):
        score += 0.15
        reasons.append(f"位置 {clause}: 历史相关 reject 高发位置（切在句中很难自然）")

    # === 极短候选特殊处理（<200ms 一般是短促口癖，边界要求高）===
    if dur < 0.2 and boundary_dbfs is not None and boundary_dbfs > -35:
        score += 0.1
        reasons.append(f"短促候选 {dur*1000:.0f}ms + 边界能量高 → 剪不干净")

    score = min(1.0, score)
    if score >= 0.7:
        # BLOCK is a risk verdict, not a visibility or decision instruction.
        # The candidate must remain in the human review scope when selected.
        verdict = "BLOCK"
    elif score >= 0.4:
        verdict = "HUMAN_REVIEW"  # 剪出来风险高，必须真人 A/B 确认
    else:
        verdict = "OK"          # 剪口应该干净
    if not reasons:
        reasons = ["无风险特征命中"]
    return {"score": round(score, 3), "reasons": reasons, "verdict": verdict, "boundary_dbfs": boundary_dbfs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    run_dir = args.run_dir.resolve()
    cands_path = run_dir / "all_candidates.json"
    d = json.loads(cands_path.read_text(encoding="utf-8"))

    # 从 review_package.json 补 clause_position / candidate_kind
    pkg_path = run_dir / "review_bundle" / "review_package.json"
    pkg_lookup = {}
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            pkg_lookup = {c["candidate_id"]: c for c in pkg.get("candidates", []) if c.get("candidate_id")}
        except Exception:
            pass

    counts = {"OK": 0, "HUMAN_REVIEW": 0, "BLOCK": 0}
    blocked_by_artifact = []
    # 收集所有候选先做一遍 predict，然后跨候选 pass
    for c in d.get("candidates", []):
        p = pkg_lookup.get(c.get("candidate_id"), {})
        for k in ("clause_position", "candidate_kind", "duration_seconds"):
            if p.get(k) is not None and c.get(k) is None:
                c[k] = p[k]
        for k in ("text_tracks", "source_track_id"):
            if p.get(k) is not None and c.get(k) is None:
                c[k] = p[k]
        r = predict_one(c)
        c["artifact_risk"] = r

    # === 跨候选 pass：P-12 加强 —— 时间重叠或紧邻(<0.5s) 拟删词繁简同字 → BLOCK 后一条 ===
    # 兼容同轨"什么/什么"和跨轨"什麼(track_01)/什么(track_02)" 两种情况（后者是双麦识别副产物）
    cands = d.get("candidates", [])
    def get_pt(c):
        # 顶层 proposed_delete_text 优先（review_package 里的 canonical 字段），再 filler_token，再 text_tracks
        for k in ("proposed_delete_text", "filler_token"):
            v = c.get(k)
            if v: return str(v).strip()
        src = c.get("source_track_id")
        tt = c.get("text_tracks") or {}
        if src and isinstance(tt.get(src), dict):
            v = tt[src].get("proposed_delete_text") or tt[src].get("text")
            if v: return str(v).strip()
        return ""
    # 从 pkg_lookup 把 proposed_delete_text / filler_token 补到 all_candidates 里的 c
    for c in cands:
        p = pkg_lookup.get(c.get("candidate_id"), {})
        for k in ("proposed_delete_text", "filler_token", "start_seconds", "end_seconds"):
            if p.get(k) is not None and c.get(k) is None:
                c[k] = p[k]
    # 按时间排序
    sorted_cands = sorted(cands, key=lambda x: x.get("start_seconds") or 0)
    for i in range(len(sorted_cands) - 1):
        a = sorted_cands[i]
        b = sorted_cands[i + 1]
        # 只对 immediate_repetition + filler_hesitation 家族做（真重复才要判等价）
        if a.get("candidate_kind") not in ("immediate_repetition", "filler_hesitation") or b.get("candidate_kind") not in ("immediate_repetition", "filler_hesitation"):
            continue
        pa, pb = get_pt(a), get_pt(b)
        if not pa or not pb:
            continue
        # 时间重叠 or 紧邻 (<0.5s)
        a_end = a.get("end_seconds") or 0
        b_start = b.get("start_seconds") or 0
        gap = b_start - a_end
        if gap > 0.5:
            continue
        # 繁简同字（规范化后相等但原文不同 → 副产物）
        if _normalize_ts(pa) == _normalize_ts(pb) and pa != pb:
            same_track = a.get("source_track_id") == b.get("source_track_id")
            b["artifact_risk"] = {
                "score": 0.9,
                "reasons": [
                    f"繁简同字并列：{a['candidate_id']}(track={a.get('source_track_id')}) 拟删「{pa}」 + {b['candidate_id']}(track={b.get('source_track_id')}) 拟删「{pb}」，规范化后同字「{_normalize_ts(pa)}」，gap={gap*1000:.0f}ms",
                    "跨轨双麦识别副产物" if not same_track else "同轨繁简两种拼写",
                    "剪掉没有意义（实际不是重复），保留 " + a["candidate_id"],
                ],
                "verdict": "BLOCK",
                "boundary_dbfs": b.get("artifact_risk", {}).get("boundary_dbfs"),
            }

    # 统计 verdict 并收集 BLOCK
    for c in cands:
        r = c["artifact_risk"]
        counts[r["verdict"]] += 1
        if r["verdict"] == "BLOCK":
            blocked_by_artifact.append(c["candidate_id"])
    d["artifact_risk_summary"] = counts

    # === BLOCK 与 artifact_risk 通过独立文件传给前端 ===
    # 不改 review_package.json（server_episode.py 会校验其 semantic hash 完整性）
    # 独立写 review_bundle/artifact_risks.json，前端 mvp.html 单独 fetch 后 merge
    if pkg_path.parent.is_dir():
        artifact_json = pkg_path.parent / "artifact_risks.json"
        artifact_data = {
            "schema_version": "artifact-risks-v1",
            "run_id": d.get("run_id"),
            "flagged_by_artifact_risk": {
                "candidate_ids": blocked_by_artifact,
                "reason": "predict_cut_artifact 判定 BLOCK（剪辑痕迹重、繁简同字非真重复、家族已 disabled 等）",
                "policy": "仅作为前端风险标记和排序依据；不得自动隐藏、自动 reject 或改变 human review 覆盖范围",
            },
            "risks": {c["candidate_id"]: {"artifact_risk": c.get("artifact_risk"), "boundary_snap_summary": c.get("boundary_snap")} for c in cands},
        }
        artifact_json.write_text(json.dumps(artifact_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 追加到 blocked_candidates.json 作为追溯。追加之后重新计算 SHA，
    # 再写回 all_candidates.json，避免阻断清单与 manifest 脱节。
    blocked_path = run_dir / "candidates" / "blocked_candidates.json"
    if blocked_by_artifact:
        if blocked_path.is_file():
            blocked_doc = json.loads(blocked_path.read_text(encoding="utf-8"))
            existing_ids = {c.get("candidate_id") for c in blocked_doc.get("candidates", [])}
            for cid in blocked_by_artifact:
                if cid in existing_ids:
                    continue
                r_c = next((c for c in cands if c.get("candidate_id") == cid), None)
                if r_c:
                    entry = {"candidate_id": cid, "candidate_kind": r_c.get("candidate_kind"), "source_track_id": r_c.get("source_track_id"), "start_seconds": r_c.get("start_seconds"), "end_seconds": r_c.get("end_seconds"), "artifact_risk": r_c.get("artifact_risk"), "block_reason": "artifact_risk_block", "block_source": "predict_cut_artifact.py"}
                    blocked_doc.setdefault("candidates", []).append(entry)
            blocked_doc["artifact_risk_block_appended"] = len(blocked_by_artifact)
            blocked_path.write_text(json.dumps(blocked_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if blocked_path.is_file():
        blocked_doc = json.loads(blocked_path.read_text(encoding="utf-8"))
        d["blocked_candidates_sha256"] = sha256_file(blocked_path)
        d["blocked_candidates_count"] = len(blocked_doc.get("candidates") or [])

    out = args.out or cands_path
    out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{counts['OK']} OK · {counts['HUMAN_REVIEW']} 需真人 A/B · {counts['BLOCK']} 标记为高风险（不自动隐藏） · 写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
