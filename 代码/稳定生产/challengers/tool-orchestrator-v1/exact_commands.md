# exact_commands · tool-orchestrator-v1

> 所有命令使用项目相对路径。执行前 `cd` 到项目根。

## 1. 静态校验注册表

```
python3 稳定生产/challengers/tool-orchestrator-v1/runner/registry_validator.py \
  main/tools/tools.json \
  --project-root . --require-scripts
# 预期：{"ok": true, "tool_count": 19, "errors": [], "warnings": []}
```

## 2. 跑三个测试文件

```
python3 稳定生产/challengers/tool-orchestrator-v1/tests/test_registry_validator.py
python3 稳定生产/challengers/tool-orchestrator-v1/tests/test_runner.py
python3 稳定生产/challengers/tool-orchestrator-v1/tests/test_safety_gates.py
# 预期：7+12+7 = 26/26 pass
```

## 3. 复现 Phase 6 端到端（inspect + summarize）

```
# 3.1 建 fixture 与 run 目录（timestamp 每次不同；不复用旧目录）
TS=$(python3 -c "from datetime import datetime; print(datetime.now().strftime('%Y%m%d-%H%M%S'))")
RUN="main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-$TS"
export RUN
mkdir -p "$RUN/fixtures"

# 3.2 生成 3 条 48 kHz mono 2s 合成 WAV
python3 - <<'PY'
import wave, struct, math, os
from pathlib import Path
run = Path(os.environ["RUN"])
fix = run / "fixtures"; fix.mkdir(parents=True, exist_ok=True)
sr, dur = 48000, 2.0; n = int(sr*dur)
for i, f in enumerate([220.0, 330.0, 440.0], 1):
    p = fix / f"track_{i:02d}.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack('<h', int(32767*0.1*math.sin(2*math.pi*f*k/sr))) for k in range(n)))
PY

# 3.3 写 episode config
RUN="$RUN" python3 - <<'PY'
import json, hashlib, os
from pathlib import Path
run = Path(os.environ["RUN"])
fix = run / "fixtures"
tracks = []
for i, p in enumerate(sorted(fix.glob("track_*.wav")), 1):
    tracks.append({
        "track_id": f"track_{i:02d}", "label": f"physical_mic_{i:02d}",
        "input_path": str(p), "sample_rate": 48000, "channel_count": 1,
        "duration_seconds": 2.0,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    })
inputs = [f"@track:track_{i:02d}.input_path" for i in range(1, len(tracks)+1)]
cfg = {
    "episode_id": run.name,
    "tracks": tracks,
    "steps": [
        {"step_id": "01_inspect_all", "tool": "inspect_audio_adapter", "phase": "pre_review",
         "params": {"input_wav": inputs, "output_json": "run:01_inspect/inspection.json"}},
        {"step_id": "02_summary", "tool": "summarize_inspection_adapter", "phase": "pre_review",
         "params": {"input_json": "run:01_inspect/inspection.json", "output_json": "run:02_summary/summary.json"}},
    ],
    "human_review_after": "02_summary",
}
(run / "episode.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
PY

# 3.4 冻结 plan + run
python3 稳定生产/challengers/tool-orchestrator-v1/runner/runner.py create \
    --config "$RUN/episode.json" --run-dir "$RUN/run" \
    --registry 稳定生产/challengers/tool-orchestrator-v1/registries/adapters.tools.json \
    --project-root .

python3 稳定生产/challengers/tool-orchestrator-v1/runner/runner.py run \
    --run-dir "$RUN/run"

# 预期尾行：[HUMAN_REVIEW_REQUIRED] after step 02_summary. Runner will not continue automatically.
```

## 4. 校验 Champion 未变

```
python3 - <<'PY'
import hashlib
from pathlib import Path
base = Path("稳定生产/challengers/tool-orchestrator-v1/baseline/champion_sha256_before.txt")
diffs = []
for line in base.read_text().splitlines():
    h, size, path = line.split(None, 2)
    now = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if now != h:
        diffs.append(path)
print(f"changed_files: {len(diffs)}")
PY
# 预期：changed_files: 0
```
