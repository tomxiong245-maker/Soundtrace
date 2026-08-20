#!/usr/bin/env python3
"""Build a focused listening page from candidate text and transition-QC tiers."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


TIER_META = {
    "mandatory_semantic_review": ("必须复听", "mandatory"),
    "additional_acoustic_spot_check": ("额外声学抽查", "additional"),
    "routine_spot_check": ("常规抽查", "routine"),
}

FEATURE_LABELS = {
    "absolute_level_step_db": "剪口前后音量差",
    "absolute_crossfade_level_change_db": "crossfade 区域音量差",
    "spectral_distance": "剪口前后频谱差",
    "boundary_jump_dbfs": "边界波形跳变",
}


def e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def text_blocks(candidate: dict[str, object]) -> str:
    blocks = []
    for track in candidate.get("text_tracks", []):
        blocks.append(
            f"""
            <div class="text-block">
              <div class="speaker">{e(track.get('speaker'))}</div>
              <div><span class="label">原始文字</span>{e(track.get('original_text'))}</div>
              <div><span class="label">删后文字</span>{e(track.get('edited_text'))}</div>
            </div>
            """
        )
    return "".join(blocks) or '<div class="muted">该区间没有转写文字，可能是停顿、呼吸或非语音。</div>'


def card(candidate: dict[str, object], qc: dict[str, object]) -> str:
    tier_label, tier_class = TIER_META[qc["review_tier"]]
    features = "、".join(
        FEATURE_LABELS.get(item, item) for item in qc.get("strongest_acoustic_features", [])
    )
    mix = qc["metrics"]["mix"]
    return f"""
    <article class="card {tier_class}" id="{e(candidate['candidate_id'])}">
      <div class="card-head">
        <div>
          <span class="candidate-id">{e(candidate['candidate_id'])}</span>
          <span class="badge {tier_class}">{e(tier_label)}</span>
          <span class="badge neutral">{e(candidate.get('category_label'))}</span>
          <span class="badge neutral">语义风险 {e(candidate.get('risk'))}</span>
        </div>
        <div class="time">{float(candidate['start_seconds']):.3f}s - {float(candidate['end_seconds']):.3f}s</div>
      </div>
      <div class="deleted"><span class="label">明确拟删</span>{e(candidate.get('deleted_text') or '（无转写文字）')}</div>
      <div class="reason"><span class="label">建议理由</span>{e(candidate.get('reason'))}</div>
      {text_blocks(candidate)}
      <div class="players">
        <label>原始上下文<audio controls preload="none" src="{e(candidate['original_preview'])}"></audio></label>
        <label>50 ms 候选删减<audio controls preload="none" src="{e(candidate['edited_preview'])}"></audio></label>
      </div>
      <details>
        <summary>客观排序依据（只用于决定先听哪些）</summary>
        <div class="metrics">异常排序分：{float(qc['acoustic_outlier_score']):.3f}；主要特征：{e(features)}。<br>
        混音剪口前后音量差：{e(mix['post_minus_pre_rms_db'])} dB；
        crossfade 相对上下文：{e(mix['crossfade_minus_context_rms_db'])} dB；
        边界跳变：{e(mix['boundary_jump_max_dbfs'])} dBFS；
        频谱差：{e(mix['pre_post_spectral_distance'])}。</div>
      </details>
    </article>
    """


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--qc", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    candidates_path = args.candidates.expanduser()
    qc_path = args.qc.expanduser()
    output_path = args.output.expanduser()
    if any(not path.is_absolute() for path in (candidates_path, qc_path, output_path)):
        parser.error("all paths must be absolute")
    if not candidates_path.is_file() or not qc_path.is_file():
        parser.error("candidate and QC files must exist")
    if output_path.exists():
        parser.error("refusing to overwrite an existing review page")

    candidate_data = json.loads(candidates_path.read_text(encoding="utf-8"))
    qc_data = json.loads(qc_path.read_text(encoding="utf-8"))
    candidates = {item["candidate_id"]: item for item in candidate_data["candidates"]}
    transitions = {item["candidate_id"]: item for item in qc_data["transitions"]}
    sections = []
    for tier in TIER_META:
        label, tier_class = TIER_META[tier]
        ids = [
            candidate_id
            for candidate_id in qc_data["ranked_candidate_ids"]
            if transitions[candidate_id]["review_tier"] == tier
        ]
        body = "".join(card(candidates[item], transitions[item]) for item in ids)
        if tier == "routine_spot_check":
            sections.append(
                f'<details class="routine-group"><summary>{e(label)}（{len(ids)} 项）</summary>{body}</details>'
            )
        else:
            sections.append(f'<section><h2>{e(label)}（{len(ids)} 项）</h2>{body}</section>')

    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EP03 剪口优先复听</title>
<style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1e2329;--muted:#667085;--line:#ddd7ce;--red:#b42318;--amber:#b54708;--green:#027a48}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:32px 20px 80px}} h1{{margin:0 0 8px;font-size:30px}} h2{{margin:36px 0 14px;font-size:22px}}
.intro{{background:#fff7e8;border:1px solid #f4d7a1;border-radius:12px;padding:16px 18px;margin:20px 0}} .muted{{color:var(--muted)}}
.card{{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--line);border-radius:12px;padding:18px;margin:12px 0;box-shadow:0 2px 8px #0000000a}}
.card.mandatory{{border-left-color:var(--red)}} .card.additional{{border-left-color:var(--amber)}} .card.routine{{border-left-color:#98a2b3}}
.card-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}} .candidate-id{{font-size:20px;font-weight:750;margin-right:8px}} .time{{color:var(--muted);white-space:nowrap}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;margin:2px 4px 2px 0}} .badge.mandatory{{background:#fee4e2;color:var(--red)}} .badge.additional{{background:#ffead5;color:var(--amber)}} .badge.neutral{{background:#f2f4f7;color:#475467}}
.label{{font-weight:700;margin-right:8px}} .deleted{{margin-top:14px;background:#fff1f0;padding:9px 11px;border-radius:8px}} .reason{{margin:10px 0}}
.text-block{{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}} .speaker{{font-weight:700;color:#344054;margin-bottom:4px}}
.players{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:14px 0}} .players label{{font-weight:650}} audio{{display:block;width:100%;margin-top:6px}}
details{{margin-top:8px}} summary{{cursor:pointer;font-weight:650}} .metrics{{color:var(--muted);padding:8px 0}} .routine-group{{margin-top:36px}} .routine-group>summary{{font-size:22px}}
@media(max-width:700px){{.players{{grid-template-columns:1fr}}.card-head{{display:block}}.time{{margin-top:6px}}}}
</style></head><body><main>
<h1>EP03 剪口优先复听</h1>
<div class="muted">修复后时间线版，共 26 个候选剪口。</div>
<div class="intro"><strong>怎么用：</strong>先听 9 个“必须复听”，再听 5 个“额外声学抽查”。客观指标只负责排序，不代表自动判定剪口好坏。当前仍是 MVP 批量接受版，不是发布批准。</div>
{''.join(sections)}
</main></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
