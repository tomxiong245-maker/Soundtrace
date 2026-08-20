#!/usr/bin/env python3
"""FROZEN 2026-08-19 · LLM Takeover

用户 2026-08-19 evening 明确: LLM 完全主导 candidate 生成 + 判决 (Stage 3.5.5).
本文件冻结 · 保留代码作 fallback · 不再是主流水消费者.
详见: 交付/最终交付文档/统筹全局/DEPRECATED_LLM_TAKEOVER_2026-08-19.md

** 何时会被 pipeline 消费 **:
- 若 Stage 3.5.5 LLM 挂 (3 mode 全挂)
- 或 --no-auto-llm-full-pipeline 明确 opt-out
- 否则 pipeline 走 LLM 主导 · 本文件 idle

---

transient-events-v1: 咳嗽 / 碰麦 / 桌敲候选检测。

工程共识（不训练模型、不联网、无外部依赖）：
- 短时 RMS / peak → crest factor（peak_dbfs - rms_dbfs）识别“瞬态”；
- 相邻 STFT 帧的幅度差平方和 → spectral flux 识别“突变”；
- 词级 activity 反证：若源轨该窗口有较多 primary 词，判为语音，不作为候选。

仅使用 numpy（Python 标准库 + numpy），不引入 scipy/librosa/torch。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """读取 mono PCM WAV。同时兼容 WAVE_FORMAT_EXTENSIBLE (0xFFFE)。

    标准库 `wave` 在遇到 fmt tag 0xFFFE 时会抛错；这里在 wave 失败后
    退回手工解析 RIFF 头，只支持 mono/多通道 16/24/32-bit PCM。
    """
    try:
        with wave.open(str(path), "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            n = wf.getnframes()
            raw = wf.readframes(n)
    except wave.Error:
        return _read_wav_extensible(path)
    if sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        # 24-bit little-endian PCM → int32
        b = np.frombuffer(raw, dtype=np.uint8)
        n_samples = b.size // 3
        b = b.reshape(n_samples, 3)
        vals = (b[:, 0].astype(np.int32)
                | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int32) << 16))
        vals[vals >= (1 << 23)] -= (1 << 24)
        data = vals.astype(np.float32) / (1 << 23)
    elif sw == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / (1 << 31)
    else:
        raise ValueError(f"unsupported sample width: {sw}")
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data, sr


def _read_wav_extensible(path: Path) -> tuple[np.ndarray, int]:
    """WAVE_FORMAT_EXTENSIBLE (0xFFFE) 手工解析，只支持 PCM 编码。"""
    import struct
    with path.open("rb") as f:
        riff = f.read(12)
        if riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise ValueError(f"not RIFF WAVE: {path}")
        fmt_bits: dict = {}
        data_bytes: bytes | None = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", hdr)
            payload = f.read(size)
            if size % 2 == 1:
                f.read(1)
            if chunk_id == b"fmt ":
                w_format_tag = struct.unpack("<H", payload[0:2])[0]
                n_ch = struct.unpack("<H", payload[2:4])[0]
                sr = struct.unpack("<I", payload[4:8])[0]
                sw_bits = struct.unpack("<H", payload[14:16])[0]
                sub = None
                if w_format_tag == 0xFFFE and len(payload) >= 40:
                    sub = payload[24:40]
                fmt_bits = {"tag": w_format_tag, "ch": n_ch, "sr": sr,
                            "bits": sw_bits, "sub": sub}
            elif chunk_id == b"data":
                data_bytes = payload
        if data_bytes is None:
            raise ValueError(f"no data chunk: {path}")
        # 只支持 PCM
        PCM_GUID_PREFIX = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        sub = fmt_bits.get("sub")
        is_pcm = (fmt_bits["tag"] == 1) or (
            fmt_bits["tag"] == 0xFFFE and sub == PCM_GUID_PREFIX)
        if not is_pcm:
            raise ValueError(f"unsupported non-PCM extensible WAV: {path}")
        n_ch = fmt_bits["ch"]
        sr = fmt_bits["sr"]
        bits = fmt_bits["bits"]
        b = np.frombuffer(data_bytes, dtype=np.uint8)
        if bits == 16:
            data = b.view("<i2").astype(np.float32) / 32768.0
        elif bits == 24:
            n_samples = b.size // 3
            b3 = b[: n_samples * 3].reshape(n_samples, 3)
            vals = (b3[:, 0].astype(np.int32)
                    | (b3[:, 1].astype(np.int32) << 8)
                    | (b3[:, 2].astype(np.int32) << 16))
            vals[vals >= (1 << 23)] -= (1 << 24)
            data = vals.astype(np.float32) / (1 << 23)
        elif bits == 32:
            data = b.view("<i4").astype(np.float32) / (1 << 31)
        else:
            raise ValueError(f"unsupported bits: {bits}")
        if n_ch > 1:
            data = data.reshape(-1, n_ch).mean(axis=1)
        return data, sr


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def dbfs(x: float) -> float:
    if x <= 1e-9:
        return -120.0
    return 20.0 * math.log10(x)


def frame_signal(x: np.ndarray, sr: int, frame_s: float, win_s: float
                 ) -> tuple[np.ndarray, int, int]:
    """按 hop=frame_s、window=win_s 分帧。返回 (frames, hop_samples, win_samples)。"""
    hop = int(round(sr * frame_s))
    win = int(round(sr * win_s))
    if win < hop:
        win = hop
    n_frames = max(0, (x.size - win) // hop + 1)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n_frames, win),
        strides=(x.strides[0] * hop, x.strides[0]),
        writeable=False,
    )
    return frames, hop, win


def compute_features(x: np.ndarray, sr: int, frame_s: float, win_s: float,
                     ) -> dict[str, np.ndarray]:
    frames, hop, win = frame_signal(x.astype(np.float32), sr, frame_s, win_s)
    if frames.shape[0] == 0:
        return {"rms_dbfs": np.array([]), "peak_dbfs": np.array([]),
                "flux": np.array([]), "low_ratio": np.array([]),
                "hop": hop, "win": win}
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    peak = np.max(np.abs(frames), axis=1) + 1e-12
    rms_db = 20.0 * np.log10(rms)
    peak_db = 20.0 * np.log10(peak)
    # 简单 FFT 用于 spectral flux 与低频能量占比
    # 使用 Hann 窗
    hann = np.hanning(win).astype(np.float32)
    windowed = frames * hann
    # 只取一半频谱
    spec = np.fft.rfft(windowed, axis=1)
    mag = np.abs(spec).astype(np.float32)
    # spectral flux
    flux = np.zeros(mag.shape[0], dtype=np.float32)
    if mag.shape[0] > 1:
        diff = mag[1:] - mag[:-1]
        diff = np.where(diff > 0, diff, 0.0)
        flux[1:] = np.sum(diff, axis=1) / (np.sum(mag[1:], axis=1) + 1e-9)
    # 低频能量占比（≤ 500 Hz）
    n_bins = mag.shape[1]
    freqs = np.linspace(0.0, sr / 2.0, n_bins)
    low_mask = freqs <= 500.0
    total_e = np.sum(mag * mag, axis=1) + 1e-9
    low_e = np.sum((mag * mag)[:, low_mask], axis=1)
    low_ratio = low_e / total_e
    return {
        "rms_dbfs": rms_db, "peak_dbfs": peak_db,
        "flux": flux, "low_ratio": low_ratio,
        "hop": hop, "win": win,
    }


@dataclass
class Event:
    start_frame: int
    end_frame: int
    max_peak_db: float
    min_rms_db: float
    crest_db: float
    max_flux: float
    mean_low_ratio: float


def find_events(feat: dict[str, Any], rules: dict[str, Any]) -> list[Event]:
    """在帧级 features 上找瞬态事件。返回 Event 列表。"""
    rms = feat["rms_dbfs"]
    peak = feat["peak_dbfs"]
    flux = feat["flux"]
    low_ratio = feat["low_ratio"]
    if rms.size == 0:
        return []
    # 通用触发：crest_db >= 10 或 flux >= 0.2
    crest = peak - rms
    min_crest = min(
        rules["cough_like"]["min_crest_db"],
        rules["mic_bump_like"]["min_crest_db"],
        rules["thump_like"]["min_crest_db"],
    )
    min_flux = rules["cough_like"]["min_spectral_flux"]
    trig = (crest >= min_crest - 2.0) | (flux >= min_flux * 0.7)
    events: list[Event] = []
    i = 0
    n = trig.size
    while i < n:
        if not trig[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and trig[j + 1]:
            j += 1
        seg = slice(i, j + 1)
        events.append(Event(
            start_frame=i, end_frame=j + 1,
            max_peak_db=float(peak[seg].max()),
            min_rms_db=float(rms[seg].min()),
            crest_db=float((peak[seg] - rms[seg]).max()),
            max_flux=float(flux[seg].max()),
            mean_low_ratio=float(low_ratio[seg].mean()),
        ))
        i = j + 1
    return events


def _classify(ev: Event, dur_s: float, rules: dict[str, Any]) -> str | None:
    """返回 reason_key 或 None."""
    r = rules
    # mic_bump 优先（能量低频、crest 极高、极短）
    mb = r["mic_bump_like"]
    if (mb["min_duration_seconds"] <= dur_s <= mb["max_duration_seconds"]
            and ev.max_peak_db >= mb["min_peak_dbfs"]
            and ev.crest_db >= mb["min_crest_db"]
            and ev.mean_low_ratio >= mb["min_low_energy_ratio"]):
        return "mic_bump_like"
    ch = r["cough_like"]
    # 用户 2026-08-19 明确关闭 · 见 rules JSON cough_like_disabled
    # 33/36 candidates 全是 cough 误报 · 中文爆破辅音 + ASR veto 失效 · 短期 disable 兜底
    cough_disabled = bool(r.get("cough_like_disabled", {}).get("value", False))
    if (not cough_disabled
            and ch["min_duration_seconds"] <= dur_s <= ch["max_duration_seconds"]
            and ev.max_peak_db >= ch["min_peak_dbfs"]
            and ev.crest_db >= ch["min_crest_db"]
            and ev.max_flux >= ch["min_spectral_flux"]):
        return "cough_like"
    th = r["thump_like"]
    if (th["min_duration_seconds"] <= dur_s <= th["max_duration_seconds"]
            and ev.max_peak_db >= th["min_peak_dbfs"]
            and ev.crest_db >= th["min_crest_db"]):
        return "thump_like"
    return None


def veto_by_asr(events_out: list[dict[str, Any]],
                words: list[dict[str, Any]] | None,
                rules: dict[str, Any]) -> list[dict[str, Any]]:
    if not words or not rules["asr_conflict"]["primary_words_veto"]:
        return events_out
    # 用户 2026-08-19 修 · unknown/None classification 也计入 (activity 分类器可能未跑)
    # 判据从 "primary 词占比 >= ratio" 改为 "语音状词占比 >= ratio"
    # 有 text 即视为词 · 不管 activity.classification (兼容分类器缺席场景)
    ratio = float(rules["asr_conflict"]["primary_words_veto_ratio"])
    kept: list[dict[str, Any]] = []
    for c in events_out:
        s, e = c["start_seconds"], c["end_seconds"]
        overlap = [w for w in words
                   if float(w.get("end_seconds", 0.0)) > s
                   and float(w.get("start_seconds", 0.0)) < e]
        total = len(overlap)
        speech_like = sum(
            1 for w in overlap
            if (w.get("activity", {}) or {}).get("classification") in ("primary", "unknown", None)
            and (w.get("text") or "").strip()
        )
        # 保留原字段供旧下游用
        prim = sum(1 for w in overlap
                   if (w.get("activity", {}) or {}).get("classification") == "primary")
        if total > 0 and (speech_like / total) >= ratio:
            c["asr_veto"] = {"reason": "LIKELY_SPEECH_NOT_TRANSIENT",
                             "primary_words": prim,
                             "speech_like_words": speech_like,
                             "total_words": total}
            continue
        kept.append(c)
    return kept


def merge_close(cands: list[dict[str, Any]], gap_s: float) -> list[dict[str, Any]]:
    if not cands:
        return cands
    cands = sorted(cands, key=lambda c: c["start_seconds"])
    out = [cands[0]]
    for c in cands[1:]:
        if c["start_seconds"] - out[-1]["end_seconds"] <= gap_s and \
                c["reason_key"] == out[-1]["reason_key"]:
            out[-1]["end_seconds"] = max(out[-1]["end_seconds"], c["end_seconds"])
            out[-1]["end_sample"] = max(out[-1]["end_sample"], c["end_sample"])
            out[-1]["merged"] = out[-1].get("merged", 1) + 1
        else:
            out.append(c)
    return out


def detect(wav_path: Path, rules: dict[str, Any],
           words: list[dict[str, Any]] | None = None,
           track_id: str = "track_01",
           chunk_seconds: float = 60.0) -> dict[str, Any]:
    x, sr = read_wav_mono(wav_path)
    # sample_rate_hz 消费点: rules 里若声明 sample_rate_hz, 断言 wav sr 与之一致 ·
    # 用户 2026-08-19 · orphan 修 · 防止参数按 48k 校准但 wav 是 44.1k 之类的隐性错配.
    _declared_sr = int(rules.get("sample_rate_hz") or 0)
    if _declared_sr and _declared_sr != sr:
        raise ValueError(
            f"transient-events rules sample_rate_hz={_declared_sr} but wav "
            f"{wav_path.name} sample rate is {sr}. crest/flux 阈值按 rules SR "
            f"校准 · 不允许静默转跑."
        )
    # 分块处理，避免长音频一次性 FFT 爆内存
    frame_s = rules["frame_seconds"]
    win_s = rules["window_seconds"]
    hop = int(round(sr * frame_s))
    win = max(hop, int(round(sr * win_s)))
    chunk_samples = int(round(sr * chunk_seconds))
    # 保证 chunk 边界对齐 hop
    chunk_samples = (chunk_samples // hop) * hop
    overlap = win  # 每块尾部保留一个 window 长度重叠，避免边界漏帧
    events_all: list[Event] = []
    total_hop = hop  # 用于最终 sample 计算
    n = x.size
    pos = 0
    while pos < n:
        end = min(n, pos + chunk_samples + overlap)
        seg = x[pos:end]
        feat = compute_features(seg, sr, frame_s, win_s)
        evs = find_events(feat, rules)
        # 只保留“开始 < 本块非重叠区”的事件，避免重叠区重复计
        cutoff_frames = max(0, (chunk_samples // hop))
        for e in evs:
            if e.start_frame >= cutoff_frames and pos + chunk_samples < n:
                continue
            e_shift = Event(
                start_frame=e.start_frame + (pos // hop),
                end_frame=e.end_frame + (pos // hop),
                max_peak_db=e.max_peak_db, min_rms_db=e.min_rms_db,
                crest_db=e.crest_db, max_flux=e.max_flux,
                mean_low_ratio=e.mean_low_ratio,
            )
            events_all.append(e_shift)
        pos += chunk_samples
        # 释放
        del feat, seg
    out_cands: list[dict[str, Any]] = []
    for ev in events_all:
        start_sample = int(ev.start_frame * total_hop)
        end_sample = int(ev.end_frame * total_hop)
        dur_s = (end_sample - start_sample) / sr
        rk = _classify(ev, dur_s, rules)
        if rk is None:
            continue
        out_cands.append({
            "reason_key": rk,
            "track_id": track_id,
            "start_sample": start_sample,
            "end_sample": end_sample,
            "start_seconds": start_sample / sr,
            "end_seconds": end_sample / sr,
            "peak_dbfs": ev.max_peak_db,
            "rms_dbfs": ev.min_rms_db,
            "crest_db": ev.crest_db,
            "spectral_flux": ev.max_flux,
            "low_energy_ratio": ev.mean_low_ratio,
            "policy": "review_only_no_automatic_accept",
        })
    out_cands = veto_by_asr(out_cands, words, rules)
    out_cands = merge_close(out_cands, rules["merge_gap_seconds"])
    return {
        "schema_version": "transient-events-candidates-v1",
        "sample_rate_hz": sr,
        "wav_sha256": sha256_file(wav_path),
        "rules_sha256": hashlib.sha256(
            json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "candidates": out_cands,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True, action="append",
                    help="LABEL=/abs/path.wav; 可多次")
    ap.add_argument("--rules", required=True)
    ap.add_argument("--transcript", action="append", default=None,
                    help="LABEL=/abs/path.classified.json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    tr_map: dict[str, list[dict[str, Any]]] = {}
    if args.transcript:
        for spec in args.transcript:
            label, p = spec.split("=", 1)
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            tr_map[label] = data.get("words", [])

    result = {
        "schema_version": "transient-events-run-v1",
        "rules_path": args.rules,
        "rules_sha256": hashlib.sha256(
            json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "tracks": [],
    }
    for spec in args.wav:
        label, p = spec.split("=", 1)
        det = detect(Path(p), rules, tr_map.get(label), track_id=label)
        det["label"] = label
        det["source_path"] = p
        result["tracks"].append(det)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(t["candidates"]) for t in result["tracks"])
    print(json.dumps({"tracks": len(result["tracks"]), "candidates": total,
                      "out": args.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
