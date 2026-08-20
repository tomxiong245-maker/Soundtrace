#!/usr/bin/env python3
"""content_verify_cut · 剪后 clip 用 ASR 检测目标词是否真被剪掉.

**用户 2026-08-19 关键洞察**: Optuna 只测剪口干净 (NISQA discontinuity) ·
没测"要剪的词真被剪掉了吗". 加本 script 用 faster-whisper CT2 int8
re-transcribe 剪后 clip · 对比目标词是否 still present.

**用途**:
Stage 6.7 Optuna 每 iter · render 后调本 script:
- 若目标词消失 → content_ok=True · Optuna 无内容 penalty
- 若目标词还在 → content_ok=False · Optuna loss += 5.0

**接口**:
  --clip-path <wav>       剪后 clip 路径
  --target-words <json>   目标词 list · 如 '["呃", "嗯"]'
  --asr-lang              default zh
  --out-json <path>       输出结果

**输出 schema**:
{
  "content_ok": bool,
  "target_words_input": [...],
  "target_words_still_present": [...],  # 剪后仍然存在的
  "target_words_absent": [...],         # 剪后确认消失的
  "detected_words": [...],              # 剪后 ASR 检出的全部 words
  "clip_path": str,
  "clip_duration_seconds": float,
  "asr_engine": "faster_whisper_small_ct2_int8",
  "computed_at_utc": ISO 8601
}
"""

from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def transcribe_clip(clip_path: Path, lang: str = "zh") -> list[dict]:
    """用 faster-whisper 转写 clip · 返回 word list."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("需 faster-whisper · pip install faster-whisper")

    # 找 local model cache
    root = Path.home() / ".cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots"
    snapshots = sorted((p for p in root.glob("*") if p.is_dir()), reverse=True) if root.is_dir() else []
    model_ref = str(snapshots[0]) if snapshots else "small"

    model = WhisperModel(model_ref, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(clip_path), language=lang, beam_size=5, word_timestamps=True,
        vad_filter=True, condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({
                "text": w.word.strip(),
                "start": float(w.start),
                "end": float(w.end),
                "prob": float(w.probability) if w.probability else None,
            })
    return words


def check_target_words_removed(detected_words: list[dict], target_words: list[str]) -> dict:
    """对比剪后 words · 判定目标词是否消失."""
    # 归一化 · 去 punctuation · 归 unicode NFKC
    import unicodedata
    import re

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s)
        s = re.sub(r"[\s,\.\!\?，。！？、；：\-]+", "", s)
        return s.lower()

    detected_texts = [norm(w["text"]) for w in detected_words]
    detected_concat = "".join(detected_texts)

    still_present = []
    absent = []
    for tw in target_words:
        tw_norm = norm(tw)
        if not tw_norm:
            absent.append(tw)
            continue
        # 直接 substring match (中文短词 · 允许包含在剪后文本里)
        if tw_norm in detected_concat:
            still_present.append(tw)
        else:
            absent.append(tw)

    return {
        "still_present": still_present,
        "absent": absent,
        "content_ok": len(still_present) == 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clip-path", required=True, type=Path)
    ap.add_argument("--target-words", required=True, type=str,
                    help="JSON list · 如 [\"呃\", \"嗯\"]")
    ap.add_argument("--asr-lang", default="zh")
    ap.add_argument("--out-json", required=True, type=Path)
    args = ap.parse_args()

    if not args.clip_path.is_file():
        raise SystemExit(f"clip 不存在: {args.clip_path}")

    target_words = json.loads(args.target_words)
    if not isinstance(target_words, list):
        raise SystemExit("--target-words 必须是 JSON list")

    # 若 target_words 空 · 直接 content_ok
    if not target_words:
        out = {
            "content_ok": True,
            "target_words_input": [],
            "target_words_still_present": [],
            "target_words_absent": [],
            "detected_words": [],
            "clip_path": str(args.clip_path),
            "clip_duration_seconds": 0.0,
            "asr_engine": "skipped_no_target_words",
            "computed_at_utc": _utc_now(),
        }
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    # 转写
    detected = transcribe_clip(args.clip_path, args.asr_lang)
    check = check_target_words_removed(detected, target_words)

    # clip 时长 (用 pydub)
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(str(args.clip_path))
        dur_s = len(seg) / 1000.0
    except Exception:
        dur_s = 0.0

    out = {
        "content_ok": check["content_ok"],
        "target_words_input": target_words,
        "target_words_still_present": check["still_present"],
        "target_words_absent": check["absent"],
        "detected_words": detected,
        "clip_path": str(args.clip_path),
        "clip_duration_seconds": dur_s,
        "asr_engine": "faster_whisper_small_ct2_int8",
        "computed_at_utc": _utc_now(),
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if check["content_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
