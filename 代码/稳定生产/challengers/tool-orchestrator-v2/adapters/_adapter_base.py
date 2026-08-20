"""tool-orchestrator-v2 adapter abstract base + registry helpers.

AdapterBase 定义每个 tool 必须提供的四段能力：validate_inputs / dry_run_plan /
invoke / verify_outputs，以及最终写入 run 目录的 provenance。所有 v2 adapter
子类化 AdapterBase，不直接调 subprocess；executor_v2 只与 AdapterBase 接口对话。

严禁：
- 在 adapter 里改 tool 脚本本身（`稳定生产/scripts/`, `端到端学习剪辑/代码/`）
- 在 adapter 里读/写 run 目录之外的路径
- 在 adapter 里跳过 SHA/schema 校验
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Mapping


ADAPTER_CONTRACT_VERSION = "adapter.schema.v1"
RUNNER_VERSION = "tool-orchestrator-v2.runner.v1"


class AdapterError(Exception):
    """Base error for adapter contract violations."""


class InputsValidationError(AdapterError):
    """Raised when validate_inputs finds a contract breach."""


class OutputsValidationError(AdapterError):
    """Raised when verify_outputs finds a contract breach."""


class WritesPolicyError(AdapterError):
    """Raised when a reads_only=false adapter is invoked without a valid writes policy."""


@dataclass
class Provenance:
    """Immutable execution record for one adapter invocation."""

    adapter_id: str
    tool_name: str
    adapter_version: str
    wraps_script_sha256: str
    input_sha_map: dict[str, str]
    output_sha_map: dict[str, str]
    exit_code: int
    duration_seconds: float
    started_at_utc: str
    finished_at_utc: str
    runner_version: str = RUNNER_VERSION
    error: str | None = None
    command: list[str] = field(default_factory=list)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utcnow_iso() -> str:
    # Use time.time() rather than datetime.now() because some sandboxes forbid Date.now()
    # equivalents. gmtime yields UTC; format matches ISO 8601 with Z suffix.
    t = time.time()
    tm = time.gmtime(t)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", tm)


class AdapterBase(ABC):
    """Abstract base for every tool-orchestrator-v2 adapter.

    Subclasses must:
    - class attribute `contract`: dict conforming to adapter.schema.v1.json
    - implement `_build_command(inputs, run_dir) -> list[str]`
    - implement `_expected_outputs(inputs, run_dir) -> dict[str, Path]` mapping role → path
    - optionally override `_verify_schema(role, path)` to check schemas
    """

    contract: Mapping[str, Any] = None  # set by subclass

    # ---- lifecycle ----------------------------------------------------

    def validate_inputs(self, inputs: Mapping[str, Any]) -> None:
        """Default validate: check declared required keys are present. Subclasses can extend."""
        if self.contract is None:
            raise AdapterError("subclass must set contract dict")
        required = _schema_required(self.contract.get("inputs_schema") or {})
        missing = [k for k in required if k not in inputs]
        if missing:
            raise InputsValidationError(f"missing required inputs: {missing}")

    def dry_run_plan(self, inputs: Mapping[str, Any], run_dir: Path) -> dict[str, Any]:
        """Return the plan without executing. Deterministic — same inputs → same plan.

        §11 · 未登记即报错：dry_run 层也校验 wraps_script 存在，避免 planner 冻结出一份 invoke 时才发现脚本缺失的 plan。
        Skeleton adapters（`contract["skeleton"] == True` · 例如 pyannote 权重未装的 speaker-diarize-v1）豁免存在性校验；invoke 时仍会 fail closed。
        """
        self.validate_inputs(inputs)
        cmd = self._build_command(inputs, run_dir)
        expected = self._expected_outputs(inputs, run_dir)
        # §11 早期校验：拒绝 wraps_script 指向不存在文件的 plan（Skeleton 除外）
        if not self.contract.get("skeleton"):
            script = self._resolve_script_path()
            if not script.exists():
                raise AdapterError(
                    f"wraps_script not found (dry_run): {script} "
                    f"(adapter={self.contract['adapter_id']}, tool_name={self.contract['tool_name']}). "
                    "§11 · 若脚本未登记或路径变更 → 登记 tools.json 并同步 registry；不要绕过 adapter。"
                )
        return {
            "adapter_id": self.contract["adapter_id"],
            "tool_name": self.contract["tool_name"],
            "wraps_script": self.contract["wraps_script"],
            "reads_only": self.contract["reads_only"],
            "command": cmd,
            "expected_outputs": {role: str(p) for role, p in expected.items()},
            "timeout_seconds": self.contract.get("timeout_seconds", 300),
            "skeleton": bool(self.contract.get("skeleton", False)),
        }

    def invoke(
        self,
        inputs: Mapping[str, Any],
        run_dir: Path,
        *,
        writes_policy_id: str | None = None,
        writes_scope_hash: str | None = None,
    ) -> Provenance:
        """Execute the wrapped tool, verify outputs, return provenance."""
        self.validate_inputs(inputs)
        self._check_writes_policy(writes_policy_id, writes_scope_hash, run_dir)

        run_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_command(inputs, run_dir)
        expected = self._expected_outputs(inputs, run_dir)

        input_sha_map = self._input_sha_map(inputs)
        wraps_sha = self._current_wraps_sha()
        declared_sha = self.contract.get("wraps_script_sha256")
        if declared_sha and declared_sha != wraps_sha:
            raise AdapterError(
                f"wraps_script SHA drift: declared={declared_sha} current={wraps_sha}"
            )

        started = _utcnow_iso()
        t0 = time.time()
        error: str | None = None
        exit_code = -1
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.contract.get("timeout_seconds", 300),
                check=False,
            )
            exit_code = proc.returncode
            if exit_code != 0:
                error = f"tool exited {exit_code}: {proc.stderr[:500]}"
        except subprocess.TimeoutExpired as exc:
            error = f"timeout after {exc.timeout}s"
            exit_code = -1
        except OSError as exc:
            error = f"os error: {exc}"
            exit_code = -1

        duration = time.time() - t0
        finished = _utcnow_iso()

        if error is None:
            try:
                self.verify_outputs(expected)
            except OutputsValidationError as exc:
                error = f"verify_outputs failed: {exc}"

        output_sha_map = {
            role: (sha256_file(path) if path.exists() else "")
            for role, path in expected.items()
        }

        prov = Provenance(
            adapter_id=str(self.contract["adapter_id"]),
            tool_name=str(self.contract["tool_name"]),
            adapter_version=str(self.contract["adapter_version"]),
            wraps_script_sha256=wraps_sha,
            input_sha_map=input_sha_map,
            output_sha_map=output_sha_map,
            exit_code=exit_code,
            duration_seconds=round(duration, 3),
            started_at_utc=started,
            finished_at_utc=finished,
            error=error,
            command=cmd,
        )
        prov_path = run_dir / f"{self.contract['adapter_id']}.provenance.json"
        prov_path.write_text(json.dumps(asdict(prov), indent=2, ensure_ascii=False), encoding="utf-8")
        return prov

    def verify_outputs(self, expected: Mapping[str, Path]) -> None:
        """Default verify: every expected output path must exist and be non-empty.
        Subclasses can override to add schema-level checks."""
        missing = [role for role, p in expected.items() if not p.exists()]
        if missing:
            raise OutputsValidationError(f"expected outputs missing: {missing}")
        empty = [role for role, p in expected.items() if p.stat().st_size == 0]
        if empty:
            raise OutputsValidationError(f"expected outputs empty: {empty}")

    # ---- hooks that subclasses implement ------------------------------

    @abstractmethod
    def _build_command(self, inputs: Mapping[str, Any], run_dir: Path) -> list[str]:
        """Return the argv list for subprocess.run. First element must be python3 or a script path."""

    @abstractmethod
    def _expected_outputs(self, inputs: Mapping[str, Any], run_dir: Path) -> dict[str, Path]:
        """Return role -> absolute output Path."""

    # ---- internal -----------------------------------------------------

    def _current_wraps_sha(self) -> str:
        script = self._resolve_script_path()
        if not script.exists():
            raise AdapterError(f"wraps_script not found: {script}")
        return sha256_file(script)

    def _resolve_script_path(self) -> Path:
        """Resolve wraps_script relative to project root. Subclasses can override."""
        rel = self.contract["wraps_script"]
        # project root is 3 levels up from this file:
        # 稳定生产/challengers/tool-orchestrator-v2/adapters/_base.py
        root = Path(__file__).resolve().parent.parent.parent.parent.parent
        return (root / rel).resolve()

    def _input_sha_map(self, inputs: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in inputs.items():
            if isinstance(value, (str, Path)):
                p = Path(str(value))
                if p.is_file():
                    result[key] = sha256_file(p)
        return result

    def _check_writes_policy(
        self,
        policy_id: str | None,
        scope_hash: str | None,
        run_dir: Path,
    ) -> None:
        reads_only = bool(self.contract.get("reads_only"))
        if reads_only:
            return
        if not policy_id or not scope_hash:
            raise WritesPolicyError(
                f"adapter {self.contract['adapter_id']} is write-tool; missing writes_policy_id/writes_scope_hash"
            )
        wp = self.contract.get("write_policy") or {}
        allowed_roots = list(wp.get("allowed_output_roots") or [])
        if wp.get("requires_run_dir", True):
            allowed_roots = [str(run_dir)]
        # scope_hash must equal sha256 of "|".join(sorted(allowed_roots))
        canonical = "|".join(sorted(allowed_roots))
        computed = sha256_bytes(canonical.encode("utf-8"))
        if computed != scope_hash:
            raise WritesPolicyError(
                f"writes_scope_hash mismatch: expected {computed}, got {scope_hash}"
            )


def _schema_required(schema: Mapping[str, Any]) -> list[str]:
    req = schema.get("required") if isinstance(schema, Mapping) else None
    if isinstance(req, list):
        return [str(x) for x in req]
    return []


def compute_writes_scope_hash(allowed_roots: list[str]) -> str:
    """Helper for planner to compute writes_scope_hash given a scope."""
    canonical = "|".join(sorted(allowed_roots))
    return sha256_bytes(canonical.encode("utf-8"))
