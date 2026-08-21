#!/usr/bin/env python3
"""extract_gold_cut_features.py · Mentor gold cut 逐条 where/how 特征
2026-08-18 · 用户 "不仅学习哪些应该剪辑,还应该学从哪里剪,怎么剪"

**输入**:
- EP03 gold EDL (56 cuts) + EP03 candidate_package + EP03 ASR + EP03 raw wav
- EP04 gold EDL (3 cuts)  + EP04 machine_assisted (crossfade_ms 来源) + EP04 ASR + EP04 raw wav

**输出**: gold_cut_features.jsonl · 每行一条:
- WHERE: candidate 位置 · ASR prev/next word gap · cross-track speaking · deleted_text
- HOW:   crossfade_ms · RMS envelope · librosa onset · silence gaps

**用途**: workflow 多角度分析这份 jsonl 提炼规则.
"""
from __future__ import annotations
import json, sys, argparse
from pathlib import Path
from typing import Any

import numpy as np
import librosa

ROOT = Path("<HOME>/Desktop/minglue/剪辑项目")
SR = 48000


def rms_db(y: np.ndarray) -> float:
    if len(y) == 0: return -80.0
    return float(20 * np.log10(np.sqrt(np.mean(y**2)) + 1e-9))


def silence_gaps(y: np.ndarray, sr: int, threshold_db: float = -35.0, min_gap_ms: int = 50) -> list[tuple[float,float]]:
    """返回 (start_s, end_s) 静音段."""
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
    rms_dbf = librosa.amplitude_to_db(rms + 1e-9, ref=1.0)
    times = librosa.frames_to_time(range(len(rms_dbf)), sr=sr, hop_length=hop)
    silent = rms_dbf < threshold_db
    gaps = []
    i = 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            gap_ms = (times[j-1] - times[i]) * 1000 if j-1 < len(times) else 0
            if gap_ms >= min_gap_ms:
                gaps.append((float(times[i]), float(times[min(j, len(times)-1)])))
            i = j
        else:
            i += 1
    return gaps


def analyze_cut_audio(raw_wav: Path, cut_start_s: float, cut_end_s: float,
                       ctx_ms: int = 300) -> dict:
    """加载 cut 附近音频, 算 where/how 声学特征."""
    lo = max(0, cut_start_s - ctx_ms/1000)
    hi = cut_end_s + ctx_ms/1000
    y, sr = librosa.load(str(raw_wav), sr=SR, offset=lo, duration=hi-lo, mono=True)
    if len(y) == 0:
        return {"error": "empty audio"}

    # 关键点样本
    s_off = int((cut_start_s - lo) * sr)
    e_off = int((cut_end_s - lo) * sr)
    s_off = max(0, min(len(y)-1, s_off))
    e_off = max(0, min(len(y)-1, e_off))

    # RMS at boundaries + middle
    win = int(0.100 * sr)  # 100ms
    rms_before_cut = rms_db(y[max(0, s_off-win):s_off])
    rms_after_cut  = rms_db(y[e_off:min(len(y), e_off+win)])
    rms_middle     = rms_db(y[s_off:e_off]) if e_off > s_off else -80.0

    # librosa onset
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=True, delta=0.02)
    onsets_abs = [lo + float(t) for t in onsets]
    onset_before_cut = [t for t in onsets_abs if cut_start_s - 0.15 <= t <= cut_start_s + 0.05]
    onset_after_cut  = [t for t in onsets_abs if cut_end_s - 0.05  <= t <= cut_end_s + 0.15]

    # silence gap 分析
    gaps = silence_gaps(y, sr, threshold_db=-35.0, min_gap_ms=50)
    gaps_abs = [(lo + s, lo + e) for s, e in gaps]

    # 判断 cut 是否 land 在 silence gap
    lands_in_silence = False
    boundary_offset_from_silence_edge_ms = None
    for s, e in gaps_abs:
        # cut_start 在静音里, 或 cut_start 与静音起止 < 60ms
        if s <= cut_start_s <= e:
            lands_in_silence = True
            boundary_offset_from_silence_edge_ms = round((cut_start_s - s) * 1000)
            break

    return {
        "rms_before_cut_db": round(rms_before_cut, 1),
        "rms_middle_db": round(rms_middle, 1),
        "rms_after_cut_db": round(rms_after_cut, 1),
        "librosa_onsets_in_window_before_cut_s": [round(t, 4) for t in onset_before_cut],
        "librosa_onsets_in_window_after_cut_s":  [round(t, 4) for t in onset_after_cut],
        "silence_gaps_abs_s": [(round(s, 3), round(e, 3), round((e-s)*1000)) for s, e in gaps_abs],
        "cut_lands_in_silence_gap": lands_in_silence,
        "boundary_offset_from_silence_edge_ms": boundary_offset_from_silence_edge_ms,
    }


def find_asr_word_boundaries(words: list[dict], cut_start_s: float, cut_end_s: float) -> dict:
    """在 ASR words 里找 cut 覆盖的词 + prev_word_end + next_word_start."""
    covered = []
    prev_end = 0.0
    next_start = float("inf")
    for w in words:
        ws = float(w.get("start_seconds", 0))
        we = float(w.get("end_seconds", 0))
        # 词与 cut 有重叠
        if we > cut_start_s and ws < cut_end_s:
            covered.append({"text": w.get("text","").strip(), "start_s": ws, "end_s": we,
                             "gap_from_cut_start_ms": round((ws - cut_start_s)*1000)})
        elif we <= cut_start_s and we > prev_end:
            prev_end = we
        elif ws >= cut_end_s and ws < next_start:
            next_start = ws
    return {
        "covered_words": covered,
        "covered_word_texts": "".join(w["text"] for w in covered),
        "prev_word_end_s": prev_end if prev_end > 0 else None,
        "next_word_start_s": next_start if next_start != float("inf") else None,
        "gap_before_cut_ms": round((cut_start_s - prev_end)*1000) if prev_end > 0 else None,
        "gap_after_cut_ms": round((next_start - cut_end_s)*1000) if next_start != float("inf") else None,
    }


def load_asr(paths: list[Path]) -> dict[str, list[dict]]:
    out = {}
    for p in paths:
        if not p.is_file(): continue
        d = json.loads(p.read_text())
        # 支持不同字段
        words = d.get("words") or d.get("segments_flat") or []
        if not words and "segments" in d:
            # flatten
            for seg in d.get("segments", []):
                for w in seg.get("words", []):
                    words.append(w)
        # 命名 track_id: 从 filename
        key = p.stem.replace(".transcript","")  # e.g. track_01, male, female
        out[key] = words
    return out


def extract_ep04(gold_edl: Path, ma_edl: Path, asr_dir: Path, raw_dir: Path, out_path: Path) -> int:
    gold = json.loads(gold_edl.read_text())
    ma = json.loads(ma_edl.read_text())
    # ma render_sync_cuts 有 crossfade_ms
    xf_by_start = {c["start_sample"]: c for c in ma.get("render_sync_cuts", [])}

    asr_files = list(asr_dir.glob("*.transcript.json"))
    asr = load_asr(asr_files)  # keys: track_01/02/03

    tr_wav = {"track_01": raw_dir/"ZOOM0009_Tr1.WAV",
              "track_02": raw_dir/"ZOOM0009_Tr2.WAV",
              "track_03": raw_dir/"ZOOM0009_Tr3.WAV"}

    # EP04 speaker_map: track_01/02=guest, track_03=host
    with out_path.open("w") as f:
        for c in gold["gold_cuts"]:
            s = c["start_sample"] / SR
            e = c["end_sample"] / SR
            row = {
                "episode": "EP04",
                "gold_cut_id": c["gold_cut_id"],
                "candidate_id": c.get("candidate_id"),
                "start_sample": c["start_sample"], "end_sample": c["end_sample"],
                "start_s": round(s, 4), "end_s": round(e, 4),
                "duration_ms": round((e-s)*1000, 1),
                "provenance": c.get("provenance"),
            }
            # crossfade_ms from ma
            xf = xf_by_start.get(c["start_sample"])
            if xf:
                row["mentor_crossfade_ms"] = xf.get("crossfade_ms")
                row["mentor_crossfade_samples"] = xf.get("crossfade_samples")

            # ASR gap: 试三个 track, 找覆盖词
            per_track = {}
            cross_speaking = False
            for tid, words in asr.items():
                if not tid.startswith("track_"): continue
                per_track[tid] = find_asr_word_boundaries(words, s, e)
                if per_track[tid]["covered_words"]:
                    if tid == "track_03":  # host
                        row["cut_on_host_track"] = True
                    else:
                        row.setdefault("cut_on_guest_tracks", []).append(tid)
            row["per_track_asr"] = per_track

            # cross-track speaking: 其他轨在剪的时刻是否在讲话
            main_track = None
            for tid, info in per_track.items():
                if info["covered_words"]:
                    main_track = tid; break
            if main_track:
                for tid, info in per_track.items():
                    if tid != main_track and info["covered_words"]:
                        cross_speaking = True
            row["cross_track_speaking"] = cross_speaking
            row["main_track_id"] = main_track

            # 声学分析: 用 main_track 对应的 raw wav
            if main_track and main_track in tr_wav and tr_wav[main_track].is_file():
                try:
                    row["acoustic"] = analyze_cut_audio(tr_wav[main_track], s, e)
                except Exception as ex:
                    row["acoustic_error"] = str(ex)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 3


def extract_ep03(edl_path: Path, cand_path: Path, asr_dir: Path, raw_dir: Path, out_path: Path) -> int:
    edl = json.loads(edl_path.read_text())
    cand_by_id = {}
    if cand_path.is_file():
        cj = json.loads(cand_path.read_text())
        for c in cj.get("candidates", []):
            cand_by_id[c["candidate_id"]] = c

    # EP03 ASR: female = track_01, male = track_03
    asr_male = json.loads((asr_dir/"male.transcript.json").read_text())
    asr_female = json.loads((asr_dir/"female.transcript.json").read_text())
    words_male = asr_male.get("words") or []
    words_female = asr_female.get("words") or []
    if not words_male and "segments" in asr_male:
        for seg in asr_male["segments"]:
            words_male.extend(seg.get("words", []))
    if not words_female and "segments" in asr_female:
        for seg in asr_female["segments"]:
            words_female.extend(seg.get("words", []))

    raw_male = raw_dir/"ZOOM0008_Tr3.WAV"
    raw_female = raw_dir/"ZOOM0008_Tr1.WAV"

    with out_path.open("w") as f:
        n = 0
        for c in edl.get("cuts", []):
            cid = c["candidate_id"]
            s = c["start_sample"] / SR
            e = c["end_sample"] / SR
            row = {
                "episode": "EP03",
                "gold_cut_id": f"GOLD_{cid}",
                "candidate_id": cid,
                "start_sample": c["start_sample"], "end_sample": c["end_sample"],
                "start_s": round(s, 4), "end_s": round(e, 4),
                "duration_ms": round((e-s)*1000, 1),
                "gold_deleted_text": c.get("deleted_text",""),
                "gold_reason": c.get("reason",""),
                "gold_category": c.get("category",""),
                "gold_bulk_decision": c.get("bulk_review_decision",""),
                "mentor_crossfade_ms": c.get("crossfade_ms"),
            }
            # candidate 详情 (若有)
            cand = cand_by_id.get(cid, {})
            row["candidate_reason_key"] = cand.get("reason_key","")
            row["candidate_source_track_id"] = cand.get("source_track_id","")

            # ASR per track
            per_track = {"track_male": find_asr_word_boundaries(words_male, s, e),
                         "track_female": find_asr_word_boundaries(words_female, s, e)}
            row["per_track_asr"] = per_track
            main_track = None
            other_speaking = False
            for tid, info in per_track.items():
                if info["covered_words"]:
                    if main_track is None:
                        main_track = tid
                    else:
                        other_speaking = True
            row["main_track_id"] = main_track
            row["cross_track_speaking"] = other_speaking

            # 声学
            raw = raw_male if main_track == "track_male" else raw_female if main_track == "track_female" else None
            if raw and raw.is_file():
                try:
                    row["acoustic"] = analyze_cut_audio(raw, s, e)
                except Exception as ex:
                    row["acoustic_error"] = str(ex)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
            if n % 10 == 0:
                print(f"  EP03 processed {n}/{len(edl['cuts'])}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT/"main/runs/EP04-GOLD-EDL-20260818-1548/gold_cut_features.jsonl")
    args = ap.parse_args()

    # EP04
    n04 = extract_ep04(
        ROOT/"main/runs/EP04-GOLD-EDL-20260818-1548/gold_edl.json",
        ROOT/"main/runs/EP04/EP04-machine-assisted-draft-20260817-002/machine_assisted_draft.edl.json",
        ROOT/"main/runs/EP04-p0-20260811/01_transcripts",
        ROOT/"音频参考库/raw material/第四集",
        args.out,
    )
    print(f"EP04: {n04} cuts")

    # EP03 · append
    ep03_out = args.out.with_name("gold_cut_features_ep03.jsonl")
    n03 = extract_ep03(
        ROOT/"main/runs/EP03-freshrun-20260810-1730/11_approved/approved.all.edl.json",
        ROOT/"main/runs/EP03-freshrun-20260810-1730/09_review/package/edit_candidates.json",
        ROOT/"main/runs/EP03-freshrun-20260810-1730/05_asr",
        ROOT/"音频参考库/raw material/第三集",
        ep03_out,
    )
    print(f"EP03: {n03} cuts → {ep03_out}")
    print(f"EP04: {n04} cuts → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
