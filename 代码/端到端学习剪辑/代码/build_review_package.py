#!/usr/bin/env python3
"""Build a local listening review package from inferred cuts and track transcripts."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_LABELS = {
    "self_correction": "自我修正 / 重说",
    "filler_hesitation": "口癖 / 犹豫",
    "repetition": "重复表达",
    "pause": "停顿 / 空白",
    "transition": "过渡语精简",
    "concision": "内容精简",
    "boundary_review": "边界不确定",
}


def parse_label_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("value must use LABEL=/absolute/path")
    label, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser()
    if not label or not path.is_absolute():
        raise argparse.ArgumentTypeError("value must use LABEL=/absolute/path")
    return label, path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_ffmpeg(explicit: Path | None) -> str:
    if explicit and explicit.expanduser().is_file():
        return str(explicit.expanduser())
    override = os.environ.get("FFMPEG_BIN")
    if override and Path(override).is_file():
        return override
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    raise FileNotFoundError("ffmpeg was not found; pass --ffmpeg or set FFMPEG_BIN")


def run(command: list[str]) -> None:
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr.strip())


def wav_info(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as audio:
        return audio.getframerate(), audio.getnframes()


def join_tokens(tokens: list[str]) -> str:
    output = ""
    for token in tokens:
        if output and output[-1].isascii() and output[-1].isalnum() and token[0].isascii() and token[0].isalnum():
            output += " "
        output += token
    return output


def text_versions_for_interval(
    transcript: dict[str, object], start_sample: int, end_sample: int, context_samples: int
) -> dict[str, object]:
    words = transcript.get("words", [])
    selected = []
    for word in words:
        activity = word.get("activity") or {}
        if activity.get("classification") == "bleed":
            continue
        if (
            word["start_sample"] < end_sample + context_samples
            and word["end_sample"] > start_sample - context_samples
        ):
            selected.append(
                {
                    "text": word["text"],
                    "deleted": word["start_sample"] < end_sample
                    and word["end_sample"] > start_sample,
                }
            )
    spans = []
    for item in selected:
        if spans and spans[-1]["deleted"] == item["deleted"]:
            spans[-1]["tokens"].append(item["text"])
        else:
            spans.append({"deleted": item["deleted"], "tokens": [item["text"]]})
    rendered_spans = [
        {"deleted": item["deleted"], "text": join_tokens(item["tokens"])} for item in spans
    ]
    return {
        "spans": rendered_spans,
        "original_text": join_tokens([item["text"] for item in selected]),
        "edited_text": join_tokens([item["text"] for item in selected if not item["deleted"]]),
        "deleted_text": join_tokens([item["text"] for item in selected if item["deleted"]]),
    }


def suggested_annotation(text: str, duration: float) -> dict[str, str]:
    fillers = ("嗯", "呃", "额", "就是", "然后然后", "那个")
    if any(token in text for token in fillers):
        return {
            "category": "filler_hesitation",
            "reason": "包含犹豫或填充表达，候选删除后再确认语义是否连贯。",
            "risk": "medium",
        }
    if duration <= 0.8:
        return {
            "category": "pause",
            "reason": "短停顿或呼吸空白候选，不应删除完整语词。",
            "risk": "low",
        }
    return {
        "category": "boundary_review",
        "reason": "该区间来自 Mentor 成品反推，当前无法可靠确定其语义删减原因。",
        "risk": "high",
    }


def build_html(payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(str(payload["project_name"]))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} 候选 EDL 审核</title>
<style>
:root {{ color-scheme: light; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #18201f; background: #f4f6f5; letter-spacing: 0; }}
header {{ position: sticky; top: 0; z-index: 10; background: #ffffff; border-bottom: 1px solid #cbd3d0; }}
.toolbar {{ max-width: 1120px; margin: auto; min-height: 68px; padding: 12px 20px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
h1 {{ margin: 0; font-size: 19px; font-weight: 700; }}
.summary {{ color: #53615d; font-size: 14px; margin-right: auto; }}
input, button, select {{ min-height: 38px; font: inherit; letter-spacing: 0; }}
input, select {{ border: 1px solid #aebbb7; border-radius: 5px; padding: 7px 9px; background: #fff; }}
button {{ border: 1px solid #8c9b96; border-radius: 5px; padding: 7px 12px; background: #fff; cursor: pointer; }}
button:disabled {{ opacity: .45; cursor: not-allowed; }}
#export {{ color: #fff; background: #176b5b; border-color: #176b5b; font-weight: 650; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 18px 20px 48px; }}
.candidate {{ background: #fff; border: 1px solid #cbd3d0; border-radius: 7px; padding: 16px; margin-bottom: 12px; }}
.candidate[data-decision="accept"] {{ border-left: 5px solid #18785f; }}
.candidate[data-decision="reject"] {{ border-left: 5px solid #b14b43; }}
.row {{ display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }}
.identity {{ min-width: 110px; font-weight: 750; }}
.time {{ font-variant-numeric: tabular-nums; color: #42514d; }}
.badge {{ font-size: 12px; padding: 3px 7px; border-radius: 4px; }}
.category {{ color: #174f64; background: #dceff5; }}
.confidence {{ color: #6a5630; background: #fff2c7; }}
.risk-high {{ color: #792e28; background: #f8d9d6; }}
.risk-medium {{ color: #6a5630; background: #fff2c7; }}
.risk-low {{ color: #315947; background: #dff0e7; }}
.decision {{ margin-left: auto; display: inline-flex; }}
.decision button {{ border-radius: 0; margin-left: -1px; }}
.decision button:first-child {{ border-radius: 5px 0 0 5px; }}
.decision button:last-child {{ border-radius: 0 5px 5px 0; }}
.decision button.active[data-value="accept"] {{ color: #fff; background: #18785f; border-color: #18785f; }}
.decision button.active[data-value="reject"] {{ color: #fff; background: #b14b43; border-color: #b14b43; }}
.decision button.active[data-value="pending"] {{ background: #e5e9e7; }}
.analysis-grid {{ display: grid; grid-template-columns: minmax(180px,.7fr) minmax(280px,1.3fr); gap: 14px; margin-top: 13px; }}
.field {{ border-left: 3px solid #bdc8c4; padding-left: 10px; }}
.field label, .text-block label {{ display: block; font-size: 12px; color: #62706c; margin-bottom: 5px; }}
.field p, .text-block p {{ margin: 0; line-height: 1.65; color: #34423e; white-space: pre-wrap; }}
.text-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 13px; }}
.text-block {{ border: 1px solid #d5dcda; border-radius: 5px; padding: 11px; min-height: 94px; }}
.speaker {{ display: block; color: #53615d; font-size: 12px; font-weight: 700; margin: 7px 0 2px; }}
.speaker:first-child {{ margin-top: 0; }}
.removed {{ color: #8d302a; background: #fde2df; text-decoration: line-through; text-decoration-thickness: 1px; }}
.audio-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 13px; }}
.audio-grid label {{ display: block; font-size: 12px; color: #62706c; margin-bottom: 5px; }}
audio {{ display: block; width: 100%; height: 38px; }}
.boundaries {{ display: grid; grid-template-columns: repeat(3,minmax(110px,170px)); gap: 10px; margin-top: 13px; }}
.boundaries label {{ font-size: 12px; color: #62706c; }}
.boundaries input {{ display: block; width: 100%; margin-top: 4px; }}
@media (max-width: 720px) {{ .audio-grid, .analysis-grid, .text-grid {{ grid-template-columns: 1fr; }} .boundaries {{ grid-template-columns: 1fr; }} .decision {{ margin-left: 0; }} }}
</style>
</head>
<body>
<header><div class="toolbar">
  <div><h1>{title}</h1><div class="summary" id="summary"></div></div>
  <input id="reviewer" aria-label="审核人" placeholder="审核人">
  <button id="export" disabled>导出 approved.edl.json</button>
</div></header>
<main id="list"></main>
<script>
const DATA = {data};
const KEY = `edl-review:${{DATA.package_id}}`;
const saved = JSON.parse(localStorage.getItem(KEY) || '{{}}');
const state = {{ reviewer: saved.reviewer || '', decisions: saved.decisions || {{}} }};
const list = document.getElementById('list');
const reviewer = document.getElementById('reviewer');
reviewer.value = state.reviewer;
function formatTime(seconds) {{
  const m = Math.floor(seconds / 60); const s = seconds - m * 60;
  return `${{String(m).padStart(2,'0')}}:${{s.toFixed(3).padStart(6,'0')}}`;
}}
function persist() {{
  state.reviewer = reviewer.value.trim();
  localStorage.setItem(KEY, JSON.stringify(state));
  refreshSummary();
}}
function refreshSummary() {{
  const values = DATA.candidates.map(c => (state.decisions[c.candidate_id] || {{}}).decision || 'pending');
  const accepted = values.filter(v => v === 'accept').length;
  const rejected = values.filter(v => v === 'reject').length;
  const pending = values.length - accepted - rejected;
  document.getElementById('summary').textContent = `接受 ${{accepted}} · 拒绝 ${{rejected}} · 待审 ${{pending}}`;
  document.getElementById('export').disabled = pending !== 0 || !reviewer.value.trim();
}}
function render() {{
  DATA.candidates.forEach(candidate => {{
    const decision = state.decisions[candidate.candidate_id] || {{
      decision: 'pending', start_seconds: candidate.start_seconds,
      end_seconds: candidate.end_seconds, crossfade_ms: candidate.crossfade_ms,
      category: candidate.category
    }};
    if (!decision.category) decision.category = candidate.category;
    state.decisions[candidate.candidate_id] = decision;
    const section = document.createElement('section');
    section.className = 'candidate'; section.dataset.decision = decision.decision;
    const top = document.createElement('div'); top.className = 'row';
    const identity = document.createElement('div'); identity.className = 'identity'; identity.textContent = candidate.candidate_id;
    const time = document.createElement('div'); time.className = 'time'; time.textContent = `${{formatTime(candidate.start_seconds)}} → ${{formatTime(candidate.end_seconds)}}`;
    const category = document.createElement('span'); category.className = 'badge category'; category.textContent = candidate.category_label;
    const confidence = document.createElement('span'); confidence.className = 'badge confidence'; confidence.textContent = `反推置信度 ${{candidate.confidence}}`;
    const risk = document.createElement('span'); risk.className = `badge risk-${{candidate.risk}}`; risk.textContent = `误删风险 ${{candidate.risk}}`;
    const controls = document.createElement('div'); controls.className = 'decision';
    [['accept','接受'],['reject','拒绝'],['pending','待审']].forEach(([value,label]) => {{
      const button = document.createElement('button'); button.dataset.value = value; button.textContent = label;
      if (decision.decision === value) button.classList.add('active');
      button.onclick = () => {{ decision.decision = value; section.dataset.decision = value; renderButtons(); persist(); }};
      controls.appendChild(button);
    }});
    const renderButtons = () => controls.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.value === decision.decision));
    top.append(identity,time,category,confidence,risk,controls);
    const analysisGrid = document.createElement('div'); analysisGrid.className = 'analysis-grid';
    const deletedField = document.createElement('div'); deletedField.className = 'field';
    const deletedLabel = document.createElement('label'); deletedLabel.textContent = '拟删内容';
    const deletedText = document.createElement('p'); deletedText.textContent = candidate.deleted_text || '（无明确语词，候选为停顿、呼吸或边界微调）';
    deletedField.append(deletedLabel, deletedText);
    const reasonField = document.createElement('div'); reasonField.className = 'field';
    const reasonLabel = document.createElement('label'); reasonLabel.textContent = '推测的删减理由';
    const reasonText = document.createElement('p'); reasonText.textContent = candidate.reason;
    reasonField.append(reasonLabel, reasonText); analysisGrid.append(deletedField, reasonField);
    const textGrid = document.createElement('div'); textGrid.className = 'text-grid';
    const originalBlock = document.createElement('div'); originalBlock.className = 'text-block';
    const originalLabel = document.createElement('label'); originalLabel.textContent = '原始文字（红色为拟删）'; originalBlock.appendChild(originalLabel);
    const editedBlock = document.createElement('div'); editedBlock.className = 'text-block';
    const editedLabel = document.createElement('label'); editedLabel.textContent = '处理后文字'; editedBlock.appendChild(editedLabel);
    candidate.text_tracks.forEach(track => {{
      const originalSpeaker = document.createElement('span'); originalSpeaker.className = 'speaker'; originalSpeaker.textContent = track.speaker;
      originalBlock.appendChild(originalSpeaker);
      const originalLine = document.createElement('p');
      track.spans.forEach(span => {{ const part = document.createElement('span'); part.textContent = span.text; if (span.deleted) part.className = 'removed'; originalLine.appendChild(part); }});
      originalBlock.appendChild(originalLine);
      const editedSpeaker = document.createElement('span'); editedSpeaker.className = 'speaker'; editedSpeaker.textContent = track.speaker;
      const editedLine = document.createElement('p'); editedLine.textContent = track.edited_text || '（无可显示语词）';
      editedBlock.append(editedSpeaker, editedLine);
    }});
    textGrid.append(originalBlock, editedBlock);
    const audioGrid = document.createElement('div'); audioGrid.className = 'audio-grid';
    [['原始上下文',candidate.original_preview],['候选删除后',candidate.edited_preview]].forEach(([label,src]) => {{
      const box = document.createElement('div'); const caption = document.createElement('label'); caption.textContent = label;
      const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = src;
      box.append(caption,audio); audioGrid.appendChild(box);
    }});
    const boundaries = document.createElement('div'); boundaries.className = 'boundaries';
    [['开始（秒）','start_seconds',0.001],['结束（秒）','end_seconds',0.001],['Crossfade（ms）','crossfade_ms',1]].forEach(([label,key,step]) => {{
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const input = document.createElement('input'); input.type = 'number'; input.step = step; input.value = decision[key];
      if (key === 'crossfade_ms') {{ input.min = 20; input.max = 80; }}
      input.onchange = () => {{ decision[key] = Number(input.value); persist(); }};
      wrapper.appendChild(input); boundaries.appendChild(wrapper);
    }});
    const categoryWrapper = document.createElement('label'); categoryWrapper.textContent = '问题类别';
    const categorySelect = document.createElement('select');
    Object.entries(DATA.categories).forEach(([value,label]) => {{ const option = document.createElement('option'); option.value = value; option.textContent = label; categorySelect.appendChild(option); }});
    categorySelect.value = decision.category; categorySelect.onchange = () => {{ decision.category = categorySelect.value; persist(); }};
    categoryWrapper.appendChild(categorySelect); boundaries.appendChild(categoryWrapper);
    section.append(top,analysisGrid,textGrid,audioGrid,boundaries); list.appendChild(section);
  }});
  persist();
}}
reviewer.addEventListener('input', persist);
document.getElementById('export').onclick = () => {{
  const cuts = DATA.candidates.filter(c => state.decisions[c.candidate_id].decision === 'accept').map(c => {{
    const d = state.decisions[c.candidate_id];
    return {{ candidate_id: c.candidate_id, start_sample: Math.round(d.start_seconds * DATA.time_base_hz), end_sample: Math.round(d.end_seconds * DATA.time_base_hz), crossfade_ms: Math.round(d.crossfade_ms), category: d.category, reason: c.reason, deleted_text: c.deleted_text }};
  }}).sort((a,b) => a.start_sample - b.start_sample);
  const edl = {{ schema_version: 1, review_status: 'approved', reviewer: reviewer.value.trim(), reviewed_at: new Date().toISOString(), time_base_hz: DATA.time_base_hz, source_sha256: DATA.source_sha256, cuts }};
  const blob = new Blob([JSON.stringify(edl,null,2) + '\\n'], {{type:'application/json'}});
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'approved.edl.json'; link.click(); URL.revokeObjectURL(link.href);
}};
render();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=parse_label_path)
    parser.add_argument("--transcript", action="append", required=True, type=parse_label_path)
    parser.add_argument("--reference-report", required=True, type=Path)
    parser.add_argument("--preview-audio", required=True, type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--context-seconds", type=float, default=5.0)
    parser.add_argument("--crossfade-ms", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tracks = [(label, path.expanduser()) for label, path in args.track]
    transcript_paths = [(label, path.expanduser()) for label, path in args.transcript]
    report_path = args.reference_report.expanduser()
    preview_audio = args.preview_audio.expanduser()
    output_dir = args.output_dir.expanduser()
    annotation_path = args.annotations.expanduser() if args.annotations else None
    all_inputs = [*(path for _, path in tracks), *(path for _, path in transcript_paths), report_path, preview_audio]
    if annotation_path:
        all_inputs.append(annotation_path)
    if not output_dir.is_absolute() or any(not path.is_absolute() for path in all_inputs):
        parser.error("all paths must be absolute")
    if any(not path.is_file() for path in all_inputs):
        parser.error("an input file does not exist")
    if not 20 <= args.crossfade_ms <= 80:
        parser.error("--crossfade-ms must be within 20-80")
    if {label for label, _ in tracks} != {label for label, _ in transcript_paths}:
        parser.error("track and transcript labels must match")
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite an existing review package")

    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    sample_rate, frame_count = wav_info(preview_audio)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    annotations = {}
    if annotation_path:
        annotation_data = json.loads(annotation_path.read_text(encoding="utf-8"))
        annotations = annotation_data.get("candidates", annotation_data)
    transcripts = {
        label: json.loads(path.read_text(encoding="utf-8")) for label, path in transcript_paths
    }
    if any(item["sample_rate_hz"] != sample_rate for item in transcripts.values()):
        raise ValueError("transcript and preview sample rates do not match")
    raw_candidates = report.get("inferred_cut_candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("reference report has no inferred_cut_candidates")

    if args.dry_run:
        print(json.dumps({"candidate_count": len(raw_candidates), "output_dir": str(output_dir)}, indent=2))
        return 0

    output_dir.mkdir(parents=True)
    preview_dir = output_dir / "previews"
    preview_dir.mkdir()
    context_samples = round(args.context_seconds * sample_rate)
    crossfade_samples = round(args.crossfade_ms * sample_rate / 1000)
    candidates = []
    for index, raw in enumerate(raw_candidates, start=1):
        start_seconds, end_seconds = raw["approximate_raw_interval_seconds"]
        start_sample = round(start_seconds * sample_rate)
        end_sample = round(end_seconds * sample_rate)
        preview_start = max(0, start_sample - context_samples)
        preview_end = min(frame_count, end_sample + context_samples)
        candidate_id = f"C{index:03d}"
        original = preview_dir / f"{candidate_id}.original.mp3"
        edited = preview_dir / f"{candidate_id}.proposed-cut.mp3"
        run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-i", str(preview_audio), "-af",
                f"atrim=start_sample={preview_start}:end_sample={preview_end},asetpts=PTS-STARTPTS",
                "-c:a", "libmp3lame", "-b:a", "128k", str(original),
            ]
        )
        filter_graph = (
            f"[0:a]atrim=start_sample={preview_start}:end_sample={start_sample},asetpts=PTS-STARTPTS[left];"
            f"[0:a]atrim=start_sample={end_sample}:end_sample={preview_end},asetpts=PTS-STARTPTS[right];"
            f"[left][right]acrossfade=ns={crossfade_samples}:c1=tri:c2=tri[out]"
        )
        run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(preview_audio),
                "-filter_complex", filter_graph, "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k", str(edited),
            ]
        )
        text_tracks = []
        deleted_parts = []
        for label in sorted(transcripts):
            versions = text_versions_for_interval(
                transcripts[label], start_sample, end_sample, context_samples
            )
            if versions["original_text"]:
                speaker = {"female": "女声（Tr1）", "male": "男声（Tr3）"}.get(label, label)
                text_tracks.append({"track": label, "speaker": speaker, **versions})
            if versions["deleted_text"]:
                deleted_parts.append(f"{label}: {versions['deleted_text']}")
        deleted_text = "\n".join(deleted_parts)
        annotation = annotations.get(candidate_id) or suggested_annotation(
            deleted_text, end_seconds - start_seconds
        )
        category_key = annotation["category"]
        if category_key not in CATEGORY_LABELS:
            raise ValueError(f"unknown category for {candidate_id}: {category_key}")
        candidates.append(
            {
                "candidate_id": candidate_id,
                "status": "pending_human_review",
                "start_seconds": round(start_sample / sample_rate, 6),
                "end_seconds": round(end_sample / sample_rate, 6),
                "start_sample": start_sample,
                "end_sample": end_sample,
                "duration_seconds": round((end_sample - start_sample) / sample_rate, 6),
                "crossfade_ms": args.crossfade_ms,
                "confidence": raw.get("confidence", "unknown"),
                "category": category_key,
                "category_label": CATEGORY_LABELS[category_key],
                "reason": annotation["reason"],
                "risk": annotation.get("risk", "medium"),
                "deleted_text": deleted_text,
                "text_tracks": text_tracks,
                "original_preview": f"previews/{original.name}",
                "edited_preview": f"previews/{edited.name}",
                "provenance": "inferred from Mentor reference timeline; not an original Mentor EDL",
            }
        )

    package_id = sha256_file(report_path)[:16]
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_name": "EP03 剪辑候选审核",
        "package_id": package_id,
        "status": "pending_human_review",
        "time_base_hz": sample_rate,
        "frame_count": frame_count,
        "source_sha256": {label: sha256_file(path) for label, path in tracks},
        "categories": CATEGORY_LABELS,
        "candidates": candidates,
    }
    candidates_path = output_dir / "edit_candidates.json"
    html_path = output_dir / "review.html"
    manifest_path = output_dir / "review_manifest.json"
    candidates_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html_path.write_text(build_html(payload), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending_human_review",
                "candidate_count": len(candidates),
                "candidates_path": str(candidates_path),
                "candidates_sha256": sha256_file(candidates_path),
                "review_html": str(html_path),
                "source_report": str(report_path),
                "source_report_sha256": sha256_file(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
