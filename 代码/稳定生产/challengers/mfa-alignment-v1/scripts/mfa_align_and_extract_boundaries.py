#!/usr/bin/env python3
"""mfa_align_and_extract_boundaries — Montreal Forced Aligner 局部对齐 + 边界提取

**动机（2026-08-17 v26 反馈固化）**:
ASR 词 timestamp（faster-whisper 或 mlx-whisper）在词边界上有 **50-150ms 误差**，
剪音频时需要手工扩 100-200ms 覆盖呼吸尾音才不留残留。这个手工扩阈值不稳定
（呃 200ms 够、有些词 300ms 才够），且会误伤相邻词的起声。

Montreal Forced Aligner (MFA 3.4+, mandarin_mfa acoustic + mandarin_china_mfa dict)
用 kaldi 音素级 alignment 得到 **10-20ms 精度**的词/音素边界。

**用法**（run_end_to_end 或独立调用）:

    python3 mfa_align_and_extract_boundaries.py \\
        --candidates /run/all_candidates.json \\
        --tracks Tr1.wav Tr2.wav Tr3.wav \\
        --asr-transcript-dir /run/analysis/ \\
        --context-seconds 5 \\
        --out /run/mfa_boundaries.json

输入：候选列表（每条含 approx start_seconds/end_seconds）+ raw 3 轨 wav + ASR
transcript（供每个局部段生成 corpus text）
输出：`mfa_boundaries.json`，每候选给出精确 word/phone boundary，供下游 EDL
生成器消费（替代 ASR-based boundary + 手工扩 100/200ms）。

**依赖**：miniforge3 + conda base env with:
    conda install -c conda-forge montreal-forced-aligner
    pip install spacy-pkuseg dragonmapper hanziconv
    mfa model download acoustic mandarin_mfa
    mfa model download dictionary mandarin_china_mfa

**约束**:
- 中文字典对英文词 OOV → 需先剥离英文 token（本脚本自动做）
- 局部段最大 10-15s（更长会影响 alignment 精度 + tmp 空间）
- 每候选 ±5s 上下文
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CONDA_MFA_BIN = Path(os.path.expanduser("~/miniforge3/bin/mfa"))


def _run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def slice_amix_mono16k(tracks: list[Path], ctx_start: float, ctx_end: float, out_wav: Path,
                        ffmpeg: str = "/opt/homebrew/bin/ffmpeg") -> None:
    """Slice N tracks in parallel, amix them, downmix to mono @ 16 kHz (MFA input)."""
    cmd = [ffmpeg, "-y", "-v", "error"]
    for t in tracks:
        cmd += ["-ss", f"{ctx_start}", "-to", f"{ctx_end}", "-i", str(t)]
    amix = f"amix=inputs={len(tracks)}:normalize=0"
    cmd += ["-filter_complex", f"{''.join(f'[{i}:a]' for i in range(len(tracks)))}{amix}[out]",
            "-map", "[out]", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)]
    subprocess.run(cmd, check=True, capture_output=True)


def load_asr_words(analysis_dir: Path, track_id: str) -> list[dict[str, Any]]:
    p = analysis_dir / f"{track_id}.transcript.json"
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("words", [])


def transcript_from_words(words: list[dict], ctx_start: float, ctx_end: float) -> str:
    """Concat text of words falling inside [ctx_start, ctx_end], strip non-CJK."""
    tokens = []
    for w in words:
        s = w.get("start_seconds")
        if s is None:
            continue
        if ctx_start <= float(s) <= ctx_end:
            tok = str(w.get("text", "")).strip()
            # 只保留中文（mandarin_china_mfa 字典 OOV 英文/数字）
            tok = "".join(c for c in tok if "一" <= c <= "鿿")
            if tok:
                tokens.append(tok)
    return " ".join(tokens)


def parse_textgrid_words(path: Path) -> list[tuple[float, float, str]]:
    """Return [(xmin, xmax, text)] for the 'words' tier."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r'name = "words"(.+?)(?=item \[|\Z)', text, re.DOTALL)
    block = m.group(1) if m else text
    intervals = []
    for iv in re.finditer(r'xmin = ([\d.]+)\s+xmax = ([\d.]+)\s+text = "([^"]*)"', block):
        xmin, xmax, t = float(iv.group(1)), float(iv.group(2)), iv.group(3).strip()
        if t:
            intervals.append((xmin, xmax, t))
    return intervals


def _detect_language(token: str) -> str:
    """Detect if token is Chinese or English. Returns 'mandarin' | 'english' | 'unknown'."""
    t = str(token or "").strip()
    if not t:
        return "unknown"
    has_zh = any("一" <= c <= "鿿" for c in t)
    has_en = any(c.isascii() and c.isalpha() for c in t)
    if has_zh:
        return "mandarin"
    if has_en:
        return "english"
    return "unknown"


LANG_MODELS = {
    "mandarin": ("mandarin_mfa", "mandarin_china_mfa"),
    "english": ("english_mfa", "english_us_mfa"),
}


def align_corpus(mfa_bin: Path, corpus_dir: Path, output_dir: Path,
                  acoustic: str = "mandarin_mfa",
                  dictionary: str = "mandarin_china_mfa") -> None:
    """Run mfa align on the corpus; must be run in the conda env where MFA lives."""
    cmd = [str(mfa_bin), "align", str(corpus_dir), dictionary, acoustic, str(output_dir),
           "--clean", "--overwrite"]
    subprocess.run(cmd, check=True)


def refine_candidate_boundary(
    candidate: dict[str, Any],
    tracks: list[Path],
    analysis_dir: Path,
    workdir: Path,
    context_s: float = 5.0,
    head_pad_ms: int = 50,
    tail_pad_ms: int = 50,
    language: str = "auto",
) -> dict[str, Any] | None:
    """For one candidate, run MFA over a ±context_s local segment and return
    {mfa_start_seconds, mfa_end_seconds, matched_word_text} in the RAW timeline,
    or None if MFA didn't find the token."""
    cid = str(candidate.get("candidate_id") or "unknown")
    center = float(candidate.get("start_seconds") or 0)
    end = float(candidate.get("end_seconds") or center)
    ctx_start = max(0, min(center, end) - context_s)
    ctx_end = max(center, end) + context_s
    target_token_raw = str(
        candidate.get("filler_token") or candidate.get("proposed_delete_text") or ""
    ).strip()

    # Choose language: auto detects from token, or use explicit
    lang = _detect_language(target_token_raw) if language == "auto" else language
    if lang not in LANG_MODELS:
        return None
    acoustic, dictionary = LANG_MODELS[lang]

    # For mandarin dict: keep only CJK. For english dict: keep only ASCII alpha.
    if lang == "mandarin":
        target_token = "".join(c for c in target_token_raw if "一" <= c <= "鿿")
    else:
        target_token = "".join(c for c in target_token_raw if c.isascii() and c.isalpha()).lower()
    if not target_token:
        return None

    main_track = str(
        candidate.get("source_track_id") or candidate.get("source_track") or "track_01"
    )
    corpus = workdir / "corpus" / "spk"
    corpus.mkdir(parents=True, exist_ok=True)
    for p in corpus.iterdir():
        p.unlink()
    seg_wav = corpus / f"{cid}.wav"
    slice_amix_mono16k(tracks, ctx_start, ctx_end, seg_wav)
    words = load_asr_words(analysis_dir, main_track)
    # Build transcript filtering to selected language script
    tokens = []
    for w in words:
        s = w.get("start_seconds")
        if s is None:
            continue
        if ctx_start <= float(s) <= ctx_end:
            tok = str(w.get("text", "")).strip()
            if lang == "mandarin":
                tok = "".join(c for c in tok if "一" <= c <= "鿿")
            else:
                tok = "".join(c for c in tok if c.isascii() and c.isalpha()).lower()
            if tok:
                tokens.append(tok)
    (corpus / f"{cid}.txt").write_text(" ".join(tokens), encoding="utf-8")

    align_out = workdir / "align_out"
    if align_out.exists():
        shutil.rmtree(align_out)
    align_corpus(CONDA_MFA_BIN, workdir / "corpus", align_out,
                  acoustic=acoustic, dictionary=dictionary)

    tg = align_out / "spk" / f"{cid}.TextGrid"
    if not tg.is_file():
        return None
    intervals = parse_textgrid_words(tg)
    if lang == "english":
        # english MFA lowercase 匹配
        hits = [(xmin, xmax) for xmin, xmax, t in intervals
                if t.lower() == target_token]
    else:
        hits = [(xmin, xmax) for xmin, xmax, t in intervals if t == target_token]
    if not hits:
        return None
    local_center = center - ctx_start
    hits.sort(key=lambda x: abs(((x[0] + x[1]) / 2) - local_center))
    xmin, xmax = hits[0]
    return {
        "candidate_id": cid,
        "target_token": target_token,
        "language": lang,
        "acoustic_model": acoustic,
        "dictionary": dictionary,
        "context_range_raw": [ctx_start, ctx_end],
        "mfa_local_start": xmin,
        "mfa_local_end": xmax,
        "mfa_raw_start": ctx_start + xmin,
        "mfa_raw_end": ctx_start + xmax,
        "head_pad_ms": head_pad_ms,
        "tail_pad_ms": tail_pad_ms,
        "refined_start_raw": ctx_start + xmin - head_pad_ms / 1000,
        "refined_end_raw": ctx_start + xmax + tail_pad_ms / 1000,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--tracks", type=Path, nargs="+", required=True)
    ap.add_argument("--asr-transcript-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--context-seconds", type=float, default=5.0)
    ap.add_argument("--head-pad-ms", type=int, default=50)
    ap.add_argument("--tail-pad-ms", type=int, default=50)
    ap.add_argument("--language", default="auto",
                    choices=["auto", "mandarin", "english"],
                    help="auto detects per candidate (default); force one to skip detection")
    ap.add_argument("--workdir", type=Path, default=None)
    args = ap.parse_args(argv)

    if not CONDA_MFA_BIN.exists():
        print(f"BLOCKED: MFA not installed at {CONDA_MFA_BIN}", file=sys.stderr)
        return 2

    doc = json.loads(args.candidates.read_text(encoding="utf-8"))
    cands = doc.get("candidates") if isinstance(doc, dict) else doc

    with tempfile.TemporaryDirectory() as tmp:
        workdir = args.workdir or Path(tmp)
        workdir.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, Any]] = []
        skipped: list[str] = []
        for c in cands or []:
            try:
                r = refine_candidate_boundary(
                    c, args.tracks, args.asr_transcript_dir,
                    workdir=workdir, context_s=args.context_seconds,
                    head_pad_ms=args.head_pad_ms, tail_pad_ms=args.tail_pad_ms,
                    language=args.language,
                )
            except Exception as exc:
                r = None
                skipped.append(f"{c.get('candidate_id')}:{type(exc).__name__}:{exc}")
            if r is None:
                skipped.append(str(c.get("candidate_id")))
                continue
            results.append(r)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "schema_version": "mfa-boundaries-v1",
        "candidates_source": str(args.candidates),
        "acoustic_model": "mandarin_mfa",
        "dictionary": "mandarin_china_mfa",
        "context_seconds": args.context_seconds,
        "head_pad_ms": args.head_pad_ms,
        "tail_pad_ms": args.tail_pad_ms,
        "refined_count": len(results),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "refined": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"refined": len(results), "skipped": len(skipped),
                      "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
