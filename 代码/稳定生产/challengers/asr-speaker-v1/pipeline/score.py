"""score.py — five metrics against silver truth (+ optional human gold).

Silver truth (per 10 ms frame): 0=silence 1=female 2=male 3=overlap 4=ambiguous.

Metrics per ASR engine:
    A. hallucination_in_silence_seconds:
        sum of word durations that fall entirely inside silence frames
    B. missed_speech_seconds:
        length of silver 'female|male|overlap' spans that no hyp word covers
    C. cross_engine_agreement_cer:
        pairwise CER between engines; low = engines agree

Metrics per Diarization engine (mapped to female/male via Hungarian):
    D. speaker_confusion_frames:
        silver has one speaker, hyp says the other
    E. overlap_recall_frames:
        silver=3 frames covered by hyp with ≥2 speakers simultaneously

If human gold is filled (per-segment reviewer + transcript), we additionally
compute absolute CER via jiwer for each ASR engine.
"""

from __future__ import annotations

import argparse
import json
import statistics
from itertools import permutations
from pathlib import Path

import numpy as np


FRAME_MS = 10


def _load_words(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    obj = json.loads(p.read_text(encoding="utf-8"))
    return obj.get("words", [])


def _load_diar_intervals(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    obj = json.loads(p.read_text(encoding="utf-8"))
    return obj.get("intervals", [])


def _text_of(p: Path) -> str:
    if not p.is_file():
        return ""
    obj = json.loads(p.read_text(encoding="utf-8"))
    return obj.get("text", "")


def _cer(ref: str, hyp: str) -> tuple[float, int, int, int]:
    """Return (cer, sub, del, ins) via Levenshtein over unicode chars."""
    n, m = len(ref), len(hyp)
    if n == 0:
        return (0.0 if m == 0 else 1.0), 0, 0, m
    # dp with ops
    dp = [list(range(m + 1))]
    ops = [[(0, i, 0) for i in range(m + 1)]]
    for i in range(1, n + 1):
        row = [i]
        oprow = [(0, i, 0)]
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                row.append(dp[i - 1][j - 1])
                oprow.append(ops[i - 1][j - 1])
            else:
                sub = dp[i - 1][j - 1] + 1
                dele = dp[i - 1][j] + 1
                ins = row[j - 1] + 1
                mv = min(sub, dele, ins)
                row.append(mv)
                if mv == sub:
                    p = ops[i - 1][j - 1]; oprow.append((p[0] + 1, p[1], p[2]))
                elif mv == dele:
                    p = ops[i - 1][j]; oprow.append((p[0], p[1] + 1, p[2]))
                else:
                    p = oprow[j - 1]; oprow.append((p[0], p[1], p[2] + 1))
        dp.append(row); ops.append(oprow)
    s, d, i = ops[n][m]
    return dp[n][m] / n, s, d, i


def _words_to_frames(words, n_frames, frame_ms=FRAME_MS):
    mask = np.zeros(n_frames, dtype=bool)
    for w in words:
        i0 = max(0, int(round(w["start_seconds"] * 1000 / frame_ms)))
        i1 = min(n_frames, int(round(w["end_seconds"] * 1000 / frame_ms)))
        if i1 > i0:
            mask[i0:i1] = True
    return mask


def _diar_to_frames(intervals, n_frames, frame_ms=FRAME_MS):
    """Return per-frame set of speaker labels (list[set[str]])."""
    frames = [set() for _ in range(n_frames)]
    for iv in intervals:
        i0 = max(0, int(round(iv["start_seconds"] * 1000 / frame_ms)))
        i1 = min(n_frames, int(round(iv["end_seconds"] * 1000 / frame_ms)))
        spk = iv["speaker_id"]
        for i in range(i0, i1):
            frames[i].add(spk)
    return frames


def _hungarian_map(silver, diar_frames):
    """Map diar speaker labels -> {female, male} minimizing frame confusion.

    Very small label set (≤4); brute force permutations is fine."""
    labels = set()
    for s in diar_frames:
        labels |= s
    labels = list(labels)
    if not labels:
        return {}
    targets = ["female", "male", None, None][: len(labels)]
    best = None
    best_map = None
    for perm in permutations(targets, len(labels)):
        mapped = [{perm[labels.index(l)] for l in s if perm[labels.index(l)] is not None}
                  for s in diar_frames]
        confusion = 0
        for silv, hyp in zip(silver, mapped):
            if silv == 0 or silv == 4:
                continue
            if silv == 1 and "female" not in hyp:
                confusion += 1
            elif silv == 2 and "male" not in hyp:
                confusion += 1
        if best is None or confusion < best:
            best = confusion
            best_map = dict(zip(labels, perm))
    return best_map


def score_asr(silver_dir: Path, norm_root: Path, engine: str, gold: dict) -> dict:
    per_seg = []
    total_hallu_s = 0.0
    total_missed_s = 0.0
    total_speech_s = 0.0
    total_silence_s = 0.0
    for seg in gold["segments"]:
        seg_id = seg["id"]
        npz_path = silver_dir / f"{seg_id}.npz"
        if not npz_path.is_file():
            continue
        silver = np.load(npz_path)["frames"]
        n_frames = len(silver)
        silence_mask = silver == 0
        speech_mask = (silver == 1) | (silver == 2) | (silver == 3)
        # Use speech_mix hypothesis if present, else union of female+male
        mix = norm_root / engine / seg_id / "speech_mix.words.json"
        if mix.is_file():
            hyp_words = _load_words(mix)
        else:
            hyp_words = _load_words(norm_root / engine / seg_id / "female.words.json") + \
                        _load_words(norm_root / engine / seg_id / "male.words.json")
        hyp_mask = _words_to_frames(hyp_words, n_frames)
        hallu_frames = int(np.sum(hyp_mask & silence_mask))
        missed_frames = int(np.sum(speech_mask & ~hyp_mask))
        hallu_s = hallu_frames * FRAME_MS / 1000
        missed_s = missed_frames * FRAME_MS / 1000
        total_hallu_s += hallu_s
        total_missed_s += missed_s
        total_speech_s += float(np.sum(speech_mask)) * FRAME_MS / 1000
        total_silence_s += float(np.sum(silence_mask)) * FRAME_MS / 1000
        per_seg.append({
            "segment_id": seg_id,
            "hallucination_in_silence_seconds": hallu_s,
            "missed_speech_seconds": missed_s,
            "silence_seconds": float(np.sum(silence_mask)) * FRAME_MS / 1000,
            "speech_seconds": float(np.sum(speech_mask)) * FRAME_MS / 1000,
        })
    return {
        "engine": engine,
        "per_segment": per_seg,
        "total_hallucination_in_silence_seconds": total_hallu_s,
        "total_missed_speech_seconds": total_missed_s,
        "total_speech_seconds": total_speech_s,
        "total_silence_seconds": total_silence_s,
        "hallucination_rate": total_hallu_s / max(total_silence_s, 1e-6),
        "miss_rate": total_missed_s / max(total_speech_s, 1e-6),
    }


def score_cross_agreement(norm_root: Path, engines: list[str], gold: dict) -> dict:
    """Pairwise mean CER over the speech_mix hypothesis text."""
    engine_texts = {eng: {} for eng in engines}
    for seg in gold["segments"]:
        for eng in engines:
            t = ""
            for track in ("speech_mix", "female", "male"):
                p = norm_root / eng / seg["id"] / f"{track}.words.json"
                if p.is_file():
                    t = _text_of(p)
                    if track == "speech_mix":
                        break
            engine_texts[eng][seg["id"]] = t
    pairs = {}
    for a in engines:
        for b in engines:
            if a >= b:
                continue
            cers = []
            for sid in engine_texts[a]:
                cer_val, *_ = _cer(engine_texts[a][sid], engine_texts[b][sid])
                cers.append(cer_val)
            pairs[f"{a} vs {b}"] = {
                "mean_cer": sum(cers) / len(cers) if cers else None,
                "median_cer": statistics.median(cers) if cers else None,
            }
    # per-engine "consensus distance": mean CER to the other engines
    consensus = {}
    for e in engines:
        vals = []
        for k, v in pairs.items():
            if e in k and v["mean_cer"] is not None:
                vals.append(v["mean_cer"])
        consensus[e] = {"mean_pairwise_cer": sum(vals) / len(vals) if vals else None}
    return {"pairs": pairs, "consensus_distance_per_engine": consensus}


def score_diar(silver_dir: Path, diar_root: Path, engine: str, gold: dict) -> dict:
    per_seg = []
    tot_conf = 0
    tot_ovl_gold = 0
    tot_ovl_hit = 0
    tot_frames = 0
    for seg in gold["segments"]:
        seg_id = seg["id"]
        npz_path = silver_dir / f"{seg_id}.npz"
        diar_path = diar_root / engine / f"{seg_id}.json"
        if not npz_path.is_file() or not diar_path.is_file():
            continue
        silver = np.load(npz_path)["frames"]
        n_frames = len(silver)
        intervals = _load_diar_intervals(diar_path)
        diar_frames = _diar_to_frames(intervals, n_frames)
        label_map = _hungarian_map(silver.tolist(), diar_frames)
        mapped = [{label_map.get(l) for l in s if label_map.get(l) is not None}
                  for s in diar_frames]
        confusion = 0
        overlap_gold = 0
        overlap_hit = 0
        for silv, hyp in zip(silver, mapped):
            if silv == 3:
                overlap_gold += 1
                if len(hyp) >= 2:
                    overlap_hit += 1
            elif silv == 1 and "female" not in hyp:
                confusion += 1
            elif silv == 2 and "male" not in hyp:
                confusion += 1
        per_seg.append({
            "segment_id": seg_id,
            "speaker_confusion_frames": confusion,
            "overlap_gold_frames": overlap_gold,
            "overlap_detected_frames": overlap_hit,
            "frame_count": n_frames,
            "label_map": label_map,
        })
        tot_conf += confusion
        tot_ovl_gold += overlap_gold
        tot_ovl_hit += overlap_hit
        tot_frames += n_frames
    return {
        "engine": engine,
        "per_segment": per_seg,
        "total_speaker_confusion_frames": tot_conf,
        "total_overlap_gold_frames": tot_ovl_gold,
        "total_overlap_detected_frames": tot_ovl_hit,
        "total_frames": tot_frames,
        "speaker_confusion_seconds": tot_conf * FRAME_MS / 1000,
        "overlap_recall": (tot_ovl_hit / tot_ovl_gold) if tot_ovl_gold else None,
    }


def score_absolute_cer(norm_root: Path, engines: list[str], gold: dict) -> dict | None:
    filled = [s for s in gold["segments"]
              if s.get("gold", {}).get("transcript") and s.get("gold", {}).get("reviewer")]
    if not filled:
        return None
    out = {}
    for eng in engines:
        cers = []
        for seg in filled:
            ref = seg["gold"]["transcript"].replace(" ", "").replace("\n", "")
            p_mix = norm_root / eng / seg["id"] / "speech_mix.words.json"
            hyp = _text_of(p_mix).replace(" ", "").replace("\n", "") if p_mix.is_file() else \
                  (_text_of(norm_root / eng / seg["id"] / "female.words.json") +
                   _text_of(norm_root / eng / seg["id"] / "male.words.json")).replace(" ", "").replace("\n", "")
            c, s, d, i = _cer(ref, hyp)
            cers.append({"seg": seg["id"], "cer": c, "sub": s, "del": d, "ins": i,
                         "ref_len": len(ref), "hyp_len": len(hyp)})
        macro = sum(x["cer"] for x in cers) / len(cers)
        micro = sum(x["sub"] + x["del"] + x["ins"] for x in cers) / max(sum(x["ref_len"] for x in cers), 1)
        out[eng] = {"per_seg": cers, "cer_macro": macro, "cer_micro": micro}
    return {"n_reviewed": len(filled), "per_engine": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--silver", type=Path, required=True)
    ap.add_argument("--norm-root", type=Path, required=True)
    ap.add_argument("--diar-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    asr_engines = sorted([p.name for p in args.norm_root.iterdir() if p.is_dir()])
    asr = {e: score_asr(args.silver, args.norm_root, e, gold) for e in asr_engines}
    cross = score_cross_agreement(args.norm_root, asr_engines, gold)
    absolute = score_absolute_cer(args.norm_root, asr_engines, gold)
    diar_engines = [p.name for p in args.diar_root.iterdir()
                    if p.is_dir() and not p.name.startswith(".")]
    diar = {e: score_diar(args.silver, args.diar_root, e, gold) for e in diar_engines}

    result = {
        "asr_metrics": asr,
        "cross_engine_agreement": cross,
        "absolute_cer_vs_human_gold": absolute,
        "diar_metrics": diar,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("scored → ", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
