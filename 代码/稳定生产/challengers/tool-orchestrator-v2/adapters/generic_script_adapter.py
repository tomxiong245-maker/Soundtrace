"""Generic subprocess-based adapter for tool-orchestrator-v2.

Given a contract dict + command_template + outputs_template, produce an
AdapterBase subclass that wraps a Champion tool script without duplicating logic.

command_template: list[str] where placeholders in each token are formatted from
                  the invocation inputs, and {script} is the resolved script path.
                  Special forms:
                    "{name}"   → format with inputs[name] (single value)
                    "{name*}"  → **spread** inputs[name] (must be list/tuple) into
                                 N argv tokens; used for CLI args declared
                                 `nargs="+"` (e.g. --tracks a.wav b.wav c.wav)
outputs_template: dict[role, str] where placeholders are formatted from inputs.
                  Paths are resolved relative to run_dir unless already absolute.

Example (from registry.json entry for inspect_audio):
    "command_template": ["python3", "{script}", "{input_wav}", "{output_json}"]
    "outputs_template": {"inspection": "{output_json}"}

Example with list spread (mfa_align_and_extract_boundaries):
    "command_template": ["python3", "{script}", "--tracks", "{tracks*}", "--out", "{out}"]
    # inputs: {"tracks": [Path("a.wav"), Path("b.wav")], "out": Path("mfa.json")}
    # produces: ["python3", "<script>", "--tracks", "a.wav", "b.wav", "--out", "mfa.json"]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _adapter_base import AdapterBase, InputsValidationError


_SPREAD_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\*\}$")


class GenericScriptAdapter(AdapterBase):
    def __init__(
        self,
        contract: Mapping[str, Any],
        command_template: list[str],
        outputs_template: Mapping[str, str],
        project_root: Path | None = None,
    ):
        self.contract = dict(contract)
        self._command_template = list(command_template)
        self._outputs_template = dict(outputs_template)
        self._project_root_override = project_root

    def _resolve_script_path(self) -> Path:
        rel = self.contract["wraps_script"]
        root = self._project_root_override or Path(__file__).resolve().parent.parent.parent.parent.parent
        return (root / rel).resolve()

    def _format_token(self, tok: str, inputs: Mapping[str, Any], run_dir: Path) -> str:
        if tok == "{script}":
            return str(self._resolve_script_path())
        # inputs 里若已有 run_dir，尊重之；否则用 executor 传来的 run_dir。
        fmt_kwargs = dict(inputs)
        fmt_kwargs.setdefault("run_dir", str(run_dir))
        try:
            return tok.format(**fmt_kwargs)
        except KeyError as exc:
            raise InputsValidationError(f"unfilled placeholder {tok!r}: missing input {exc}")

    def _build_command(self, inputs: Mapping[str, Any], run_dir: Path) -> list[str]:
        out: list[str] = []
        for tok in self._command_template:
            m = _SPREAD_RE.match(tok)
            if m:
                # "{name*}" · spread a list value into multiple argv tokens
                key = m.group(1)
                if key not in inputs:
                    raise InputsValidationError(
                        f"unfilled placeholder {tok!r}: missing input {key!r} (list expected)"
                    )
                val = inputs[key]
                if not isinstance(val, (list, tuple)):
                    raise InputsValidationError(
                        f"placeholder {tok!r} expects list/tuple, got {type(val).__name__}"
                    )
                out.extend(str(x) for x in val)
            else:
                out.append(self._format_token(tok, inputs, run_dir))
        return out

    def _expected_outputs(self, inputs: Mapping[str, Any], run_dir: Path) -> dict[str, Path]:
        result: dict[str, Path] = {}
        for role, tmpl in self._outputs_template.items():
            raw = self._format_token(tmpl, inputs, run_dir)
            p = Path(raw)
            if not p.is_absolute():
                p = run_dir / p
            result[role] = p
        return result


def load_registry(registry_path: Path, project_root: Path | None = None) -> dict[str, GenericScriptAdapter]:
    """Instantiate all adapters from a registry.json file."""
    import json
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "tool-orchestrator-v2.registry.v1":
        raise ValueError(f"unknown registry schema: {data.get('schema_version')}")
    out: dict[str, GenericScriptAdapter] = {}
    for entry in data["adapters"]:
        out[entry["contract"]["adapter_id"]] = GenericScriptAdapter(
            contract=entry["contract"],
            command_template=entry["command_template"],
            outputs_template=entry["outputs_template"],
            project_root=project_root,
        )
    return out
