"""automix-v1 · N 轨 mono → 主麦 automix stereo mp3

设计：
  1. 每 20 ms 窗口对每轨算 RMS
  2. 主导轨 = RMS 最高的那条（若最响与次响差 < min_gap_db 则视为 ambiguous）
  3. 生成每轨 gain envelope：
     - primary：0 dB
     - non-primary：secondary_atten_db（默认 -12 dB）
     - ambiguous：所有轨均分（-3 dB for 2-track，-4.8 dB for 3-track）
  4. envelope 之间 crossfade（默认 30 ms）以避免咔嚓声
  5. 每轨应用 gain envelope 输出 mono PCM
  6. 全部主导轨 pcm amix → single mono
  7. 拼片头/片尾音乐（`reference-linear-v1` 时序）
  8. ffmpeg loudnorm 归一到 release-spec 目标（integrated LUFS + TP）
  9. mp3 192 kbps stereo encode

不依赖 numpy；只用 stdlib (wave, array, math) + subprocess ffmpeg。
输入必须是同长度、同采样率的 mono WAV；采样率支持 48 kHz（其它值自动 fail closed）。

用法：
  python3 automix_v1.py \
    --tracks track1.wav track2.wav [track3.wav ...] \
    --music music.mp3 \
    --release-spec /path/to/release_specs.json \
    --music-template /path/to/music_templates.json \
    --template-id reference-linear-v1 \
    --output out.mp3 \
    --tmp-dir /path/to/tmp
"""
from __future__ import annotations

import argparse
import array
import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any


FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
FRAME_MS = 20
DEFAULT_MIN_GAP_DB = 3.0
DEFAULT_SECONDARY_ATTEN_DB = -12.0
DEFAULT_CROSSFADE_MS = 30


def read_mono_wav_int16(path: Path) -> tuple[array.array, int]:
    """Read a mono WAV → int16 array. If input is 24-bit or float, ffmpeg-convert to 16-bit first."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1:
            raise ValueError(f"{path} is not mono")
        rate = w.getframerate()
        sampwidth = w.getsampwidth()
        n = w.getnframes()
        raw = w.readframes(n)
    if sampwidth == 2:
        buf = array.array("h")
        buf.frombytes(raw)
        return buf, rate
    if sampwidth == 3:
        # 24-bit LE → int16 by scaling down
        buf = array.array("h")
        for i in range(0, len(raw), 3):
            b0, b1, b2 = raw[i], raw[i + 1], raw[i + 2]
            val = b0 | (b1 << 8) | (b2 << 16)
            if val & 0x800000:
                val -= 0x1000000
            buf.append(val >> 8)  # 24→16
        return buf, rate
    raise ValueError(f"{path}: unsupported sampwidth {sampwidth}")


def write_mono_wav_int16(path: Path, samples: array.array, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def rms_frames(samples: array.array, frame_size: int) -> list[float]:
    """Compute RMS per frame_size samples. Return list of RMS values (linear)."""
    n = len(samples)
    frames = n // frame_size
    result = []
    for i in range(frames):
        chunk = samples[i * frame_size:(i + 1) * frame_size]
        s = 0.0
        for x in chunk:
            s += x * x
        rms = math.sqrt(s / max(1, len(chunk)))
        result.append(rms)
    return result


def db_from_linear(x: float) -> float:
    return 20.0 * math.log10(x) if x > 0 else -120.0


def decide_primary(rms_per_track: list[list[float]], min_gap_db: float) -> list[int]:
    """For each frame index, return primary track index or -1 for ambiguous."""
    n_frames = min(len(r) for r in rms_per_track)
    n_tracks = len(rms_per_track)
    out = []
    for f in range(n_frames):
        vals = [rms_per_track[t][f] for t in range(n_tracks)]
        top_idx = max(range(n_tracks), key=lambda i: vals[i])
        top_db = db_from_linear(vals[top_idx])
        # second-highest
        second_idx = None
        second_db = -120.0
        for i in range(n_tracks):
            if i == top_idx:
                continue
            d = db_from_linear(vals[i])
            if d > second_db:
                second_db = d
                second_idx = i
        # If everyone is silent (top < -50 dBFS), also ambiguous → keep small
        if top_db < -50.0:
            out.append(-1)
        elif (top_db - second_db) < min_gap_db:
            out.append(-1)
        else:
            out.append(top_idx)
    return out


def gain_envelope_for_track(
    primary_seq: list[int],
    track_idx: int,
    n_tracks: int,
    frame_size: int,
    n_samples: int,
    secondary_atten_db: float,
    crossfade_ms: int,
    rate: int,
) -> array.array:
    """Return per-sample gain (float 0..1) for a track, with crossfaded transitions."""
    # Per-frame target gain (linear)
    ambig_db = -20.0 * math.log10(n_tracks) if n_tracks > 1 else 0.0
    def _target(f: int) -> float:
        p = primary_seq[f]
        if p == track_idx:
            return 1.0
        if p == -1:
            return 10 ** (ambig_db / 20.0)
        return 10 ** (secondary_atten_db / 20.0)

    xf_samples = max(1, int(rate * crossfade_ms / 1000))
    gain = array.array("f", [0.0] * n_samples)
    for f in range(len(primary_seq)):
        start = f * frame_size
        end = min(start + frame_size, n_samples)
        tgt = _target(f)
        if f == 0:
            # ramp from zero-ish to tgt over xf_samples of the first frame
            prev = tgt
        else:
            prev = _target(f - 1)
        # Ramp over first xf_samples of the frame from prev → tgt
        for s in range(start, end):
            offset = s - start
            if offset < xf_samples and prev != tgt:
                a = offset / xf_samples
                gain[s] = prev + (tgt - prev) * a
            else:
                gain[s] = tgt
    return gain


def apply_gain(samples: array.array, gain: array.array) -> array.array:
    out = array.array("h", [0] * len(samples))
    for i in range(len(samples)):
        v = int(samples[i] * gain[i])
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out[i] = v
    return out


def mix_mono(all_samples: list[array.array]) -> array.array:
    """Straight sum-and-clip mixdown."""
    n = min(len(s) for s in all_samples)
    out = array.array("h", [0] * n)
    for i in range(n):
        v = 0
        for s in all_samples:
            v += s[i]
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out[i] = v
    return out


# ---------------------------------------------------------------------------
# EDL render_sync_cuts application (sample-precise, symmetric crossfade)
# ---------------------------------------------------------------------------

def parse_render_sync_cuts(edl_path: Path) -> list[dict[str, int]]:
    """Read `render_sync_cuts` from a `human_approved.edl.json`. Returns a list
    of {start_sample, end_sample, crossfade_samples} sorted by start ascending.
    Returns [] if EDL has no such field or the file is missing.
    """
    if not edl_path.exists():
        raise FileNotFoundError(f"EDL not found: {edl_path}")
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    cuts_raw = edl.get("render_sync_cuts") or []
    cuts: list[dict[str, int]] = []
    for c in cuts_raw:
        cuts.append({
            "start_sample": int(c["start_sample"]),
            "end_sample": int(c["end_sample"]),
            "crossfade_samples": int(c.get("crossfade_samples") or 0),
            "insert_silence_samples": int(c.get("insert_silence_samples") or 0),
        })
    cuts.sort(key=lambda c: c["start_sample"])
    return cuts


def _apply_one_cut(samples: array.array, start: int, end: int, xf: int,
                    insert_silence_samples: int = 0) -> array.array:
    """Cut [start, end) from samples with a symmetric linear crossfade of length
    xf/2 on each side of the join. If insert_silence_samples > 0, add that many
    zero-valued samples right after the crossfade — this simulates a natural
    micro-pause after removing filler/repetition, addressing the 2026-08-17
    user feedback that "cutting alone isn't enough; you also need to model the
    residual pause". If xf is 0 or too close to boundaries, falls back to a
    hard cut (+ optional silence).
    """
    if end <= start:
        return samples
    silence = array.array("h", [0] * insert_silence_samples) if insert_silence_samples > 0 else array.array("h")
    if xf < 2:
        return samples[:start] + silence + samples[end:]
    xf_half = xf // 2
    if start < xf_half or end + xf_half > len(samples):
        return samples[:start] + silence + samples[end:]
    pre = samples[: start - xf_half]
    fade_out_src = samples[start - xf_half : start]
    fade_in_src = samples[end : end + xf_half]
    post = samples[end + xf_half :]
    crossed = array.array("h", [0] * xf_half)
    for i in range(xf_half):
        a = i / xf_half
        v = int(fade_out_src[i] * (1.0 - a) + fade_in_src[i] * a)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        crossed[i] = v
    return pre + crossed + silence + post


def apply_render_sync_cuts(
    samples: array.array, cuts: list[dict[str, int]]
) -> array.array:
    """Apply each render_sync_cut in reverse-start order so earlier cut indices
    remain valid across the mutation. Result stays sample-aligned across tracks
    when the SAME cuts list is applied to each mono track."""
    out = samples
    for c in sorted(cuts, key=lambda x: x["start_sample"], reverse=True):
        pause_samples = int(c.get("insert_silence_samples") or 0)
        out = _apply_one_cut(
            out,
            c["start_sample"],
            c["end_sample"],
            c["crossfade_samples"],
            insert_silence_samples=pause_samples,
        )
    return out


def ffmpeg_ensure_mono_48k(path: Path, out_path: Path) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-i", str(path), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(out_path)],
        check=True, capture_output=True,
    )


def _parse_loudnorm_json(stderr_text: str) -> dict[str, str] | None:
    """Extract the ffmpeg loudnorm JSON block from stderr text. Returns None if
    not found."""
    import re
    # ffmpeg loudnorm emits the JSON as one contiguous block at the end
    matches = list(re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr_text, re.DOTALL))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].group(0))
    except json.JSONDecodeError:
        return None


def _loudnorm_filter_pass1(target_lufs: float, target_tp: float, target_lra: float) -> str:
    return (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}:print_format=json"
    )


def _loudnorm_filter_pass2(
    target_lufs: float, target_tp: float, target_lra: float, m: dict[str, str]
) -> str:
    return (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={m['input_i']}"
        f":measured_TP={m['input_tp']}"
        f":measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}"
        f":linear=true:print_format=summary"
    )


def ffmpeg_wrap_music_and_loudnorm(
    speech_wav: Path,
    music_wav: Path,
    music_template: dict[str, Any],
    release_spec: dict[str, Any],
    output_mp3: Path,
    tmp_dir: Path,
    loudnorm_passes: int = 2,
) -> dict[str, Any]:
    """Prepend intro music (0-16s crossfade with speech), append outro music,
    then loudnorm (single- or two-pass) and mp3-encode.

    Returns a dict with `loudnorm_passes` and (if two-pass) `pass1_measurements`
    parsed from ffmpeg's print_format=json output. Useful for QC.
    """
    # Speech duration
    dur = float(subprocess.check_output(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(speech_wav)]
    ).decode().strip())
    voice_start = music_template["voice_start_seconds"]  # 5.0
    intro_fade_end = music_template["intro_fade_out_end_seconds"]  # 16.0
    outro_lead = music_template["outro_fade_in_lead_seconds"]  # 22.0
    outro_tail = music_template["outro_music_tail_seconds"]  # 37.976
    music_gain_db = music_template.get("music_gain_db", -12.0)

    total_dur = voice_start + dur + outro_tail
    target_lufs = release_spec.get("target_integrated_lufs", -22.2)
    # Prefer the safety_floor if declared; else fall back to the mentor-observed peak.
    target_tp = min(release_spec.get("target_true_peak_dbfs", -0.1),
                     release_spec.get("target_true_peak_dbfs_safety_floor", -1.0))
    target_lra = release_spec.get("target_lra_lu", 7.9)
    bit_rate = release_spec.get("bit_rate_bps", 192000)

    # Everything up to (but not including) the loudnorm step is identical across
    # both passes; build it once as a filter fragment.
    prefix_filter = (
        # Music intro: full volume 0-5s, then linear fade from 0dB to music_gain_db over 5-16s
        f"[1:a]atrim=0:{intro_fade_end},asetpts=PTS-STARTPTS,"
        f"volume=volume='if(lt(t,{voice_start}),1,if(lt(t,{intro_fade_end}),"
        f"1+(pow(10,{music_gain_db}/20)-1)*(t-{voice_start})/({intro_fade_end}-{voice_start}),"
        f"pow(10,{music_gain_db}/20)))':eval=frame,apad=whole_dur={total_dur}[intro_music];"
        # Music outro: hold music_gain_db, fade to 0dB before program ends? For simplicity keep at music_gain_db
        f"[1:a]atrim=0:{outro_tail},asetpts=PTS-STARTPTS,"
        f"adelay={int((voice_start + dur - outro_lead) * 1000)}|{int((voice_start + dur - outro_lead) * 1000)},"
        f"volume=volume={math.pow(10, music_gain_db/20)},apad=whole_dur={total_dur}[outro_music];"
        # Speech: delay to voice_start
        f"[0:a]adelay={int(voice_start * 1000)}|{int(voice_start * 1000)},"
        f"apad=whole_dur={total_dur}[speech_positioned];"
        f"[speech_positioned][intro_music]amix=inputs=2:normalize=0:duration=longest[mix1];"
        f"[mix1][outro_music]amix=inputs=2:normalize=0:duration=longest[mixed]"
    )

    result: dict[str, Any] = {"loudnorm_passes": loudnorm_passes}
    measurements: dict[str, str] | None = None

    if loudnorm_passes == 2:
        # Pass 1: measure only (write to /dev/null; do not encode).
        pass1_filter = prefix_filter + f";[mixed]{_loudnorm_filter_pass1(target_lufs, target_tp, target_lra)}[out]"
        r1 = subprocess.run(
            [
                FFMPEG, "-y",
                "-i", str(speech_wav),
                "-i", str(music_wav),
                "-filter_complex", pass1_filter,
                "-map", "[out]",
                "-ac", "2", "-ar", "48000",
                "-f", "null", "-",
            ],
            capture_output=True, check=True,
        )
        stderr_text = r1.stderr.decode("utf-8", errors="replace")
        measurements = _parse_loudnorm_json(stderr_text)
        if measurements is None:
            # Fall back to single-pass rather than fail; log the reason.
            result["pass1_parse_failed"] = True
            result["loudnorm_passes"] = 1
        else:
            result["pass1_measurements"] = measurements
            # Persist raw pass1 stderr for QC audit
            (tmp_dir / "loudnorm_pass1_stderr.txt").write_text(stderr_text, encoding="utf-8")

    if measurements is not None:
        loudnorm_fragment = _loudnorm_filter_pass2(target_lufs, target_tp, target_lra, measurements)
    else:
        loudnorm_fragment = f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
    final_filter = prefix_filter + f";[mixed]{loudnorm_fragment}[out]"

    r2 = subprocess.run(
        [
            FFMPEG, "-y",
            "-i", str(speech_wav),
            "-i", str(music_wav),
            "-filter_complex", final_filter,
            "-map", "[out]",
            "-ac", "2",
            "-ar", "48000",
            "-c:a", "libmp3lame", "-b:a", f"{bit_rate // 1000}k",
            str(output_mp3),
        ],
        capture_output=True, check=True,
    )
    (tmp_dir / "loudnorm_pass2_stderr.txt").write_text(
        r2.stderr.decode("utf-8", errors="replace"), encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracks", nargs="+", required=True, type=Path)
    ap.add_argument("--music", required=True, type=Path)
    ap.add_argument("--release-spec", required=True, type=Path)
    ap.add_argument("--music-template", required=True, type=Path)
    ap.add_argument("--template-id", default="reference-linear-v1")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--tmp-dir", required=True, type=Path)
    ap.add_argument("--min-gap-db", type=float, default=DEFAULT_MIN_GAP_DB)
    ap.add_argument("--secondary-atten-db", type=float, default=DEFAULT_SECONDARY_ATTEN_DB)
    ap.add_argument("--crossfade-ms", type=int, default=DEFAULT_CROSSFADE_MS)
    ap.add_argument("--frame-ms", type=int, default=FRAME_MS)
    ap.add_argument("--edl", type=Path, default=None,
                    help="human_approved.edl.json path; when given, apply "
                         "render_sync_cuts to each mono track BEFORE automix")
    ap.add_argument("--loudnorm-passes", type=int, default=2, choices=[1, 2],
                    help="1 = single-pass (legacy, ±1-2 LU accuracy), "
                         "2 = double-pass (measure + linear apply, ±0.3 LU)")
    args = ap.parse_args(argv)

    args.tmp_dir.mkdir(parents=True, exist_ok=True)

    # Load specs
    release_specs = json.loads(args.release_spec.read_text(encoding="utf-8"))["specs"]
    if args.template_id not in release_specs:
        raise ValueError(f"release spec missing template {args.template_id}")
    release_spec = release_specs[args.template_id]
    music_template = json.loads(args.music_template.read_text(encoding="utf-8"))["templates"][args.template_id]

    # Normalize inputs → 16-bit mono 48kHz PCM
    normed = []
    for i, t in enumerate(args.tracks):
        n = args.tmp_dir / f"track_{i:02d}.mono.16k.wav"
        ffmpeg_ensure_mono_48k(t, n)
        normed.append(n)

    # Also normalize music to 16-bit 48k stereo? Keep original for later use
    tracks_samples: list[array.array] = []
    rate = 0
    for p in normed:
        buf, r = read_mono_wav_int16(p)
        if rate == 0:
            rate = r
        elif r != rate:
            raise ValueError("mixed sample rates after normalization")
        tracks_samples.append(buf)

    # ---- EDL apply (sample-precise cut + symmetric crossfade) --------------
    # Applied BEFORE automix so all downstream stages (rms, primary decision,
    # gain envelope, mono mix) see the cut timeline uniformly.
    edl_cuts_applied = 0
    if args.edl:
        cuts = parse_render_sync_cuts(args.edl)
        for i in range(len(tracks_samples)):
            tracks_samples[i] = apply_render_sync_cuts(tracks_samples[i], cuts)
        edl_cuts_applied = len(cuts)

    # Frame RMS + primary decision
    frame_size = int(rate * args.frame_ms / 1000)
    rms_per_track = [rms_frames(s, frame_size) for s in tracks_samples]
    primary_seq = decide_primary(rms_per_track, args.min_gap_db)
    n_frames = len(primary_seq)
    n_samples = n_frames * frame_size

    # Truncate each track to n_samples
    for i in range(len(tracks_samples)):
        tracks_samples[i] = tracks_samples[i][:n_samples]

    # Per-track gain envelope, then apply
    ducked_paths = []
    for i, samples in enumerate(tracks_samples):
        env = gain_envelope_for_track(
            primary_seq, i, len(tracks_samples),
            frame_size, n_samples,
            args.secondary_atten_db, args.crossfade_ms, rate,
        )
        out_samples = apply_gain(samples, env)
        p = args.tmp_dir / f"track_{i:02d}.ducked.wav"
        write_mono_wav_int16(p, out_samples, rate)
        ducked_paths.append(p)

    # Mix mono
    mixed = mix_mono([read_mono_wav_int16(p)[0] for p in ducked_paths])
    speech_wav = args.tmp_dir / "speech.mono.wav"
    write_mono_wav_int16(speech_wav, mixed, rate)

    # Stats: how many frames each track was primary
    counts = [0] * len(tracks_samples)
    ambig = 0
    for p in primary_seq:
        if p == -1:
            ambig += 1
        else:
            counts[p] += 1
    stats = {
        "n_frames": n_frames,
        "frame_ms": args.frame_ms,
        "primary_frame_counts": counts,
        "ambiguous_frame_count": ambig,
        "primary_percent": [round(100 * c / n_frames, 2) for c in counts],
        "ambiguous_percent": round(100 * ambig / n_frames, 2),
        "edl_cuts_applied": edl_cuts_applied,
        "loudnorm_passes_requested": args.loudnorm_passes,
    }
    stats_path = args.tmp_dir / "automix_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    # Wrap with music + loudnorm + mp3
    lo_info = ffmpeg_wrap_music_and_loudnorm(
        speech_wav, args.music,
        music_template, release_spec,
        args.output, args.tmp_dir,
        loudnorm_passes=args.loudnorm_passes,
    )
    stats["loudnorm"] = lo_info
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"output": str(args.output), "stats": stats}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
