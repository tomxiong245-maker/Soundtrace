"""planner_v2 · 从 episode config + 政策绑定生成 plan.json (delivery-plan-v1).

用法（作为库）：
    plan = build_plan(episode_config, policy_bindings, tools_registry_path, plan_id)
    Path("plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))

用法（CLI）：
    python3 planner_v2.py --episode-config episode.json --run-dir main/runs/EP03-v2-<ts>

planner 只产 plan；不调 tool、不写 run 目录任何非 plan 文件。executor_v2 负责执行。

注意：这是 tool-orchestrator-v2 Challenger 的独立骨架。它不改 delivery_orchestrator.py
的旧路径；主流程的晋升在通过契约测试 + 独立复核后另行安排。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters"))

import _adapter_base as ab  # noqa: E402


PLANNER_ID = "planner-v2"
PLANNER_VERSION = "v1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return ab.sha256_file(path)


def _utcnow_iso() -> str:
    t = time.time()
    tm = time.gmtime(t)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", tm)


def _planner_source_sha() -> str:
    return sha256_file(Path(__file__))


def build_plan(
    episode_config: dict[str, Any],
    policy_bindings: dict[str, Any],
    registry_path: Path,
    *,
    plan_id: str,
    run_id: str,
    run_dir: Path,
) -> dict[str, Any]:
    """Produce a delivery-plan-v1 dict.

    Steps are ordered by dependency:
      01-inspect-track-XX (per track)
      02-estimate-sync
      03-denoise-tracks
      04-transcribe-tracks
      05-classify-activity
      06-build-review-package (human gate = after)
    """
    tracks = episode_config.get("tracks") or []
    if not tracks:
        raise ValueError("episode_config.tracks must be non-empty")

    inputs = {}
    for i, t in enumerate(tracks, start=1):
        key = f"track_{i:02d}"
        inputs[key] = {
            "relpath": t["relpath"],
            "sha256": t["sha256"],
            "role": f"raw_track_{i:02d}",
        }
    if music := episode_config.get("music"):
        inputs["music"] = {"relpath": music["relpath"], "sha256": music["sha256"], "role": "fixed_intro_outro_music"}

    scope = ab.compute_writes_scope_hash([str(run_dir)])
    steps: list[dict[str, Any]] = []

    # 01 inspect each track
    for i, t in enumerate(tracks, start=1):
        out_rel = f"01_inspect/track_{i:02d}.inspection.json"
        steps.append({
            "step_id": f"01-inspect-track-{i:02d}",
            "adapter_id": "inspect-audio-v2",
            "tool_name": "inspect_audio",
            "inputs": {
                "input_wav": str(PROJECT_ROOT / t["relpath"]),
                "output_json": str(run_dir / out_rel),
            },
            "expected_outputs": [{"relpath": out_rel, "role": "inspection"}],
            "depends_on": [],
            "timeout_seconds": 60,
            "retryable": True,
            "human_gate": "none",
        })

    # 02 estimate sync between first two tracks (baseline)
    if len(tracks) >= 2:
        steps.append({
            "step_id": "02-estimate-sync",
            "adapter_id": "estimate-sync-v2",
            "tool_name": "estimate_sync",
            "inputs": {
                "track_a": str(PROJECT_ROOT / tracks[0]["relpath"]),
                "track_b": str(PROJECT_ROOT / tracks[1]["relpath"]),
                "output_json": str(run_dir / "02_sync/sync.json"),
            },
            "expected_outputs": [{"relpath": "02_sync/sync.json", "role": "sync"}],
            "depends_on": [f"01-inspect-track-01", f"01-inspect-track-02"],
            "timeout_seconds": 120,
            "retryable": True,
            "human_gate": "none",
        })

    return {
        "schema_version": "delivery-plan-v1",
        "plan_id": plan_id,
        "run_id": run_id,
        "episode_id": episode_config["episode_id"],
        "created_at": _utcnow_iso(),
        "created_by": {
            "planner_id": PLANNER_ID,
            "planner_version": PLANNER_VERSION,
            "planner_source_sha256": _planner_source_sha(),
        },
        "inputs": inputs,
        "policy_bindings": policy_bindings,
        "steps": steps,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-config", required=True, type=Path)
    ap.add_argument("--policy-bindings", required=True, type=Path)
    ap.add_argument("--registry", default=PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters/registry.json", type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--plan-id", default=None)
    args = ap.parse_args(argv)

    args.run_dir.mkdir(parents=True, exist_ok=True)
    episode_config = json.loads(args.episode_config.read_text(encoding="utf-8"))
    policy_bindings = json.loads(args.policy_bindings.read_text(encoding="utf-8"))
    plan_id = args.plan_id or f"plan-{episode_config['episode_id']}-{int(time.time())}"
    plan = build_plan(
        episode_config,
        policy_bindings,
        args.registry,
        plan_id=plan_id,
        run_id=args.run_dir.name,
        run_dir=args.run_dir,
    )
    out = args.run_dir / "plan.json"
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"plan_id": plan_id, "steps": len(plan["steps"]), "path": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
