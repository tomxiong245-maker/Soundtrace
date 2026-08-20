#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_nisqa_mos.py · Challenger nisqa-cutverify-v1.

单条 WAV 片段跑 NISQA v2.0 无参考 MOS 预测。

Check 5 · 无参考 MOS 预测（NISQA）· 补充非替代 Champion 4 项 check。

调用契约::

    python check_nisqa_mos.py \\
        --clip-path <path/to/clip.wav> \\
        --out-json  <path/to/out.json> \\
        --mode      overall | delta

    - mode=overall : 单条 clip 独立打分。
    - mode=delta   : 打印提示指向 compute_mos_delta.py · exit=2。

输出 JSON schema (overall mode)::

    {
      "schema": "nisqa-mos-v1",
      "clip": "<absolute path>",
      "sha256": "<hex>",
      "duration_seconds": <float | null>,
      "scores": {
        "overall": <float>,
        "noisiness": <float>,
        "coloration": <float>,
        "discontinuity": <float>,
        "loudness": <float | null>
      },
      "model": "nisqa_v2.0",
      "computed_at_utc": "<ISO 8601>"
    }

fail-closed: nisqa 不可用时写
    {"schema": "nisqa-mos-v1", "clip": "...", "error": "nisqa_unavailable", "detail": "...",
     "computed_at_utc": "..."}
并 exit=1。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


ENGINE_ID = "nisqa-2.0"
MODEL_TAG = "nisqa_v2.0"
SCHEMA_ID = "nisqa-mos-v1"
MODES = ("overall", "delta")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_of_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _duration_seconds(path: Path) -> Optional[float]:
    """尽力估算 WAV 时长 · 不引入 numpy/librosa 等重依赖。"""
    try:
        import wave
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or 0
            if rate <= 0:
                return None
            return float(frames) / float(rate)
    except Exception:
        return None


def _find_default_weights() -> Optional[Path]:
    """在 Challenger venv 附近搜索 nisqa v2.0 权重文件。

    典型放在 environment/venv/lib/.../site-packages/nisqa/weights/nisqa.tar
    或 environment/weights/nisqa.tar。
    """
    env_var = os.environ.get("NISQA_WEIGHTS")
    if env_var:
        p = Path(env_var)
        if p.exists():
            return p

    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "environment" / "weights" / "nisqa.tar",
        here / "environment" / "weights" / "nisqa_mos_v20.tar",
    ]
    # site-packages 内也扫一次
    site_root = here / "environment" / "venv"
    if site_root.exists():
        for tar in site_root.rglob("nisqa*.tar"):
            candidates.append(tar)
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------- #
# NISQA thin wrapper
# --------------------------------------------------------------------------- #

class NisqaUnavailableError(RuntimeError):
    """nisqa 库缺失或权重缺失时抛出 · 主入口把它翻译成 fail-closed JSON。"""


def load_nisqa_model(weights_path: Optional[Path] = None, deg_path: Optional[Path] = None) -> Any:
    """加载 NISQA v2.0 预测器 · 找不到就抛 NisqaUnavailableError。

    **NISQA 官方 API 特殊性**: nisqaModel(args) 构造时立刻调 _loadDatasets() ·
    args["deg"] 在构造时就要填 · 构造后改无效。所以每次 predict 都要新构造。
    调用方**请传 deg_path** · 一次性构造 + 立即 predict。
    """
    try:
        from nisqa.NISQA_model import nisqaModel  # type: ignore
    except Exception as e:  # ImportError 或 依赖 (torch) 缺失
        raise NisqaUnavailableError(f"import nisqa failed: {e!r}") from e

    if weights_path is None:
        weights_path = _find_default_weights()
    if weights_path is None or not weights_path.exists():
        raise NisqaUnavailableError(
            "nisqa weights not found (looked for env NISQA_WEIGHTS and "
            "environment/weights/nisqa*.tar)"
        )

    args: Dict[str, Any] = {
        "mode": "predict_file",
        "pretrained_model": str(weights_path),
        "deg": str(deg_path) if deg_path else "",  # 构造时必须已填
        "data_dir": None,
        "output_dir": None,
        "csv_file": None,
        "csv_deg": None,
        "num_workers": 0,
        "bs": 1,
        "ms_channel": None,
        "tr_bs_val": 1,
        "tr_num_workers": 0,
        # NISQA v2.0 checkpoint 默认 ms_max_segments=1300 (≈52s 音频 @ hop 0.04) ·
        # 我们要打分 5-10min 整片,必须显式抬高上限,否则 predict 抛
        # "n_wins X > max_length 1300. Increase max window length ms_max_segments!"
        # 20000 覆盖 ~13min 音频,单 clip 内存足够。
        "ms_max_segments": 20000,
    }
    return nisqaModel(args)


def predict_mos(clip_path: Path, model: Optional[Any] = None) -> Dict[str, Optional[float]]:
    """对单条 WAV 输出 5 维 MOS。

    返回 dict::

        {"overall": <float>, "noisiness": <float>, "coloration": <float>,
         "discontinuity": <float>, "loudness": <float | None>}

    nisqa 不可用时抛 NisqaUnavailableError。

    **注意** · NISQA 官方 API 是"构造 + 立即 predict"一次性 · 不能复用同一 model 对象换 deg。
    所以 model 参数保留为兼容 · 但每次都重新构造。
    """
    # NISQA 构造时就锁定 deg · 必须每次新构造
    try:
        model = load_nisqa_model(deg_path=clip_path)
        df = model.predict()
    except NisqaUnavailableError:
        raise
    except Exception as e:
        raise NisqaUnavailableError(f"nisqa predict failed: {e!r}") from e

    def _get(col: str) -> Optional[float]:
        try:
            if hasattr(df, "iloc"):
                row = df.iloc[0]
                if col in row:
                    v = row[col]
                    return float(v) if v is not None else None
            if isinstance(df, dict) and col in df:
                v = df[col]
                if hasattr(v, "__iter__") and not isinstance(v, (str, bytes)):
                    v = list(v)[0]
                return float(v) if v is not None else None
        except Exception:
            return None
        return None

    return {
        "overall": _get("mos_pred"),
        "noisiness": _get("noi_pred"),
        "coloration": _get("col_pred"),
        "discontinuity": _get("dis_pred"),
        "loudness": _get("loud_pred"),
    }


# --------------------------------------------------------------------------- #
# main entry
# --------------------------------------------------------------------------- #

def check_nisqa_mos(clip_path: Path, mode: str) -> Dict[str, Any]:
    """入口 · 返回符合 SCHEMA_ID 的 dict · 不写文件。"""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    clip_path = Path(clip_path).resolve()
    if not clip_path.exists():
        return {
            "schema": SCHEMA_ID,
            "clip": str(clip_path),
            "error": "clip_not_found",
            "detail": f"no file at {clip_path}",
            "computed_at_utc": _utc_now_iso(),
        }

    scores = predict_mos(clip_path)

    return {
        "schema": SCHEMA_ID,
        "clip": str(clip_path),
        "sha256": _sha256_of_file(clip_path),
        "duration_seconds": _duration_seconds(clip_path),
        "scores": scores,
        "model": MODEL_TAG,
        "computed_at_utc": _utc_now_iso(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check 5 · NISQA no-reference MOS prediction",
    )
    parser.add_argument("--clip-path", required=True, type=Path, help="input WAV clip path")
    parser.add_argument("--out-json", required=True, type=Path, help="output JSON path")
    parser.add_argument(
        "--mode",
        required=True,
        choices=list(MODES),
        help="overall: standalone MOS · delta: use compute_mos_delta.py instead",
    )
    return parser


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.mode == "delta":
        # fallback 提示 · 真实 delta 走 compute_mos_delta.py
        payload = {
            "schema": SCHEMA_ID,
            "clip": str(Path(args.clip_path).resolve()),
            "error": "wrong_entry_point",
            "detail": "use compute_mos_delta.py --before-clip ... --after-clip ... instead",
            "computed_at_utc": _utc_now_iso(),
        }
        _write_json(args.out_json, payload)
        sys.stderr.write(
            "check_nisqa_mos.py: mode=delta is not supported here; "
            "use compute_mos_delta.py.\n"
        )
        return 2

    try:
        result = check_nisqa_mos(clip_path=args.clip_path, mode=args.mode)
    except NisqaUnavailableError as e:
        result = {
            "schema": SCHEMA_ID,
            "clip": str(Path(args.clip_path).resolve()),
            "error": "nisqa_unavailable",
            "detail": str(e),
            "computed_at_utc": _utc_now_iso(),
        }
        _write_json(args.out_json, result)
        return 1

    _write_json(args.out_json, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
