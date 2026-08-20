#!/usr/bin/env python3
"""build_asr_mini_gold_benchmark.py — 建立 12 片段 ASR gold set 包（等人工标注）"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ffmpeg_slice(ffmpeg: Path, src: Path, out: Path, start_s: float, dur_s: float):
    cmd = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{start_s:.3f}", "-i", str(src), "-t", f"{dur_s:.3f}",
        "-c:a", "pcm_s24le", "-ar", "48000", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.decode('utf-8', errors='ignore')[:400]}")


def load_words(p: Path):
    d = json.loads(p.read_text(encoding="utf-8"))
    return [{
        "word_id": w["word_id"], "text": w["text"],
        "s": w["start_seconds"], "e": w["end_seconds"],
        "cls": w.get("activity", {}).get("classification"),
    } for w in d["words"]]


def words_in_window(words, s, e):
    return [w for w in words if w["e"] > s and w["s"] < e]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freshrun-dir", type=Path,
                    default=PROJECT_ROOT / "main/runs/EP03-freshrun-20260810-1730")
    ap.add_argument("--candidates-json", type=Path,
                    default=PROJECT_ROOT / "审核前端/candidates.json")
    ap.add_argument("--output-dir", type=Path,
                    default=PROJECT_ROOT / "benchmark/EP03-ASR-mini-gold-v1")
    ap.add_argument("--ffmpeg", type=Path,
                    default=PROJECT_ROOT / ".tools/bin/ffmpeg")
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    segments_dir = args.output_dir / "segments"
    segments_dir.mkdir(exist_ok=True)

    fem_wav = args.freshrun_dir / "04_denoise/female.denoised.wav"
    male_wav = args.freshrun_dir / "04_denoise/male.denoised.wav"
    mix_wav = args.freshrun_dir / "04_denoise/speech_mix.denoised.wav"

    fem_words = load_words(args.freshrun_dir / "06_activity/female.classified.json")
    male_words = load_words(args.freshrun_dir / "06_activity/male.classified.json")

    # 从 candidates 拿定位点
    cands = json.loads(args.candidates_json.read_text(encoding="utf-8"))["candidates"]
    cand_by_id = {c["candidate_id"]: c for c in cands}

    # 手工挑 12 个片段，每个 15-30s，覆盖任务书要求
    # 必须覆盖 C001, C007, C010, C036
    def make_seg(sid, kind, anchor_s, dur, note):
        return {"id": sid, "kind": kind, "start_seconds": anchor_s,
                "duration_seconds": dur, "note": note}

    # 用候选的 start_seconds 附近 ±dur/2 作为片段
    def around(c, dur_s, note):
        s = max(0, c["start_seconds"] - dur_s / 2)
        return s, dur_s, note

    plan = []

    # 覆盖任务书指定的 4 个候选
    for cid, tag in [("C001", "must_cover"), ("C007", "must_cover"),
                     ("C010", "must_cover"), ("C036", "must_cover")]:
        c = cand_by_id.get(cid)
        if c is None:
            continue
        s, d, n = around(c, 20.0, f"必覆盖 {cid} ({c['category']}, risk={c['risk']})")
        plan.append({"id": f"S{len(plan)+1:02d}", "kind": tag,
                     "start_seconds": s, "duration_seconds": d, "note": n,
                     "linked_candidate": cid})

    # 3 女声 primary 为主：找 female primary 密集段
    def find_primary_dense(words, top_n=3, dur=20):
        # 20s 窗口滑动，取 primary 占比最高的
        candidates = []
        step = 10
        maxT = words[-1]["e"] if words else 0
        for t in range(20, int(maxT) - 20, step):
            ws = words_in_window(words, t, t + dur)
            if not ws: continue
            pr = sum(1 for w in ws if w["cls"] == "primary")
            candidates.append((pr, t))
        candidates.sort(reverse=True)
        return [t for _, t in candidates[:top_n]]

    female_primary_starts = find_primary_dense(fem_words, 3)
    male_primary_starts = find_primary_dense(male_words, 3)

    for i, s in enumerate(female_primary_starts[:3]):
        plan.append({"id": f"S{len(plan)+1:02d}", "kind": "female_primary",
                     "start_seconds": float(s), "duration_seconds": 20.0,
                     "note": f"女声 primary 密集段 #{i+1}"})
    for i, s in enumerate(male_primary_starts[:3]):
        plan.append({"id": f"S{len(plan)+1:02d}", "kind": "male_primary",
                     "start_seconds": float(s), "duration_seconds": 20.0,
                     "note": f"男声 primary 密集段 #{i+1}"})

    # 2 快速切换：找两轨都活跃的段
    def find_alternating(fw, mw, top_n=2, dur=20):
        step = 10
        maxT = min(fw[-1]["e"], mw[-1]["e"])
        cands = []
        for t in range(20, int(maxT) - 20, step):
            f = sum(1 for w in words_in_window(fw, t, t + dur) if w["cls"] == "primary")
            m = sum(1 for w in words_in_window(mw, t, t + dur) if w["cls"] == "primary")
            if f > 3 and m > 3:
                cands.append((min(f, m), t))
        cands.sort(reverse=True)
        return [t for _, t in cands[:top_n]]

    for i, s in enumerate(find_alternating(fem_words, male_words, 2)):
        plan.append({"id": f"S{len(plan)+1:02d}", "kind": "alternating",
                     "start_seconds": float(s), "duration_seconds": 20.0,
                     "note": f"快速说话人切换 #{i+1}"})

    # 2 ambiguous/overlap
    def find_ambiguous(fw, mw, top_n=2, dur=20):
        step = 10
        maxT = min(fw[-1]["e"], mw[-1]["e"])
        cands = []
        for t in range(20, int(maxT) - 20, step):
            fa = sum(1 for w in words_in_window(fw, t, t + dur) if w["cls"] == "ambiguous")
            ma = sum(1 for w in words_in_window(mw, t, t + dur) if w["cls"] == "ambiguous")
            if fa + ma > 0:
                cands.append((fa + ma, t))
        cands.sort(reverse=True)
        return [t for _, t in cands[:top_n]]

    for i, s in enumerate(find_ambiguous(fem_words, male_words, 2)):
        plan.append({"id": f"S{len(plan)+1:02d}", "kind": "ambiguous_or_overlap",
                     "start_seconds": float(s), "duration_seconds": 20.0,
                     "note": f"ambiguous/重叠 #{i+1}"})

    # 限制到 12 条
    plan = plan[:12]

    # 切音频 + 收集 gold 骨架
    gold_skeleton = {
        "schema_version": 1,
        "status": "WAITING_FOR_HUMAN_GOLD",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "本文件是 gold set 骨架。人工必须填 segments[*].gold.transcript 和 gold.speaker_attribution 后才能算 CER 等指标。",
        "segments": [],
    }

    for seg in plan:
        sid = seg["id"]
        s, d = seg["start_seconds"], seg["duration_seconds"]

        seg_dir = segments_dir / sid
        seg_dir.mkdir(exist_ok=True)

        female_seg = seg_dir / "female.wav"
        male_seg = seg_dir / "male.wav"
        mix_seg = seg_dir / "speech_mix.wav"
        ffmpeg_slice(args.ffmpeg, fem_wav, female_seg, s, d)
        ffmpeg_slice(args.ffmpeg, male_wav, male_seg, s, d)
        ffmpeg_slice(args.ffmpeg, mix_wav, mix_seg, s, d)

        # ASR baseline：直接从现有转写切片段的词
        fw_preds = [{"text": w["text"], "s": round(w["s"] - s, 3), "e": round(w["e"] - s, 3),
                     "cls": w["cls"]} for w in words_in_window(fem_words, s, s + d)]
        mw_preds = [{"text": w["text"], "s": round(w["s"] - s, 3), "e": round(w["e"] - s, 3),
                     "cls": w["cls"]} for w in words_in_window(male_words, s, s + d)]

        gold_skeleton["segments"].append({
            "id": sid,
            "kind": seg["kind"],
            "start_seconds_in_ep03": s,
            "duration_seconds": d,
            "note": seg["note"],
            "linked_candidate": seg.get("linked_candidate"),
            "files": {
                "female_wav": f"segments/{sid}/female.wav",
                "female_wav_sha256": sha256_file(female_seg),
                "male_wav": f"segments/{sid}/male.wav",
                "male_wav_sha256": sha256_file(male_seg),
                "speech_mix_wav": f"segments/{sid}/speech_mix.wav",
                "speech_mix_wav_sha256": sha256_file(mix_seg),
            },
            "asr_predictions": {
                "faster_whisper_small_vad_on": {
                    "female_words": fw_preds,
                    "male_words": mw_preds,
                    "note": "从 EP03-freshrun 转写切片，不重跑 ASR",
                },
                "faster_whisper_small_vad_off": {
                    "status": "NOT_YET_RUN",
                    "note": "本轮不重跑 ASR；未来若要跑 VAD off，在此写入",
                },
            },
            "gold": {
                "transcript": "",
                "speaker_attribution": [],
                "missed_sentences": [],
                "reviewer": "",
                "reviewed_at": "",
                "note": "人工填。speaker_attribution: [{start, end, speaker: female|male|overlap|uncertain}]",
            },
        })

    (args.output_dir / "gold.json").write_text(
        json.dumps(gold_skeleton, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 生成 label.html
    label_html = generate_label_html(gold_skeleton)
    (args.output_dir / "label.html").write_text(label_html, encoding="utf-8")

    print(f"=== ASR benchmark 包生成 ===")
    print(f"  片段数: {len(gold_skeleton['segments'])}")
    print(f"  输出目录: {args.output_dir}")
    print(f"  状态: WAITING_FOR_HUMAN_GOLD")
    return 0


def generate_label_html(gold):
    segments_json = json.dumps(gold, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>ASR mini gold 标注</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 20px auto; padding: 20px; }}
  .segment {{ border: 1px solid #ccc; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
  .segment h3 {{ margin: 0 0 8px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 12px; }}
  audio {{ width: 100%; margin: 4px 0; }}
  .track-label {{ font-size: 12px; color: #666; margin-top: 8px; }}
  .asr-preview {{ background: #f4f4f4; padding: 8px; border-radius: 4px; font-size: 13px; margin: 8px 0; }}
  textarea {{ width: 100%; min-height: 100px; margin-top: 8px; padding: 8px; }}
  #export {{ padding: 10px 20px; background: #18785f; color: #fff; border: 0; border-radius: 6px; cursor: pointer; font-size: 15px; }}
  .status {{ padding: 12px; background: #fff4d6; border: 1px solid #e5c98a; border-radius: 6px; margin-bottom: 20px; }}
</style></head>
<body>
<h1>EP03 ASR mini gold set — 人工标注</h1>
<div class="status">状态：<b>WAITING_FOR_HUMAN_GOLD</b>。填完 12 段后点导出。</div>
<div id="segments"></div>
<button id="export">导出 gold.json</button>
<script>
const DATA = {segments_json};
const state = {{}};
document.getElementById('segments').innerHTML = DATA.segments.map(seg => `
  <div class="segment" data-id="${{seg.id}}">
    <h3>${{seg.id}} · ${{seg.kind}}</h3>
    <div class="meta">EP03 时间：${{seg.start_seconds_in_ep03.toFixed(1)}}s → ${{(seg.start_seconds_in_ep03+seg.duration_seconds).toFixed(1)}}s · ${{seg.note}}</div>
    <div class="track-label">合成 speech_mix</div>
    <audio controls preload="none" src="${{seg.files.speech_mix_wav}}"></audio>
    <div class="track-label">女声 Tr1</div>
    <audio controls preload="none" src="${{seg.files.female_wav}}"></audio>
    <div class="track-label">男声 Tr3</div>
    <audio controls preload="none" src="${{seg.files.male_wav}}"></audio>
    <div class="asr-preview">
      <b>ASR 当前预测（vad_on）</b><br>
      女声：${{seg.asr_predictions.faster_whisper_small_vad_on.female_words.map(w=>w.text).join(' ')}}<br>
      男声：${{seg.asr_predictions.faster_whisper_small_vad_on.male_words.map(w=>w.text).join(' ')}}
    </div>
    <label>正确完整文本（合成 mix 的听感转写）：</label>
    <textarea data-field="transcript" data-id="${{seg.id}}" placeholder="逐句填正确文本"></textarea>
    <label>speaker attribution（每句一行：female|male|overlap|uncertain）：</label>
    <textarea data-field="speaker_attribution" data-id="${{seg.id}}" placeholder="例如：\\nfemale: 大家好\\nmale: 嗯\\nfemale: 我们今天讨论"></textarea>
    <label>系统漏识别的整句（每行一句）：</label>
    <textarea data-field="missed_sentences" data-id="${{seg.id}}"></textarea>
  </div>
`).join('');

document.querySelectorAll('textarea').forEach(t => {{
  t.addEventListener('input', () => {{
    const id = t.dataset.id, field = t.dataset.field;
    state[id] = state[id] || {{}};
    state[id][field] = t.value;
    localStorage.setItem('asr-gold-draft', JSON.stringify(state));
  }});
}});

// 恢复草稿
try {{
  Object.assign(state, JSON.parse(localStorage.getItem('asr-gold-draft') || '{{}}'));
  document.querySelectorAll('textarea').forEach(t => {{
    const s = state[t.dataset.id];
    if (s && s[t.dataset.field]) t.value = s[t.dataset.field];
  }});
}} catch(e) {{}}

document.getElementById('export').addEventListener('click', () => {{
  const filled = DATA.segments.map(seg => {{
    const s = state[seg.id] || {{}};
    return {{
      ...seg,
      gold: {{
        ...seg.gold,
        transcript: s.transcript || '',
        speaker_attribution: (s.speaker_attribution || '').split('\\n').filter(Boolean),
        missed_sentences: (s.missed_sentences || '').split('\\n').filter(Boolean),
        reviewed_at: new Date().toISOString(),
      }}
    }};
  }});
  const output = {{...DATA, status: 'HUMAN_GOLD_FILLED', segments: filled}};
  const blob = new Blob([JSON.stringify(output, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'gold.json';
  a.click();
}});
</script>
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
