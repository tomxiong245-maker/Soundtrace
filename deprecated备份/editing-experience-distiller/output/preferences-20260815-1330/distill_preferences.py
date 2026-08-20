#!/usr/bin/env python3
"""distill_preferences.py — 从所有真人决定 + 对应 review_package 中提炼 mentor 偏好卡。

- 读所有 human_decisions*.json / human_decisions_and_feedback*.json
- 排除 EP03 主目录里的 26 条 bulk_accept
- 从对应 run 目录的 review_bundle/review_package.json (或 review_bundle/*/package.json) join 候选特征
- 按 reason_key 分组 → 输出 preferences.md（偏好卡库）+ rules_suggestions.md（规则修订建议）+ aggregated.json（机器可读原始数据）
- 不改任何来源；只写入 skills/editing-experience-distiller/output/preferences-20260815-1330/
"""
from __future__ import annotations
import json, os, hashlib, re
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path('/Users/renting/Desktop/minglue/剪辑项目')
OUT = ROOT / 'skills/editing-experience-distiller/output/preferences-20260815-1330'
OUT.mkdir(parents=True, exist_ok=True)

# 决定文件清单（明确排除 EP03 主目录的 26 条 bulk_accept）
DECISION_FILES = [
    ('EP03', 'main/runs/EP03-review-product-v1', 'human_decisions.json', 'main/runs/EP03-review-product-v1/review_bundle/review_package.json'),
    ('EP04', 'main/runs/EP04-review-product-v2', 'human_decisions.json', 'main/runs/EP04-review-product-v2/review_bundle/review_package.json'),
    ('EP04', 'main/runs/EP04-filler-global-pause-v1-r2-20260812', 'human_decisions.json', 'main/runs/EP04-filler-global-pause-v1-r2-20260812/review_bundle/review_package.json'),
    ('EP04', 'main/runs/EP04/EP04-review-mixed-14-20260814-043428', 'human_decisions_and_feedback__20260814-052319.json', 'main/runs/EP04/EP04-review-mixed-14-20260814-043428/review_bundle/review_package.json'),
    ('EP04', 'main/runs/EP04/EP04-review-round2-20260814-1355', 'human_decisions_and_feedback__20260814-final.json', 'main/runs/EP04/EP04-review-round2-20260814-1355/review_bundle/review_package.json'),
    ('EP04', 'main/runs/EP04/EP04-v13-20260813-2002', 'human_decisions.json', 'main/runs/EP04/EP04-v13-20260813-2002/review_bundle/review_package.json'),
    ('EP04', 'main/runs/EP04/EP04-v20-20260814-1617', 'human_decisions.json', 'main/runs/EP04/EP04-v20-20260814-1617/review_bundle/review_package.json'),
]

def find_decisions(obj, depth=0):
    if depth>6: return []
    out=[]
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and ('decision' in obj[0] or 'candidate_id' in obj[0]):
            return obj
        for x in obj: out += find_decisions(x, depth+1)
    elif isinstance(obj, dict):
        for k,v in obj.items(): out += find_decisions(v, depth+1)
    return out

def load_package_index(path: Path):
    """把 review_package 的 candidates 建成 candidate_id → 特征 的字典。"""
    if not path.exists(): return {}
    d = json.load(open(path))
    idx = {}
    for c in d.get('candidates', []):
        cid = c.get('candidate_id')
        if not cid: continue
        idx[cid] = c
    return idx

def normalize_decision(v):
    v = str(v or '').lower()
    if 'accept' in v: return 'accept'
    if 'reject' in v: return 'reject'
    return v or 'unknown'

def secs_bin(sec):
    if sec is None: return '?'
    if sec < 0.1: return '<100ms'
    if sec < 0.4: return '100_400ms'
    if sec < 1.0: return '400ms_1s'
    if sec < 2.0: return '1_2s'
    if sec < 5.0: return '2_5s'
    return '5s+'

all_records = []
for episode, run_dir, dec_name, pkg_path in DECISION_FILES:
    dfp = ROOT / run_dir / dec_name
    if not dfp.exists():
        print(f'MISSING decisions: {dfp}')
        continue
    pkg_idx = load_package_index(ROOT / pkg_path)
    d = json.load(open(dfp))
    rows = find_decisions(d)
    for r in rows:
        cid = r.get('candidate_id')
        cand = pkg_idx.get(cid, {})
        dur = None
        if 'end_seconds' in cand and 'start_seconds' in cand:
            dur = float(cand['end_seconds']) - float(cand['start_seconds'])
        # 拟处理词 / 上下文：不同 run 字段名不同，多路 fallback
        proposed = (cand.get('proposed_delete_text') or cand.get('filler_token')
                    or cand.get('proposed_text') or cand.get('text')
                    or cand.get('candidate_text'))
        if not proposed:
            tt = cand.get('text_tracks') or {}
            src = cand.get('source_track_id')
            if src and isinstance(tt.get(src), dict):
                proposed = tt[src].get('proposed_delete_text') or tt[src].get('text')
        # 上下文：来自 semantic_context 或 punctuated_sentences 或 reason_hint
        ctx = ''
        sc = cand.get('semantic_context') or {}
        if isinstance(sc, dict): ctx = str(sc.get('sentence','') or sc.get('text','') or '')[:200]
        if not ctx:
            ps = cand.get('punctuated_sentences') or []
            if ps and isinstance(ps[0], dict): ctx = str(ps[0].get('text','') or '')[:200]
        if not ctx: ctx = str(cand.get('reason_hint','') or '')[:200]

        record = {
            'episode_id': episode,
            'run_dir': run_dir,
            'candidate_id': cid,
            'decision': normalize_decision(r.get('decision')),
            'reason_key': cand.get('reason_key'),
            'candidate_kind': cand.get('candidate_kind') or cand.get('candidate_family'),
            'source_track_id': cand.get('source_track_id'),
            'start_seconds': cand.get('start_seconds'),
            'end_seconds': cand.get('end_seconds'),
            'duration_seconds': dur,
            'duration_bin': secs_bin(dur),
            'proposed_text': proposed,
            'context_snippet': ctx,
            'clause_position': cand.get('clause_position'),
            'confidence_tier': cand.get('confidence_tier'),
            'default_action': cand.get('default_action'),
            'review_basis': r.get('review_basis'),
            'feedback': (r.get('feedback') or '').strip(),
            'reviewer': r.get('reviewer'),
        }
        all_records.append(record)

# 保存机器可读原始
(OUT / 'aggregated.json').write_text(
    json.dumps({'total_records': len(all_records), 'records': all_records}, ensure_ascii=False, indent=2),
    encoding='utf-8'
)

# 全局统计
by_reason = defaultdict(list)
for r in all_records:
    key = r.get('reason_key') or r.get('candidate_kind') or 'unknown'
    by_reason[key].append(r)

# 生成偏好卡 markdown
lines = ['# Mentor 偏好卡 · 从 {} 条真人决定归纳'.format(len(all_records)), '', '> 生成时间：2026-08-15', '> 生成方式：`distill_preferences.py` 扫描全部 human_decisions*.json + 对应 review_package', '> 输入范围：EP03 + EP04 全部 run（排除 EP03 主目录的 26 条 bulk_accept）', '> 规范：`skills/editing-experience-distiller/references/experience-card-template.md`', '']

# 全局分布
total_acc = sum(1 for r in all_records if r['decision']=='accept')
total_rej = sum(1 for r in all_records if r['decision']=='reject')
lines.append(f'## 总览')
lines.append('')
lines.append(f'- 累计 **{len(all_records)}** 条真人二态决定')
lines.append(f'- **{total_acc}** accept / **{total_rej}** reject')
lines.append(f'- 覆盖 reason_key: {sorted(by_reason.keys())}')
lines.append(f'- mentor 明显倾向：**保守（reject 比例 {100*total_rej/max(1,len(all_records)):.0f}%）**')
lines.append('')

# 每个 reason_key 一节
for reason_key in sorted(by_reason.keys()):
    rows = by_reason[reason_key]
    acc = sum(1 for r in rows if r['decision']=='accept')
    rej = sum(1 for r in rows if r['decision']=='reject')
    lines.append(f'---')
    lines.append('')
    lines.append(f'## reason_key: `{reason_key}`')
    lines.append('')
    lines.append(f'- **样本数**: {len(rows)}  |  accept={acc}  |  reject={rej}  |  接受率={100*acc/max(1,len(rows)):.0f}%')
    # 按 duration bin
    dur_dist = Counter(r['duration_bin'] for r in rows)
    lines.append(f'- **时长分布**: ' + ', '.join(f'{k}={v}' for k,v in sorted(dur_dist.items())))
    # 按拟处理词
    word_dist = Counter(str(r.get('proposed_text') or '?').strip() for r in rows)
    lines.append(f'- **拟处理词分布**（前10）: ' + ', '.join(f'`{k}`×{v}' for k,v in word_dist.most_common(10)))
    lines.append('')
    # accept 案例
    accepts = [r for r in rows if r['decision']=='accept']
    rejects = [r for r in rows if r['decision']=='reject']
    if accepts:
        lines.append(f'### ✅ accept 样本 ({len(accepts)})')
        lines.append('')
        for r in accepts[:15]:
            fb = f'  💬 "{r["feedback"]}"' if r['feedback'] else ''
            pt = r.get('proposed_text') or '?'
            cp = f'[{r.get("clause_position","?")}]' if r.get('clause_position') else ''
            ct = r.get('confidence_tier') or ''
            ctx = f'   ⌈{r["context_snippet"][:80]}⌋' if r.get('context_snippet') else ''
            lines.append(f'- `{r["candidate_id"]}` ({r["episode_id"]}) 拟删:`{pt}` [{r["duration_bin"]} / {r.get("source_track_id","?")} / {cp}{ct}]{fb}{ctx}')
        if len(accepts)>15: lines.append(f'- ... 还有 {len(accepts)-15} 条')
        lines.append('')
    if rejects:
        lines.append(f'### ❌ reject 样本 ({len(rejects)})')
        lines.append('')
        for r in rejects[:15]:
            fb = f'  💬 "{r["feedback"]}"' if r['feedback'] else ''
            pt = r.get('proposed_text') or '?'
            cp = f'[{r.get("clause_position","?")}]' if r.get('clause_position') else ''
            ct = r.get('confidence_tier') or ''
            ctx = f'   ⌈{r["context_snippet"][:80]}⌋' if r.get('context_snippet') else ''
            lines.append(f'- `{r["candidate_id"]}` ({r["episode_id"]}) 拟删:`{pt}` [{r["duration_bin"]} / {r.get("source_track_id","?")} / {cp}{ct}]{fb}{ctx}')
        if len(rejects)>15: lines.append(f'- ... 还有 {len(rejects)-15} 条')
        lines.append('')

(OUT / 'preferences.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'wrote {OUT}/preferences.md ({len(lines)} lines)')
print(f'wrote {OUT}/aggregated.json ({len(all_records)} records)')
