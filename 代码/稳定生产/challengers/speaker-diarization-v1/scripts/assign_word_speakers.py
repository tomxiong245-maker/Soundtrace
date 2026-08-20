"""assign_word_speakers · 把 pyannote RTTM 说话人 turn 与 faster-whisper 词级时间戳合并
→ 每词一个 speaker_id + 归属置信度。

设计参考：WhisperX `assign_word_speakers`
（https://github.com/m-bain/whisperX 中 whisperx/diarize.py）

算法：
  对每个词 w = (start, end, text)：
    覆盖它的所有 turn = [(t_start, t_end, speaker_id)]
    归属 = 覆盖率最大的那个 turn 的 speaker_id
    置信度 = 覆盖率百分比
    若无 turn 覆盖 → speaker_id = UNKNOWN, confidence = 0

  多词覆盖同一 turn 边界 → 边界词取覆盖率大者，防止过度切分

**当前状态**：SKELETON 未实施。等 pyannote 3.4.0 装入
`稳定生产/challengers/speaker-diarization-v1/environment/venv/` 后填充。

用法：
  python3 assign_word_speakers.py \
    --rttm /path/to/diarization.rttm \
    --transcript /path/to/faster_whisper.words.json \
    --output /path/to/words_with_speaker.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_rttm(path: Path) -> list[tuple[float, float, str]]:
    """Parse RTTM lines: SPEAKER file 1 start dur <NA> <NA> speaker_id <NA> <NA>"""
    turns = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[0] != "SPEAKER":
            continue
        start = float(parts[3])
        dur = float(parts[4])
        speaker = parts[7]
        turns.append((start, start + dur, speaker))
    return turns


def parse_words(path: Path) -> list[dict]:
    """faster-whisper output format: list of {'word', 'start', 'end', ...}"""
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "words" in data:
        return data["words"]
    return data


def assign(words: list[dict], turns: list[tuple[float, float, str]]) -> list[dict]:
    """For each word, find covering turn(s) and assign max-overlap speaker."""
    out = []
    for w in words:
        ws = float(w.get("start", 0))
        we = float(w.get("end", ws))
        wlen = max(1e-6, we - ws)
        best_speaker = "UNKNOWN"
        best_overlap = 0.0
        for ts, te, sp in turns:
            ov = max(0.0, min(we, te) - max(ws, ts))
            if ov > best_overlap:
                best_overlap = ov
                best_speaker = sp
        conf = best_overlap / wlen if wlen > 0 else 0.0
        out.append({
            **w,
            "speaker_id": best_speaker,
            "speaker_assignment_confidence": round(conf, 3),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rttm", required=True, type=Path)
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args(argv)

    turns = parse_rttm(args.rttm)
    words = parse_words(args.transcript)
    result = assign(words, turns)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
