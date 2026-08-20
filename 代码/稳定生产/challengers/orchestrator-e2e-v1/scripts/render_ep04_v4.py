#!/usr/bin/env python3
"""EP04-v4：从真人标签学激进阈值 + 更多长停顿 + 片头片尾音乐。

新增/调整：
- 长停顿：trigger 0.6s（v3=1.2s），keep 300/300ms（v3=400/400ms）
- 瞬态 cough：peak > -22 dBFS（v3=-20），dur < 0.5s（v3=0.4s）
- 串音 medium：也 accept，走源轨 gate（不改时长）
- 片头片尾：授权 mp3 前 15s + speech + 后 15s，3s equal-power crossfade
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np


def _decode(raw: bytes, sw: int) -> np.ndarray:
    if sw == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8); n = b.size // 3
        b = b[: n * 3].reshape(n, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
             | (b[:, 2].astype(np.int32) << 16))
        v[v >= (1 << 23)] -= (1 << 24)
        return v.astype(np.float32) / (1 << 23)
    if sw == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float32) / (1 << 31)
    raise ValueError(sw)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as wf:
            n_ch = wf.getnchannels(); sw = wf.getsampwidth(); sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        data = _decode(raw, sw)
    except wave.Error:
        return _read_ext(path)
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data, sr


def _read_ext(path: Path) -> tuple[np.ndarray, int]:
    with path.open("rb") as f:
        f.read(12); fmt = None; data_bytes = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8: break
            cid, sz = struct.unpack("<4sI", hdr); payload = f.read(sz)
            if sz % 2: f.read(1)
            if cid == b"fmt ":
                tag = struct.unpack("<H", payload[0:2])[0]
                ch = struct.unpack("<H", payload[2:4])[0]
                sr = struct.unpack("<I", payload[4:8])[0]
                bits = struct.unpack("<H", payload[14:16])[0]
                fmt = (tag, ch, sr, bits)
            elif cid == b"data":
                data_bytes = payload
    _, ch, sr, bits = fmt
    d = _decode(data_bytes, bits // 8)
    if ch > 1: d = d.reshape(-1, ch).mean(axis=1)
    return d, sr


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""): h.update(c)
    return h.hexdigest()


def _append_int16(w: wave.Wave_write, x: np.ndarray) -> None:
    xi = np.clip(x, -1.0, 1.0); xi = (xi * 32767.0).astype("<i2")
    w.writeframes(xi.tobytes())


def merge_intervals(items):
    if not items: return []
    items = sorted(items); out = [items[0]]
    for s, e in items[1:]:
        if s <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], e))
        else: out.append((s, e))
    return out


def silencedetect(source: Path, noise_db: float, min_seconds: float):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(source),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}",
         "-f", "null", "-"], capture_output=True, text=True)
    log = r.stderr
    ss = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", log)]
    ee = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", log)]
    n = min(len(ss), len(ee))
    return list(zip(ss[:n], ee[:n]))


def apply_gate_inplace(x, gates, fade_samples):
    for s, e in gates:
        s = max(0, min(x.size, s)); e = max(s, min(x.size, e))
        if e <= s: continue
        f = min(fade_samples, (e - s) // 2)
        if f > 0:
            t = np.linspace(0, np.pi / 2, f, dtype=np.float32)
            x[s:s + f] *= np.cos(t)
            x[s + f:e - f] = 0.0
            x[e - f:e] *= np.sin(t)
        else:
            x[s:e] = 0.0


def apply_sync_cuts_ep(x, sr, cuts, out_path, crossfade_ms):
    fade = int(sr * crossfade_ms / 1000)
    keeps = []; cur = 0
    for s, e in cuts:
        s = max(0, min(x.size, s)); e = max(s, min(x.size, e))
        if s > cur: keeps.append((cur, s))
        cur = e
    if cur < x.size: keeps.append((cur, x.size))
    total = 0
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        tail = None; first = True
        t = np.linspace(0, np.pi / 2, fade, dtype=np.float32) if fade > 0 else np.zeros(0, dtype=np.float32)
        r_out = np.cos(t) if fade > 0 else t
        r_in = np.sin(t) if fade > 0 else t
        for a, b in keeps:
            seg = x[a:b].astype(np.float32, copy=False)
            if first:
                if seg.size <= fade: tail = seg.copy(); first = False; continue
                _append_int16(w, seg[:-fade] if fade > 0 else seg)
                total += (seg.size - fade) if fade > 0 else seg.size
                tail = seg[-fade:].copy() if fade > 0 else np.zeros(0, dtype=np.float32)
                first = False; continue
            f = min(fade, tail.size if tail is not None else 0, seg.size)
            if f > 0:
                head = tail[-f:] * r_out[:f] + seg[:f] * r_in[:f]
                pre = tail[:-f] if tail.size > f else np.zeros(0, dtype=np.float32)
                if pre.size: _append_int16(w, pre); total += pre.size
                _append_int16(w, head); total += head.size
                mid = seg[f:]
            else:
                if tail is not None and tail.size: _append_int16(w, tail); total += tail.size
                mid = seg
            if mid.size <= fade: tail = mid.copy()
            else:
                _append_int16(w, mid[:-fade] if fade > 0 else mid)
                total += (mid.size - fade) if fade > 0 else mid.size
                tail = mid[-fade:].copy() if fade > 0 else np.zeros(0, dtype=np.float32)
        if tail is not None and tail.size: _append_int16(w, tail); total += tail.size
    return total


def long_pause_cuts(mix_source, sr, noise_db, min_silence_seconds,
                    trigger_seconds, safety_ms, keep_head_ms, keep_tail_ms):
    sils = silencedetect(mix_source, noise_db, min_silence_seconds)
    safety = safety_ms / 1000.0
    kh = keep_head_ms / 1000.0; kt = keep_tail_ms / 1000.0
    out = []
    for s, e in sils:
        dur = e - s
        if dur < trigger_seconds: continue
        cs = s + safety + kh; ce = e - safety - kt
        if ce - cs < 0.05: continue
        out.append({
            "silence_start_seconds": round(s, 3),
            "silence_end_seconds": round(e, 3),
            "silence_duration_seconds": round(dur, 3),
            "cut_start_sample": int(round(cs * sr)),
            "cut_end_sample": int(round(ce * sr)),
            "cut_duration_seconds": round(ce - cs, 3),
        })
    return out


def relabel_candidates(candidates: list[dict]) -> tuple[list[dict], dict]:
    """从更宽松的规则重新打标所有 candidate（学到"用户想剪多"后放宽）。

    - transient.mic_bump_like → accept sync
    - transient.cough_like/thump_like → peak > -22 且 dur < 0.5 → accept sync
    - crosstalk.high → accept gate
    - crosstalk.medium → accept gate（新增：不动时长，只降串音）
    - 其它 → reject
    """
    decisions = []
    stats = {"accept_sync": 0, "accept_gate": 0, "reject": 0}
    by_fam = {}
    for c in candidates:
        fam = c["reason_family"]; rk = c["reason_key"]
        dur = float(c.get("end_seconds", 0) - c.get("start_seconds", 0))
        decision = "reject"; action = None; reason = ""
        if fam == "transient" and dur <= 0.5:
            if rk == "mic_bump_like":
                decision = "accept"; action = "sync_cut_all_tracks"
                reason = "mic_bump 明确碰麦"
            elif rk in ("cough_like", "thump_like"):
                peak = float(c.get("peak_dbfs", -60))
                if peak > -22.0 and dur < 0.5:
                    decision = "accept"; action = "sync_cut_all_tracks"
                    reason = f"{rk} peak={peak:.1f}dBFS dur={dur:.2f}s（v4 放宽）"
        elif fam == "crosstalk":
            conf = c.get("confidence", "medium")
            decision = "accept"; action = "gate_source_track"
            reason = f"crosstalk {conf} → 源轨 gate（v4：medium 也 accept，只降串音）"
        decisions.append({
            "candidate_id": c["candidate_id"],
            "decision": decision, "action": action,
            "reason": reason,
        })
        key = "accept_sync" if action == "sync_cut_all_tracks" else \
              ("accept_gate" if action == "gate_source_track" else "reject")
        stats[key] += 1
        by_fam.setdefault(fam, {"accept": 0, "reject": 0})[
            "accept" if decision == "accept" else "reject"] += 1
    return decisions, {"totals": stats, "by_family": by_fam}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", action="append", required=True)
    ap.add_argument("--filler-edl", required=True)
    ap.add_argument("--merged-candidates", required=True)
    ap.add_argument("--mix-for-silencedetect", required=True)
    ap.add_argument("--intro-outro-music", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sync-crossfade-ms", type=int, default=200)
    ap.add_argument("--gate-fade-ms", type=int, default=30)
    ap.add_argument("--long-pause-noise-db", type=float, default=-30.0)
    ap.add_argument("--long-pause-trigger-seconds", type=float, default=0.6)
    ap.add_argument("--long-pause-safety-ms", type=int, default=100)
    ap.add_argument("--long-pause-keep-head-ms", type=int, default=300)
    ap.add_argument("--long-pause-keep-tail-ms", type=int, default=300)
    ap.add_argument("--intro-music-seconds", type=float, default=15.0)
    ap.add_argument("--outro-music-seconds", type=float, default=15.0)
    ap.add_argument("--music-speech-crossfade-seconds", type=float, default=3.0)
    ap.add_argument("--tail-fade-seconds", type=float, default=3.0)
    args = ap.parse_args()

    tracks: dict[str, Path] = {}
    for spec in args.track:
        label, p = spec.split("=", 1)
        tracks[label] = Path(p)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    filler_edl = json.loads(Path(args.filler_edl).read_text(encoding="utf-8"))
    merged = json.loads(Path(args.merged_candidates).read_text(encoding="utf-8"))

    # 学阈值 → 重新给全部候选打标
    decisions, stats = relabel_candidates(merged["candidates"])
    (out_dir / "human_decisions.v4_learned.json").write_text(json.dumps({
        "schema_version": "human-decisions-mvp-v1",
        "package_id": "EP04-v4-learned",
        "reviewer": "LEARNED_FROM_HUMAN_v4",
        "review_mode": "learned_thresholds_v4",
        "review_mode_explanation": "从 EP03/EP04 27 条真人 accept/reject 与用户选 A 档长停顿的偏好中学到激进但保留呼吸的阈值；应用到全部候选。",
        "stats": stats,
        "decisions": decisions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    by_id = {c["candidate_id"]: c for c in merged["candidates"]}

    sr = 48000
    sync_items = []; gates_by_track = {t: [] for t in tracks}
    records = []

    # 1) 真人 EDL 6 段（保留）
    for c in filler_edl["cuts"]:
        sync_items.append((c["start_sample"], c["end_sample"]))
        records.append({"source": "真人-filler", "candidate_id": c["candidate_id"],
                         "start_sample": c["start_sample"], "end_sample": c["end_sample"],
                         "action": "sync_cut"})

    # 2) v4 学阈值打的标
    for d in decisions:
        if d["decision"] != "accept": continue
        cid = d["candidate_id"]; c = by_id.get(cid)
        if not c: continue
        s = int(c["start_sample"]); e = int(c["end_sample"])
        if d["action"] == "sync_cut_all_tracks":
            sync_items.append((s, e))
            records.append({"source": "LEARNED_v4_transient",
                             "candidate_id": cid, "start_sample": s, "end_sample": e,
                             "action": "sync_cut", "reason_key": c.get("reason_key")})
        elif d["action"] == "gate_source_track":
            src = c["track_id"]
            if src in gates_by_track:
                gates_by_track[src].append((s, e))
                records.append({"source": "LEARNED_v4_crosstalk",
                                 "candidate_id": cid, "start_sample": s, "end_sample": e,
                                 "action": "gate", "track_id": src,
                                 "confidence": c.get("confidence")})

    # 3) v4 aggressive 长停顿
    lps = long_pause_cuts(
        Path(args.mix_for_silencedetect), sr,
        args.long_pause_noise_db, 0.5,
        args.long_pause_trigger_seconds, args.long_pause_safety_ms,
        args.long_pause_keep_head_ms, args.long_pause_keep_tail_ms)
    for i, lp in enumerate(lps, 1):
        sync_items.append((lp["cut_start_sample"], lp["cut_end_sample"]))
        records.append({"source": "LONG_PAUSE_v4",
                         "candidate_id": f"LPv4-{i:03d}",
                         "start_sample": lp["cut_start_sample"],
                         "end_sample": lp["cut_end_sample"],
                         "action": "sync_cut",
                         "silence_seconds": lp["silence_duration_seconds"],
                         "cut_seconds": lp["cut_duration_seconds"]})

    sync_merged = merge_intervals(sync_items)
    gates_merged = {t: merge_intervals(v) for t, v in gates_by_track.items()}
    total_cut = sum(e - s for s, e in sync_merged) / sr

    edl_out = {
        "schema_version": "approved-edl-v4",
        "episode_id": "EP04-v4",
        "sample_rate_hz": sr,
        "sync_crossfade_ms": args.sync_crossfade_ms,
        "crossfade_curve": "equal_power (sin/cos)",
        "cuts": records,
        "sync_cuts_merged": [{"start_sample": s, "end_sample": e,
                              "duration_seconds": (e - s) / sr}
                             for s, e in sync_merged],
        "gates_by_track": {t: [{"start_sample": s, "end_sample": e,
                                 "duration_seconds": (e - s) / sr}
                                for s, e in v] for t, v in gates_merged.items()},
        "total_sync_cut_seconds": round(total_cut, 3),
        "long_pause_params": {
            "noise_db": args.long_pause_noise_db,
            "trigger_seconds": args.long_pause_trigger_seconds,
            "safety_ms": args.long_pause_safety_ms,
            "keep_head_ms": args.long_pause_keep_head_ms,
            "keep_tail_ms": args.long_pause_keep_tail_ms,
            "segments": lps,
        },
        "stats": stats,
    }
    (out_dir / "EP04-v4.edl.json").write_text(
        json.dumps(edl_out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 渲染三条 stem
    stem_paths = []; edited_seconds = 0.0; original_seconds = 0.0
    for label, wp in tracks.items():
        x, sr_probe = _read_wav(wp); sr = sr_probe
        original_seconds = x.size / sr
        apply_gate_inplace(x, gates_merged.get(label, []),
                            int(sr * args.gate_fade_ms / 1000))
        out = out_dir / f"{label}.edited.wav"
        n = apply_sync_cuts_ep(x, sr, sync_merged, out, args.sync_crossfade_ms)
        edited_seconds = n / sr
        del x
        stem_paths.append(out)

    # amix
    speech_wav = out_dir / "EP04-v4.speech-only.wav"
    inputs = []
    for p in stem_paths: inputs += ["-i", str(p)]
    subprocess.run(
        ["ffmpeg", "-y", *inputs,
         "-filter_complex", f"amix=inputs={len(stem_paths)}:normalize=0",
         "-c:a", "pcm_s16le", "-ac", "1", "-ar", "48000", str(speech_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 片头片尾拼接
    music_src = Path(args.intro_outro_music)
    intro_wav = out_dir / "_intro_music.wav"
    outro_wav = out_dir / "_outro_music.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(music_src),
         "-t", str(args.intro_music_seconds),
         "-af", f"afade=t=in:d=1.5",
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(intro_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(music_src),
         "-t", str(args.outro_music_seconds),
         "-af", f"afade=t=out:d={args.tail_fade_seconds}:st={max(0, args.outro_music_seconds - args.tail_fade_seconds)}",
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(outro_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # intro + speech + outro，用 acrossfade
    cx = args.music_speech_crossfade_seconds
    step1 = out_dir / "_intro_plus_speech.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(intro_wav), "-i", str(speech_wav),
         "-filter_complex", f"[0:a][1:a]acrossfade=d={cx}:c1=esin:c2=esin[out]",
         "-map", "[out]", "-c:a", "pcm_s16le", "-ac", "1", "-ar", "48000",
         str(step1)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    final_wav = out_dir / "EP04-v4.master.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(step1), "-i", str(outro_wav),
         "-filter_complex", f"[0:a][1:a]acrossfade=d={cx}:c1=esin:c2=esin[out]",
         "-map", "[out]", "-c:a", "pcm_s16le", "-ac", "1", "-ar", "48000",
         str(final_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    final_mp3 = out_dir / "EP04-v4.master.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(final_wav),
         "-c:a", "libmp3lame", "-b:a", "192k", str(final_mp3)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 清理中间
    for p in (intro_wav, outro_wav, step1):
        try: p.unlink()
        except FileNotFoundError: pass

    # 计算 master 长度
    import wave as _wave
    with _wave.open(str(final_wav), "rb") as wf:
        master_seconds = wf.getnframes() / wf.getframerate()

    manifest = {
        "schema_version": "render-manifest-v4",
        "episode_id": "EP04-v4",
        "sample_rate_hz": sr,
        "sync_crossfade_ms": args.sync_crossfade_ms,
        "sync_curve": "equal_power (sin/cos)",
        "long_pause": edl_out["long_pause_params"],
        "sync_cuts_count": len(sync_merged),
        "gate_count_by_track": {t: len(v) for t, v in gates_merged.items()},
        "total_sync_cut_seconds": edl_out["total_sync_cut_seconds"],
        "original_duration_seconds": round(original_seconds, 3),
        "speech_only_seconds": round(edited_seconds, 3),
        "master_seconds": round(master_seconds, 3),
        "intro_music_seconds": args.intro_music_seconds,
        "outro_music_seconds": args.outro_music_seconds,
        "music_speech_crossfade_seconds": args.music_speech_crossfade_seconds,
        "learn_stats": stats,
        "outputs": {
            "edl": str((out_dir / "EP04-v4.edl.json").resolve()),
            "human_decisions_learned": str((out_dir / "human_decisions.v4_learned.json").resolve()),
            "stems": [str(p.resolve()) for p in stem_paths],
            "speech_only_wav": str(speech_wav.resolve()),
            "master_wav": str(final_wav.resolve()),
            "master_wav_sha256": _sha(final_wav),
            "master_mp3": str(final_mp3.resolve()),
            "master_mp3_sha256": _sha(final_mp3),
        },
    }
    (out_dir / "render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "sync_cuts": len(sync_merged),
        "gates": {t: len(v) for t, v in gates_merged.items()},
        "sync_cut_seconds": edl_out["total_sync_cut_seconds"],
        "long_pause_segments": len(lps),
        "learn_stats": stats,
        "speech_only_seconds": manifest["speech_only_seconds"],
        "master_seconds": manifest["master_seconds"],
        "master_mp3": str(final_mp3),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
