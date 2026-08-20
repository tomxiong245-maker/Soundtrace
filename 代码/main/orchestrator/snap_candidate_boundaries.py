#!/usr/bin/env python3
"""snap_candidate_boundaries.py — 把候选的 start_sample/end_sample 精修到静音区/零交叉点。

原始候选边界来自 ASR 词级时间戳，误差 20-50ms，直接剪会有咔哒/尾音残留。
本脚本在每个边界 ±150ms 内扫 20ms 窗口 RMS，找最低点，再在附近找零交叉点，
把边界移到那里。剪出来自然干净。

用法：
  python3 snap_candidate_boundaries.py --run-dir main/runs/EP04/EP04-v23-...

依赖：纯 stdlib（wave + struct + json）。支持 24-bit EXTENSIBLE WAV（Python 3.12+）。
"""
from __future__ import annotations
import argparse, hashlib, json, struct, sys, wave
from pathlib import Path

WINDOW_MS = 150       # 边界左右各扫描窗口
RMS_WINDOW_MS = 20    # 计算 RMS 的窗口大小
STEP_MS = 5           # 扫描步长
ZC_RANGE_MS = 5       # 在最低点附近找零交叉的范围


def read_window_samples(wav_path: Path, start_sample: int, count_samples: int) -> tuple[list[int], int]:
    """读 WAV 从 start_sample 开始 count_samples 个采样，返回 (int list, sample_rate)."""
    with wave.open(str(wav_path), 'rb') as w:
        sw = w.getsampwidth()
        rate = w.getframerate()
        nframes = w.getnframes()
        start = max(0, min(start_sample, nframes))
        end = max(0, min(start_sample + count_samples, nframes))
        if end <= start:
            return [], rate
        w.setpos(start)
        raw = w.readframes(end - start)
    samples = []
    if sw == 3:
        # 24-bit signed little-endian → int
        for i in range(0, len(raw), 3):
            b = raw[i:i+3]
            v = b[0] | (b[1] << 8) | (b[2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            samples.append(v)
    elif sw == 2:
        samples = list(struct.unpack('<' + 'h' * (len(raw) // 2), raw))
    elif sw == 4:
        samples = list(struct.unpack('<' + 'i' * (len(raw) // 4), raw))
    else:
        raise ValueError(f"unsupported sample width: {sw}")
    return samples, rate


def rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return (sum(x * x for x in samples) / len(samples)) ** 0.5


def snap(wav_path: Path, target_sample: int, sr: int = 48000) -> tuple[int, float, str]:
    """返回 (snapped_sample, min_rms, method)."""
    window_samples = int(WINDOW_MS * sr / 1000)
    rms_window = int(RMS_WINDOW_MS * sr / 1000)
    step = int(STEP_MS * sr / 1000)

    scan_start = max(0, target_sample - window_samples)
    total_read = 2 * window_samples + rms_window
    all_samples, actual_sr = read_window_samples(wav_path, scan_start, total_read)
    if not all_samples:
        return target_sample, 0.0, "no_audio_available"

    # 扫 RMS 找最低窗口
    best_rms = float('inf')
    best_i = 0
    for i in range(0, len(all_samples) - rms_window, step):
        r = rms(all_samples[i:i + rms_window])
        if r < best_rms:
            best_rms = r
            best_i = i
    best_pos_abs = scan_start + best_i + rms_window // 2

    # 在最低点附近 ±5ms 找零交叉点
    zc_range = int(ZC_RANGE_MS * actual_sr / 1000)
    zc_scan_start = max(0, best_i + rms_window // 2 - zc_range)
    zc_scan_end = min(len(all_samples) - 1, best_i + rms_window // 2 + zc_range)
    for i in range(zc_scan_start, zc_scan_end):
        if all_samples[i] == 0 or all_samples[i] * all_samples[i + 1] < 0:
            return scan_start + i, best_rms, "zero_crossing"
    return best_pos_abs, best_rms, "rms_minimum"


def snap_candidates(run_dir: Path, denoise_dir: Path, out_path: Path | None = None) -> dict:
    """对 run 里所有候选做边界精修。返回更新后的 dict."""
    cands_path = run_dir / "all_candidates.json"
    if not cands_path.is_file():
        raise SystemExit(f"missing {cands_path}")
    d = json.loads(cands_path.read_text(encoding="utf-8"))

    # 每轨的 WAV 路径
    track_wavs = {}
    for wav in sorted(denoise_dir.glob("track_*.deepfiltered.wav")):
        stem = wav.stem.replace(".deepfiltered", "")
        track_wavs[stem] = wav

    # source_track_id 在 all_candidates.json 里常为 None（精简版），从 review_package.json 补
    pkg_source = {}
    pkg_path = run_dir / "review_bundle" / "review_package.json"
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            pkg_source = {c["candidate_id"]: c.get("source_track_id") for c in pkg.get("candidates", []) if c.get("candidate_id")}
        except Exception:
            pass

    def resolve_track(c):
        src = c.get("source_track_id") or pkg_source.get(c.get("candidate_id"))
        if src:
            return src
        # 从 stratum 尾部提取 track_XX
        st = str(c.get("stratum") or "")
        if ":" in st:
            tail = st.rsplit(":", 1)[-1]
            if tail.startswith("track_"):
                return tail
        return None

    snap_stats = {
        "snapped": 0,
        "moved_ms_total": 0.0,
        "unchanged": 0,
        "no_audio": 0,
        "invalid_order": 0,
    }
    snap_by_cid: dict[str, dict] = {}
    for c in d.get("candidates", []):
        # v19 contract: candidates flagged boundary_lock=True are anchored to
        # whole-word ASR bounds (e.g. immediate_repetition chains). Snap MUST
        # NOT shrink them to a stable inner segment — the rendering_gate
        # crossfade already suppresses pops at the word edges.
        if c.get("boundary_lock") is True:
            c["boundary_snap"] = {
                "status": "locked",
                "reason": c.get("boundary_lock_reason", "boundary_lock=true"),
            }
            snap_stats["unchanged"] += 1
            continue
        src = resolve_track(c)
        wav = track_wavs.get(src) if src else None
        if not wav:
            c["boundary_snap"] = {"status": "no_audio", "reason": f"no wav for {src}"}
            snap_stats["no_audio"] += 1
            continue
        with wave.open(str(wav), 'rb') as w:
            sr = w.getframerate()
        start = int(c.get("start_sample", 0))
        end = int(c.get("end_sample", 0))
        s_snap, s_rms, s_method = snap(wav, start, sr)
        e_snap, e_rms, e_method = snap(wav, end, sr)
        # A low-energy search can choose the same point for both sides of a
        # very short candidate.  Never let that become an executable zero-
        # length interval.  Keep the original boundaries and fail the snap
        # for this candidate so the reviewer sees the real candidate instead.
        if s_snap >= e_snap:
            c["boundary_snap"] = {
                "status": "invalid_order",
                "start_sample_original": start,
                "end_sample_original": end,
                "start_sample_proposed": s_snap,
                "end_sample_proposed": e_snap,
                "sample_rate": sr,
                "reason": "snapped boundary would be empty or reversed; original boundary retained",
            }
            snap_stats["invalid_order"] += 1
            continue
        moved_ms = (abs(s_snap - start) + abs(e_snap - end)) * 1000.0 / sr
        c["start_sample_original"] = start
        c["end_sample_original"] = end
        c["start_sample_snapped"] = s_snap
        c["end_sample_snapped"] = e_snap
        # The snapped interval is the canonical interval for all downstream
        # consumers.  The original interval remains explicitly available for
        # audit, but no later stage may silently fall back to it.
        c["start_sample"] = s_snap
        c["end_sample"] = e_snap
        if "start_seconds" in c:
            c["start_seconds"] = s_snap / sr
        if "end_seconds" in c:
            c["end_seconds"] = e_snap / sr
        c["boundary_snap"] = {
            "status": "snapped",
            "start_method": s_method,
            "end_method": e_method,
            "start_rms_at_snap": s_rms,
            "end_rms_at_snap": e_rms,
            "start_moved_samples": s_snap - start,
            "end_moved_samples": e_snap - end,
            "total_moved_ms": moved_ms,
            "sample_rate": sr,
            "window_ms": WINDOW_MS,
        }
        snap_stats["snapped"] += 1
        snap_stats["moved_ms_total"] += moved_ms
        snap_by_cid[c["candidate_id"]] = c["boundary_snap"]
    d["boundary_snap_summary"] = snap_stats

    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # Update the generator's candidate source as well as the derived files.
    # This makes candidate_source.json the one canonical boundary authority.
    source_path = run_dir / "candidates" / "candidate_source.json"
    source_updated = 0
    if source_path.is_file():
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source_by_id = {str(c.get("candidate_id")): c for c in source.get("candidates", [])}
        for cid, info in snap_by_cid.items():
            target = source_by_id.get(str(cid))
            if not target or info.get("status") != "snapped":
                continue
            original_start = int(target.get("start_sample", 0))
            original_end = int(target.get("end_sample", 0))
            target.setdefault("start_sample_original", original_start)
            target.setdefault("end_sample_original", original_end)
            target["start_sample"] = original_start + int(info["start_moved_samples"])
            target["end_sample"] = original_end + int(info["end_moved_samples"])
            sr = int(info["sample_rate"])
            if "start_seconds" in target:
                target["start_seconds"] = target["start_sample"] / sr
            if "end_seconds" in target:
                target["end_seconds"] = target["end_sample"] / sr
            target["boundary_snap"] = info
            source_updated += 1
        source["boundary_snap_applied"] = {
            "candidates_snapped": source_updated,
            "policy": "candidate_source.json is the canonical snapped boundary source; originals are retained for audit",
        }
        source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")

        # Keep both hashes: the pre-review learning sidecar was generated
        # before boundary snapping, while the current candidate source now
        # carries the canonical snapped boundaries.
        d["candidate_source_sha256_before_boundary_snap"] = d.get("candidate_source_sha256")
        d["candidate_source_sha256"] = _sha256(source_path)
        d["boundary_source"] = {
            "relpath": "candidates/candidate_source.json",
            "sha256": d["candidate_source_sha256"],
            "authority": "canonical_snapped_boundaries",
        }

    out_path = out_path or (run_dir / "all_candidates.json")
    out_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同步更新 calibration_source.json：把候选的 start_sample/end_sample 移到 snapped 边界
    # 这样 build_calibration_package 生成 preview 时用新边界，前端听到的 A/B 与最终成片一致
    cal_path = run_dir / "calibration_source.json"
    if cal_path.is_file():
        cal = json.loads(cal_path.read_text(encoding="utf-8"))
        cal_changed = 0
        for c in cal.get("candidates", []):
            info = snap_by_cid.get(c.get("candidate_id"))
            if not info or info.get("status") != "snapped":
                continue
            # 保留原边界供追溯
            c.setdefault("start_sample_original", c.get("start_sample"))
            c.setdefault("end_sample_original", c.get("end_sample"))
            sr = info["sample_rate"]
            c["start_sample"] = c["start_sample_original"] + info["start_moved_samples"]
            c["end_sample"] = c["end_sample_original"] + info["end_moved_samples"]
            # start/end_seconds 也同步（可选：一些下游用秒）
            if "start_seconds" in c: c["start_seconds"] = c["start_sample"] / sr
            if "end_seconds" in c: c["end_seconds"] = c["end_sample"] / sr
            c["boundary_snap"] = info
            cal_changed += 1
        selection = cal.get("delivery_calibration_selection")
        if isinstance(selection, dict) and source_path.is_file():
            selection["all_candidate_source_sha256"] = _sha256(source_path)
        cal["boundary_snap_applied"] = {"candidates_snapped": cal_changed, "policy": "start_sample/end_sample migrated to snapped boundary; originals kept in start_sample_original / end_sample_original"}
        cal_path.write_text(json.dumps(cal, ensure_ascii=False, indent=2), encoding="utf-8")

    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--denoise-dir", type=Path, help="override denoise dir (defaults to run_dir/denoise or reused run)")
    ap.add_argument("--out", type=Path, help="override output path (default overwrite all_candidates.json)")
    args = ap.parse_args()

    run_dir = args.run_dir.resolve()
    denoise = args.denoise_dir
    if denoise is None:
        # 试当前 run 的 denoise，否则从 analysis_reuse_manifest 找上游
        local = run_dir / "denoise"
        if local.is_dir() and any(local.glob("track_*.deepfiltered.wav")):
            denoise = local
        else:
            reuse_path = run_dir / "analysis_reuse_manifest.json"
            if reuse_path.is_file():
                reuse = json.loads(reuse_path.read_text(encoding="utf-8"))
                denoise = Path(reuse["source_run_dir"]) / "denoise"
    if denoise is None or not denoise.is_dir():
        print(f"BLOCKED: cannot find denoise directory (tried {run_dir/'denoise'} and analysis_reuse_manifest)", file=sys.stderr)
        return 2

    result = snap_candidates(run_dir, denoise, args.out)
    summary = result.get("boundary_snap_summary", {})
    n = summary.get("snapped", 0)
    avg = summary.get("moved_ms_total", 0.0) / max(1, n)
    print(f"snapped {n} candidates; avg boundary moved {avg:.1f} ms; wrote {args.out or (run_dir/'all_candidates.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
