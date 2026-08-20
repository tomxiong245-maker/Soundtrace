#!/usr/bin/env python3
"""apply_learned_filter.py — 用 65 条真人历史决定过滤当前候选池。

不改任何现有脚本、不装任何东西。做的事：
1. 读 aggregated.json (65 条历史决定)
2. 对每条历史 reject，提取指纹 (reason_key, proposed_text_normalized, source_track, clause_position)
3. 对当前 run 的 all_candidates.json，每条候选查历史指纹：
   - 命中过去 reject → 标 REJECT_BY_HISTORY（应从候选池阻断）
   - 命中过去 accept → 标 PROMOTE_BY_HISTORY（可以提升到 high tier / propagate）
   - 都不命中 → NEUTRAL（继续按 v18 规则走）
4. 输出对比报告 md：v22 现有 12 条候选，经过历史过滤后每条状态

用法：
  python3 apply_learned_filter.py \
    --history skills/editing-experience-distiller/output/preferences-20260815-1330/aggregated.json \
    --run-dir main/runs/EP04/EP04-v22-20260815-1315 \
    --out skills/editing-experience-distiller/output/preferences-20260815-1330/v22_learned_filter_report.md
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from collections import Counter, defaultdict

# 词表：从 65 条决定观察出的模式，用来归纳指纹
FUNCTIONAL_WORDS = {"这个","那个","一些","我们","然后","就是","什么","什麼","也是","因为","所以","但是","或者","而且","但","如果","它","他","她","我","你们"}
CONTENT_WORDS_REJECTED_IN_HISTORY = {"报告","工具","我自己"}  # S038、S056 等历史里 reject 的实词重复
STRONG_HESITATION_ACCEPTED_TOKENS = {"呃","唔","嗯不"}  # 明显犹豫音，历史 accept 过
NEXT_CHAR_TRAP = {  # 词内匹配陷阱 (token, next_char) → 阻断，因为它是完整词的首字
    ("额","度"), ("额","外"),
    ("er","o"), ("er","a"), ("er","e"),  # er 后接字母通常是英文单词
}

def norm(s: str) -> str:
    return (s or "").strip().lower()

def build_history_index(records):
    """按 (reason_key, proposed_text_norm) 分组，统计 accept/reject。"""
    idx = defaultdict(lambda: {"accept": 0, "reject": 0, "cases": []})
    # 也建 fingerprint 的更粗粒度（只 reason_key + track_position）以便命中
    for r in records:
        rk = r.get("reason_key") or r.get("candidate_kind") or "unknown"
        pt = norm(r.get("proposed_text") or "")
        clause = r.get("clause_position") or ""
        for key in [(rk, pt), (rk, pt, clause)]:
            g = idx[key]
            g[r["decision"]] += 1
            g["cases"].append({"case_id": r.get("candidate_id"), "episode_id": r.get("episode_id"), "decision": r["decision"], "feedback": r.get("feedback","")})
    return idx

def get_candidate_text(c):
    """从候选拿拟处理文本，兼容不同 schema."""
    for k in ("proposed_delete_text", "filler_token", "proposed_text", "text"):
        v = c.get(k)
        if v: return norm(v)
    tt = c.get("text_tracks") or {}
    src = c.get("source_track_id")
    if src and isinstance(tt.get(src), dict):
        for k in ("proposed_delete_text", "text"):
            v = tt[src].get(k)
            if v: return norm(v)
    return ""

def get_next_char(c):
    """尝试拿拟处理词后面紧邻的一个字符（用于 next_char_trap 匹配）。"""
    tt = c.get("text_tracks") or {}
    src = c.get("source_track_id")
    if not (src and isinstance(tt.get(src), dict)):
        return ""
    words = tt[src].get("words") or []
    end_s = c.get("end_seconds")
    for w in words:
        if w.get("start_seconds", 0) >= (end_s or 0):
            text = (w.get("text") or "").strip()
            return text[:1] if text else ""
    return ""

def classify(c, hist_idx):
    """返回 (verdict, evidence_list) 其中 verdict ∈ {REJECT_BY_HISTORY, PROMOTE_BY_HISTORY, NEUTRAL}."""
    rk = c.get("reason_key") or c.get("candidate_kind") or "unknown"
    pt = get_candidate_text(c)
    clause = c.get("clause_position") or ""
    ev = []

    # === 硬阻断规则（从历史 reject 中稳定归纳出的模式） ===
    # 1) filler_immediate_repetition 家族 (历史 2/2 reject)
    if c.get("candidate_kind") == "filler_immediate_repetition":
        ev.append("family disabled: filler_immediate_repetition 历史 2/2 reject (S038 报告报告 / S056 我自己我自己)")
        return "REJECT_BY_HISTORY", ev
    # 2) cough_like / mic_bump_like (历史 3/3 假警报)
    if c.get("candidate_kind") in ("cough_like","mic_bump_like"):
        ev.append("family disabled: 历史 3/3 假警报")
        return "REJECT_BY_HISTORY", ev
    # 3) immediate_repetition + 内容实词 (历史 reject: 报告/我自己)
    if rk == "immediate_repetition" and pt in {norm(w) for w in CONTENT_WORDS_REJECTED_IN_HISTORY}:
        ev.append(f"immediate_repetition + 实词「{pt}」历史 reject (S038/S056 明确说'不需要剪')")
        return "REJECT_BY_HISTORY", ev
    # 4) filler_hesitation + strong_token + 下一个字符是"度/外" (额度陷阱)
    if rk == "filler_hesitation":
        nc = get_next_char(c)
        if (pt, nc) in NEXT_CHAR_TRAP:
            ev.append(f"next_char trap: 「{pt}」+ 下一字符「{nc}」= 完整词 (E301/E302/E303 历史 3/3 reject '额度是一个词')")
            return "REJECT_BY_HISTORY", ev
    # 5) 嗯 作 backchannel (历史 4/4 reject)
    if pt == "嗯" or c.get("candidate_kind","").startswith("filler_ack"):
        ev.append("嗯 作 backchannel 历史 4/4 reject (N004-N007 明确'保留活人感')")
        return "REJECT_BY_HISTORY", ev
    # 6) filler_weak + clause-mid (历史 3/3 reject '句中不需要')
    if rk == "filler_hesitation" and c.get("filler_subtype")=="repeated_weak_filler" and clause in ("clause-mid","cross-clause","unknown"):
        ev.append(f"filler_weak + {clause} 历史 3/3 reject (N003 '要保证完整性，这个是在句中')")
        return "REJECT_BY_HISTORY", ev

    # === 正向加分 ===
    # A) filler_hesitation + strong_token + clause-tail/head (C003 明确'很好')
    if rk == "filler_hesitation" and pt in {norm(w) for w in STRONG_HESITATION_ACCEPTED_TOKENS} and clause in ("clause-tail","clause-head","clause-boundary"):
        ev.append(f"strong hesitation「{pt}」+ {clause} 历史 accept (C003 v20 明确留言'很好')")
        return "PROMOTE_BY_HISTORY", ev
    # B) immediate_repetition + 功能词 (N012 这个/这个 + N014 一些/一些 accept '很好')
    if rk == "immediate_repetition" and pt in {norm(w) for w in FUNCTIONAL_WORDS}:
        ev.append(f"immediate_repetition + 功能词「{pt}」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')")
        return "PROMOTE_BY_HISTORY", ev
    # C) global_long_pause + duration >= 1s (S047/C032 accept)
    if rk in ("global_long_pause","long_pause"):
        dur = (c.get("end_seconds") or 0) - (c.get("start_seconds") or 0)
        if dur >= 1.0:
            ev.append(f"长停顿 {dur:.2f}s ≥ 1s 历史 accept (S022/S047/C032)")
            return "PROMOTE_BY_HISTORY", ev

    # === 精确指纹匹配（同 reason_key + 同拟处理词的历史投票） ===
    key = (rk, pt, clause)
    g = hist_idx.get(key) or hist_idx.get((rk, pt))
    if g:
        if g["accept"] > g["reject"] and g["accept"] >= 2:
            ev.append(f"同类历史 {g['accept']} accept / {g['reject']} reject → 倾向 promote")
            return "PROMOTE_BY_HISTORY", ev
        if g["reject"] > g["accept"] and g["reject"] >= 2:
            ev.append(f"同类历史 {g['accept']} accept / {g['reject']} reject → 倾向 reject")
            return "REJECT_BY_HISTORY", ev

    return "NEUTRAL", ev

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path, required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    hist = json.loads(args.history.read_text(encoding="utf-8"))
    records = hist.get("records") or []
    hist_idx = build_history_index(records)

    cands = json.loads((args.run_dir / "all_candidates.json").read_text(encoding="utf-8"))["candidates"]

    # 拟处理词从 review_package.json 更全（含 filler_token 等）
    try:
        pkg = json.loads((args.run_dir / "review_bundle/review_package.json").read_text(encoding="utf-8"))
        pkg_idx = {c["candidate_id"]: c for c in pkg.get("candidates", [])}
    except FileNotFoundError:
        pkg_idx = {}

    # 合并候选特征
    enriched = []
    for c in cands:
        merged = dict(c)
        p = pkg_idx.get(c["candidate_id"], {})
        for k in ("filler_token","filler_subtype","clause_position","text_tracks","candidate_kind","proposed_delete_text","end_seconds","start_seconds"):
            if k in p and k not in merged: merged[k] = p[k]
        enriched.append(merged)

    results = []
    counts = Counter()
    for c in enriched:
        verdict, ev = classify(c, hist_idx)
        counts[verdict] += 1
        results.append({"candidate_id": c["candidate_id"], "reason_key": c.get("reason_key"), "proposed_text": get_candidate_text(c), "clause_position": c.get("clause_position"), "verdict": verdict, "evidence": ev})

    # 输出报告
    lines = [f"# v22 学到的过滤报告", "", f"> 生成方式：`apply_learned_filter.py` · 用 65 条真人历史决定过滤 v22 候选池", f"> 目的：证明历史标签是否真的能影响候选而不是只在前端展示", ""]
    lines.append(f"## 总览")
    lines.append("")
    lines.append(f"- v22 原始候选池: **{len(enriched)}** 条")
    lines.append(f"- 应从历史阻断（不该出现在候选池）: **{counts['REJECT_BY_HISTORY']}** 条")
    lines.append(f"- 应从历史 promote（可自动进 machine_assisted_draft）: **{counts['PROMOTE_BY_HISTORY']}** 条")
    lines.append(f"- 无历史依据、继续人审: **{counts['NEUTRAL']}** 条")
    lines.append(f"- **理论上真人只需审 {counts['NEUTRAL']} 条**（其余由历史直接判定）")
    lines.append("")

    for verdict_label, section_title in [("REJECT_BY_HISTORY","🚫 应被历史阻断（不该提名给你）"),("PROMOTE_BY_HISTORY","✅ 历史 promote（可自动进机器辅助剪）"),("NEUTRAL","❔ 无同类历史依据（需要真人判断）")]:
        rows = [r for r in results if r["verdict"] == verdict_label]
        if not rows: continue
        lines.append(f"## {section_title} · {len(rows)} 条")
        lines.append("")
        for r in rows:
            lines.append(f"- `{r['candidate_id']}` · {r['reason_key']} · 拟删「{r['proposed_text']}」 · {r['clause_position'] or '-'}")
            for e in r["evidence"]:
                lines.append(f"  - 依据: {e}")
        lines.append("")

    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告写入: {args.out}")
    print(f"REJECT={counts['REJECT_BY_HISTORY']}  PROMOTE={counts['PROMOTE_BY_HISTORY']}  NEUTRAL={counts['NEUTRAL']}  TOTAL={len(enriched)}")

if __name__ == "__main__":
    main()
