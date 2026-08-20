"""executor_v2 · 消费 plan.json 逐步调 v2 adapter，写 provenance，失败精准重试。

用法：
    python3 executor_v2.py --run-dir main/runs/EP03-v2-<ts> [--stop-at STEP_ID] [--dry-run]

executor 只从 plan.json 读要跑的步骤和输入；不做业务决策。业务决策留给 planner。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters"))

import _adapter_base as ab  # noqa: E402
from generic_script_adapter import load_registry  # noqa: E402


EXECUTOR_ID = "executor-v2"
EXECUTOR_VERSION = "v1"


def topological_order(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {s["step_id"]: s for s in steps}
    seen: set[str] = set()
    order: list[dict[str, Any]] = []

    def visit(sid: str, stack: list[str]):
        if sid in seen:
            return
        if sid in stack:
            raise ValueError(f"dependency cycle: {stack + [sid]}")
        stack.append(sid)
        for dep in by_id[sid].get("depends_on") or []:
            if dep not in by_id:
                raise ValueError(f"step {sid} depends on unknown step {dep}")
            visit(dep, stack)
        stack.pop()
        seen.add(sid)
        order.append(by_id[sid])

    for s in steps:
        visit(s["step_id"], [])
    return order


def execute_plan(
    plan_path: Path,
    *,
    run_dir: Path,
    registry_path: Path,
    stop_at: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != "delivery-plan-v1":
        raise ValueError(f"unsupported plan schema: {plan.get('schema_version')}")

    adapters = load_registry(registry_path, project_root=PROJECT_ROOT)
    ordered = topological_order(plan["steps"])
    scope_hash = ab.compute_writes_scope_hash([str(run_dir)])

    results: list[dict[str, Any]] = []
    for step in ordered:
        adapter_id = step["adapter_id"]
        if adapter_id not in adapters:
            raise ValueError(f"unknown adapter {adapter_id} in step {step['step_id']}")
        adapter = adapters[adapter_id]

        # If dry_run, just emit the plan for this step; do not invoke.
        if dry_run:
            planned = adapter.dry_run_plan(step["inputs"], run_dir)
            results.append({
                "step_id": step["step_id"],
                "status": "DRY_RUN",
                "planned": planned,
            })
        else:
            # Only write-tool steps need policy args. Passing None for reads-only is fine.
            wpid = step.get("writes_policy_id") or adapter.contract.get("write_policy", {}).get("policy_id")
            wsh = step.get("writes_scope_hash") or scope_hash
            prov = adapter.invoke(
                step["inputs"],
                run_dir,
                writes_policy_id=wpid if adapter.contract["reads_only"] is False else None,
                writes_scope_hash=wsh if adapter.contract["reads_only"] is False else None,
            )
            results.append({
                "step_id": step["step_id"],
                "status": "OK" if prov.error is None else "FAILED",
                "error": prov.error,
                "provenance_relpath": f"{adapter_id}.provenance.json",
            })
            if prov.error is not None:
                # Fail closed: stop here so operator can inspect/re-run this step.
                break

        if stop_at and step["step_id"] == stop_at:
            break

    manifest = {
        "schema_version": "executor-manifest-v1",
        "plan_id": plan["plan_id"],
        "run_id": plan["run_id"],
        "executed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "executor_id": EXECUTOR_ID,
        "executor_version": EXECUTOR_VERSION,
        "results": results,
    }
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--registry", default=PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters/registry.json", type=Path)
    ap.add_argument("--stop-at", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    plan_path = args.run_dir / "plan.json"
    manifest = execute_plan(
        plan_path,
        run_dir=args.run_dir,
        registry_path=args.registry,
        stop_at=args.stop_at,
        dry_run=args.dry_run,
    )
    out = args.run_dir / "execution_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"executed_steps": len(manifest["results"]), "manifest": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
