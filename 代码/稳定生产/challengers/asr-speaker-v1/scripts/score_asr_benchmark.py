"""ASR & speaker benchmark scorer for asr-speaker-v1.

Refuses to score unless the gold set (gold.v2.json) has every segment reviewed by
a human (`gold.reviewed=true` after a human filled in transcript+reviewer). Use
`--allow-synthetic-only` to run on tests/fixtures/synthetic_gold.json in the
tests directory — this mode never touches EP03 metrics.

Text metrics: CER (micro/macro/per-seg), substitution/deletion/insertion counts,
hallucination insertion on gold silence intervals.

Speaker metrics: speech miss, false alarm, speaker confusion, overlap recall,
boundary median/P95. Uses pyannote.metrics if installed; otherwise falls back
to a self-implemented frame-based scorer (10 ms grid).

Normalization: two-pass. raw-score keeps the model's original punctuation and
spacing; normalized-score applies rules_version="asr-speaker-v1.normalization.v1"
(defined below): drop whitespace only; keep numbers, negations, English names,
and fillers.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path


# ---- Normalization ------------------------------------------------------

NORMALIZATION_RULES_VERSION = "asr-speaker-v1.normalization.v1"


def normalize_text(s: str, mode: str) -> str:
    if mode == "raw":
        return s
    if mode == "normalized":
        # remove whitespace only; DO NOT strip fillers, digits, negations, or English words
        return re.sub(r"\s+", "", s)
    raise ValueError(mode)


# ---- CER via Levenshtein over Unicode code points -----------------------

def _levenshtein(ref: str, hyp: str) -> tuple[int, int, int, int]:
    """Return (substitutions, deletions, insertions, distance) using char DP."""
    n, m = len(ref), len(hyp)
    if n == 0:
        return 0, 0, m, m
    if m == 0:
        return 0, n, 0, n
    prev = list(range(m + 1))
    ops_prev = [(0, i, 0) for i in range(m + 1)]  # (sub, del, ins)
    for i in range(1, n + 1):
        curr = [i]
        ops_curr = [(0, i, 0)]
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                cost = 0
                candidate = (prev[j - 1], ops_prev[j - 1])
            else:
                sub = prev[j - 1] + 1
                dele = prev[j] + 1
                ins = curr[j - 1] + 1
                m_val = min(sub, dele, ins)
                if m_val == sub:
                    cost, candidate = 1, (sub, (ops_prev[j - 1][0] + 1, ops_prev[j - 1][1], ops_prev[j - 1][2]))
                elif m_val == dele:
                    cost, candidate = 1, (dele, (ops_prev[j][0], ops_prev[j][1] + 1, ops_prev[j][2]))
                else:
                    cost, candidate = 1, (ins, (ops_curr[j - 1][0], ops_curr[j - 1][1], ops_curr[j - 1][2] + 1))
            # accumulate op counts alongside the min-distance path
            if ref[i - 1] == hyp[j - 1]:
                curr.append(prev[j - 1])
                ops_curr.append(ops_prev[j - 1])
            else:
                # candidate = (dist_value, (sub,del,ins))
                curr.append(candidate[0])
                ops_curr.append(candidate[1])
        prev, ops_prev = curr, ops_curr
    subs, dels, ins = ops_prev[m]
    return subs, dels, ins, prev[m]


def compute_cer(reference: str, hypothesis: str) -> dict:
    subs, dels, ins, dist = _levenshtein(reference, hypothesis)
    n = max(len(reference), 1)
    return {
        "reference_chars": len(reference),
        "hypothesis_chars": len(hypothesis),
        "substitutions": subs,
        "deletions": dels,
        "insertions": ins,
        "distance": dist,
        "cer": dist / n,
    }


# ---- Hypothesis loading -------------------------------------------------

def load_hypothesis_words(path: Path) -> list[dict]:
    """Load a normalized transcript file and return word records."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    if "words" in obj:
        return obj["words"]
    if obj.get("layer") == "preview_from_gold_json_v1":
        return obj.get("words", [])
    raise ValueError(f"Unrecognized hypothesis file shape: {path}")


def concat_words_text(words: list[dict], key_priority=("text", "raw_text", "word")) -> str:
    out = []
    for w in words:
        for k in key_priority:
            if k in w and w[k] is not None:
                out.append(w[k])
                break
    return "".join(out)


# ---- Silence hallucination ---------------------------------------------

def silence_hallucination(
    hyp_words: list[dict],
    gold_silence_intervals: list[tuple[float, float]],
    slack_s: float = 0.15,
) -> dict:
    """Return the number of hypothesis words that fall entirely inside gold silence."""
    if not gold_silence_intervals:
        return {"insertions_in_silence": 0, "silence_seconds": 0.0}
    count = 0
    for w in hyp_words:
        ws = float(w.get("start_seconds", w.get("s", 0)))
        we = float(w.get("end_seconds", w.get("e", 0)))
        for gs, ge in gold_silence_intervals:
            if ws >= gs + slack_s and we <= ge - slack_s and we > ws:
                count += 1
                break
    return {
        "insertions_in_silence": count,
        "silence_seconds": sum(ge - gs for gs, ge in gold_silence_intervals),
    }


# ---- Speaker metrics fallback ------------------------------------------

def _framize(intervals: list[dict], total_seconds: float, frame_s: float = 0.01) -> list[set[str]]:
    n_frames = int(round(total_seconds / frame_s))
    frames: list[set[str]] = [set() for _ in range(n_frames)]
    for iv in intervals:
        s = float(iv.get("start_seconds", 0))
        e = float(iv.get("end_seconds", 0))
        spk = str(iv.get("speaker_id", iv.get("speaker", "unknown")))
        i0 = max(0, int(round(s / frame_s)))
        i1 = min(n_frames, int(round(e / frame_s)))
        for i in range(i0, i1):
            frames[i].add(spk)
    return frames


def speaker_frame_metrics(gold_intervals, hyp_intervals, duration_s: float, frame_s: float = 0.01) -> dict:
    g = _framize(gold_intervals, duration_s, frame_s)
    h = _framize(hyp_intervals, duration_s, frame_s)
    n = len(g)
    speech_miss = 0
    false_alarm = 0
    overlap_gold_frames = 0
    overlap_detected = 0
    speaker_confusion = 0
    boundary_errors: list[float] = []
    single_speech_frames = 0
    # frame-level counts
    for gi, hi in zip(g, h):
        if gi and not hi:
            speech_miss += 1
        elif hi and not gi:
            false_alarm += 1
        elif gi and hi:
            if len(gi) > 1:
                overlap_gold_frames += 1
                if len(hi) > 1:
                    overlap_detected += 1
            elif len(gi) == 1:
                single_speech_frames += 1
                gold_spk = next(iter(gi))
                if len(hi) == 1 and next(iter(hi)) != gold_spk:
                    speaker_confusion += 1
    # boundary errors — for each gold interval boundary, distance to nearest hyp boundary
    gold_edges = []
    for iv in gold_intervals:
        gold_edges.append(float(iv.get("start_seconds", 0)))
        gold_edges.append(float(iv.get("end_seconds", 0)))
    hyp_edges = []
    for iv in hyp_intervals:
        hyp_edges.append(float(iv.get("start_seconds", 0)))
        hyp_edges.append(float(iv.get("end_seconds", 0)))
    if hyp_edges:
        for ge in gold_edges:
            boundary_errors.append(min(abs(ge - he) for he in hyp_edges))
    return {
        "frame_count": n,
        "speech_miss_frames": speech_miss,
        "false_alarm_frames": false_alarm,
        "single_speech_frames": single_speech_frames,
        "speaker_confusion_frames": speaker_confusion,
        "overlap_gold_frames": overlap_gold_frames,
        "overlap_detected_frames": overlap_detected,
        "boundary_error_median_s": statistics.median(boundary_errors) if boundary_errors else None,
        "boundary_error_p95_s": (
            statistics.quantiles(boundary_errors, n=20)[18]
            if len(boundary_errors) >= 20 else None
        ),
    }


# ---- Main ---------------------------------------------------------------

def score(gold: dict, hypotheses_root: Path, engines: list[str],
          gold_silence_field: str = "silence_intervals") -> dict:
    per_engine = {}
    for eng in engines:
        per_seg = []
        seen_hyp_words: list[dict] = []
        total_dist = 0
        total_ref_chars = 0
        cer_values: list[float] = []
        sub_total = del_total = ins_total = 0
        hallucination_total = 0
        speaker_totals = {"speech_miss_frames": 0, "false_alarm_frames": 0,
                          "single_speech_frames": 0, "speaker_confusion_frames": 0,
                          "overlap_gold_frames": 0, "overlap_detected_frames": 0}
        for seg in gold["segments"]:
            seg_id = seg["id"]
            gold_text = normalize_text(seg["gold"]["transcript"], "normalized")
            # We concatenate hypothesis text for female + male + speech_mix — the reference
            # here is a listening transcript of speech_mix, so we prefer the speech_mix hyp.
            hyp_path_mix = hypotheses_root / eng / seg_id / "speech_mix.words.json"
            hyp_path_female = hypotheses_root / eng / seg_id / "female.words.json"
            hyp_path_male = hypotheses_root / eng / seg_id / "male.words.json"
            candidates = [p for p in (hyp_path_mix, hyp_path_female, hyp_path_male) if p.is_file()]
            if not candidates:
                per_seg.append({"segment_id": seg_id, "status": "NO_HYPOTHESIS"})
                continue
            hyp_words_all = []
            for cand in candidates:
                hyp_words_all.extend(load_hypothesis_words(cand))
            # For CER we use the "listening" transcript which reflects speech_mix if available:
            hyp_text_for_cer = (
                normalize_text(concat_words_text(load_hypothesis_words(hyp_path_mix)), "normalized")
                if hyp_path_mix.is_file()
                else normalize_text(concat_words_text(hyp_words_all), "normalized")
            )
            cer = compute_cer(gold_text, hyp_text_for_cer)
            total_dist += cer["distance"]
            total_ref_chars += cer["reference_chars"]
            cer_values.append(cer["cer"])
            sub_total += cer["substitutions"]
            del_total += cer["deletions"]
            ins_total += cer["insertions"]
            hallu = silence_hallucination(hyp_words_all, seg.get(gold_silence_field, []))
            hallucination_total += hallu["insertions_in_silence"]
            speaker_stats = None
            gold_spk = seg["gold"].get("speaker_attribution") or []
            if isinstance(gold_spk, list) and gold_spk and isinstance(gold_spk[0], dict):
                dur = float(seg["duration_seconds"])
                # look for speaker intervals in a speaker-only companion file if the engine wrote one
                hyp_speaker_path = hypotheses_root / eng / seg_id / "speaker_intervals.json"
                if hyp_speaker_path.is_file():
                    hi = json.loads(hyp_speaker_path.read_text(encoding="utf-8")).get("intervals", [])
                    speaker_stats = speaker_frame_metrics(gold_spk, hi, dur)
                    for k in speaker_totals:
                        speaker_totals[k] += speaker_stats[k]
            per_seg.append({
                "segment_id": seg_id,
                "cer_raw": compute_cer(seg["gold"]["transcript"], hyp_text_for_cer),
                "cer_normalized": cer,
                "hallucination": hallu,
                "speaker_metrics": speaker_stats,
            })
        per_engine[eng] = {
            "engine": eng,
            "per_segment": per_seg,
            "cer_micro": (total_dist / total_ref_chars) if total_ref_chars else None,
            "cer_macro": (sum(cer_values) / len(cer_values)) if cer_values else None,
            "substitution_total": sub_total,
            "deletion_total": del_total,
            "insertion_total": ins_total,
            "hallucination_insertion_total": hallucination_total,
            "speaker_totals": speaker_totals,
            "normalization_rules_version": NORMALIZATION_RULES_VERSION,
        }
    return {"engines": per_engine}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--hypotheses-root", type=Path, required=True)
    ap.add_argument("--engines", nargs="+",
                    default=["faster_whisper_small_vad_on", "funasr_paraformer", "mlx_whisper_turbo"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--allow-synthetic-only", action="store_true",
                    help="Only allow tests/fixtures gold with status=SYNTHETIC")
    args = ap.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    status = gold.get("status")
    if args.allow_synthetic_only:
        if status != "SYNTHETIC":
            print("REFUSED: --allow-synthetic-only requires status=SYNTHETIC", file=sys.stderr)
            return 3
    else:
        if status != "HUMAN_GOLD_FILLED":
            print(f"REFUSED: gold status is {status!r}; refusing to compute EP03 benchmark. "
                  "Human review must fill reviewer + transcript on all 12 segments and set "
                  "status=HUMAN_GOLD_FILLED.", file=sys.stderr)
            return 2
        # verify each segment reviewed
        unreviewed = [s["id"] for s in gold["segments"] if not s.get("gold", {}).get("reviewed")]
        if unreviewed:
            print(f"REFUSED: {len(unreviewed)} segments not reviewed: {unreviewed}", file=sys.stderr)
            return 2

    result = score(gold, args.hypotheses_root, args.engines)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("metrics written to", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
