#!/usr/bin/env python3
"""orchestrator-e2e-v1: 按 plan 编排整条候选生成流水线。

- 只调既有的 Challenger 脚本；不改任何 Champion。
- 每一步的命令、耗时、输出 SHA、退出码全部记录到 run_manifest.json。
- 任何禁止目录被本 orchestrator 写入 → fail-closed。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def snapshot_dir(root: Path) -> dict:
    if not root.exists():
        return {"missing": True}
    if root.is_file():
        return {"file": sha256_file(root), "size": root.stat().st_size}
    files = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            files[str(f)] = sha256_file(f)
    return {
        "count": len(files),
        "aggregate": hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }


def run(cmd: list[str]) -> dict:
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": p.returncode,
        "elapsed_seconds": round(time.time() - t0, 3),
        "stdout_tail": p.stdout[-800:],
        "stderr_tail": p.stderr[-800:],
    }


def step_classify_activity(plan: dict, project_root: Path,
                           run_dir: Path) -> dict:
    args = ["python3",
            str(project_root
                / "稳定生产/challengers/orchestrator-e2e-v1/scripts/classify_activity_local.py")]
    for t in plan["inputs"]["tracks"]:
        args += ["--track", f"{t['track_id']}={project_root / t['wav']}"]
        args += ["--transcript",
                 f"{t['track_id']}={project_root / t['canonical']}"]
    args += ["--out-dir", str(run_dir / "01_inputs/activity")]
    return run(args)


def step_crosstalk(plan: dict, project_root: Path, run_dir: Path) -> dict:
    activity = run_dir / "01_inputs/activity"
    args = ["python3",
            str(project_root
                / "稳定生产/challengers/crosstalk-candidate-v1/scripts/detect_crosstalk_candidates.py")]
    for t in plan["inputs"]["tracks"]:
        args += ["--transcript",
                 f"{t['track_id']}={activity / (t['track_id'] + '.classified.json')}"]
    args += ["--rules",
             str(project_root
                 / "稳定生产/challengers/crosstalk-candidate-v1/rules/crosstalk-candidate.v1.json")]
    args += ["--sample-rate-hz", str(plan["sample_rate_hz"])]
    args += ["--out", str(run_dir / "02_candidates/crosstalk.candidates.json")]
    return run(args)


def step_self_correction(plan: dict, project_root: Path, run_dir: Path) -> dict:
    activity = run_dir / "01_inputs/activity"
    args = ["python3",
            str(project_root
                / "稳定生产/challengers/self-correction-v1/scripts/detect_self_correction.py")]
    for t in plan["inputs"]["tracks"]:
        args += ["--transcript",
                 f"{t['track_id']}={activity / (t['track_id'] + '.classified.json')}"]
    args += ["--rules",
             str(project_root
                 / "稳定生产/challengers/self-correction-v1/rules/self-correction.v1.json")]
    args += ["--sample-rate-hz", str(plan["sample_rate_hz"])]
    args += ["--out", str(run_dir / "02_candidates/self_correction.candidates.json")]
    return run(args)


def step_transient(plan: dict, project_root: Path, run_dir: Path) -> dict:
    activity = run_dir / "01_inputs/activity"
    args = ["python3",
            str(project_root
                / "稳定生产/challengers/transient-events-v1/scripts/detect_transient_events.py")]
    for t in plan["inputs"]["tracks"]:
        args += ["--wav",
                 f"{t['track_id']}={project_root / t['wav']}"]
        args += ["--transcript",
                 f"{t['track_id']}={activity / (t['track_id'] + '.classified.json')}"]
    args += ["--rules",
             str(project_root
                 / "稳定生产/challengers/transient-events-v1/rules/transient-events.v1.json")]
    args += ["--out", str(run_dir / "02_candidates/transient.candidates.json")]
    return run(args)


def step_merge_candidates(plan: dict, project_root: Path, run_dir: Path) -> dict:
    args = ["python3",
            str(project_root
                / "稳定生产/challengers/orchestrator-e2e-v1/scripts/merge_candidates.py"),
            "--episode-id", plan["episode_id"],
            "--sample-rate-hz", str(plan["sample_rate_hz"]),
            "--crosstalk", str(run_dir / "02_candidates/crosstalk.candidates.json"),
            "--self-correction", str(run_dir / "02_candidates/self_correction.candidates.json"),
            "--transient", str(run_dir / "02_candidates/transient.candidates.json"),
            "--out", str(run_dir / "02_candidates/all.candidates.json")]
    return run(args)


STEPS = {
    "classify_activity": step_classify_activity,
    "candidates_crosstalk": step_crosstalk,
    "candidates_self_correction": step_self_correction,
    "candidates_transient": step_transient,
    "candidates_merge": step_merge_candidates,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--skip", action="append", default=[],
                    help="跳过 step id；例如 human_review 由真人做")
    args = ap.parse_args(argv)

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    project_root = Path(args.project_root).resolve()
    run_dir = (project_root / plan["run_dir"]).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    prohibited = [project_root / p.rstrip("/**")
                  for p in plan.get("prohibited_writes", [])]
    before = {str(p): snapshot_dir(p) for p in prohibited}

    manifest = {
        "schema_version": "orchestrator-e2e-run-v1",
        "plan_id": plan["plan_id"],
        "episode_id": plan["episode_id"],
        "run_dir": str(run_dir),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "steps": [],
    }

    for step in plan["steps"]:
        sid = step["id"]
        if sid in args.skip or sid in ("human_review", "post_review_learning"):
            manifest["steps"].append({"id": sid, "status": "skipped"})
            continue
        fn = STEPS.get(sid)
        if fn is None:
            manifest["steps"].append({"id": sid, "status": "unknown_step"})
            continue
        res = fn(plan, project_root, run_dir)
        # 收集输出 SHA
        out_hint = step.get("out")
        out_shas = {}
        if out_hint:
            target = (run_dir / out_hint).resolve() if not out_hint.startswith("/") else Path(out_hint)
            if target.exists():
                out_shas[str(target.relative_to(project_root))] = snapshot_dir(target)
        manifest["steps"].append({
            "id": sid,
            "status": "ok" if res["returncode"] == 0 else "failed",
            **res,
            "outputs": out_shas,
        })
        if res["returncode"] != 0:
            print(f"step {sid} failed rc={res['returncode']}")
            print(res["stderr_tail"])
            manifest["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
            (run_dir / "03_orchestrator/run_manifest.json").parent.mkdir(
                parents=True, exist_ok=True)
            (run_dir / "03_orchestrator/run_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return res["returncode"] or 1

    after = {str(p): snapshot_dir(p) for p in prohibited}
    manifest["prohibited_writes_before"] = before
    manifest["prohibited_writes_after"] = after
    manifest["prohibited_writes_unchanged"] = all(
        before[k] == after[k] for k in before)

    manifest["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    (run_dir / "03_orchestrator").mkdir(parents=True, exist_ok=True)
    (run_dir / "03_orchestrator/run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "plan": plan["plan_id"],
        "run_dir": str(run_dir),
        "prohibited_writes_unchanged": manifest["prohibited_writes_unchanged"],
        "manifest": str(run_dir / "03_orchestrator/run_manifest.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
