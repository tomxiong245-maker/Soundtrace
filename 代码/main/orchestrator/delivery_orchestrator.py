#!/usr/bin/env python3
"""可恢复的本地多轨播客交付入口。

这是 ``delivery-contract-v1.2`` 的第一个可运行入口，而不是把机器判断伪装成
人工标签的快捷脚本。常规路径：

    start -> （浏览器人工校准审核） -> resume -> （整片试听） -> record-final

``promote-v12`` 是一个刻意狭窄的恢复路径：当负责人已经试听并明确批准一个冻结的
历史试听成片时，把该*整套冻结动作*登记成 ``human_whole_episode_audition``。它不会
反向制造逐项 ``human_accept`` 标签，也不会把这次批准升级为跨期自动剪辑政策。

所有写入都在新建的 ``main/runs/<episode>/<run>/`` 下完成；原始 WAV、历史 run、
Mentor 成果和 Champion 都只读。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ORCHESTRATOR_DIR = Path(__file__).resolve().parent
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

# tool_lookup lives one directory up (main/tools). Add main/ to sys.path so
# `from tools.tool_lookup import script_for` resolves. Adding tools.json as the
# single source of truth for subprocess script paths fixes the
# "12 hardcoded PROJECT_ROOT / '..' constants unknown to tools.json" problem;
# see main/orchestrator/tests/test_orchestrator_uses_tools_json.py for the contract.
_MAIN_DIR = Path(__file__).resolve().parents[1]
if str(_MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_MAIN_DIR))

from production_edit_policy import apply_policy as apply_editing_policy
from production_edit_policy import load_policy as load_editing_policy
from tools.tool_lookup import script_for as _script_for  # noqa: E402
from case_memory import validate_case_memory  # noqa: E402
from integration_governance import (  # noqa: E402
    load_registry as load_integration_registry,
    mainline_capabilities,
)
from automix_adapter import run_automix_speech_mix  # noqa: E402

# Delivery report generation lives in its own file (registered as
# `write_delivery_report` tool in tools.json). Imported here for backward
# compatibility with existing caller sites; also runnable as `python3
# write_delivery_report.py --run-dir ... --final-status ...`.
from write_delivery_report import write_delivery_report  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "main" / "runs"
CONTRACT_VERSION = "delivery-contract-v1.2"

# ============================================================
# v2 executor · Champion opt-in bridge (Session 3 · 2026-08-19)
# ============================================================
# stage_command 里被调的 tool 脚本 · 若在 tool-orchestrator-v2/adapters/registry.json
# 有对应 v2 adapter，且开关 USE_V2_EXECUTOR 为真，则走 executor_v2 · 拿 provenance +
# SHA drift 校验 + 未登记即报错。默认关闭以保证 v1 路径行为不变；由 CLI 标志
# --executor v2 或 环境变量 MINGLUE_USE_V2_EXECUTOR=1 打开。EP04 端到端等价性验证
# 在 Session 3 后期由项目负责人手动跑（不由 agent 自动跑）。
# ============================================================
USE_V2_EXECUTOR = os.environ.get("MINGLUE_USE_V2_EXECUTOR", "").strip() in ("1", "true", "yes")
_V2_REGISTRY_PATH = PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters/registry.json"
_V2_ADAPTERS_CACHE: dict[str, Any] | None = None  # tool_name → adapter
_V2_LOAD_ATTEMPTED = False


def _load_v2_adapters() -> dict[str, Any] | None:
    """Lazily import v2 adapters registry · fail closed if unavailable."""
    global _V2_ADAPTERS_CACHE, _V2_LOAD_ATTEMPTED
    if _V2_LOAD_ATTEMPTED:
        return _V2_ADAPTERS_CACHE
    _V2_LOAD_ATTEMPTED = True
    try:
        v2_dir = PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters"
        if not v2_dir.is_dir() or not _V2_REGISTRY_PATH.is_file():
            return None
        if str(v2_dir) not in sys.path:
            sys.path.insert(0, str(v2_dir))
        from generic_script_adapter import load_registry as _load_registry
        by_adapter_id = _load_registry(_V2_REGISTRY_PATH, project_root=PROJECT_ROOT)
        # index by tool_name for easy lookup from stage_command
        by_tool = {a.contract["tool_name"]: a for a in by_adapter_id.values()}
        _V2_ADAPTERS_CACHE = by_tool
        return by_tool
    except Exception as exc:  # noqa: BLE001
        # fail closed · v1 fallback still works
        print(f"[v2 bridge] failed to load registry: {exc}; falling back to v1 subprocess", file=sys.stderr)
        return None


def _tool_name_for_script(script_path: Path) -> str | None:
    """Reverse-lookup: script full_path → tool_name (from tools.json)."""
    try:
        tj = json.loads((_MAIN_DIR / "tools/tools.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    rel = str(script_path.relative_to(PROJECT_ROOT)) if script_path.is_absolute() else str(script_path)
    for t in tj.get("tools", []):
        if t.get("full_path") == rel or t.get("script") == rel:
            return t.get("name")
        # basename fallback
        if Path(t.get("full_path") or t.get("script") or "").name == script_path.name:
            return t.get("name")
    return None


RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
MUSIC_SOURCE = PROJECT_ROOT / "音频参考库/raw material/第三集/片头片尾music.mp3"
MUSIC_SHA256 = "3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83"
MUSIC_TEMPLATE_CONFIG_SOURCE = PROJECT_ROOT / "main/orchestrator/music_templates.json"
PREFERENCE_SOURCE = PROJECT_ROOT / "统筹全局/当前剪辑偏好快照.md"
PREFERENCE_ID = "editing-preference-profile-v15-draft"
P0_SCRIPT = _script_for("p0_transcribe_mvp")
SEMANTIC_SCRIPT = _script_for("build_semantic_transcript")
CANDIDATE_SCRIPT = _script_for("build_filler_global_pause_candidates")
DEFAULT_CANDIDATE_RULES = (
    PROJECT_ROOT
    / "稳定生产/challengers/filler-global-pause-v14/rules/candidate_rules.v18.json"
)
DEFAULT_EDITING_POLICY = PROJECT_ROOT / "main/orchestrator/editing_policy.guards-v1.json"
DEFAULT_INTEGRATION_REGISTRY = (
    PROJECT_ROOT / "main/knowledge/integration_governance/owner_attested_mainline.v1.json"
)
REVIEW_SERVER = _script_for("serve_review_ui")
REVIEW_FRONTEND = PROJECT_ROOT / "审核前端/challenger-review-product-v1/mvp.html"
DEEPFILTER_DENOISE_SCRIPT = _script_for("denoise_tracks")
TRANSITION_QC_SCRIPT = _script_for("analyze_transition_qc")
SNAP_BOUNDARIES_SCRIPT = _script_for("snap_candidate_boundaries")
PREDICT_ARTIFACT_SCRIPT = _script_for("predict_cut_artifact")
EVENT_ROUTE_SCRIPT = _script_for("review_event_routes")
APPLY_PREFERENCE_SCRIPT = _script_for("apply_preference_snapshot")
LABEL_LEARNING_DRIVER_SCRIPT = _script_for("label_learning_driver")
CASE_MEMORY_SCRIPT = _script_for("build_case_memory")
CANDIDATE_FAMILY_SCRIPT = _script_for("build_candidate_family_bundle")
DEFAULT_EXPERIENCE_SNAPSHOT = (
    PROJECT_ROOT
    / "main/runs/LABEL-LEARNING-v3-20260816/preference_snapshot/snapshot_manifest.json"
)
DEVELOPMENT_BENCHMARK_SCRIPT = _script_for("run_development_benchmark")


class DeliveryError(RuntimeError):
    """A fail-closed condition which must not be silently repaired."""


def resolve_default_experience_snapshot() -> Path:
    """Prefer the atomically activated human-feedback snapshot for new runs.

    The fallback keeps old projects usable before any completed review has
    created an active pointer.  A broken pointer is a hard failure: silently
    reverting to an older snapshot would hide that a reviewer submission did
    not reach future runs.
    """

    try:
        from refresh_label_learning_snapshot import SnapshotRefreshError, resolve_active_snapshot

        active = resolve_active_snapshot(PROJECT_ROOT)
    except SnapshotRefreshError as exc:
        raise DeliveryError(f"active label-learning snapshot is invalid: {exc}") from exc
    return active or DEFAULT_EXPERIENCE_SNAPSHOT


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DeliveryError(f"JSON object required: {path}")
    return value


def music_template_definition(template_id: str) -> dict[str, Any]:
    """Load one centrally defined music template without exposing mutable shared state."""

    document = read_json(MUSIC_TEMPLATE_CONFIG_SOURCE)
    if document.get("schema_version") != "music-template-definitions-v1":
        raise DeliveryError("music template definitions have an unsupported schema")
    templates = document.get("templates")
    if not isinstance(templates, dict) or template_id not in templates:
        raise DeliveryError(f"unsupported music template: {template_id}")
    definition = templates[template_id]
    if not isinstance(definition, dict):
        raise DeliveryError(f"invalid music template definition: {template_id}")
    return json.loads(json.dumps(definition, ensure_ascii=False))


def validate_reference_linear_timing(definition: dict[str, Any]) -> None:
    """Fail closed if the user-approved five-second voice entrance drifts."""

    expected = {
        "voice_start_seconds": 5.0,
        "intro_music_only_end_seconds": 5.0,
        "intro_fade_out_start_seconds": 5.0,
        "intro_fade_out_end_seconds": 16.0,
        "outro_fade_in_lead_seconds": 22.0,
        "outro_music_tail_seconds": 37.976,
    }
    for key, value in expected.items():
        if float(definition.get(key, -1)) != value:
            raise DeliveryError(
                f"reference-linear-v1 hard requirement changed: {key} must be {value}"
            )


def resolve_run_music_timing(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Resolve and verify the timing snapshot used by a render.

    A plan created before the checkpoint field was introduced may fall back to
    the canonical definition, but a checkpoint, when present, always wins only
    after its content and SHA are verified.  This keeps an old pre-review v20
    run safe without silently changing a later run's frozen timing.
    """

    music = plan.get("music") or {}
    template_id = str(music.get("music_template_id") or "")
    if not template_id:
        raise DeliveryError("plan is missing music_template_id")
    canonical = music_template_definition(template_id)
    timing = music.get("timing")
    checkpoint_relpath = plan.get("requirements_checkpoint_relpath")
    checkpoint_path = run_dir / str(checkpoint_relpath) if checkpoint_relpath else run_dir / "requirements_checkpoint.json"
    if checkpoint_path.is_file():
        checkpoint = read_json(checkpoint_path)
        checkpoint_music = checkpoint.get("music") or {}
        if checkpoint_music.get("template_id") != template_id:
            raise DeliveryError("requirements checkpoint music template disagrees with plan")
        checkpoint_timing = checkpoint_music.get("timing")
        checkpoint_sha = checkpoint_music.get("timing_sha256")
        if not isinstance(checkpoint_timing, dict) or checkpoint_sha != sha256_bytes(checkpoint_timing):
            raise DeliveryError("requirements checkpoint music timing SHA is invalid")
        if checkpoint_timing != canonical:
            raise DeliveryError("requirements checkpoint no longer matches canonical music template")
        if isinstance(timing, dict) and timing != checkpoint_timing:
            raise DeliveryError("plan music timing disagrees with requirements checkpoint")
        timing = checkpoint_timing
    if not isinstance(timing, dict):
        timing = canonical
    if template_id == "reference-linear-v1":
        validate_reference_linear_timing(timing)
    return timing


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        # Do not resolve ``path`` here: inputs and the fixed music are intentionally
        # represented by symlinks whose targets live outside the run.  We need the
        # run-local link path, not the external target path.
        lexical = path.absolute()
        return lexical.relative_to(run_dir.absolute()).as_posix()
    except ValueError as exc:
        raise DeliveryError(f"artifact must be inside its run: {path}") from exc


def tool_reference(path: Path) -> str:
    """Keep project tools relative while retaining an explicit path for audited local overrides."""

    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def safe_identifier(value: str, label: str) -> str:
    value = value.strip()
    if not RUN_ID_RE.fullmatch(value):
        raise DeliveryError(f"{label} must match {RUN_ID_RE.pattern}: {value!r}")
    return value


def validate_event_history_runs(
    history_runs: Iterable[Path] | None,
    *,
    episode_id: str,
) -> list[dict[str, Any]]:
    """Freeze explicit human-review sources for a future review sidecar.

    An empty list is valid and intentionally produces ``new_event`` metadata
    for every candidate.  We never auto-discover arbitrary runs: callers must
    explicitly name each historical run so an old machine draft cannot become
    a hidden source of human truth.
    """

    result: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw_path in history_runs or ():
        path = Path(raw_path).expanduser().resolve()
        if path in seen:
            raise DeliveryError(f"duplicate event history run: {path}")
        seen.add(path)
        try:
            relpath = path.relative_to(PROJECT_ROOT.resolve()).as_posix()
        except ValueError as exc:
            raise DeliveryError(f"event history run must be inside project: {path}") from exc
        if not path.is_dir() or not path.name.startswith(f"{episode_id}-"):
            raise DeliveryError(f"event history run must be an existing {episode_id} run: {path}")
        input_manifest_path = path / "input_manifest.json"
        package_path = path / "review_bundle" / "review_package.json"
        decisions_path = path / "human_decisions.json"
        for required in (input_manifest_path, package_path, decisions_path):
            if not required.is_file():
                raise DeliveryError(f"event history run is missing {required.relative_to(path)}: {path}")
        history_identity = read_json(input_manifest_path)
        if history_identity.get("episode_id") != episode_id:
            raise DeliveryError(f"event history episode mismatch: {path}")
        result.append(
            {
                "run_relpath": relpath,
                "run_id": history_identity.get("run_id") or path.name,
                "episode_id": episode_id,
                "input_manifest_sha256": sha256_file(input_manifest_path),
                "review_package_sha256": sha256_file(package_path),
                "human_decisions_sha256": sha256_file(decisions_path),
                "scope": "itemized human decisions reference only; no current decision/EDL inheritance",
            }
        )
    return result


def derive_episode_id(input_dir: Path) -> str:
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", input_dir.name).strip(".-_")
    return safe_identifier(raw[:64] or "episode", "derived episode_id")


def derive_run_id(episode_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return safe_identifier(f"{episode_id}-delivery-{stamp}", "run_id")


def wav_info(path: Path) -> dict[str, int | float]:
    """Read PCM / PCM-extensible WAV metadata without decoding or rewriting it."""

    fmt: tuple[int, int, int, int] | None = None
    data_size: int | None = None
    with path.open("rb") as handle:
        if handle.read(4) != b"RIFF":
            raise DeliveryError(f"not RIFF WAV: {path}")
        handle.seek(4, 1)
        if handle.read(4) != b"WAVE":
            raise DeliveryError(f"not WAVE WAV: {path}")
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", header)
            start = handle.tell()
            if chunk_id == b"fmt ":
                raw = handle.read(size)
                if len(raw) < 16:
                    raise DeliveryError(f"invalid WAV fmt chunk: {path}")
                tag, channels, sample_rate, _, block_align, bits = struct.unpack("<HHIIHH", raw[:16])
                if tag == 0xFFFE and len(raw) >= 26:
                    tag = struct.unpack("<H", raw[24:26])[0]
                if tag != 1:
                    raise DeliveryError(f"only PCM WAV is supported (format={tag}): {path}")
                fmt = channels, sample_rate, block_align, bits
            elif chunk_id == b"data":
                data_size = size
            handle.seek(start + size + (size & 1))
    if fmt is None or data_size is None:
        raise DeliveryError(f"WAV missing fmt/data chunk: {path}")
    channels, sample_rate, block_align, bits = fmt
    frame_count = data_size // block_align
    return {
        "channels": channels,
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "bits_per_sample": bits,
        "duration_seconds": frame_count / sample_rate,
    }


def resolve_ffmpeg(explicit: str | None = None) -> str:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise DeliveryError(f"ffmpeg does not exist: {candidate}")
    found = shutil.which("ffmpeg")
    if not found:
        raise DeliveryError("ffmpeg was not found; install it locally or pass --ffmpeg")
    return found


def audio_probe(path: Path, ffprobe: str | None = None) -> dict[str, Any]:
    binary = ffprobe or shutil.which("ffprobe")
    if not binary:
        # macOS ships afinfo even on machines where the optional ffmpeg bundle is
        # unavailable.  It is sufficient for a structural fallback probe; it is
        # intentionally not presented as a loudness measurement.
        afinfo = shutil.which("afinfo")
        if not afinfo:
            raise DeliveryError("neither ffprobe nor afinfo was found")
        completed = subprocess.run([afinfo, str(path)], capture_output=True, text=True, check=False)
        if completed.returncode:
            raise DeliveryError(f"afinfo failed for {path.name}: {completed.stderr.strip()}")
        result: dict[str, Any] = {
            "probe_tool": "afinfo",
            "structural_summary": completed.stdout.strip(),
        }
        if path.suffix.lower() == ".wav":
            result["wav_info"] = wav_info(path)
        return result
    command = [
        binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_name,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise DeliveryError(f"ffprobe failed for {path.name}: {completed.stderr.strip()}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DeliveryError(f"ffprobe returned invalid JSON for {path.name}") from exc
    return {
        "duration_seconds": float(result.get("format", {}).get("duration", 0.0)),
        "streams": result.get("streams", []),
    }


def relative_symlink(link: Path, target: Path) -> None:
    if not target.is_file():
        raise DeliveryError(f"link target is missing: {target}")
    if link.exists() or link.is_symlink():
        raise DeliveryError(f"refusing to replace existing input link: {link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(target.resolve(), link.parent.resolve()), link)


def known_music() -> dict[str, Any]:
    if not MUSIC_SOURCE.is_file():
        raise DeliveryError(f"fixed music is missing: {MUSIC_SOURCE}")
    actual = sha256_file(MUSIC_SOURCE)
    if actual != MUSIC_SHA256:
        raise DeliveryError(
            "fixed music SHA-256 mismatch; refusing to substitute a different track: "
            f"expected {MUSIC_SHA256}, got {actual}"
        )
    return {
        "music_template_id": "reference-linear-v1",
        "source_sha256": actual,
        "source_file_name": MUSIC_SOURCE.name,
        "source_status": "FIXED_AUTHORIZED_ASSET",
    }


def create_state(run_dir: Path, episode_id: str, run_id: str, state: str, note: str) -> None:
    write_json(
        run_dir / "state.json",
        {
            "schema_version": "delivery-state-v1",
            "episode_id": episode_id,
            "run_id": run_id,
            "state": state,
            "history": [{"from": None, "to": state, "at": utc_now(), "note": note}],
        },
    )


def transition(run_dir: Path, target: str, note: str) -> None:
    path = run_dir / "state.json"
    state = read_json(path)
    previous = state.get("state")
    state["state"] = target
    state.setdefault("history", []).append(
        {"from": previous, "to": target, "at": utc_now(), "note": note}
    )
    write_json(path, state)


def _benchmark_fallback_evidence(run_dir: Path, *, phase: str, error: str) -> None:
    """Leave an honest non-blocking record if the benchmark wrapper cannot run.

    Benchmark evidence helps diagnose candidate coverage and listening effort, but
    it is not a semantic deletion authority and must never turn a successful
    review/render action into a failed delivery merely because its JSON-only
    diagnostics are temporarily unavailable.
    """

    try:
        identity = read_json(run_dir / "run_identity.json")
        episode_id = identity.get("episode_id")
        run_id = identity.get("run_id")
        identity_sha = sha256_file(run_dir / "run_identity.json")
    except Exception:
        episode_id = None
        run_id = None
        identity_sha = None
    payload = {
        "schema_version": "delivery-development-benchmark-evidence-v1",
        "episode_id": episode_id,
        "run_id": run_id,
        "run_identity_sha256": identity_sha,
        "refreshed_at": utc_now(),
        "phase": phase,
        "status": "BENCHMARK_EVIDENCE_UNAVAILABLE",
        "delivery_effect": "NON_BLOCKING_DEVELOPMENT_EVIDENCE_ONLY",
        "error": error,
        "next_rule": "fix benchmark evidence separately; do not invent human results or change an EDL to make this pass",
    }
    try:
        write_json(run_dir / "benchmark_evidence.json", payload)
    except OSError:
        # The normal production paths will independently surface a material
        # filesystem failure.  Do not mask their outcome by raising here.
        pass


def refresh_development_benchmark_nonblocking(
    run_dir: Path,
    *,
    phase: str,
    python: str,
) -> dict[str, Any]:
    """Refresh development-only benchmark evidence without changing delivery state.

    The called wrapper reads only run JSON plus existing benchmark JSON/Markdown.
    It deliberately does not decode source or preview media and cannot create an
    accept/reject decision, EDL, Champion promotion, or release approval.
    """

    command = [
        python,
        str(DEVELOPMENT_BENCHMARK_SCRIPT),
        "--run-dir",
        str(run_dir),
        "--phase",
        phase,
    ]
    started_at = utc_now()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        invocation_error = None
    except OSError as exc:
        returncode = None
        stdout = ""
        stderr = ""
        invocation_error = f"benchmark wrapper could not start: {exc}"

    log = {
        "schema_version": "delivery-development-benchmark-command-v1",
        "phase": phase,
        "started_at": started_at,
        "finished_at": utc_now(),
        "command": command,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "media_boundary": "wrapper only invokes JSON/Markdown benchmark tools; no source or preview media is decoded, copied, hashed, or uploaded",
    }
    try:
        logs = run_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        write_json(logs / f"development_benchmark_{phase}.command.json", log)
    except OSError:
        # See the same boundary in _benchmark_fallback_evidence: this evidence
        # refresh is expressly non-blocking for the delivery lifecycle.
        pass

    evidence_path = run_dir / "benchmark_evidence.json"
    if invocation_error is None and returncode == 0 and evidence_path.is_file():
        try:
            evidence = read_json(evidence_path)
            identity = read_json(run_dir / "run_identity.json")
            if (
                evidence.get("status") == "PASS"
                and evidence.get("phase") == phase
                and evidence.get("run_id") == identity.get("run_id")
                and evidence.get("run_identity_sha256") == sha256_file(run_dir / "run_identity.json")
            ):
                return {
                    "status": "PASS",
                    "phase": phase,
                    "evidence_relpath": "benchmark_evidence.json",
                    "scorecard_status": ((evidence.get("scorecard") or {}).get("status")),
                }
            invocation_error = "benchmark wrapper returned success but its evidence is incomplete or binds to a different run"
        except Exception as exc:
            invocation_error = f"benchmark wrapper returned success but evidence could not be validated: {exc}"
    elif invocation_error is None:
        tail = (stderr or stdout)[-1600:].strip()
        invocation_error = f"benchmark wrapper exited with {returncode}: {tail or 'no diagnostic output'}"

    _benchmark_fallback_evidence(run_dir, phase=phase, error=invocation_error)
    return {
        "status": "BENCHMARK_EVIDENCE_UNAVAILABLE",
        "phase": phase,
        "evidence_relpath": "benchmark_evidence.json",
        "error": invocation_error,
    }


def benchmark_refresh_enabled(args: argparse.Namespace) -> bool:
    """CLI defaults to automatic refresh; direct fixture calls stay isolated."""

    return getattr(args, "benchmark_mode", "off") == "auto"


def require_state(run_dir: Path, *allowed: str) -> dict[str, Any]:
    state = read_json(run_dir / "state.json")
    if state.get("state") not in allowed:
        raise DeliveryError(
            f"run state is {state.get('state')!r}, expected one of {', '.join(allowed)}"
        )
    return state


def identity_errors(run_dir: Path) -> list[str]:
    identity_path = run_dir / "run_identity.json"
    if not identity_path.is_file():
        return ["run_identity.json is missing"]
    identity = read_json(identity_path)
    episode_id = identity.get("episode_id")
    run_id = identity.get("run_id")
    errors: list[str] = []
    if run_dir.name != run_id:
        errors.append("run directory basename does not match run_identity.run_id")
    if run_dir.parent.name != episode_id:
        errors.append("run directory parent does not match run_identity.episode_id")
    expected_rel = f"main/runs/{episode_id}/{run_id}"
    if identity.get("run_dir_rel") != expected_rel:
        errors.append("run_identity.run_dir_rel is inconsistent")
    if identity.get("contract_version") != CONTRACT_VERSION:
        errors.append("contract_version is inconsistent")
    for filename in (
        "input_manifest.json",
        "plan.json",
        "processing_manifest.json",
        "analysis_manifest.json",
        "analysis_reuse_manifest.json",
        "all_candidates.json",
        "calibration_report.json",
        "prediction_manifest.json",
        "human_approved.edl.json",
        "machine_assisted_draft.edl.json",
        "music_manifest.json",
        "qc_report.json",
        "final_listening_decision.json",
    ):
        path = run_dir / filename
        if not path.is_file():
            continue
        try:
            document = read_json(path)
        except DeliveryError as exc:
            errors.append(str(exc))
            continue
        if "episode_id" in document and document["episode_id"] != episode_id:
            errors.append(f"{filename} has another episode_id")
        if "run_id" in document and document["run_id"] != run_id:
            errors.append(f"{filename} has another run_id")
        if document.get("run_identity_sha256") and document["run_identity_sha256"] != sha256_file(identity_path):
            errors.append(f"{filename} has another run_identity SHA")
    return errors


def require_identity(run_dir: Path) -> dict[str, Any]:
    errors = identity_errors(run_dir)
    if errors:
        raise DeliveryError("BLOCKED: RUN_IDENTITY_MISMATCH: " + "; ".join(errors))
    return read_json(run_dir / "run_identity.json")


def run_relative_path(run_dir: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise DeliveryError(f"{label} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DeliveryError(f"{label} must be relative to the current run")
    target = run_dir / candidate
    try:
        target.absolute().relative_to(run_dir.absolute())
    except ValueError as exc:
        raise DeliveryError(f"{label} escapes the run") from exc
    return target


def transition_qc_required(run_dir: Path, state: str | None = None) -> bool:
    """Normal post-render runs require objective transition-priority evidence.

    The narrow whole-episode v12 promotion path is preserved as historical
    approval evidence and is intentionally not retrofitted in place.  Pending
    review runs have no EDL/render yet, so they must not be reported as missing
    transition QC merely because an operator ran ``status`` or ``verify``.
    """

    if state is None:
        state = str(read_json(run_dir / "state.json").get("state") or "")
    return state in {
        "MACHINE_ASSISTED_DRAFT_RENDERED",
        "FINAL_QC_REQUIRED",
        "DELIVERY_DECISION_RECORDED",
    } and not (run_dir / "human_approval_scope.json").is_file()


def load_transition_qc_module() -> Any:
    """Load the local post-render diagnostic without relying on package layout."""

    if not TRANSITION_QC_SCRIPT.is_file():
        raise DeliveryError("transition QC module is missing")
    spec = importlib.util.spec_from_file_location("delivery_transition_qc", TRANSITION_QC_SCRIPT)
    if spec is None or spec.loader is None:
        raise DeliveryError("transition QC module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "generate_transition_qc", None)):
        raise DeliveryError("transition QC module has no generate_transition_qc entrypoint")
    return module


def transition_qc_report_index(run_dir: Path, *, required: bool) -> tuple[dict[str, Any], list[str]]:
    """Validate and compactly index both rendered-transition priority reports."""

    index: dict[str, Any] = {
        "required": required,
        "status": "NOT_REQUIRED" if not required else "PASS",
        "reports": {},
    }
    if not required:
        return index, []

    errors: list[str] = []
    identity = require_identity(run_dir)
    identity_sha = sha256_file(run_dir / "run_identity.json")
    for variant in ("human_approved", "machine_assisted_draft"):
        report_path = run_dir / f"render_{variant}/transition_qc.json"
        if not report_path.is_file():
            errors.append(f"missing {variant} transition QC report")
            continue
        try:
            report = read_json(report_path)
        except DeliveryError as exc:
            errors.append(str(exc))
            continue
        if report.get("schema_version") != "rendered-transition-qc-v1":
            errors.append(f"{variant} transition QC schema mismatch")
        if report.get("status") != "OBJECTIVE_ANOMALY_RANKING_SUBJECTIVE_LISTENING_REQUIRED":
            errors.append(f"{variant} transition QC status mismatch")
        if report.get("episode_id") != identity["episode_id"] or report.get("run_id") != identity["run_id"]:
            errors.append(f"{variant} transition QC run identity mismatch")
        if report.get("variant") != variant or report.get("run_identity_sha256") != identity_sha:
            errors.append(f"{variant} transition QC variant or identity SHA mismatch")
        source = report.get("source_evidence") or {}
        edl_path = run_dir / f"{variant}.edl.json"
        render_path = run_dir / f"render_{variant}/render_manifest.json"
        if source.get("edl_relpath") != edl_path.name or not edl_path.is_file() or source.get("edl_sha256") != sha256_file(edl_path):
            errors.append(f"{variant} transition QC EDL reference mismatch")
        expected_render_relpath = relative_to_run(run_dir, render_path)
        if (
            source.get("render_manifest_relpath") != expected_render_relpath
            or not render_path.is_file()
            or source.get("render_manifest_sha256") != sha256_file(render_path)
        ):
            errors.append(f"{variant} transition QC render manifest reference mismatch")
        try:
            analysis_audio = run_relative_path(
                run_dir, source.get("analysis_audio_relpath"), f"{variant} transition QC analysis audio"
            )
        except DeliveryError as exc:
            errors.append(str(exc))
        else:
            if not analysis_audio.is_file() or source.get("analysis_audio_sha256") != sha256_file(analysis_audio):
                errors.append(f"{variant} transition QC analysis audio SHA mismatch")
        if (report.get("timeline_validation") or {}).get("mapping_status") != "PASS":
            errors.append(f"{variant} transition QC timeline mapping did not pass")
        transitions = report.get("transitions")
        ranked_ids = report.get("ranked_transition_ids")
        if not isinstance(transitions, list) or not isinstance(ranked_ids, list):
            errors.append(f"{variant} transition QC ranking structure is invalid")
        elif report.get("transition_count") != len(transitions) or ranked_ids != [
            item.get("transition_id") for item in transitions if isinstance(item, dict)
        ]:
            errors.append(f"{variant} transition QC ranking is inconsistent")
        index["reports"][variant] = {
            "relpath": relative_to_run(run_dir, report_path),
            "sha256": sha256_file(report_path),
            "transition_count": report.get("transition_count"),
            "priority_relisten_count": report.get("priority_relisten_count"),
            "ranked_transition_ids": report.get("ranked_transition_ids"),
        }
    if errors:
        index["status"] = "FAIL"
        index["errors"] = errors
    return index, errors


def generate_transition_qc_reports(run_dir: Path) -> dict[str, Any]:
    """Create both reports only after rendering, before automatic/final QC."""

    state = str(read_json(run_dir / "state.json").get("state") or "")
    if not transition_qc_required(run_dir, state):
        raise DeliveryError("transition QC is only available for normal post-render delivery runs")
    try:
        module = load_transition_qc_module()
        for variant in ("human_approved", "machine_assisted_draft"):
            module.generate_transition_qc(run_dir, variant)
    except Exception as exc:
        raise DeliveryError(f"transition QC generation failed: {exc}") from exc
    index, errors = transition_qc_report_index(run_dir, required=True)
    if errors:
        raise DeliveryError("transition QC validation failed: " + "; ".join(errors))
    return index


def delivery_artifact_errors(run_dir: Path) -> list[str]:
    """Check the hash/reference chain used before reporting a delivery as usable."""

    errors = identity_errors(run_dir)
    if errors:
        return errors
    try:
        identity = read_json(run_dir / "run_identity.json")
        state = str(read_json(run_dir / "state.json").get("state") or "")
        input_manifest = read_json(run_dir / "input_manifest.json")
        plan = read_json(run_dir / "plan.json")
        for track in input_manifest.get("tracks") or []:
            path = run_relative_path(run_dir, track.get("input_relpath"), "input_relpath")
            if not path.is_file():
                errors.append(f"input link missing: {track.get('track_id')}")
            elif sha256_file(path) != track.get("audio_sha256"):
                errors.append(f"input SHA mismatch: {track.get('track_id')}")
        music = run_relative_path(run_dir, identity.get("music_asset_relpath"), "music_asset_relpath")
        if not music.is_file() or sha256_file(music) != MUSIC_SHA256:
            errors.append("fixed music asset is missing or SHA mismatched")
        candidate_strategy = plan.get("candidate_strategy") or {}
        rules_relpath = candidate_strategy.get("rules_relpath")
        if not rules_relpath and identity.get("candidate_rules_sha256"):
            errors.append("frozen candidate rules are missing from plan")
        elif rules_relpath:
            try:
                rules_path = run_relative_path(run_dir, rules_relpath, "candidate rules path")
            except DeliveryError as exc:
                errors.append(str(exc))
            else:
                expected_rules_sha = candidate_strategy.get("rules_sha256")
                if not rules_path.is_file() or sha256_file(rules_path) != expected_rules_sha:
                    errors.append("frozen candidate rules SHA mismatch")
                if identity.get("candidate_rules_sha256") != expected_rules_sha:
                    errors.append("run identity candidate rules SHA mismatch")
        editing_policy = plan.get("editing_policy") or {}
        if editing_policy:
            try:
                policy_path = run_relative_path(run_dir, editing_policy.get("relpath"), "editing policy path")
            except DeliveryError as exc:
                errors.append(str(exc))
            else:
                expected_policy_sha = editing_policy.get("sha256")
                if not policy_path.is_file() or sha256_file(policy_path) != expected_policy_sha:
                    errors.append("frozen editing policy SHA mismatch")
                elif identity.get("editing_policy_sha256") != expected_policy_sha:
                    errors.append("run identity editing policy SHA mismatch")
                else:
                    try:
                        loaded_policy = load_editing_policy(policy_path)
                    except ValueError as exc:
                        errors.append(f"invalid frozen editing policy: {exc}")
                    else:
                        if plan.get("autocut_policy") != loaded_policy.get("autocut_policy"):
                            errors.append("run plan autocut policy does not match frozen editing policy")
        integration = plan.get("integration_governance") or {}
        integration_path_value = integration.get("relpath")
        if not integration_path_value:
            errors.append("integration governance registry is missing from plan")
        else:
            try:
                integration_path = run_relative_path(
                    run_dir, integration_path_value, "integration governance registry"
                )
            except DeliveryError as exc:
                errors.append(str(exc))
            else:
                if not integration_path.is_file() or sha256_file(integration_path) != integration.get("sha256"):
                    errors.append("integration governance registry SHA mismatch")
                else:
                    try:
                        _, loaded_integration = load_integration_registry(integration_path)
                    except (OSError, ValueError) as exc:
                        errors.append(f"invalid frozen integration governance registry: {exc}")
                    else:
                        if loaded_integration.get("registry_id") != integration.get("registry_id"):
                            errors.append("integration governance registry ID mismatch")
                        if identity.get("integration_registry_sha256") != integration.get("sha256"):
                            errors.append("run identity integration governance SHA mismatch")
        if plan.get("denoise", {}).get("backend") == "deepfilternet":
            processing_path = run_dir / "processing_manifest.json"
            denoise_manifest_path = run_dir / "denoise/denoise_manifest.json"
            if not processing_path.is_file():
                errors.append("DeepFilterNet processing manifest is missing")
            elif not denoise_manifest_path.is_file():
                errors.append("DeepFilterNet denoise manifest is missing")
            else:
                processing = read_json(processing_path)
                if processing.get("denoise_manifest_sha256") != sha256_file(denoise_manifest_path):
                    errors.append("DeepFilterNet processing manifest SHA mismatch")
                for track in processing.get("tracks") or []:
                    try:
                        path = run_relative_path(run_dir, track.get("input_relpath"), "processed input_relpath")
                    except DeliveryError as exc:
                        errors.append(str(exc))
                        continue
                    if not path.is_file() or sha256_file(path) != track.get("audio_sha256"):
                        errors.append(f"DeepFilterNet output SHA mismatch: {track.get('track_id')}")

        edls: dict[str, dict[str, Any]] = {}
        for variant in ("human_approved", "machine_assisted_draft"):
            edl_path = run_dir / f"{variant}.edl.json"
            if not edl_path.is_file():
                errors.append(f"missing {variant} EDL")
                continue
            edl = read_json(edl_path)
            edls[variant] = edl
            if edl.get("variant") != variant:
                errors.append(f"{variant} EDL variant mismatch")
            render_path = run_dir / f"render_{variant}/render_manifest.json"
            if not render_path.is_file():
                errors.append(f"missing {variant} render manifest")
                continue
            render = read_json(render_path)
            if render.get("source_edl_relpath") != f"{variant}.edl.json":
                errors.append(f"{variant} render points to another EDL")
            if render.get("source_edl_sha256") != sha256_file(edl_path):
                errors.append(f"{variant} render EDL SHA mismatch")
            outputs = render.get("outputs") or {}
            for key, hash_key in (("master_wav", "master_wav_sha256"), ("master_mp3", "master_mp3_sha256")):
                try:
                    output = run_relative_path(run_dir, outputs.get(key), f"{variant}.{key}")
                except DeliveryError as exc:
                    errors.append(str(exc))
                    continue
                if not output.is_file():
                    errors.append(f"missing {variant} {key}")
                elif sha256_file(output) != outputs.get(hash_key):
                    errors.append(f"{variant} {key} SHA mismatch")

        if plan.get("music", {}).get("music_template_id") == "reference-linear-v1" and len(edls) == 2:
            music_manifest_path = run_dir / "music_manifest.json"
            if not music_manifest_path.is_file():
                errors.append("missing normal-render music_manifest.json")
            else:
                music_manifest = read_json(music_manifest_path)
                timing = resolve_run_music_timing(run_dir, plan)
                if music_manifest.get("music_template_id") != "reference-linear-v1":
                    errors.append("music manifest template mismatch")
                if music_manifest.get("asset_sha256") != MUSIC_SHA256:
                    errors.append("music manifest asset SHA mismatch")
                if music_manifest.get("timing") != timing or music_manifest.get("timing_sha256") != sha256_bytes(timing):
                    errors.append("music manifest timing mismatch")
                for variant in ("human_approved", "machine_assisted_draft"):
                    variant_entry = (music_manifest.get("variants") or {}).get(variant) or {}
                    render_path = run_dir / f"render_{variant}/render_manifest.json"
                    if variant_entry.get("render_manifest_relpath") != relative_to_run(run_dir, render_path):
                        errors.append(f"music manifest {variant} render path mismatch")
                    elif variant_entry.get("render_manifest_sha256") != sha256_file(render_path):
                        errors.append(f"music manifest {variant} render SHA mismatch")

        require_transition_qc = transition_qc_required(run_dir, state)
        transition_index, transition_errors = transition_qc_report_index(
            run_dir, required=require_transition_qc
        )
        errors.extend(transition_errors)
        qc_report_path = run_dir / "qc_report.json"
        if require_transition_qc and qc_report_path.is_file():
            recorded_index = read_json(qc_report_path).get("transition_qc") or {}
            if recorded_index.get("required") is not True or recorded_index.get("status") != "PASS":
                errors.append("QC report does not record passing transition QC")
            for variant in ("human_approved", "machine_assisted_draft"):
                expected = (transition_index.get("reports") or {}).get(variant) or {}
                recorded = (recorded_index.get("reports") or {}).get(variant) or {}
                if recorded.get("relpath") != expected.get("relpath"):
                    errors.append(f"QC report {variant} transition QC path mismatch")
                if recorded.get("sha256") != expected.get("sha256"):
                    errors.append(f"QC report {variant} transition QC SHA mismatch")

        scope_path = run_dir / "human_approval_scope.json"
        if scope_path.is_file():
            scope_sha = sha256_file(scope_path)
            for variant, edl in edls.items():
                scope = edl.get("whole_episode_approval_scope") or {}
                if scope.get("relpath") != "human_approval_scope.json" or scope.get("sha256") != scope_sha:
                    errors.append(f"{variant} whole-episode approval scope mismatch")
            final = run_dir / "final_listening_decision.json"
            if not final.is_file():
                errors.append("whole-episode scope has no final decision")
            else:
                decision = read_json(final)
                if decision.get("approval_scope_sha256") != scope_sha:
                    errors.append("final decision approval scope SHA mismatch")
        feedback = run_dir / "feedback_bundle.json"
        final = run_dir / "final_listening_decision.json"
        if feedback.is_file() and final.is_file():
            feedback_document = read_json(feedback)
            if feedback_document.get("final_decision_sha256") != sha256_file(final):
                errors.append("feedback bundle final decision SHA mismatch")
            decision_relpath = feedback_document.get("candidate_feedback_source_relpath")
            decision_sha = feedback_document.get("candidate_feedback_source_sha256")
            if decision_relpath or decision_sha:
                try:
                    decision_path = run_relative_path(run_dir, decision_relpath, "candidate feedback source")
                except DeliveryError as exc:
                    errors.append(str(exc))
                else:
                    if not decision_path.is_file() or sha256_file(decision_path) != decision_sha:
                        errors.append("feedback bundle candidate feedback source SHA mismatch")
        if final.is_file() and (run_dir / "qc_report.json").is_file():
            qc_sha = read_json(final).get("qc_report_sha256")
            if qc_sha and qc_sha != sha256_file(run_dir / "qc_report.json"):
                errors.append("final decision QC report SHA mismatch")
    except DeliveryError as exc:
        errors.append(str(exc))
    return errors


def _try_v2_route(run_dir: Path, stage: str, command: list[str]) -> tuple[bool, dict[str, Any] | None]:
    """尝试把 command 路由到 v2 executor · 返回 (routed, provenance_dict_or_None)。

    仅当以下全部满足才走 v2：
      - USE_V2_EXECUTOR 打开
      - command[0] 是 python3/python 解释器
      - command[1] 是 .py 脚本路径 · 且能 reverse-lookup 到 tool_name
      - tools.json 里该 tool 有 v2 adapter
      - 剩余 argv 能解析成 --flag value 对（不含 positional / 复杂 nargs=+）

    否则 return (False, None) · caller fallback subprocess.run。
    """
    if not USE_V2_EXECUTOR:
        return False, None
    if not command or len(command) < 2:
        return False, None
    if not str(command[0]).endswith(("python", "python3")):
        return False, None
    script_path = Path(command[1])
    if not str(script_path).endswith(".py"):
        return False, None
    adapters = _load_v2_adapters()
    if not adapters:
        return False, None
    tool_name = _tool_name_for_script(script_path)
    if not tool_name or tool_name not in adapters:
        return False, None
    adapter = adapters[tool_name]

    # 简易解析剩余 argv 为 inputs dict · 只支持 --flag value 与 --flag=value · 不处理 positional / store_true
    argv = list(command[2:])
    inputs: dict[str, Any] = {}
    i = 0
    while i < len(argv):
        tok = str(argv[i])
        if tok.startswith("--"):
            if "=" in tok:
                key, val = tok[2:].split("=", 1)
                inputs[key.replace("-", "_")] = val
                i += 1
            elif i + 1 < len(argv) and not str(argv[i + 1]).startswith("--"):
                inputs[tok[2:].replace("-", "_")] = str(argv[i + 1])
                i += 2
            else:
                # boolean flag · store_true
                inputs[tok[2:].replace("-", "_")] = True
                i += 1
        else:
            # positional or unsupported form · give up · fallback v1
            return False, None

    try:
        # write-tool 需要 policy · 从 adapter contract 取默认
        wpid = adapter.contract.get("write_policy", {}).get("policy_id")
        wsh = None
        if wpid:
            # scope_hash · 走 run_dir 计算
            v2_dir = PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters"
            if str(v2_dir) not in sys.path:
                sys.path.insert(0, str(v2_dir))
            import _adapter_base as _ab  # noqa: E402
            wsh = _ab.compute_writes_scope_hash([str(run_dir)])
        prov = adapter.invoke(
            inputs, run_dir,
            writes_policy_id=wpid if adapter.contract["reads_only"] is False else None,
            writes_scope_hash=wsh if adapter.contract["reads_only"] is False else None,
        )
        # provenance side-car · logs/<stage>.v2.provenance.json
        prov_dict = {
            "adapter_id": prov.adapter_id,
            "tool_name": prov.tool_name,
            "exit_code": prov.exit_code,
            "duration_seconds": prov.duration_seconds,
            "wraps_script_sha256": prov.wraps_script_sha256,
            "error": prov.error,
            "started_at_utc": prov.started_at_utc,
            "finished_at_utc": prov.finished_at_utc,
        }
        logs = run_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        write_json(logs / f"{stage}.v2.provenance.json", prov_dict)
        return True, prov_dict
    except Exception as exc:  # noqa: BLE001
        print(f"[v2 bridge] {stage} routing failed ({exc}); falling back to v1 subprocess", file=sys.stderr)
        return False, None


def stage_command(run_dir: Path, stage: str, command: list[str]) -> None:
    """Run a local tool and preserve its stdout/stderr for diagnosis.

    § v2 bridge (2026-08-19)：若 USE_V2_EXECUTOR 打开且 command 是可识别的 Python tool 调用，
    优先走 v2 executor（拿 provenance + SHA drift 检查）；不满足条件时透明 fallback 到 subprocess。
    """
    # v2 opt-in 尝试
    routed, prov = _try_v2_route(run_dir, stage, command)
    if routed:
        # v2 路径成功 · 仍写一份 stage command log 保持向后兼容
        logs = run_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        write_json(logs / f"{stage}.command.json", {
            "schema_version": "delivery-stage-command-v1",
            "stage": stage,
            "started_at": (prov or {}).get("started_at_utc"),
            "finished_at": (prov or {}).get("finished_at_utc"),
            "returncode": (prov or {}).get("exit_code", 0),
            "command": command,
            "stdout": "",
            "stderr": "",
            "executor": "v2",
            "v2_provenance_relpath": f"logs/{stage}.v2.provenance.json",
        })
        if (prov or {}).get("error"):
            raise DeliveryError(f"{stage} v2 failed (see logs/{stage}.v2.provenance.json): {prov['error']}")
        return

    # v1 subprocess fallback
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    payload = {
        "schema_version": "delivery-stage-command-v1",
        "stage": stage,
        "started_at": started,
        "finished_at": utc_now(),
        "returncode": completed.returncode,
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    write_json(logs / f"{stage}.command.json", payload)
    if completed.returncode:
        tail = (completed.stderr or completed.stdout)[-1200:].strip()
        raise DeliveryError(f"{stage} failed (see logs/{stage}.command.json): {tail}")


def normalized_track_sources(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise DeliveryError(f"input directory does not exist: {input_dir}")
    tracks = sorted(
        [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".wav"],
        key=lambda item: item.name.casefold(),
    )
    if not tracks:
        raise DeliveryError("no WAV files were found in the input directory")
    return tracks


def make_base_run(
    *,
    episode_id: str,
    run_id: str,
    source_tracks: list[tuple[str, str, Path]],
    purpose: str,
    music_template_id: str,
    source_audio_mode: str = "deepfilternet",
    candidate_rules_source: Path | None = None,
    editing_policy_source: Path | None = None,
    review_budget: int | None = None,
    experience_snapshot_source: Path | None = None,
    event_history_runs: Iterable[Path] | None = None,
    integration_registry_source: Path | None = None,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    """Create only a fresh run plus immutable links/manifests; never mutate inputs."""

    episode_id = safe_identifier(episode_id, "episode_id")
    run_id = safe_identifier(run_id, "run_id")
    if not run_id.startswith(f"{episode_id}-"):
        raise DeliveryError("run_id must start with '<episode_id>-' to prevent cross-episode writes")
    run_dir = RUNS_ROOT / episode_id / run_id
    if run_dir.exists() or run_dir.is_symlink():
        raise DeliveryError(f"refusing to overwrite existing run: {run_dir}")
    if not source_tracks:
        raise DeliveryError("at least one source track is required")
    if source_audio_mode not in {"deepfilternet", "frozen_approved_source"}:
        raise DeliveryError(f"unsupported source audio mode: {source_audio_mode}")
    if review_budget is not None and review_budget < 1:
        raise DeliveryError("review_budget must be at least 1 when it is set")
    event_history = validate_event_history_runs(event_history_runs, episode_id=episode_id)
    candidate_rules_source = (candidate_rules_source or DEFAULT_CANDIDATE_RULES).expanduser().resolve()
    if not candidate_rules_source.is_file():
        raise DeliveryError(f"candidate rules are missing: {candidate_rules_source}")
    candidate_rules = read_json(candidate_rules_source)
    if candidate_rules.get("policy") != "review_only_no_automatic_accept":
        raise DeliveryError("candidate rules must keep review_only_no_automatic_accept")
    if not candidate_rules.get("rules_version"):
        raise DeliveryError("candidate rules must declare rules_version")
    editing_policy_source = (editing_policy_source or DEFAULT_EDITING_POLICY).expanduser().resolve()
    if not editing_policy_source.is_file():
        raise DeliveryError(f"editing policy is missing: {editing_policy_source}")
    try:
        editing_policy = load_editing_policy(editing_policy_source)
    except ValueError as exc:
        raise DeliveryError(str(exc)) from exc
    integration_registry_source = (
        integration_registry_source or DEFAULT_INTEGRATION_REGISTRY
    ).expanduser().resolve()
    try:
        integration_registry_path, integration_registry = load_integration_registry(
            integration_registry_source
        )
    except (OSError, ValueError) as exc:
        raise DeliveryError(f"integration governance registry is invalid: {exc}") from exc
    if experience_snapshot_source is not None:
        experience_snapshot_source = experience_snapshot_source.expanduser().resolve()
        if experience_snapshot_source.is_dir():
            experience_snapshot_source = experience_snapshot_source / "snapshot_manifest.json"
        if not experience_snapshot_source.is_file():
            raise DeliveryError(f"experience snapshot manifest is missing: {experience_snapshot_source}")
        snapshot_doc = read_json(experience_snapshot_source)
        if snapshot_doc.get("schema_version") != "preference-snapshot-manifest-v1":
            raise DeliveryError("experience snapshot has an unsupported schema")

    music = known_music()
    music_template = music_template_definition(music_template_id)
    if music_template_id == "reference-linear-v1":
        validate_reference_linear_timing(music_template)

    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        inputs_dir = run_dir / "inputs"
        tracks: list[dict[str, Any]] = []
        expected_timeline: tuple[int, int] | None = None
        for index, (track_id, label, source) in enumerate(source_tracks, 1):
            if not source.is_file():
                raise DeliveryError(f"source track is missing: {source}")
            track_id = safe_identifier(track_id, "track_id")
            info = wav_info(source)
            if int(info["channels"]) != 1:
                raise DeliveryError(f"{source.name} is not a mono WAV")
            current_timeline = (int(info["sample_rate_hz"]), int(info["frame_count"]))
            if expected_timeline is None:
                expected_timeline = current_timeline
            elif current_timeline != expected_timeline:
                raise DeliveryError("input WAV files are not on one common sample timeline")
            suffix = source.suffix.lower() or ".wav"
            link = inputs_dir / f"{track_id}_{source.stem}{suffix}"
            relative_symlink(link, source)
            tracks.append(
                {
                    "track_id": track_id,
                    "label": label,
                    "input_relpath": relative_to_run(run_dir, link),
                    "source_filename": source.name,
                    "audio_sha256": sha256_file(source),
                    "sample_rate_hz": int(info["sample_rate_hz"]),
                    "frame_count": int(info["frame_count"]),
                    "channels": int(info["channels"]),
                    "bits_per_sample": int(info["bits_per_sample"]),
                    "duration_seconds": round(float(info["duration_seconds"]), 6),
                }
            )
        assert expected_timeline is not None

        frozen = run_dir / "frozen"
        frozen.mkdir()
        shutil.copy2(PREFERENCE_SOURCE, frozen / "editing_preference_profile.md")
        shutil.copy2(candidate_rules_source, frozen / "candidate_rules.json")
        shutil.copy2(editing_policy_source, frozen / "editing_policy.json")
        frozen_integration_registry = frozen / "integration_governance.json"
        shutil.copy2(integration_registry_path, frozen_integration_registry)
        frozen_integration_registry_sha256 = sha256_file(frozen_integration_registry)
        experience_snapshot_relpath = None
        experience_snapshot_sha256 = None
        experience_snapshot_id = None
        if experience_snapshot_source is not None:
            snapshot_dir = experience_snapshot_source.parent
            frozen_snapshot = frozen / "experience_snapshot"
            frozen_snapshot.mkdir()
            for name in ("snapshot_manifest.json", "aggregated.json", "preferences.md", "preferences_for_agent.md", "rules_suggestions.json"):
                source_file = snapshot_dir / name
                if source_file.is_file():
                    shutil.copy2(source_file, frozen_snapshot / name)
            experience_snapshot_relpath = "frozen/experience_snapshot/snapshot_manifest.json"
            experience_snapshot_sha256 = sha256_file(frozen_snapshot / "snapshot_manifest.json")
            experience_snapshot_id = snapshot_doc.get("snapshot_id")
        music_link = run_dir / "assets" / "fixed_intro_outro_music.mp3"
        relative_symlink(music_link, MUSIC_SOURCE)

        identity = {
            "schema_version": "run-identity-v1",
            "episode_id": episode_id,
            "run_id": run_id,
            "contract_version": CONTRACT_VERSION,
            "run_dir_rel": f"main/runs/{episode_id}/{run_id}",
            "created_at": utc_now(),
            "purpose": purpose,
            "preference_profile_id": PREFERENCE_ID,
            "preference_profile_relpath": "frozen/editing_preference_profile.md",
            "preference_profile_sha256": sha256_file(frozen / "editing_preference_profile.md"),
            "candidate_rules_relpath": "frozen/candidate_rules.json",
            "candidate_rules_sha256": sha256_file(frozen / "candidate_rules.json"),
            "candidate_rules_version": candidate_rules["rules_version"],
            "editing_policy_relpath": "frozen/editing_policy.json",
            "editing_policy_sha256": sha256_file(frozen / "editing_policy.json"),
            "editing_policy_id": editing_policy["policy_id"],
            "editing_policy_version": editing_policy.get("version"),
            "integration_registry_id": integration_registry["registry_id"],
            "integration_registry_relpath": "frozen/integration_governance.json",
            "integration_registry_sha256": frozen_integration_registry_sha256,
            "music_asset_relpath": relative_to_run(run_dir, music_link),
            "music_asset_sha256": music["source_sha256"],
            "music_template_id": music_template_id,
            "music_template_sha256": sha256_bytes(music_template),
            "experience_snapshot_id": experience_snapshot_id,
            "experience_snapshot_relpath": experience_snapshot_relpath,
            "experience_snapshot_sha256": experience_snapshot_sha256,
        }
        write_json(run_dir / "run_identity.json", identity)
        identity_sha = sha256_file(run_dir / "run_identity.json")
        input_manifest = {
            "schema_version": "delivery-input-manifest-v1",
            "episode_id": episode_id,
            "run_id": run_id,
            "run_identity_sha256": identity_sha,
            "track_count": len(tracks),
            "sample_rate_hz": expected_timeline[0],
            "frame_count": expected_timeline[1],
            "tracks": tracks,
            "source_access": "relative symlinks within this run; raw sources are read only",
        }
        write_json(run_dir / "input_manifest.json", input_manifest)
        plan = {
            "schema_version": "delivery-plan-v1",
            "episode_id": episode_id,
            "run_id": run_id,
            "run_identity_sha256": identity_sha,
            "contract_version": CONTRACT_VERSION,
            "input_manifest_relpath": "input_manifest.json",
            "preference_profile": {
                "id": PREFERENCE_ID,
                "sha256": identity["preference_profile_sha256"],
                "scope": "candidate nomination, ranking and audition render parameters only",
            },
            "experience_learning": {
                "snapshot_id": experience_snapshot_id,
                "snapshot_relpath": experience_snapshot_relpath,
                "snapshot_sha256": experience_snapshot_sha256,
                "scope": "review priority, calibration ordering and machine suggestions only; no human decision, EDL, autocut or rendering",
                "required": experience_snapshot_source is not None,
            },
            "event_routing": {
                "schema_version": "review-event-routes-v1",
                "mode": "metadata_only",
                "history_runs": event_history,
                "scope": "future review-package sidecar only; never creates current human decisions, EDLs or autocut permission",
            },
            "candidate_strategy": {
                "rules_relpath": "frozen/candidate_rules.json",
                "rules_sha256": identity["candidate_rules_sha256"],
                "rules_version": candidate_rules["rules_version"],
                "scope": "candidate nomination only; rules cannot create human labels or automatic policy approval",
            },
            "editing_policy": {
                "relpath": "frozen/editing_policy.json",
                "sha256": identity["editing_policy_sha256"],
                "id": editing_policy["policy_id"],
                "version": editing_policy.get("version"),
                "status": editing_policy.get("status"),
                "scope": "conservative candidate preserve/review routing only; never creates a human decision, EDL action or automatic semantic deletion",
            },
            "integration_governance": {
                "relpath": "frozen/integration_governance.json",
                "sha256": frozen_integration_registry_sha256,
                "registry_id": integration_registry["registry_id"],
                "mainline_capabilities": mainline_capabilities(integration_registry),
                "mainline_exclusions": integration_registry.get("mainline_exclusions", []),
                "scope": "component adoption gate only; semantic edit gate and publish gate remain separate",
            },
            "review_strategy": {
                "max_human_review_items": review_budget,
                "budget_behavior": "fail_closed_if_mandatory_high_risk_exceeds_budget; otherwise defer insufficiently sampled low-risk strata to preserve",
                "human_review_mode": "offline_packet_or_local_review_page",
            },
            "autocut_policy": dict(editing_policy["autocut_policy"]),
            "music": {
                **music,
                "music_template_id": music_template_id,
                "timing": music_template,
                "timing_sha256": sha256_bytes(music_template),
                "asset_relpath": identity["music_asset_relpath"],
                "release_parameter_status": "AUDITION_DEFAULTS_ONLY",
            },
            "mixing": {
                "mode": "automix_v1",
                "fallback_mode": "direct_mix",
                "ducking_profile": "sidechain_v1",
                "source_track_gate_execution": "before_mix",
                "scope": "mixing/electrical level automation only; never changes semantic EDL actions",
            },
            "denoise": (
                {
                    "backend": "deepfilternet",
                    "status": "PENDING",
                    "scope": "mandatory run-local analysis, review previews and render input; raw source links remain immutable",
                    "processing_manifest_relpath": "processing_manifest.json",
                }
                if source_audio_mode == "deepfilternet"
                else {
                    "backend": "frozen_approved_source",
                    "status": "NOT_APPLICABLE__FROZEN_APPROVED_AUDIO",
                    "scope": "special historical promotion only; preserves an already human-approved frozen master without reprocessing it",
                    "processing_manifest_relpath": None,
                }
            ),
            "candidate_coverage": {
                "connected": [
                    "filler_hesitation",
                    "global_long_pause",
                    "self_correction",
                    "transient_events"
                ],
                "not_connected": [
                    "semantic_duplicate",
                    "off_topic",
                    "crosstalk_attribution"
                ],
                "safety_behavior": "owner-attested families may enter candidate generation, but self_correction/cough_like remain human_review_required; unconnected families remain reported gaps",
            },
            "random_seed": f"{episode_id}:{run_id}:calibration-v1",
            "requirements_checkpoint_relpath": "requirements_checkpoint.json",
        }
        write_json(run_dir / "plan.json", plan)
        write_json(
            run_dir / "requirements_checkpoint.json",
            {
                "schema_version": "delivery-requirements-checkpoint-v1",
                "episode_id": episode_id,
                "run_id": run_id,
                "recorded_at": utc_now(),
                "purpose": "resume-safe hard requirements; write before handoff or context compression",
                "music": {
                    "template_id": music_template_id,
                    "timing": music_template,
                    "timing_sha256": sha256_bytes(music_template),
                },
                "review": {
                    "human_decision_required": True,
                    "feedback_field": "feedback",
                    "feedback_max_chars": 500,
                    "draft_is_not_final_decision": True,
                },
                "development_benchmark": {
                    "status": "ACTIVE_EVIDENCE_LOOP_NOT_AUTOCUT_POLICY",
                    "missing_evidence_rule": "NOT_MEASURED is not zero problems, a pass, or permission to reduce human review further",
                },
                "experience_learning": {
                    "status": "REVIEW_PRIORITY_ONLY",
                    "snapshot_id": experience_snapshot_id,
                    "snapshot_sha256": experience_snapshot_sha256,
                    "never_creates_human_decision": True,
                    "never_creates_autocut_permission": True,
                },
                "editing_policy": {
                    "id": editing_policy["policy_id"],
                    "sha256": identity["editing_policy_sha256"],
                    "status": editing_policy.get("status"),
                    "autocut_policy": dict(editing_policy["autocut_policy"]),
                    "scope": "active preserve/review guards only; any machine draft still requires current-run calibration and final human listening",
                },
                "integration_governance": {
                    "registry_id": integration_registry["registry_id"],
                    "registry_sha256": frozen_integration_registry_sha256,
                    "owner_attested_adoption_is_not_semantic_approval": True,
                    "independent_verification_is_post_integration_and_reopenable": True,
                },
                "mixing": {
                    "mode": "automix_v1",
                    "ducking_profile": "sidechain_v1",
                    "source_track_gate_execution": "before_mix",
                    "semantic_decision": False,
                },
            },
        )
        create_state(run_dir, episode_id, run_id, "RECEIVED", "fresh run created")
        return run_dir, identity, tracks
    except Exception:
        # Preserve partial evidence for diagnosis, but never overwrite/reuse it as a clean run.
        if (run_dir / "state.json").exists():
            transition(run_dir, "FAILED", "base-run setup failed")
        raise


def active_audio_tracks(run_dir: Path) -> list[dict[str, Any]]:
    """Return current run's mandatory derived tracks or a frozen approved import."""

    plan = read_json(run_dir / "plan.json")
    backend = plan.get("denoise", {}).get("backend")
    if backend == "frozen_approved_source":
        return list(read_json(run_dir / "input_manifest.json")["tracks"])
    if backend != "deepfilternet":
        raise DeliveryError("DeepFilterNet is the only supported normal-run denoise backend")
    processing_path = run_dir / "processing_manifest.json"
    if not processing_path.is_file():
        raise DeliveryError("DeepFilterNet processing is required before analysis or rendering")
    processing = read_json(processing_path)
    if processing.get("run_identity_sha256") != sha256_file(run_dir / "run_identity.json"):
        raise DeliveryError("DeepFilterNet processing manifest run identity mismatch")
    manifest_path = run_dir / "denoise/denoise_manifest.json"
    if not manifest_path.is_file() or processing.get("denoise_manifest_sha256") != sha256_file(manifest_path):
        raise DeliveryError("DeepFilterNet processing manifest does not bind the denoise output")
    tracks = processing.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise DeliveryError("DeepFilterNet processing manifest has no tracks")
    for track in tracks:
        if not isinstance(track, dict):
            raise DeliveryError("DeepFilterNet processing track is invalid")
        path = run_relative_path(run_dir, track.get("input_relpath"), "processed input_relpath")
        if not path.is_file() or sha256_file(path) != track.get("audio_sha256"):
            raise DeliveryError(f"DeepFilterNet output changed or is unavailable: {track.get('track_id')}")
    return tracks


def run_deepfilternet_denoise(run_dir: Path, *, python: str, ffmpeg: str) -> None:
    """Create run-local DeepFilterNet inputs while leaving raw sources untouched."""

    identity = require_identity(run_dir)
    input_manifest = read_json(run_dir / "input_manifest.json")
    output_dir = run_dir / "denoise"
    if output_dir.exists():
        raise DeliveryError("DeepFilterNet output directory already exists")
    command = [
        python,
        str(DEEPFILTER_DENOISE_SCRIPT),
        "--output-dir",
        str(output_dir),
        "--ffmpeg",
        ffmpeg,
    ]
    for track in input_manifest["tracks"]:
        source = run_relative_path(run_dir, track["input_relpath"], "input_relpath")
        command += ["--track", f"{track['track_id']}={source}"]
    stage_command(run_dir, "deepfilternet_denoise", command)
    manifest_path = output_dir / "denoise_manifest.json"
    if not manifest_path.is_file():
        raise DeliveryError("DeepFilterNet completed without denoise_manifest.json")
    denoise = read_json(manifest_path)
    if denoise.get("status") != "USER_AUTHORIZED_DIRECT_INTEGRATION__SUBJECTIVE_REVIEW_PENDING":
        raise DeliveryError("DeepFilterNet manifest has an unexpected status")
    by_id = {str(item.get("track_id")): item for item in denoise.get("tracks") or [] if isinstance(item, dict)}
    processed: list[dict[str, Any]] = []
    for original in input_manifest["tracks"]:
        track_id = str(original["track_id"])
        item = by_id.get(track_id)
        expected = output_dir / f"{track_id}.deepfiltered.wav"
        if not item or not expected.is_file() or item.get("output_sha256") != sha256_file(expected):
            raise DeliveryError(f"DeepFilterNet output is missing or mismatched for {track_id}")
        output_info = wav_info(expected)
        if (
            int(output_info["sample_rate_hz"]) != int(input_manifest["sample_rate_hz"])
            or int(output_info["frame_count"]) != int(input_manifest["frame_count"])
            or int(output_info["channels"]) != 1
        ):
            raise DeliveryError(f"DeepFilterNet changed the shared timeline for {track_id}")
        processed.append(
            {
                "track_id": track_id,
                "label": original["label"],
                "input_relpath": relative_to_run(run_dir, expected),
                "audio_sha256": item["output_sha256"],
                "source_input_relpath": original["input_relpath"],
                "source_audio_sha256": original["audio_sha256"],
                "sample_rate_hz": int(output_info["sample_rate_hz"]),
                "frame_count": int(output_info["frame_count"]),
                "channels": int(output_info["channels"]),
                "bits_per_sample": int(output_info["bits_per_sample"]),
            }
        )
    write_json(
        run_dir / "processing_manifest.json",
        {
            "schema_version": "delivery-processing-manifest-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "backend": "deepfilternet",
            "denoise_manifest_relpath": "denoise/denoise_manifest.json",
            "denoise_manifest_sha256": sha256_file(manifest_path),
            "tracks": processed,
            "subjective_review_status": "PENDING",
        },
    )
    plan = read_json(run_dir / "plan.json")
    plan["denoise"]["status"] = "TIMELINE_RESTORED__SUBJECTIVE_REVIEW_PENDING"
    plan["denoise"]["denoise_manifest_sha256"] = sha256_file(manifest_path)
    write_json(run_dir / "plan.json", plan)
    transition(run_dir, "DENOISED", "DeepFilterNet run-local tracks created with 30 ms raw-tail restoration")


def _validate_semantic_reuse_dir(
    path: Path,
    *,
    source_identity: dict[str, Any],
    source_report_sha256: str,
) -> dict[str, Any]:
    """Return a semantic manifest only when it is bound to this exact source run."""

    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise DeliveryError(f"semantic reuse manifest is missing: {manifest_path}")
    document = read_json(manifest_path)
    if document.get("status") != "PASS":
        raise DeliveryError(f"semantic reuse run did not pass: {path}")
    if document.get("episode_id") != source_identity.get("episode_id"):
        raise DeliveryError("semantic reuse run belongs to a different episode")
    if document.get("source_run_id") != source_identity.get("run_id"):
        raise DeliveryError("semantic reuse run is not bound to the selected ASR source run")
    if (document.get("input_report") or {}).get("sha256") != source_report_sha256:
        raise DeliveryError("semantic reuse run is not bound to the selected P0 report")
    return document


def _find_semantic_reuse_dir(
    source_run: Path,
    source_report_sha256: str,
    *,
    explicit_semantic_run: Path | None = None,
) -> Path:
    """Find exactly one semantic transcript bound to the frozen source P0 report.

    A semantic transcript changes the downstream context used to nominate a
    candidate.  When two archived semantic runs are both eligible, silently
    choosing by filename would make the review package non-reproducible.  The
    caller must therefore select one explicitly.
    """

    source_run = source_run.expanduser().resolve()
    source_identity = require_identity(source_run)
    if explicit_semantic_run is not None:
        path = explicit_semantic_run.expanduser().resolve()
        if not path.is_dir():
            raise DeliveryError(f"explicit semantic reuse run does not exist: {path}")
        _validate_semantic_reuse_dir(
            path,
            source_identity=source_identity,
            source_report_sha256=source_report_sha256,
        )
        return path

    episode_dir = source_run.parent
    episode_id = str(source_identity["episode_id"])
    # Normal delivery runs may keep the semantic layer below analysis; older
    # EP04 evidence uses an independently archived semantic run beside it.
    candidates = [source_run / "analysis" / "semantic-transcript-v1"]
    candidates.extend(sorted(episode_dir.glob(f"{episode_id}-semantic-transcript-v1-*")))
    valid: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        path = path.expanduser().resolve()
        if path in seen or not path.is_dir():
            continue
        seen.add(path)
        try:
            _validate_semantic_reuse_dir(
                path,
                source_identity=source_identity,
                source_report_sha256=source_report_sha256,
            )
        except DeliveryError:
            continue
        valid.append(path)
    if not valid:
        raise DeliveryError(
            f"no archived semantic transcript is bound to source P0 report {source_report_sha256}"
        )
    if len(valid) != 1:
        names = ", ".join(path.name for path in valid)
        raise DeliveryError(
            "ambiguous semantic transcript reuse source "
            f"({names}); pass --reuse-semantic-run explicitly"
        )
    return valid[0]


def _validate_reuse_source(
    run_dir: Path,
    source_run: Path,
    *,
    explicit_semantic_run: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    """Validate a frozen prior run before any ASR reuse is allowed."""

    source_run = source_run.expanduser().resolve()
    if not source_run.is_dir():
        raise DeliveryError(f"reuse source run does not exist: {source_run}")
    source_identity = require_identity(source_run)
    target_identity = require_identity(run_dir)
    if source_identity.get("episode_id") != target_identity.get("episode_id"):
        raise DeliveryError("ASR reuse source belongs to a different episode")
    source_input = read_json(source_run / "input_manifest.json")
    target_input = read_json(run_dir / "input_manifest.json")
    source_tracks = {str(item.get("track_id")): item for item in source_input.get("tracks") or []}
    target_tracks = {str(item.get("track_id")): item for item in target_input.get("tracks") or []}
    if set(source_tracks) != set(target_tracks):
        raise DeliveryError("ASR reuse source and target have different track ids")
    for track_id in sorted(target_tracks):
        if source_tracks[track_id].get("audio_sha256") != target_tracks[track_id].get("audio_sha256"):
            raise DeliveryError(f"raw input SHA mismatch; cannot reuse ASR for {track_id}")
        if source_tracks[track_id].get("sample_rate_hz") != target_tracks[track_id].get("sample_rate_hz"):
            raise DeliveryError(f"sample rate mismatch; cannot reuse ASR for {track_id}")
        if source_tracks[track_id].get("frame_count") != target_tracks[track_id].get("frame_count"):
            raise DeliveryError(f"frame count mismatch; cannot reuse ASR for {track_id}")

    source_processing_path = source_run / "processing_manifest.json"
    source_report_path = source_run / "analysis/p0_mvp_report.json"
    source_analysis_manifest_path = source_run / "analysis_manifest.json"
    if not source_processing_path.is_file() or not source_report_path.is_file() or not source_analysis_manifest_path.is_file():
        raise DeliveryError("reuse source is missing processing or P0 analysis evidence")
    source_processing = read_json(source_processing_path)
    source_report = read_json(source_report_path)
    source_analysis_manifest = read_json(source_analysis_manifest_path)
    source_identity_sha256 = sha256_file(source_run / "run_identity.json")
    if source_processing.get("run_identity_sha256") != source_identity_sha256:
        raise DeliveryError("reuse source processing manifest run identity mismatch")
    if source_analysis_manifest.get("run_identity_sha256") != source_identity_sha256:
        raise DeliveryError("reuse source analysis manifest run identity mismatch")
    if source_report.get("engineering_gate") != "PASS":
        raise DeliveryError("reuse source P0 engineering gate is not PASS")
    source_report_sha256 = sha256_file(source_report_path)
    if source_analysis_manifest.get("p0_report_sha256") != source_report_sha256:
        raise DeliveryError("reuse source analysis manifest P0 report SHA mismatch")
    source_denoise_manifest_path = run_relative_path(
        source_run,
        source_processing.get("denoise_manifest_relpath"),
        "reuse source denoise manifest",
    )
    if not source_denoise_manifest_path.is_file():
        raise DeliveryError("reuse source denoise manifest is missing")
    source_denoise_manifest_sha256 = sha256_file(source_denoise_manifest_path)
    if source_processing.get("denoise_manifest_sha256") != source_denoise_manifest_sha256:
        raise DeliveryError("reuse source denoise manifest SHA mismatch")
    source_denoise = read_json(source_denoise_manifest_path)
    semantic_dir = _find_semantic_reuse_dir(
        source_run,
        source_report_sha256,
        explicit_semantic_run=explicit_semantic_run,
    )
    semantic_manifest = read_json(semantic_dir / "manifest.json")
    _validate_semantic_reuse_dir(
        semantic_dir,
        source_identity=source_identity,
        source_report_sha256=source_report_sha256,
    )

    processing_tracks = {str(item.get("track_id")): item for item in source_processing.get("tracks") or []}
    report_tracks = {str(item.get("track_id")): item for item in source_report.get("tracks") or []}
    denoise_tracks = {str(item.get("track_id")): item for item in source_denoise.get("tracks") or []}
    semantic_tracks = {str(item.get("track_id")): item for item in semantic_manifest.get("outputs") or []}
    if (
        set(processing_tracks) != set(target_tracks)
        or set(report_tracks) != set(target_tracks)
        or set(denoise_tracks) != set(target_tracks)
        or set(semantic_tracks) != set(target_tracks)
    ):
        raise DeliveryError("reuse source track evidence is incomplete")
    asr_engines: set[str] = set()
    model_refs: set[str] = set()
    timestamp_policies: set[str] = set()
    track_evidence: list[dict[str, Any]] = []
    for track_id in sorted(target_tracks):
        source_track = source_tracks[track_id]
        source_raw = run_relative_path(source_run, source_track.get("input_relpath"), "reuse source raw input")
        if not source_raw.is_file() or sha256_file(source_raw) != source_track.get("audio_sha256"):
            raise DeliveryError(f"reuse source raw input changed or is missing: {track_id}")
        processing_item = processing_tracks[track_id]
        if processing_item.get("source_audio_sha256") != source_track.get("audio_sha256"):
            raise DeliveryError(f"reuse source raw/denoise lineage mismatch: {track_id}")
        source_audio = run_relative_path(source_run, processing_item.get("input_relpath"), "reuse source processed input")
        if not source_audio.is_file() or sha256_file(source_audio) != processing_item.get("audio_sha256"):
            raise DeliveryError(f"reuse source denoised audio changed or is missing: {track_id}")
        denoise_item = denoise_tracks[track_id]
        if (
            denoise_item.get("source_sha256") != source_track.get("audio_sha256")
            or denoise_item.get("output_sha256") != processing_item.get("audio_sha256")
        ):
            raise DeliveryError(f"reuse source DeepFilterNet lineage mismatch: {track_id}")
        transcript_path = resolve_path_from_report(str(report_tracks[track_id].get("transcript_path")), source_report_path)
        if not transcript_path.is_file():
            raise DeliveryError(f"reuse source transcript is missing: {track_id}")
        transcript = read_json(transcript_path)
        transcript_sha256 = sha256_file(transcript_path)
        if (
            transcript.get("track_id") != track_id
            or transcript.get("sample_rate_hz") != processing_item.get("sample_rate_hz")
            or transcript.get("frame_count") != processing_item.get("frame_count")
            or transcript.get("source_audio_sha256") != processing_item.get("audio_sha256")
        ):
            raise DeliveryError(f"transcript source audio SHA mismatch: {track_id}")
        if transcript.get("engine") != "faster_whisper_small":
            raise DeliveryError(f"reuse source is not faster-whisper small: {track_id}")
        model_ref = transcript.get("model_ref")
        timestamp_policy = transcript.get("timestamp_repair_policy")
        if not isinstance(model_ref, str) or not model_ref or not isinstance(timestamp_policy, str) or not timestamp_policy:
            raise DeliveryError(f"reuse source transcript metadata is incomplete: {track_id}")
        asr_engines.add(str(transcript["engine"]))
        model_refs.add(model_ref)
        timestamp_policies.add(timestamp_policy)

        semantic_item = semantic_tracks[track_id]
        semantic_path = run_relative_path(semantic_dir, semantic_item.get("path"), "semantic output path")
        if not semantic_path.is_file() or sha256_file(semantic_path) != semantic_item.get("sha256"):
            raise DeliveryError(f"semantic output changed or is missing: {track_id}")
        semantic = read_json(semantic_path)
        semantic_source = semantic.get("source_transcript") or {}
        if (
            semantic_source.get("track_id") != track_id
            or semantic_source.get("sha256") != transcript_sha256
            or semantic_source.get("source_audio_sha256") != processing_item.get("audio_sha256")
            or semantic_source.get("sample_rate_hz") != processing_item.get("sample_rate_hz")
            or semantic_source.get("frame_count") != processing_item.get("frame_count")
        ):
            raise DeliveryError(f"semantic transcript lineage mismatch: {track_id}")
        track_evidence.append(
            {
                "track_id": track_id,
                "raw_sha256": source_track["audio_sha256"],
                "denoised_sha256": processing_item["audio_sha256"],
                "transcript_sha256": transcript_sha256,
                "transcript_source_audio_sha256": transcript["source_audio_sha256"],
                "semantic_sha256": semantic_item["sha256"],
                "semantic_source_transcript_sha256": semantic_source["sha256"],
            }
        )
    if len(asr_engines) != 1 or len(model_refs) != 1 or len(timestamp_policies) != 1:
        raise DeliveryError("reuse source ASR metadata differs across tracks")
    if source_report.get("timestamp_repair_policy") != next(iter(timestamp_policies)):
        raise DeliveryError("reuse source P0 report timestamp repair policy mismatch")
    source_p0_command_path = source_run / "logs/p0_analysis.command.json"
    if not source_p0_command_path.is_file():
        raise DeliveryError("reuse source P0 command evidence is missing")
    source_p0_command = read_json(source_p0_command_path)
    if source_p0_command.get("returncode") != 0:
        raise DeliveryError("reuse source P0 command did not complete successfully")

    return source_input, source_processing, semantic_dir, {
        "source_identity": source_identity,
        "source_identity_sha256": source_identity_sha256,
        "source_report_path": source_report_path,
        "source_report_sha256": source_report_sha256,
        "source_analysis_manifest_sha256": sha256_file(source_analysis_manifest_path),
        "source_processing_manifest_sha256": sha256_file(source_processing_path),
        "source_denoise_manifest_sha256": source_denoise_manifest_sha256,
        "source_p0_command_sha256": sha256_file(source_p0_command_path),
        "source_semantic_manifest_sha256": sha256_file(semantic_dir / "manifest.json"),
        "source_semantic_run_id": semantic_manifest.get("run_id"),
        "source_semantic_dir": semantic_dir,
        "asr": {
            "engine": next(iter(asr_engines)),
            "model_ref": next(iter(model_refs)),
            "timestamp_repair_policy": next(iter(timestamp_policies)),
        },
        "tracks": track_evidence,
        "report_tracks": report_tracks,
        "processing_tracks": processing_tracks,
    }


def resolve_path_from_report(value: str, report_path: Path) -> Path:
    """Resolve a transcript path exactly as the P0 candidate reader does."""

    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    from_report = (report_path.parent / path).resolve()
    if from_report.exists():
        return from_report
    return (PROJECT_ROOT / path).resolve()


def reuse_denoise_artifacts(
    run_dir: Path,
    source_run: Path,
    *,
    explicit_semantic_run: Path | None = None,
) -> dict[str, Any]:
    """Bind the target run to immutable denoise artifacts from a validated source run."""

    _, source_processing, _, evidence = _validate_reuse_source(
        run_dir,
        source_run,
        explicit_semantic_run=explicit_semantic_run,
    )
    output_dir = run_dir / "denoise"
    output_dir.mkdir()
    source_manifest = source_run / "denoise/denoise_manifest.json"
    target_manifest = output_dir / "denoise_manifest.json"
    shutil.copy2(source_manifest, target_manifest)
    target_tracks: list[dict[str, Any]] = []
    for item in source_processing.get("tracks") or []:
        track_id = str(item["track_id"])
        source_path = run_relative_path(source_run, item["input_relpath"], "reuse source processed input")
        target_path = output_dir / source_path.name
        relative_symlink(target_path, source_path)
        target_item = dict(item)
        target_item["input_relpath"] = relative_to_run(run_dir, target_path)
        target_item["source_artifact_mode"] = "frozen_prior_analysis"
        target_item["reused_from_run"] = evidence["source_identity"]["run_id"]
        target_tracks.append(target_item)
    identity = require_identity(run_dir)
    processing = {
        "schema_version": "delivery-processing-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "backend": "deepfilternet",
        "source_artifact_mode": "frozen_prior_analysis",
        "reused_from_run": evidence["source_identity"]["run_id"],
        "source_processing_manifest_sha256": evidence["source_processing_manifest_sha256"],
        "denoise_manifest_relpath": relative_to_run(run_dir, target_manifest),
        "denoise_manifest_sha256": sha256_file(target_manifest),
        "tracks": target_tracks,
        "subjective_review_status": "INHERITED_FROM_SOURCE_RUN__NOT_REPROCESSED",
    }
    write_json(run_dir / "processing_manifest.json", processing)
    plan = read_json(run_dir / "plan.json")
    plan["denoise"].update(
        {
            "status": "REUSED_FROZEN_PRIOR_ANALYSIS",
            "source_artifact_mode": "frozen_prior_analysis",
            "reused_from_run": evidence["source_identity"]["run_id"],
            "denoise_manifest_sha256": sha256_file(target_manifest),
        }
    )
    write_json(run_dir / "plan.json", plan)
    transition(
        run_dir,
        "DENOISED",
        f"reused immutable DeepFilterNet artifacts from {evidence['source_identity']['run_id']}; no denoise rerun",
    )
    return evidence


def reuse_analysis_artifacts(run_dir: Path, source_run: Path, *, evidence: dict[str, Any]) -> Path:
    """Copy immutable P0/semantic artifacts into a new run and record provenance."""

    source_report_path: Path = evidence["source_report_path"]
    source_analysis = source_run / "analysis"
    target_analysis = run_dir / "analysis"
    if target_analysis.exists():
        raise DeliveryError("analysis directory already exists; use a new run")
    target_analysis.mkdir()
    for source_file in sorted(source_analysis.glob("track_*.transcript.json")):
        shutil.copy2(source_file, target_analysis / source_file.name)
    source_report = read_json(source_report_path)
    target_report = json.loads(json.dumps(source_report, ensure_ascii=False))
    for item in target_report.get("tracks") or []:
        track_id = str(item.get("track_id") or "")
        target_transcript = target_analysis / f"{track_id}.transcript.json"
        if not track_id or not target_transcript.is_file():
            raise DeliveryError(f"reused transcript is missing after copy: {track_id}")
        item["transcript_path"] = str(target_transcript)
    write_json(target_analysis / "p0_mvp_report.json", target_report)
    source_p0_manifest = source_analysis / "p0_input_manifest.json"
    if source_p0_manifest.is_file():
        shutil.copy2(source_p0_manifest, target_analysis / "p0_input_manifest.source.json")
    semantic_target = target_analysis / "semantic-transcript-v1"
    (semantic_target / "semantic_transcripts").mkdir(parents=True)
    semantic_source = evidence.get("source_semantic_dir")
    if not isinstance(semantic_source, Path):
        raise DeliveryError("validated semantic reuse directory is unavailable")
    for source_file in sorted((semantic_source / "semantic_transcripts").glob("*.semantic.json")):
        shutil.copy2(source_file, semantic_target / "semantic_transcripts" / source_file.name)
    shutil.copy2(semantic_source / "manifest.json", semantic_target / "manifest.json")
    identity = require_identity(run_dir)
    reuse_manifest = {
        "schema_version": "delivery-analysis-reuse-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "source_artifact_mode": "frozen_prior_analysis",
        "reused_from_run": evidence["source_identity"]["run_id"],
        "source_run_dir": str(source_run),
        "source_run_dir_rel": tool_reference(source_run),
        "raw_input_sha_match": True,
        "source_processing_manifest_sha256": evidence["source_processing_manifest_sha256"],
        "source_denoise_manifest_sha256": evidence["source_denoise_manifest_sha256"],
        "source_p0_report_relpath": str(source_report_path.relative_to(source_run)),
        "source_p0_report_sha256": evidence["source_report_sha256"],
        "source_p0_command_sha256": evidence["source_p0_command_sha256"],
        "source_analysis_manifest_sha256": evidence["source_analysis_manifest_sha256"],
        "source_semantic_manifest_sha256": evidence["source_semantic_manifest_sha256"],
        "semantic_source_run_id": evidence["source_semantic_run_id"],
        "semantic_source_run_dir_rel": tool_reference(semantic_source),
        "asr": evidence["asr"],
        "tracks": evidence["tracks"],
        "semantic_policy": "timing_text_heuristic_v1",
        "reason": "candidate rules changed; immutable prior ASR is sufficient and new ASR would duplicate work",
    }
    write_json(run_dir / "analysis_reuse_manifest.json", reuse_manifest)
    semantic_manifest = read_json(semantic_source / "manifest.json")
    write_json(
        run_dir / "analysis_manifest.json",
        {
            "schema_version": "delivery-analysis-manifest-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "p0_report_relpath": "analysis/p0_mvp_report.json",
            "p0_report_sha256": sha256_file(target_analysis / "p0_mvp_report.json"),
            "semantic_transcript_relpath": "analysis/semantic-transcript-v1",
            "semantic_transcript_manifest_sha256": sha256_file(semantic_target / "manifest.json"),
            "semantic_transcript_policy": semantic_manifest.get("generator", {}).get("policy_version", "timing_text_heuristic_v1"),
            "engineering_gate": source_report.get("engineering_gate"),
            "quality_gate": source_report.get("quality_gate"),
            "analysis_audio_source": "frozen_prior_analysis",
            "reuse_manifest_relpath": "analysis_reuse_manifest.json",
            "reuse_manifest_sha256": sha256_file(run_dir / "analysis_reuse_manifest.json"),
            "tool": {"script_reference": tool_reference(P0_SCRIPT), "python": "/private/tmp/venv-v13-py313/bin/python"},
        },
    )
    transition(run_dir, "ANALYZED", f"reused P0 and semantic transcript artifacts from {evidence['source_identity']['run_id']}")
    return target_analysis / "p0_mvp_report.json"


def run_local_analysis(
    run_dir: Path,
    *,
    model: str | None,
    context_prompt: str,
    python: str,
    reuse_source_run: Path | None = None,
    reuse_semantic_run: Path | None = None,
    reuse_evidence: dict[str, Any] | None = None,
) -> Path:
    if reuse_source_run is not None:
        if model or context_prompt:
            raise DeliveryError(
                "--model and --context-prompt cannot be used with --reuse-analysis-run; "
                "the reused ASR output and configuration are frozen"
            )
        evidence = reuse_evidence
        if evidence is None:
            _, _, _, evidence = _validate_reuse_source(
                run_dir,
                reuse_source_run,
                explicit_semantic_run=reuse_semantic_run,
            )
        return reuse_analysis_artifacts(run_dir, reuse_source_run.expanduser().resolve(), evidence=evidence)
    identity = require_identity(run_dir)
    input_manifest = read_json(run_dir / "input_manifest.json")
    analysis = run_dir / "analysis"
    if analysis.exists():
        raise DeliveryError("analysis directory already exists; use a new run or resume its existing state")
    analysis_tracks = active_audio_tracks(run_dir)
    p0_manifest = {
        "schema_version": "ntrack-input-v1",
        "episode_id": identity["episode_id"],
        "tracks": [
            {
                "track_id": track["track_id"],
                "label": track["label"],
                "audio_path": os.path.relpath(run_dir / track["input_relpath"], analysis),
            }
            for track in analysis_tracks
        ],
    }
    analysis.mkdir()
    write_json(analysis / "p0_input_manifest.json", p0_manifest)
    command = [python, str(P0_SCRIPT), "--manifest", str(analysis / "p0_input_manifest.json"), "--out", str(analysis)]
    if model:
        command += ["--model", model]
    if context_prompt:
        command += ["--context-prompt", context_prompt]
    stage_command(run_dir, "p0_analysis", command)
    report = analysis / "p0_mvp_report.json"
    if not report.is_file():
        raise DeliveryError("P0 completed without p0_mvp_report.json")
    p0 = read_json(report)
    if p0.get("engineering_gate") != "PASS":
        raise DeliveryError("P0 engineering gate did not pass")
    semantic_dir = analysis / "semantic-transcript-v1"
    semantic_command = [
        python,
        str(SEMANTIC_SCRIPT),
        "--input-report",
        str(report),
        "--episode-id",
        str(identity["episode_id"]),
        "--source-run-id",
        str(identity["run_id"]),
        "--run-id",
        f"{identity['run_id']}-semantic-transcript-v1",
        "--out",
        str(semantic_dir),
    ]
    stage_command(run_dir, "semantic_transcript", semantic_command)
    semantic_manifest = semantic_dir / "manifest.json"
    if not semantic_manifest.is_file():
        raise DeliveryError("semantic transcript completed without manifest.json")
    semantic = read_json(semantic_manifest)
    if semantic.get("status") != "PASS":
        raise DeliveryError("semantic transcript did not pass its contract")
    transition(run_dir, "ANALYZED", "P0 ASR/VAD engineering gate passed")
    write_json(
        run_dir / "analysis_manifest.json",
        {
            "schema_version": "delivery-analysis-manifest-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "p0_report_relpath": "analysis/p0_mvp_report.json",
            "p0_report_sha256": sha256_file(report),
            "semantic_transcript_relpath": "analysis/semantic-transcript-v1",
            "semantic_transcript_manifest_sha256": sha256_file(semantic_manifest),
            "semantic_transcript_policy": "timing_text_heuristic_v1",
            "engineering_gate": p0.get("engineering_gate"),
            "quality_gate": p0.get("quality_gate"),
            "analysis_audio_source": read_json(run_dir / "plan.json").get("denoise", {}).get("backend"),
            "tool": {"script_reference": tool_reference(P0_SCRIPT), "python": python},
        },
    )
    return report


def risk_level(candidate: dict[str, Any]) -> str:
    policy = candidate.get("editing_policy") or {}
    if policy.get("route") == "auto_preserve":
        return "preserve"
    if policy.get("route") == "human_review_required":
        return "high"
    kind = str(candidate.get("candidate_kind") or candidate.get("reason_key") or "")
    high = {
        "global_long_pause",
        "self_correction",
        "semantic_duplicate",
        "off_topic",
        "crosstalk",
        "crosstalk_attribution",
        "transient",
        "transient_events",
        "cough",
        "mic_bump",
    }
    display = candidate.get("review_display") or {}
    if kind in high or display.get("requires_audio_review"):
        return "high"
    return "low"


def review_eligible(candidate: dict[str, Any]) -> bool:
    """Return whether a candidate may enter calibration, predictions or an EDL.

    A policy ``auto_preserve`` result is an explicit protection action, not an
    invisible human decision. It remains in policy_application/all_candidates
    but is excluded from review and every form of automatic cut.
    """

    return (candidate.get("editing_policy") or {}).get("route") != "auto_preserve"


def duration_bin(candidate: dict[str, Any]) -> str:
    seconds = float(candidate.get("duration_seconds") or 0.0)
    if seconds < 0.10:
        return "lt_100ms"
    if seconds <= 0.40:
        return "100_400ms"
    if seconds <= 1.0:
        return "400ms_1s"
    return "gt_1s"


def stratum_for(candidate: dict[str, Any]) -> str:
    return ":".join(
        [
            str(candidate.get("reason_key", "unknown")),
            str(candidate.get("filler_subtype", "none")),
            duration_bin(candidate),
            str(candidate.get("source_track", "all_tracks")),
        ]
    )


def deterministic_rank(seed: str, candidate_id: str) -> str:
    return hashlib.sha256(f"{seed}:{candidate_id}".encode("utf-8")).hexdigest()


def compact_review_context(candidate: dict[str, Any], limit: int = 220) -> str:
    """Turn package text context into a short line suitable for a human packet."""

    semantic = candidate.get("semantic_context") or {}
    semantic_rows = [
        str(row.get("text_punctuated") or row.get("raw_text_joined") or "")
        for row in semantic.get("sentences") or []
        if isinstance(row, dict)
    ]
    semantic_text = " ".join(row for row in semantic_rows if row).strip()
    if semantic_text:
        return semantic_text if len(semantic_text) <= limit else semantic_text[: limit - 1] + "…"
    chunks: list[str] = []
    for track_id, item in sorted((candidate.get("text_tracks") or {}).items()):
        words = item.get("words") if isinstance(item, dict) else None
        text = "".join(str(word.get("text", "")) for word in words or [] if isinstance(word, dict))
        if text:
            chunks.append(f"{track_id}：{text}")
    result = " / ".join(chunks)
    return result if len(result) <= limit else result[: limit - 1] + "…"


def write_offline_review_packet(run_dir: Path) -> None:
    """Create a no-browser review brief and a deliberately incomplete decision template.

    The packet is a human-facing aid, not a shortcut around the decision validator:
    the template intentionally contains placeholders and cannot be resumed until a
    real reviewer has provided accept/reject decisions and the required A/B record.
    """

    identity = require_identity(run_dir)
    package = read_json(run_dir / "review_bundle/review_package.json")
    candidates = package.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise DeliveryError("review package has no candidates for offline review packet")
    case_memory_path = run_dir / "review_bundle/case_memory.json"
    case_memory_by_id: dict[str, Any] = {}
    if case_memory_path.is_file():
        case_memory = read_json(case_memory_path)
        errors = validate_case_memory(case_memory, package)
        if errors:
            raise DeliveryError("case-memory sidecar cannot be used in review packet: " + "; ".join(errors))
        case_memory_by_id = dict(case_memory.get("candidate_memory") or {})
    high_count = sum(risk_level(item) == "high" for item in candidates if isinstance(item, dict))
    lines = [
        f"# {identity['episode_id']} 审核包（无需前端）",
        "",
        f"- Run：`{identity['run_id']}`",
        f"- 本包共 **{len(candidates)}** 项，其中高风险 **{high_count}** 项。",
        "- 这只是候选，不会因为生成本文件而剪掉任何音频。",
        "- 完整句子中的弱口语词、普通单个“嗯”默认保留；长停顿是“压缩而不是删光”。",
        "",
        "## 怎么给 Agent 回答",
        "",
        "逐项回复 `C001 accept` 或 `C001 reject` 即可。标为“必须 A/B”的项，只有在听完原版和压缩版后才能回答；可写成 `C001 accept（已听 A/B）`。Agent 必须把你的真实姓名、决定时间和试听记录写入 `human_decisions.json`，不能替你填写。",
        "",
        "## 候选",
        "",
    ]
    template_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise DeliveryError("review package contains an invalid candidate")
        candidate_id = str(candidate["candidate_id"])
        requirements = candidate.get("review_requirements") or {}
        must_listen = requirements.get("must_listen_to") or []
        high_risk = risk_level(candidate) == "high"
        previews = candidate.get("previews") or {}
        proposed_text = candidate.get("proposed_delete_text") or candidate.get("evidence_text") or "（无文字；请以试听为准）"
        summary = ((candidate.get("review_display") or {}).get("summary") or "").strip()
        memory = case_memory_by_id.get(candidate_id) or {}
        matches = memory.get("matches") or []
        memory_lines: list[str] = []
        if matches:
            first = matches[0]
            reasons = "；".join(str(item) for item in first.get("matching_reasons") or [])
            feedback = str(first.get("feedback") or "").strip()
            if len(feedback) > 180:
                feedback = feedback[:179] + "…"
            memory_lines.extend(
                [
                    f"- 相似历史案例（仅供参考）：`{first.get('case_id')}` / {first.get('decision_display', first.get('decision'))}；匹配：{reasons or '见 case-memory 侧车。'}",
                    f"- 历史备注：{feedback or '（当时未填写备注）'}",
                    "- 历史案例不会代替本轮真人决定，也不会直接生成 EDL 或自动剪辑权限。",
                ]
            )
        elif memory:
            memory_lines.append(f"- 相似历史案例：{memory.get('summary') or '无可解释相似案例；请按本轮证据独立判断。'}")
        lines.extend(
            [
                f"### {candidate_id} · {'高风险' if high_risk else '低风险代表样本'}",
                "",
                f"- 时间：{float(candidate.get('start_seconds', 0.0)):.2f}s–{float(candidate.get('end_seconds', 0.0)):.2f}s",
                f"- 类型：`{candidate.get('candidate_kind')}` / `{candidate.get('filler_subtype', candidate.get('reason_key', 'unknown'))}`",
                f"- 拟处理：{proposed_text}",
                f"- 说明：{summary or '请结合上下文判断是否保留。'}",
                f"- 上下文：{compact_review_context(candidate) or '（没有可显示的文字上下文）'}",
                f"- {'必须 A/B：原版和压缩版都要听。' if must_listen else '可先看文字；听感或边界不确定时再试听。'}",
                f"- 原版试听：`{run_dir / 'review_bundle' / str(previews.get('original_path', ''))}`",
                f"- 压缩版试听：`{run_dir / 'review_bundle' / str(previews.get('proposed_cut_path', ''))}`",
                *memory_lines,
                "",
            ]
        )
        listened: dict[str, Any] = {}
        if must_listen:
            listened = {
                "original_sha256": f"<listened original SHA: {previews.get('original_sha256', '')}>",
                "original_listened_at": "<ISO-8601 after real listening>",
                "proposed_cut_sha256": f"<listened proposed-cut SHA: {previews.get('proposed_cut_sha256', '')}>",
                "proposed_cut_listened_at": "<ISO-8601 after real listening>",
            }
        template_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_semantic_sha256": candidate.get("semantic_sha256"),
                "decision": "<accept|reject>",
                "reviewer": "<same real reviewer name as top-level reviewer>",
                "decided_at": "<ISO-8601 after real review>",
                "review_basis": "<text_only|text_and_audio>",
                "listened_previews": listened,
                "feedback": "<optional human note; do not use this field to change the boundary>",
            }
        )
    write_text(run_dir / "review_packet.md", "\n".join(lines) + "\n")
    write_json(
        run_dir / "review_decisions.template.json",
        {
            "schema_version": "human-decisions-mvp-v1",
            "package_id": package.get("package_id"),
            "review_manifest_sha256": package.get("review_manifest_sha256"),
            "reviewer": "<real human reviewer name>",
            "decisions": template_rows,
            "template_note": "Copy to human_decisions.json only after a real reviewer has filled every placeholder. This file is not a human decision record.",
        },
    )


def learning_review_priority(candidate: Mapping[str, Any]) -> int:
    """Return non-authoritative review priority from history and driver evidence."""

    experience = candidate.get("experience_signal") or {}
    learned = candidate.get("label_learning_prediction") or {}
    case_memory = candidate.get("case_memory_signal") or {}
    try:
        return max(
            int(experience.get("review_priority", 0)),
            int(learned.get("review_priority", 0)),
            int(case_memory.get("review_priority", 0)),
        )
    except (AttributeError, TypeError, ValueError):
        return 0


def select_calibration_candidates(
    candidates: list[dict[str, Any]], seed: str, review_budget: int | None = None
) -> tuple[list[str], dict[str, Any]]:
    """Select all high-risk candidates plus bounded, reproducible low-risk strata.

    A budget is a comfort limit for the human review packet, never permission to
    omit a mandatory high-risk item.  If high-risk items alone exceed it, caller
    must stop rather than silently converting them into machine decisions.
    """

    low_groups: dict[str, list[dict[str, Any]]] = {}
    selected: set[str] = set()
    report: dict[str, Any] = {
        "review_budget": review_budget,
        "high_risk": [],
        "low_risk_strata": {},
    }
    for candidate in candidates:
        if not review_eligible(candidate):
            continue
        candidate_id = str(candidate["candidate_id"])
        if risk_level(candidate) == "high":
            selected.add(candidate_id)
            report["high_risk"].append(candidate_id)
        else:
            low_groups.setdefault(stratum_for(candidate), []).append(candidate)

    if review_budget is not None:
        if review_budget < 1:
            raise DeliveryError("review budget must be at least 1")
        if len(report["high_risk"]) > review_budget:
            raise DeliveryError(
                f"review budget {review_budget} cannot cover {len(report['high_risk'])} mandatory high-risk candidates; "
                "no high-risk candidate was omitted or auto-decided"
            )
    remaining = None if review_budget is None else review_budget - len(selected)
    for stratum, items in sorted(low_groups.items()):
        # Historical label evidence may change review priority, never the
        # candidate's decision.  Mixed/reject history is surfaced first so a
        # human can resolve known failure modes within the same budget.
        ordered = sorted(
            items,
            key=lambda item: (
                -learning_review_priority(item),
                str(item["candidate_id"]),
            ),
        )
        count = len(ordered)
        target = min(count, max(3, min(10, math.ceil(count * 0.10))))
        required_for_valid_sample = min(3, count)
        if remaining is not None and remaining < required_for_valid_sample:
            report["low_risk_strata"][stratum] = {
                "population": count,
                "selected": 0,
                "target_without_budget": target,
                "selection_rule": "deferred because remaining review budget cannot form a valid representative sample",
                "candidate_ids": [],
                "deferred_behavior": "preserve; later prediction becomes human_review_required",
            }
            continue
        selected_target = target if remaining is None else min(target, remaining)
        # Keep duration extremes as deterministic boundary examples, then reserve
        # the remaining representative slots for historic failure/conflict
        # signals before using the seed-stable random fill.  A memory signal
        # never changes a decision; it only makes a bounded human review packet
        # spend one of its low-risk slots on the most relevant prior case.
        boundaries = sorted(ordered, key=lambda item: (float(item.get("duration_seconds") or 0), str(item["candidate_id"])))
        picks: list[dict[str, Any]] = []
        for item in (boundaries[0], boundaries[-1]):
            if item not in picks:
                picks.append(item)
        priority_items = [item for item in ordered if learning_review_priority(item) > 0]
        for item in priority_items:
            if len(picks) >= selected_target:
                break
            if item not in picks:
                picks.append(item)
        for item in sorted(ordered, key=lambda item: deterministic_rank(seed, str(item["candidate_id"]))):
            if len(picks) >= selected_target:
                break
            if item not in picks:
                picks.append(item)
        picks = picks[:selected_target]
        ids = sorted(str(item["candidate_id"]) for item in picks)
        selected.update(ids)
        if remaining is not None:
            remaining -= len(ids)
        report["low_risk_strata"][stratum] = {
            "population": count,
            "selected": len(ids),
            "target_without_budget": target,
            "selection_rule": "min(whole_stratum, max(3, min(10, ceil(10%)))) plus duration boundaries, historic review priority, then seeded fill",
            "priority_candidate_ids": [str(item["candidate_id"]) for item in priority_items],
            "priority_selected_ids": [
                str(item["candidate_id"])
                for item in picks
                if learning_review_priority(item) > 0
            ],
            "candidate_ids": ids,
        }
    report["selected_total"] = len(selected)
    report["remaining_budget"] = remaining
    return sorted(selected), report


def render_mix_preview(
    ffmpeg: str,
    audio_paths: list[Path],
    start_seconds: float,
    duration_seconds: float,
    output: Path,
) -> None:
    """Render a short, level-matched direct mix for subjective A/B checks."""

    if duration_seconds <= 0:
        raise DeliveryError("listening preview duration must be positive")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for audio in audio_paths:
        command += [
            "-ss",
            f"{max(0.0, start_seconds):.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-i",
            str(audio),
        ]
    labels = []
    filters = []
    for index in range(len(audio_paths)):
        labels.append(f"[a{index}]")
        filters.append(f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[a{index}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(audio_paths)}:duration=longest:normalize=1[mix]"
    )
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[mix]",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output),
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, check=True)


def render_single_preview(
    ffmpeg: str,
    audio: Path,
    start_seconds: float,
    duration_seconds: float,
    output: Path,
) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, start_seconds):.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        str(audio),
        "-ar",
        "48000",
        "-ac",
        "1",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output),
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, check=True)


def build_listening_checks(run_dir: Path, *, ffmpeg: str, candidates: list[dict[str, Any]]) -> None:
    """Create local raw/denoised/direct-mix listening checks beside the review UI."""

    identity = require_identity(run_dir)
    input_manifest = read_json(run_dir / "input_manifest.json")
    raw_tracks = [
        run_relative_path(run_dir, track["input_relpath"], "input_relpath")
        for track in input_manifest.get("tracks") or []
    ]
    denoised_tracks = [
        run_relative_path(run_dir, track["input_relpath"], "processed input_relpath")
        for track in active_audio_tracks(run_dir)
    ]
    if not raw_tracks or len(raw_tracks) != len(denoised_tracks):
        raise DeliveryError("listening checks require matching raw and denoised track sets")
    frame_count = int(input_manifest["frame_count"])
    sample_rate = int(input_manifest["sample_rate_hz"])
    total_seconds = frame_count / sample_rate
    segments: list[dict[str, Any]] = []

    # A known, speech-rich EP04 window is kept as the primary check.  For a
    # shorter future episode it gracefully clamps to the available timeline.
    dialogue_start = min(60.0, max(0.0, total_seconds - 30.0))
    segments.append({"id": "dialogue", "label": "正常对话段", "start_seconds": dialogue_start, "duration_seconds": min(30.0, total_seconds - dialogue_start)})
    pause_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("candidate_kind") == "global_long_pause"
    ]
    if pause_candidates:
        pause = min(pause_candidates, key=lambda item: float(item.get("start_seconds", 0.0)))
        quiet_start = max(0.0, float(pause.get("start_seconds", 0.0)) - 10.0)
    else:
        quiet_start = min(2418.0, max(0.0, total_seconds - 30.0))
    quiet_start = min(quiet_start, max(0.0, total_seconds - 30.0))
    if abs(quiet_start - dialogue_start) > 0.5:
        segments.append({"id": "quiet_or_pause", "label": "停顿/低活动段", "start_seconds": quiet_start, "duration_seconds": min(30.0, total_seconds - quiet_start)})

    listening_dir = run_dir / "review_bundle" / "listening"
    listening_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    for segment in segments:
        start = float(segment["start_seconds"])
        duration = float(segment["duration_seconds"])
        raw_mix = listening_dir / f"{segment['id']}.raw.direct-mix.mp3"
        denoised_mix = listening_dir / f"{segment['id']}.denoised.direct-mix.mp3"
        render_mix_preview(ffmpeg, raw_tracks, start, duration, raw_mix)
        render_mix_preview(ffmpeg, denoised_tracks, start, duration, denoised_mix)
        checks.append(
            {
                **segment,
                "raw_direct_mix_path": f"listening/{raw_mix.name}",
                "raw_direct_mix_sha256": sha256_file(raw_mix),
                "denoised_direct_mix_path": f"listening/{denoised_mix.name}",
                "denoised_direct_mix_sha256": sha256_file(denoised_mix),
                "mix_method": "ffmpeg_amix_normalize_1_no_loudness_normalization",
            }
        )
        if segment["id"] == "dialogue":
            raw_single = listening_dir / "dialogue.track_01.raw.mp3"
            denoised_single = listening_dir / "dialogue.track_01.denoised.mp3"
            render_single_preview(ffmpeg, raw_tracks[0], start, duration, raw_single)
            render_single_preview(ffmpeg, denoised_tracks[0], start, duration, denoised_single)
            checks[-1]["track_01_raw_path"] = f"listening/{raw_single.name}"
            checks[-1]["track_01_raw_sha256"] = sha256_file(raw_single)
            checks[-1]["track_01_denoised_path"] = f"listening/{denoised_single.name}"
            checks[-1]["track_01_denoised_sha256"] = sha256_file(denoised_single)

    checks_doc = {
        "schema_version": "denoise-mix-listening-check-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "raw_input_tracks": [
            {"track_id": item["track_id"], "sha256": item["audio_sha256"]}
            for item in input_manifest["tracks"]
        ],
        "denoised_tracks": [
            {"track_id": item["track_id"], "sha256": item["audio_sha256"]}
            for item in active_audio_tracks(run_dir)
        ],
        "checks": checks,
        "scope": "subjective listening only; no human semantic decision and no EDL",
        "notes": [
            "direct-mix 是三轨等电平试听，不是主麦 automix；用于检查并轨是否自然。",
            "raw 与 denoised 使用相同片段、采样率和编码，便于听降噪改善与语音损伤。",
            "这是审核前的质量检查，不会自动批准任何候选。",
        ],
    }
    write_json(run_dir / "listening_checks.json", checks_doc)
    rows = []
    for check in checks:
        rows.append(
            f"<section><h2>{escape_html(check['label'])} · {check['start_seconds']:.2f}s–{check['start_seconds'] + check['duration_seconds']:.2f}s</h2>"
            f"<p>三轨直混：原始</p><audio controls preload='metadata' src='{escape_html(check['raw_direct_mix_path'])}'></audio>"
            f"<p>三轨直混：DeepFilterNet 降噪后</p><audio controls preload='metadata' src='{escape_html(check['denoised_direct_mix_path'])}'></audio>"
        )
        if check.get("track_01_raw_path"):
            rows.append(
                f"<p>Track 01 单轨：原始</p><audio controls preload='metadata' src='{escape_html(check['track_01_raw_path'])}'></audio>"
                f"<p>Track 01 单轨：降噪后</p><audio controls preload='metadata' src='{escape_html(check['track_01_denoised_path'])}'></audio>"
            )
        rows.append("</section>")
    write_text(
        run_dir / "review_bundle/listening_checks.html",
        "<!doctype html><meta charset='utf-8'><title>降噪与并轨试听</title>"
        "<style>body{font:16px -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;max-width:900px;margin:32px auto;padding:0 18px;background:#f5f7fa;color:#20242a}section{background:white;border:1px solid #dfe3e8;border-radius:10px;padding:18px;margin:18px 0}audio{width:100%;margin:5px 0 14px}small{color:#5e6873}</style>"
        "<h1>降噪与三轨并轨试听</h1>"
        "<p><b>先听这里。</b>每一段都按同一时间范围给出：原始三轨直混、DeepFilterNet 后三轨直混；正常对话段另外给 Track 01 单轨。这里不保存语义删剪决定，也不会改原始 WAV。</p>"
        "<p><small>直混只是并轨检查，不等同于主麦 automix；如果发现降噪伤害人声或三轨叠加不自然，请先记下对应时间段，再回到候选审核页。</small></p>"
        + "".join(rows)
        + "<p><a href='index.html'>返回候选审核页</a></p>",
    )


def escape_html(value: Any) -> str:
    value = str(value)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
        .replace("'", "&#39;")
    )


def build_event_route_metadata(run_dir: Path, *, python: str) -> dict[str, Any]:
    """Create the future-review event route sidecar without changing the package."""

    plan = read_json(run_dir / "plan.json")
    routing = plan.get("event_routing") or {}
    if routing.get("schema_version") != "review-event-routes-v1":
        raise DeliveryError("plan is missing review-event-routes-v1 configuration")
    command = [python, str(EVENT_ROUTE_SCRIPT), "--run-dir", str(run_dir)]
    for history in routing.get("history_runs") or []:
        if not isinstance(history, dict) or not history.get("run_relpath"):
            raise DeliveryError("event route history entry is malformed")
        path = (PROJECT_ROOT / str(history["run_relpath"])).resolve()
        if not path.is_dir():
            raise DeliveryError(f"event route history run is unavailable: {path}")
        for key, filename in (
            ("input_manifest_sha256", "input_manifest.json"),
            ("review_package_sha256", "review_bundle/review_package.json"),
            ("human_decisions_sha256", "human_decisions.json"),
        ):
            expected = history.get(key)
            actual_path = path / filename
            if not expected or not actual_path.is_file() or sha256_file(actual_path) != expected:
                raise DeliveryError(f"event route history evidence changed: {path}/{filename}")
        command += ["--history-run", str(path)]
    stage_command(run_dir, "event_route_enrichment", command)
    output = run_dir / "review_bundle/event_routes.json"
    if not output.is_file():
        raise DeliveryError("event route enrichment did not create review_bundle/event_routes.json")
    document = read_json(output)
    if document.get("schema_version") != "review-event-routes-v1":
        raise DeliveryError("event route sidecar has an unsupported schema")
    return document


def _validate_case_memory_document(
    document: dict[str, Any],
    *,
    run_dir: Path,
    source_path: Path,
    package: dict[str, Any] | None = None,
    expected_source_sha256: str | None = None,
    expected_snapshot_sha256: str | None = None,
    expected_candidate_ids: set[str] | None = None,
    sidecar_path: Path | None = None,
    expected_sidecar_sha256: str | None = None,
) -> None:
    """Fail closed if a similar-case sidecar drifted from its frozen evidence.

    The case-memory tool is intentionally allowed to *describe* all eligible
    historic itemized decisions.  It is not allowed to create a present-tense
    decision or silently point at a different candidate/package after a UI
    refresh.
    """

    errors = validate_case_memory(document, package)
    target = document.get("target_identity") or {}
    candidate_input = document.get("candidate_input") or {}
    if target.get("run_id") != require_identity(run_dir)["run_id"]:
        errors.append("case memory target run_id does not match current run")
    if target.get("run_identity_sha256") != sha256_file(run_dir / "run_identity.json"):
        errors.append("case memory target run identity SHA does not match current run")
    if target.get("input_manifest_sha256") != sha256_file(run_dir / "input_manifest.json"):
        errors.append("case memory target input manifest SHA does not match current run")
    source_sha256 = expected_source_sha256 or sha256_file(source_path)
    if candidate_input.get("sha256") != source_sha256:
        errors.append("case memory candidate source SHA does not match frozen source")
    snapshot = document.get("snapshot") or {}
    if expected_snapshot_sha256 and snapshot.get("snapshot_manifest_sha256") != expected_snapshot_sha256:
        errors.append("case memory snapshot SHA does not match the frozen experience snapshot")
    if expected_candidate_ids is not None:
        memory_ids = {str(candidate_id) for candidate_id in (document.get("candidate_memory") or {})}
        if memory_ids != expected_candidate_ids:
            errors.append("case memory candidate IDs do not match the frozen candidate source")
    if expected_sidecar_sha256:
        if sidecar_path is None or not sidecar_path.is_file():
            errors.append("case memory sidecar is missing for SHA verification")
        elif sha256_file(sidecar_path) != expected_sidecar_sha256:
            errors.append("case memory sidecar SHA drifted from its frozen evidence")
    if errors:
        raise DeliveryError("case-memory sidecar is invalid or exceeds its authority: " + "; ".join(errors))


def build_pre_review_case_memory(
    run_dir: Path,
    *,
    python: str,
    snapshot_manifest: Path,
    source_path: Path,
) -> dict[str, Any]:
    """Create per-candidate historical references before calibration selection.

    This stage creates a root-level sidecar and only returns priority metadata
    to the in-memory candidate rows.  It never rewrites candidate_source.json.
    """

    output = run_dir / "case_memory.pre_review.json"
    stage_command(
        run_dir,
        "case_memory_pre_review",
        [
            python,
            str(CASE_MEMORY_SCRIPT),
            "--snapshot-dir",
            str(snapshot_manifest.parent),
            "--candidate-source",
            str(source_path),
            "--target-run-dir",
            str(run_dir),
            "--out",
            str(output),
        ],
    )
    if not output.is_file():
        raise DeliveryError("case-memory pre-review stage did not create its sidecar")
    document = read_json(output)
    source_ids = {
        str(row.get("candidate_id"))
        for row in read_json(source_path).get("candidates") or []
        if isinstance(row, dict)
    }
    _validate_case_memory_document(
        document,
        run_dir=run_dir,
        source_path=source_path,
        expected_snapshot_sha256=sha256_file(snapshot_manifest),
        expected_candidate_ids=source_ids,
    )
    memory_ids = {str(key) for key in (document.get("candidate_memory") or {})}
    if source_ids != memory_ids:
        raise DeliveryError("case-memory pre-review sidecar does not cover exactly the candidate source")
    return document


def build_case_memory_metadata(run_dir: Path, *, python: str) -> dict[str, Any] | None:
    """Attach a hash-bound, read-only similar-case sidecar to a new review bundle."""

    plan = read_json(run_dir / "plan.json")
    learning = plan.get("experience_learning") or {}
    snapshot_relpath = learning.get("snapshot_relpath")
    if not snapshot_relpath:
        return None
    snapshot_manifest = run_relative_path(run_dir, snapshot_relpath, "experience snapshot path")
    source_path = run_dir / "candidates/candidate_source.json"
    package_path = run_dir / "review_bundle/review_package.json"
    output = run_dir / "review_bundle/case_memory.json"
    if not snapshot_manifest.is_file() or not source_path.is_file() or not package_path.is_file():
        raise DeliveryError("case-memory inputs are missing")
    stage_command(
        run_dir,
        "case_memory_review_bundle",
        [
            python,
            str(CASE_MEMORY_SCRIPT),
            "--snapshot-dir",
            str(snapshot_manifest.parent),
            "--candidate-source",
            str(source_path),
            "--candidate-overlay",
            str(run_dir / "all_candidates.json"),
            "--target-run-dir",
            str(run_dir),
            "--review-package",
            str(package_path),
            "--out",
            str(output),
        ],
    )
    if not output.is_file():
        raise DeliveryError("case-memory review sidecar was not created")
    document = read_json(output)
    _validate_case_memory_document(
        document,
        run_dir=run_dir,
        source_path=source_path,
        package=read_json(package_path),
        expected_snapshot_sha256=sha256_file(snapshot_manifest),
    )
    return document


def integrate_approved_candidate_families(run_dir: Path, *, python: str) -> dict[str, Any] | None:
    """Run owner-approved candidate families and normalize them before policy/selection.

    This is a component-adoption step, not a semantic approval step.  The
    adapter only enables self-correction and cough; all imported rows remain
    high-risk and require a real human decision before any EDL action.
    """

    plan = read_json(run_dir / "plan.json")
    governance = plan.get("integration_governance") or {}
    enabled = set(str(item) for item in governance.get("mainline_capabilities") or [])
    wanted = {"self_correction_wordlevel", "transient_cough_detection"}
    if not (enabled & wanted):
        return None
    source_path = run_dir / "candidates/candidate_source.json"
    output_path = run_dir / "candidates/candidate_source.family_integrated.json"
    base_path = run_dir / "candidates/candidate_source.base.json"
    if not source_path.is_file():
        raise DeliveryError("candidate source is missing before candidate-family integration")
    if base_path.exists() or output_path.exists():
        raise DeliveryError("candidate-family integration artifacts already exist")
    shutil.copy2(source_path, base_path)
    analysis = run_dir / "analysis"
    self_transcripts = sorted(analysis.glob("track_*.transcript.json"))
    active_tracks = active_audio_tracks(run_dir)
    transient_wavs = {
        str(track["track_id"]): run_relative_path(run_dir, track["input_relpath"], "active audio track")
        for track in active_tracks
    }
    transient_transcripts = {
        path.stem.split(".", 1)[0]: path
        for path in self_transcripts
        if path.is_file()
    }
    command = [
        python,
        str(CANDIDATE_FAMILY_SCRIPT),
        "--base-source",
        str(base_path),
        "--out",
        str(output_path),
        "--detector-out-dir",
        str(run_dir / "candidates/family_detectors"),
        "--sample-rate-hz",
        str(read_json(run_dir / "input_manifest.json")["sample_rate_hz"]),
    ]
    if "self_correction_wordlevel" in enabled:
        for track_id, path in sorted(
            (str(path.stem.split(".", 1)[0]), path) for path in self_transcripts
        ):
            command.extend(["--self-transcript", f"{track_id}={path}"])
    if "transient_cough_detection" in enabled:
        for track_id, path in sorted(transient_wavs.items()):
            command.extend(["--transient-wav", f"{track_id}={path}"])
            transcript = transient_transcripts.get(track_id)
            if transcript is not None:
                command.extend(["--transient-transcript", f"{track_id}={transcript}"])
    stage_command(run_dir, "candidate_family_integration", command)
    if not output_path.is_file():
        raise DeliveryError("candidate-family adapter did not emit its normalized source")
    shutil.move(str(output_path), str(source_path))
    integrated = read_json(source_path).get("candidate_family_integration") or {}
    if integrated.get("schema_version") != "candidate-family-integration-v1":
        raise DeliveryError("candidate-family adapter emitted an unsupported integration schema")
    return integrated


def build_candidates_and_review(
    run_dir: Path, *, python: str, ffmpeg: str
) -> None:
    identity = require_identity(run_dir)
    if not (run_dir / "analysis/p0_mvp_report.json").is_file():
        raise DeliveryError("analysis P0 report is missing")
    plan = read_json(run_dir / "plan.json")
    candidate_strategy = plan.get("candidate_strategy") or {}
    rules_path = run_relative_path(
        run_dir, candidate_strategy.get("rules_relpath"), "candidate rules path"
    )
    if not rules_path.is_file() or sha256_file(rules_path) != candidate_strategy.get("rules_sha256"):
        raise DeliveryError("frozen candidate rules changed or are unavailable")
    candidates_dir = run_dir / "candidates"
    if candidates_dir.exists():
        raise DeliveryError("candidates directory already exists")
    command = [
        python,
        str(CANDIDATE_SCRIPT),
        "--p0-report",
        str(run_dir / "analysis/p0_mvp_report.json"),
        "--out",
        str(candidates_dir),
        "--episode-id",
        str(identity["episode_id"]),
        "--rules",
        str(rules_path),
        "--semantic-dir",
        str(run_dir / "analysis/semantic-transcript-v1/semantic_transcripts"),
    ]
    stage_command(run_dir, "candidate_generation", command)
    source_path = candidates_dir / "candidate_source.json"
    blocked_path = candidates_dir / "blocked_candidates.json"
    if not source_path.is_file() or not blocked_path.is_file():
        raise DeliveryError("candidate generator did not emit the expected source packages")
    source = read_json(source_path)
    candidates = source.get("candidates")
    if not isinstance(candidates, list):
        raise DeliveryError("candidate source has no candidate array")
    candidate_family_integration = integrate_approved_candidate_families(
        run_dir, python=python
    )
    if candidate_family_integration is not None:
        source = read_json(source_path)
        candidates = source.get("candidates")
        if not isinstance(candidates, list):
            raise DeliveryError("candidate family integration removed candidate array")
    source_rules_sha = (source.get("input_provenance") or {}).get("rules_sha256")
    if source_rules_sha != candidate_strategy.get("rules_sha256"):
        raise DeliveryError("candidate source does not bind this run's frozen candidate rules")
    editing_policy_config = plan.get("editing_policy") or {}
    editing_policy_path = run_relative_path(
        run_dir, editing_policy_config.get("relpath"), "editing policy path"
    )
    expected_editing_policy_sha = editing_policy_config.get("sha256")
    if not editing_policy_path.is_file() or sha256_file(editing_policy_path) != expected_editing_policy_sha:
        raise DeliveryError("frozen editing policy changed or is unavailable")
    try:
        active_editing_policy = load_editing_policy(editing_policy_path)
        candidates, policy_application = apply_editing_policy(candidates, active_editing_policy)
    except ValueError as exc:
        raise DeliveryError(f"editing policy application failed: {exc}") from exc
    policy_application.update(
        {
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "policy_relpath": "frozen/editing_policy.json",
            "policy_sha256": expected_editing_policy_sha,
            "candidate_source_relpath": "candidates/candidate_source.json",
            "candidate_source_sha256": sha256_file(source_path),
        }
    )
    write_json(run_dir / "policy_application.json", policy_application)
    # Apply an immutable preference snapshot before selecting representative
    # samples.  The helper only adds review-priority evidence; it cannot write
    # a decision, EDL, audio, or autocut permission.
    learning = plan.get("experience_learning") or {}
    snapshot_relpath = learning.get("snapshot_relpath")
    experience_report: dict[str, Any] | None = None
    label_learning_application: dict[str, Any] | None = None
    post_boundary_label_learning_application: dict[str, Any] | None = None
    snapshot_manifest: Path | None = None
    case_memory_pre_review: dict[str, Any] | None = None
    case_memory_review_bundle: dict[str, Any] | None = None
    if snapshot_relpath:
        snapshot_manifest = run_relative_path(run_dir, snapshot_relpath, "experience snapshot path")
        if not snapshot_manifest.is_file():
            raise DeliveryError("frozen experience snapshot is missing")
        report_path = run_dir / "experience_application_report.json"
        stage_command(
            run_dir,
            "experience_snapshot_application",
            [
                python,
                str(APPLY_PREFERENCE_SCRIPT),
                "--snapshot-dir",
                str(snapshot_manifest.parent),
                "--run-dir",
                str(run_dir),
                "--out",
                str(report_path),
            ],
        )
        experience_report = read_json(report_path)
        signal_by_id = {
            str(item.get("candidate_id")): item.get("experience_signal")
            for item in experience_report.get("candidates") or []
        }
        for candidate in candidates:
            candidate["experience_signal"] = signal_by_id.get(str(candidate.get("candidate_id")), {
                "signal": "no_matching_history",
                "review_priority": 0,
                "case_ids": [],
                "policy": "review_priority_only; no decision, no auto-cut, no filtering",
            })
        if not LABEL_LEARNING_DRIVER_SCRIPT.is_file():
            raise DeliveryError("label learning driver is missing")
        driver_path = run_dir / "label_learning_application.pre_review.json"
        stage_command(
            run_dir,
            "label_learning_pre_review",
            [
                python,
                str(LABEL_LEARNING_DRIVER_SCRIPT),
                "predict",
                "--snapshot-dir",
                str(snapshot_manifest.parent),
                "--candidate-source",
                str(source_path),
                "--input-manifest",
                str(run_dir / "input_manifest.json"),
                "--target-run-dir",
                str(run_dir),
                "--exclude-run",
                str(identity["run_id"]),
                "--target-run-id",
                str(identity["run_id"]),
                "--out",
                str(driver_path),
            ],
        )
        label_learning_application = read_json(driver_path)
        candidate_input = label_learning_application.get("candidate_input") or {}
        target_identity = label_learning_application.get("target_identity") or {}
        driver_policy = label_learning_application.get("policy") or {}
        prediction_rows = label_learning_application.get("predictions") or []
        if (
            label_learning_application.get("schema_version") != "label-learning-prediction-v1"
            or candidate_input.get("sha256") != sha256_file(source_path)
            or target_identity.get("run_id") != identity["run_id"]
            or target_identity.get("run_identity_sha256") != sha256_file(run_dir / "run_identity.json")
            or target_identity.get("input_manifest_sha256") != sha256_file(run_dir / "input_manifest.json")
            or not driver_policy.get("never_creates_human_decision")
            or not driver_policy.get("never_creates_edl")
            or not driver_policy.get("never_creates_autocut_permission")
        ):
            raise DeliveryError("label learning application is invalid or exceeds its authority")
        prediction_by_id = {str(item.get("candidate_id")): item for item in prediction_rows if isinstance(item, dict)}
        source_ids = {str(item.get("candidate_id")) for item in source.get("candidates") or [] if isinstance(item, dict)}
        if set(prediction_by_id) != source_ids:
            raise DeliveryError("label learning application does not cover exactly the candidate source")
        if any(
            item.get("creates_human_decision")
            or item.get("creates_edl_action")
            or item.get("creates_autocut_permission")
            for item in prediction_by_id.values()
        ):
            raise DeliveryError("label learning driver attempted an unauthorized decision or EDL action")
        for candidate in candidates:
            candidate["label_learning_prediction"] = prediction_by_id[str(candidate["candidate_id"])]
        # Case memory is a separate, human-readable retrieval aid.  Unlike the
        # generic pattern driver it keeps legacy identity-incomplete cases
        # visible (and clearly labeled) so a reviewer can learn from all 65
        # frozen decisions without mistaking them for independent auto-cut
        # evidence.
        case_memory_pre_review = build_pre_review_case_memory(
            run_dir,
            python=python,
            snapshot_manifest=snapshot_manifest,
            source_path=source_path,
        )
        case_memory_by_id = case_memory_pre_review.get("candidate_memory") or {}
        for candidate in candidates:
            candidate["case_memory_signal"] = case_memory_by_id.get(
                str(candidate.get("candidate_id")),
                {
                    "signal": "no_similar_case",
                    "review_priority": 0,
                    "similar_case_count": 0,
                    "matches": [],
                    "policy": "reference_and_review_priority_only; never creates a current decision, EDL action or autocut permission",
                },
            )
    review_budget = (plan.get("review_strategy") or {}).get("max_human_review_items")
    selected_ids, selection_report = select_calibration_candidates(
        candidates, str(plan["random_seed"]), review_budget=review_budget
    )
    selected_set = set(selected_ids)
    all_rows = []
    for candidate in candidates:
        row = {
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": sha256_bytes(candidate),
            "candidate_kind": candidate.get("candidate_kind"),
            "reason_key": candidate.get("reason_key"),
            "risk_level": risk_level(candidate),
            "stratum": stratum_for(candidate) if risk_level(candidate) == "low" else None,
            "selected_for_calibration": candidate["candidate_id"] in selected_set,
            "safety_status": candidate.get("safety_status"),
            "start_sample": candidate.get("start_sample"),
            "end_sample": candidate.get("end_sample"),
            "preference_profile_id": PREFERENCE_ID,
            "preference_rules": PREFERENCE_ID,
            "candidate_rules_version": candidate_strategy.get("rules_version"),
            "candidate_rules_sha256": candidate_strategy.get("rules_sha256"),
            "experience_signal": candidate.get("experience_signal"),
            "label_learning_prediction": candidate.get("label_learning_prediction"),
            "case_memory_signal": candidate.get("case_memory_signal"),
            "editing_policy": candidate.get("editing_policy"),
        }
        all_rows.append(row)
    blocked = read_json(blocked_path)
    write_json(
        run_dir / "all_candidates.json",
        {
            "schema_version": "delivery-all-candidates-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "candidate_source_relpath": "candidates/candidate_source.json",
            "candidate_source_sha256": sha256_file(source_path),
            "blocked_candidates_relpath": "candidates/blocked_candidates.json",
            "blocked_candidates_sha256": sha256_file(blocked_path),
            "preference_profile_id": PREFERENCE_ID,
            "candidate_strategy": candidate_strategy,
            "candidate_family_integration": candidate_family_integration,
            "review_strategy": plan.get("review_strategy"),
            "candidates": all_rows,
            "blocked_count": len(blocked.get("candidates") or []),
            "candidate_coverage": plan["candidate_coverage"],
            "experience_learning": learning,
            "label_learning_application": {
                "relpath": "label_learning_application.pre_review.json" if label_learning_application else None,
                "sha256": sha256_file(run_dir / "label_learning_application.pre_review.json") if label_learning_application else None,
                "prediction_counts": label_learning_application.get("prediction_counts") if label_learning_application else None,
                "scope": "machine suggestions and review ordering only; never a human decision, EDL or autocut policy",
            },
            "case_memory": {
                "pre_review_relpath": "case_memory.pre_review.json" if case_memory_pre_review else None,
                "pre_review_sha256": sha256_file(run_dir / "case_memory.pre_review.json") if case_memory_pre_review else None,
                "summary": case_memory_pre_review.get("memory_summary") if case_memory_pre_review else None,
                "scope": "similar historical human cases and review ordering only; never a human decision, EDL or autocut policy",
            },
            "editing_policy": {
                "relpath": "policy_application.json",
                "sha256": sha256_file(run_dir / "policy_application.json"),
                "summary": policy_application.get("summary"),
                "autocut_policy": policy_application.get("autocut_policy"),
            },
        },
    )
    calibration_source = dict(source)
    calibration_source["candidates"] = sorted(
        [item for item in candidates if item["candidate_id"] in selected_set],
        key=lambda item: (
            0 if risk_level(item) == "high" else 1,
            -learning_review_priority(item),
            deterministic_rank(str(plan["random_seed"]), str(item["candidate_id"])),
            str(item["candidate_id"]),
        ),
    )
    calibration_source["delivery_calibration_selection"] = {
        "all_candidate_source_sha256": sha256_file(source_path),
        "random_seed": plan["random_seed"],
        "selection_report": selection_report,
        "high_risk_policy": "all high-risk candidates are selected",
        "auto_preserve_policy": "active policy guards are recorded but excluded from review, prediction and every EDL",
    }
    calibration_source["run_id"] = identity["run_id"]
    write_json(run_dir / "calibration_source.json", calibration_source)
    review_config = {
        "schema_version": "review-episode-config-v1",
        "episode_id": identity["episode_id"],
        "source_package": "calibration_source.json",
        "tracks_manifest": "candidates/tracks.manifest.json",
        "previews_dir": "candidates/previews",
        "frontend": str(REVIEW_FRONTEND),
        "run_dir": ".",
        "ffmpeg": ffmpeg,
        "port": 8771,
        "event_routes": {
            "schema_version": "review-event-routes-v1",
            "metadata_relpath": "review_bundle/event_routes.json",
            "history_runs": (plan.get("event_routing") or {}).get("history_runs") or [],
            "policy": "metadata_only; historical decisions never become current human decisions",
        },
        "label_learning": {
            "schema_version": "label-learning-prediction-v1",
            "pre_review_relpath": "label_learning_application.pre_review.json",
            "post_boundary_relpath": "review_bundle/label_learning_application.post_boundary.json",
            "policy": "machine suggestions only; never a human decision, EDL action or autocut permission",
        },
        "case_memory": {
            "schema_version": "case-memory-v1",
            "pre_review_relpath": "case_memory.pre_review.json",
            "review_bundle_relpath": "review_bundle/case_memory.json",
            "policy": "similar historical human cases and review ordering only; never a human decision, EDL action or autocut permission",
        },
    }
    write_json(run_dir / "review-episode-config.json", review_config)
    # V19 边界精修 + 剪口质量预测 (2026-08-15)：候选边界从 ASR 词级时间戳精修到静音区/零交叉点，
    # 剪出来更自然；每条候选打 artifact_risk_score，前端可看到"剪辑痕迹风险"。
    # 依据：65 条真人历史决定中 8+ 条 "剪辑痕迹" reject 的边界特征归纳。
    # 详见 skills/editing-experience-distiller/output/preferences-20260815-1330/preferences_for_agent.md
    # 顺序：先 snap（更新 calibration_source 边界）→ 再 build_calibration_package（用新边界生成 preview）→ 最后 predict（读 review_package 的 clause_position 精细打分）
    stage_command(
        run_dir,
        "snap_candidate_boundaries",
        [python, str(SNAP_BOUNDARIES_SCRIPT), "--run-dir", str(run_dir)],
    )
    stage_command(
        run_dir,
        "build_calibration_package",
        [python, str(REVIEW_SERVER), "--config", str(run_dir / "review-episode-config.json"), "--build-only"],
    )
    stage_command(
        run_dir,
        "predict_cut_artifact",
        [python, str(PREDICT_ARTIFACT_SCRIPT), "--run-dir", str(run_dir)],
    )
    if snapshot_relpath and label_learning_application is not None:
        post_boundary_path = run_dir / "review_bundle/label_learning_application.post_boundary.json"
        stage_command(
            run_dir,
            "label_learning_post_boundary",
            [
                python,
                str(LABEL_LEARNING_DRIVER_SCRIPT),
                "predict",
                "--snapshot-dir",
                str(snapshot_manifest.parent),
                "--candidate-source",
                str(source_path),
                "--candidate-overlay",
                str(run_dir / "all_candidates.json"),
                "--input-manifest",
                str(run_dir / "input_manifest.json"),
                "--target-run-dir",
                str(run_dir),
                "--exclude-run",
                str(identity["run_id"]),
                "--target-run-id",
                str(identity["run_id"]),
                "--out",
                str(post_boundary_path),
            ],
        )
        post_boundary_label_learning_application = read_json(post_boundary_path)
        post_target_identity = post_boundary_label_learning_application.get("target_identity") or {}
        post_policy = post_boundary_label_learning_application.get("policy") or {}
        if (
            post_boundary_label_learning_application.get("schema_version") != "label-learning-prediction-v1"
            or (post_boundary_label_learning_application.get("candidate_input") or {}).get("sha256") != sha256_file(source_path)
            or post_target_identity.get("run_id") != identity["run_id"]
            or post_target_identity.get("run_identity_sha256") != sha256_file(run_dir / "run_identity.json")
            or post_target_identity.get("input_manifest_sha256") != sha256_file(run_dir / "input_manifest.json")
            or not post_policy.get("never_creates_human_decision")
            or not post_policy.get("never_creates_edl")
            or not post_policy.get("never_creates_autocut_permission")
        ):
            raise DeliveryError("post-boundary label learning application is invalid or exceeds its authority")
    package = run_dir / "review_bundle/review_package.json"
    if not package.is_file():
        raise DeliveryError("review package was not created")
    case_memory_review_bundle = build_case_memory_metadata(run_dir, python=python)
    event_route_metadata = build_event_route_metadata(run_dir, python=python)
    build_listening_checks(run_dir, ffmpeg=ffmpeg, candidates=candidates)
    write_offline_review_packet(run_dir)
    transition(
        run_dir,
        "CANDIDATES_FROZEN",
        f"frozen {len(candidates)} reviewable candidates; {len(selected_ids)} selected for calibration (budget={review_budget})",
    )
    transition(
        run_dir,
        "CALIBRATION_REVIEW_REQUIRED",
        "review high-risk candidates and representative low-risk samples using review_packet.md or review_bundle",
    )
    write_json(
        run_dir / "preference_application_report.json",
        {
            "schema_version": "preference-application-report-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "profile_id": PREFERENCE_ID,
            "profile_sha256": sha256_file(run_dir / "frozen/editing_preference_profile.md"),
            "used_for": ["candidate nomination", "calibration ordering", "audition rendering parameters"],
            "not_used_for": ["human labels", "high-risk auto-cut", "autocut policy"],
            "music_template_id": plan["music"]["music_template_id"],
            "coverage_gaps": plan["candidate_coverage"]["not_connected"],
            "candidate_rules": candidate_strategy,
            "candidate_family_integration": candidate_family_integration,
            "review_budget": review_budget,
            "experience_learning": learning,
            "experience_application_report": experience_report,
            "label_learning_application": {
                "pre_review_relpath": "label_learning_application.pre_review.json" if label_learning_application else None,
                "pre_review_sha256": sha256_file(run_dir / "label_learning_application.pre_review.json") if label_learning_application else None,
                "pre_review_counts": label_learning_application.get("prediction_counts") if label_learning_application else None,
                "post_boundary_relpath": "review_bundle/label_learning_application.post_boundary.json" if post_boundary_label_learning_application else None,
                "post_boundary_sha256": sha256_file(run_dir / "review_bundle/label_learning_application.post_boundary.json") if post_boundary_label_learning_application else None,
                "post_boundary_counts": post_boundary_label_learning_application.get("prediction_counts") if post_boundary_label_learning_application else None,
                "scope": "machine suggestions and review ordering only; never a human decision, EDL or autocut policy",
            },
            "case_memory": {
                "pre_review_relpath": "case_memory.pre_review.json" if case_memory_pre_review else None,
                "pre_review_sha256": sha256_file(run_dir / "case_memory.pre_review.json") if case_memory_pre_review else None,
                "pre_review_summary": case_memory_pre_review.get("memory_summary") if case_memory_pre_review else None,
                "review_bundle_relpath": "review_bundle/case_memory.json" if case_memory_review_bundle else None,
                "review_bundle_sha256": sha256_file(run_dir / "review_bundle/case_memory.json") if case_memory_review_bundle else None,
                "review_bundle_summary": case_memory_review_bundle.get("memory_summary") if case_memory_review_bundle else None,
                "scope": "similar historical human cases and review ordering only; never a human decision, EDL or autocut policy",
            },
            "editing_policy": {
                "policy_id": active_editing_policy["policy_id"],
                "policy_sha256": expected_editing_policy_sha,
                "application_relpath": "policy_application.json",
                "application_sha256": sha256_file(run_dir / "policy_application.json"),
                "summary": policy_application.get("summary"),
                "autocut_policy": policy_application.get("autocut_policy"),
                "scope": "preserve/review guards only; no auto_cut_eligible actions under current policy",
            },
            "event_routes": {
                "relpath": "review_bundle/event_routes.json",
                "sha256": sha256_file(run_dir / "review_bundle/event_routes.json"),
                "summary": event_route_metadata.get("route_summary") or {},
                "policy": "metadata_only; no current human decision, EDL or autocut permission",
            },
        },
    )


def review_server_command(run_dir: Path, *, python: str, port: int | None, no_open: bool) -> list[str]:
    require_identity(run_dir)
    require_state(run_dir, "CALIBRATION_REVIEW_REQUIRED")
    command = [python, str(REVIEW_SERVER), "--config", str(run_dir / "review-episode-config.json")]
    if port is not None:
        command += ["--port", str(port)]
    if no_open:
        command.append("--no-open")
    return command


def _review_candidate_fingerprint(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Identity-bearing fields that a UI-only package refresh must not change."""

    return [
        {
            "candidate_id": item.get("candidate_id"),
            "source_track_id": item.get("source_track_id"),
            "start_sample": item.get("start_sample"),
            "end_sample": item.get("end_sample"),
            "reason_key": item.get("reason_key"),
        }
        for item in package.get("candidates") or []
        if isinstance(item, dict)
    ]


def refresh_review_package(run_dir: Path, *, python: str, ffmpeg: str, reason: str) -> dict[str, Any]:
    """Replace an unreviewed package only after preserving the old evidence.

    Review UI, semantic display and feedback-capture changes must never be
    silently written over a hash-bound package.  This operation is intentionally
    limited to the pre-review state: it archives the old bundle, rebuilds from
    the same frozen candidate source, checks candidate boundaries are unchanged,
    and writes a revision record.  It never reruns denoise, ASR or candidates.
    """

    identity = require_identity(run_dir)
    require_state(run_dir, "CALIBRATION_REVIEW_REQUIRED")
    if (run_dir / "human_decisions.json").exists():
        raise DeliveryError("cannot refresh a review package after final human decisions exist")
    if (run_dir / "review_draft.json").exists():
        raise DeliveryError("cannot refresh while a reviewer draft exists; preserve or submit the draft first")
    bundle = run_dir / "review_bundle"
    package_path = bundle / "review_package.json"
    config_path = run_dir / "review-episode-config.json"
    if not package_path.is_file() or not config_path.is_file():
        raise DeliveryError("review package/config is missing; cannot create an auditable UI revision")
    old_package = read_json(package_path)
    old_fingerprint = _review_candidate_fingerprint(old_package)
    old_bundle_sha = sha256_file(package_path)
    source_sha = sha256_file(run_dir / "calibration_source.json")
    revisions_root = run_dir / "review_bundle_revisions"
    revision_index = len([path for path in revisions_root.glob("*") if path.is_dir()]) + 1
    revision_dir = revisions_root / f"{revision_index:02d}-superseded-{old_package.get('review_manifest_sha256', '')[:12]}"
    archived_bundle = revision_dir / "review_bundle"
    if revision_dir.exists():
        raise DeliveryError(f"review package revision destination already exists: {revision_dir.name}")
    revisions_root.mkdir(parents=True, exist_ok=True)
    revision_dir.mkdir()
    shutil.move(str(bundle), str(archived_bundle))
    try:
        stage_command(
            run_dir,
            "refresh_calibration_package",
            [python, str(REVIEW_SERVER), "--config", str(config_path), "--build-only"],
        )
        new_package_path = run_dir / "review_bundle/review_package.json"
        if not new_package_path.is_file():
            raise DeliveryError("review package refresh did not create a new package")
        new_package = read_json(new_package_path)
        if _review_candidate_fingerprint(new_package) != old_fingerprint:
            raise DeliveryError("review package refresh changed candidate identity or boundary")
        if sha256_file(run_dir / "calibration_source.json") != source_sha:
            raise DeliveryError("review package refresh changed the frozen calibration source")
        event_route_metadata = build_event_route_metadata(run_dir, python=python)
        # Re-attach non-semantic candidate diagnostics after rebuilding the UI.
        # The predictor is deliberately downstream-only: it may update the
        # risk annotation and blocked provenance SHA, but it cannot alter the
        # frozen candidate source, review scope, or human decisions.
        stage_command(
            run_dir,
            "refresh_predict_cut_artifact",
            [python, str(PREDICT_ARTIFACT_SCRIPT), "--run-dir", str(run_dir)],
        )
        case_memory_metadata = build_case_memory_metadata(run_dir, python=python)
        preference_report_path = run_dir / "preference_application_report.json"
        if not preference_report_path.is_file():
            raise DeliveryError("preference application report is missing during review refresh")
        preference_report = read_json(preference_report_path)
        case_memory_report = dict(preference_report.get("case_memory") or {})
        case_memory_report.update(
            {
                "review_bundle_relpath": "review_bundle/case_memory.json" if case_memory_metadata else None,
                "review_bundle_sha256": sha256_file(run_dir / "review_bundle/case_memory.json") if case_memory_metadata else None,
                "review_bundle_summary": case_memory_metadata.get("memory_summary") if case_memory_metadata else None,
            }
        )
        preference_report["case_memory"] = case_memory_report
        write_json(preference_report_path, preference_report)
        all_candidates = read_json(run_dir / "candidates/candidate_source.json").get("candidates") or []
        build_listening_checks(run_dir, ffmpeg=ffmpeg, candidates=all_candidates)
        write_offline_review_packet(run_dir)
    except Exception:
        partial = run_dir / "review_bundle"
        if partial.exists():
            shutil.rmtree(partial)
        shutil.move(str(archived_bundle), str(bundle))
        raise

    revision = {
        "schema_version": "delivery-review-package-revision-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "reason": reason,
        "scope": "review UI/context/feedback only; frozen audio, ASR, semantic source and candidate boundaries were not regenerated",
        "superseded_bundle_relpath": relative_to_run(run_dir, archived_bundle),
        "superseded_review_package_sha256": old_bundle_sha,
        "superseded_review_manifest_sha256": old_package.get("review_manifest_sha256"),
        "superseded_ui_sha256": old_package.get("ui_sha256"),
        "current_bundle_relpath": "review_bundle",
        "current_review_package_sha256": sha256_file(run_dir / "review_bundle/review_package.json"),
        "current_review_manifest_sha256": new_package.get("review_manifest_sha256"),
        "current_ui_sha256": new_package.get("ui_sha256"),
        "current_event_routes_sha256": sha256_file(run_dir / "review_bundle/event_routes.json"),
        "event_route_summary": event_route_metadata.get("route_summary") or {},
        "current_case_memory_sha256": sha256_file(run_dir / "review_bundle/case_memory.json") if case_memory_metadata else None,
        "case_memory_summary": case_memory_metadata.get("memory_summary") if case_memory_metadata else None,
        "calibration_source_sha256": source_sha,
        "candidate_fingerprint": old_fingerprint,
    }
    write_json(revision_dir / "revision.json", revision)
    write_json(run_dir / "review_package_revision.json", revision)
    return revision


def validate_human_decisions(run_dir: Path, source_candidates: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = run_dir / "human_decisions.json"
    if not path.is_file():
        raise DeliveryError("human_decisions.json is missing; complete the review page first")
    raw = read_json(path)
    reviewer = str(raw.get("reviewer", "")).strip()
    prohibited = ("AUTOMATED_", "LEARNED_", "MACHINE", "AGENT")
    if not reviewer or reviewer.upper().startswith(prohibited):
        raise DeliveryError("human reviewer is missing or looks like an automated reviewer")
    decisions = raw.get("decisions")
    if not isinstance(decisions, list):
        raise DeliveryError("human_decisions.decisions must be an array")
    package_path = run_dir / "review_bundle/review_package.json"
    if not package_path.is_file():
        raise DeliveryError("review package is missing while validating human decisions")
    package = read_json(package_path)
    if raw.get("package_id") != package.get("package_id"):
        raise DeliveryError("human decisions package_id does not match the current review package")
    if raw.get("review_manifest_sha256") != package.get("review_manifest_sha256"):
        raise DeliveryError("human decisions review manifest does not match the current review package")
    package_candidates = {
        str(item.get("candidate_id")): item
        for item in package.get("candidates") or []
        if isinstance(item, dict)
    }
    expected = set(source_candidates)
    actual = [str(item.get("candidate_id", "")) for item in decisions if isinstance(item, dict)]
    if set(actual) != expected or len(actual) != len(set(actual)):
        raise DeliveryError("human decisions must cover exactly the calibration package, once each")
    normalized: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise DeliveryError("invalid human decision row")
        candidate_id = str(decision["candidate_id"])
        value = str(decision.get("decision", ""))
        if value not in {"accept", "reject"}:
            raise DeliveryError(f"invalid decision for {candidate_id}: {value}")
        feedback = decision.get("feedback", "")
        if not isinstance(feedback, str):
            raise DeliveryError(f"feedback must be a string for {candidate_id}")
        if len(feedback) > 500:
            raise DeliveryError(f"feedback exceeds 500 characters for {candidate_id}")
        candidate = source_candidates[candidate_id]
        package_candidate = package_candidates.get(candidate_id)
        if package_candidate is None:
            raise DeliveryError(f"candidate {candidate_id} is not in the current review package")
        if decision.get("candidate_semantic_sha256") != package_candidate.get("semantic_sha256"):
            raise DeliveryError(f"candidate semantic SHA mismatch for {candidate_id}")
        if risk_level(candidate) == "high":
            listened = decision.get("listened_previews") or {}
            if not listened.get("original_sha256") or not listened.get("proposed_cut_sha256"):
                raise DeliveryError(f"high-risk {candidate_id} requires original and proposed A/B listening evidence")
        normalized.append(
            {
                "candidate_id": candidate_id,
                "candidate_sha256": sha256_bytes(candidate),
                "decision": f"human_{value}",
                "reviewed_at": decision.get("decided_at"),
                "review_basis": decision.get("review_basis"),
                "listened_previews": decision.get("listened_previews") or {},
                "feedback": feedback.strip(),
                "decision_provenance": "human_individual_review",
            }
        )
    return raw, normalized


def merge_sync_actions(actions: list[dict[str, Any]], frame_count: int, sample_rate: int) -> list[dict[str, Any]]:
    ordered = sorted(actions, key=lambda item: (int(item["start_sample"]), int(item["end_sample"]), item["action_id"]))
    merged: list[dict[str, Any]] = []
    for action in ordered:
        start, end = int(action["start_sample"]), int(action["end_sample"])
        if not (0 <= start < end <= frame_count):
            raise DeliveryError(f"EDL action out of input range: {action['action_id']}")
        if not merged or start > int(merged[-1]["end_sample"]):
            merged.append({"start_sample": start, "end_sample": end, "source_action_ids": [action["action_id"]]})
        else:
            merged[-1]["end_sample"] = max(int(merged[-1]["end_sample"]), end)
            merged[-1]["source_action_ids"].append(action["action_id"])
    for index, cut in enumerate(merged):
        left_start = 0 if index == 0 else int(merged[index - 1]["end_sample"])
        right_end = frame_count if index == len(merged) - 1 else int(merged[index + 1]["start_sample"])
        allowed = min(
            int(sample_rate * 0.20),
            (int(cut["end_sample"]) - int(cut["start_sample"])) // 2,
            (int(cut["start_sample"]) - left_start) // 2,
            (right_end - int(cut["end_sample"])) // 2,
        )
        cut["crossfade_samples"] = max(0, allowed)
        cut["crossfade_ms"] = round(cut["crossfade_samples"] * 1000 / sample_rate, 3)
    return merged


def edl_document(
    *,
    run_dir: Path,
    variant: str,
    actions: list[dict[str, Any]],
    decision_summary: dict[str, int],
    source_track_gates: list[dict[str, Any]] | None = None,
    whole_episode_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = require_identity(run_dir)
    input_manifest = read_json(run_dir / "input_manifest.json")
    sample_rate = int(input_manifest["sample_rate_hz"])
    frame_count = int(input_manifest["frame_count"])
    merged = merge_sync_actions(actions, frame_count, sample_rate)
    result: dict[str, Any] = {
        "schema_version": "delivery-edl-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "variant": variant,
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "tracks": [
            {"track_id": track["track_id"], "input_relpath": track["input_relpath"], "audio_sha256": track["audio_sha256"]}
            for track in active_audio_tracks(run_dir)
        ],
        "global_sync_actions": actions,
        "render_sync_cuts": merged,
        "source_track_gates": list(source_track_gates or []),
        "decision_summary": decision_summary,
        "autocut_policy": dict(
            read_json(run_dir / "plan.json").get("autocut_policy")
            or {"id": "NOT_APPROVED", "status": "NOT_APPROVED"}
        ),
    }
    if whole_episode_scope is not None:
        result["approval_mode"] = "human_whole_episode_audition"
        result["whole_episode_approval_scope"] = whole_episode_scope
    return result


def calibration_and_predictions(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    identity = require_identity(run_dir)
    require_state(run_dir, "CALIBRATION_REVIEW_REQUIRED")
    source = read_json(run_dir / "calibration_source.json")
    all_source = read_json(run_dir / "candidates/candidate_source.json")
    calibration_candidates = {str(item["candidate_id"]): item for item in source.get("candidates") or []}
    plan = read_json(run_dir / "plan.json")
    learning = plan.get("experience_learning") or {}
    if learning.get("snapshot_relpath"):
        application_path = run_dir / "label_learning_application.pre_review.json"
        if not application_path.is_file():
            raise DeliveryError("label learning application is missing before calibration")
        application = read_json(application_path)
        application_policy = application.get("policy") or {}
        expected_source_sha = sha256_file(run_dir / "candidates/candidate_source.json")
        # Boundary snapping is allowed to rewrite the canonical candidate
        # source after the pre-review prediction.  Validate the prediction
        # against the source SHA frozen in all_candidates.json, while the
        # post-boundary sidecar is checked against the current source later.
        all_candidates_manifest = read_json(run_dir / "all_candidates.json")
        snapshot_manifest = run_relative_path(
            run_dir,
            learning.get("snapshot_relpath"),
            "experience snapshot path",
        )
        expected_snapshot_sha = learning.get("snapshot_sha256")
        if (
            not isinstance(expected_snapshot_sha, str)
            or not snapshot_manifest.is_file()
            or sha256_file(snapshot_manifest) != expected_snapshot_sha
        ):
            raise DeliveryError("frozen experience snapshot drifted before calibration")
        pre_review_source_sha = (
            all_candidates_manifest.get("candidate_source_sha256_before_boundary_snap")
            or all_candidates_manifest.get("candidate_source_sha256")
            or expected_source_sha
        )
        prediction_ids = {
            str(row.get("candidate_id")) for row in application.get("predictions") or [] if isinstance(row, dict)
        }
        source_ids = {
            str(row.get("candidate_id")) for row in all_source.get("candidates") or [] if isinstance(row, dict)
        }
        drift_reasons: list[str] = []
        if application.get("schema_version") != "label-learning-prediction-v1":
            drift_reasons.append("schema")
        if (application.get("candidate_input") or {}).get("sha256") != pre_review_source_sha:
            drift_reasons.append("candidate_source_sha256")
        if prediction_ids != source_ids:
            drift_reasons.append(
                f"candidate_ids(predictions={len(prediction_ids)}, source={len(source_ids)})"
            )
        if not application_policy.get("never_creates_human_decision"):
            drift_reasons.append("human_decision_authority")
        if not application_policy.get("never_creates_edl"):
            drift_reasons.append("edl_authority")
        if not application_policy.get("never_creates_autocut_permission"):
            drift_reasons.append("autocut_authority")
        if drift_reasons:
            raise DeliveryError(
                "label learning application drifted or exceeds its authority before calibration: "
                + ",".join(drift_reasons)
            )
        # Case-memory evidence is deliberately separate from the prediction
        # driver, but it must be frozen against the same candidate source and
        # visible review package before a human decision can be resumed.
        pre_memory_path = run_dir / "case_memory.pre_review.json"
        post_memory_path = run_dir / "review_bundle/case_memory.json"
        if not pre_memory_path.is_file() or not post_memory_path.is_file():
            raise DeliveryError("case-memory sidecar is missing before calibration")
        pre_memory = read_json(pre_memory_path)
        pre_source_sha = (
            all_candidates_manifest.get("candidate_source_sha256_before_boundary_snap")
            or all_candidates_manifest.get("candidate_source_sha256")
            or expected_source_sha
        )
        pre_memory_record = all_candidates_manifest.get("case_memory") or {}
        expected_pre_memory_sha = pre_memory_record.get("pre_review_sha256")
        if not isinstance(expected_pre_memory_sha, str):
            raise DeliveryError("all-candidates manifest has no frozen case-memory pre-review SHA")
        source_ids = {
            str(row.get("candidate_id"))
            for row in all_source.get("candidates") or []
            if isinstance(row, dict)
        }
        try:
            _validate_case_memory_document(
                pre_memory,
                run_dir=run_dir,
                source_path=run_dir / "candidates/candidate_source.json",
                expected_source_sha256=pre_source_sha,
                expected_snapshot_sha256=expected_snapshot_sha,
                expected_candidate_ids=source_ids,
                sidecar_path=pre_memory_path,
                expected_sidecar_sha256=expected_pre_memory_sha,
            )
        except DeliveryError as exc:
            raise DeliveryError("case-memory pre-review sidecar drifted or exceeds its authority: " + str(exc)) from exc
        preference_report_path = run_dir / "preference_application_report.json"
        if not preference_report_path.is_file():
            raise DeliveryError("preference application report is missing before case-memory verification")
        preference_report = read_json(preference_report_path)
        memory_report = preference_report.get("case_memory") or {}
        if memory_report.get("pre_review_sha256") != expected_pre_memory_sha:
            raise DeliveryError("case-memory pre-review SHA disagrees across frozen reports")
        expected_post_memory_sha = memory_report.get("review_bundle_sha256")
        if not isinstance(expected_post_memory_sha, str):
            raise DeliveryError("preference application report has no frozen case-memory review-bundle SHA")
        _validate_case_memory_document(
            read_json(post_memory_path),
            run_dir=run_dir,
            source_path=run_dir / "candidates/candidate_source.json",
            package=read_json(run_dir / "review_bundle/review_package.json"),
            expected_snapshot_sha256=expected_snapshot_sha,
            sidecar_path=post_memory_path,
            expected_sidecar_sha256=expected_post_memory_sha,
        )
    autocut_policy = dict(plan.get("autocut_policy") or {"id": "NOT_APPROVED", "status": "NOT_APPROVED"})
    all_candidates = [item for item in all_source.get("candidates") or [] if isinstance(item, dict)]
    editing_policy_config = plan.get("editing_policy") or {}
    if editing_policy_config:
        policy_path = run_relative_path(run_dir, editing_policy_config.get("relpath"), "editing policy path")
        if not policy_path.is_file() or sha256_file(policy_path) != editing_policy_config.get("sha256"):
            raise DeliveryError("frozen editing policy changed before calibration")
        try:
            all_candidates, applied = apply_editing_policy(all_candidates, load_editing_policy(policy_path))
        except ValueError as exc:
            raise DeliveryError(f"could not reapply frozen editing policy: {exc}") from exc
        application_path = run_dir / "policy_application.json"
        if not application_path.is_file():
            raise DeliveryError("policy_application.json is missing")
        recorded = read_json(application_path)
        if recorded.get("policy_sha256") != editing_policy_config.get("sha256"):
            raise DeliveryError("policy application is not bound to the frozen policy")
        recorded_routes = {
            str(row.get("candidate_id")): row.get("route")
            for row in recorded.get("candidates") or [] if isinstance(row, dict)
        }
        actual_routes = {
            str(row.get("candidate_id")): row.get("editing_policy", {}).get("route")
            for row in all_candidates
        }
        if recorded_routes != actual_routes or recorded.get("autocut_policy") != autocut_policy:
            raise DeliveryError("policy application route/provenance drifted before calibration")
    all_candidates = [candidate for candidate in all_candidates if review_eligible(candidate)]
    if not calibration_candidates:
        raise DeliveryError("calibration source is empty; zero-candidate route is not yet implemented")
    raw, human_rows = validate_human_decisions(run_dir, calibration_candidates)
    raw_copy = run_dir / "human_decisions.raw_review_ui.json"
    if not raw_copy.exists():
        shutil.copy2(run_dir / "human_decisions.json", raw_copy)
    normalized = {
        "schema_version": "delivery-human-decisions-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "reviewer": raw["reviewer"],
        "source_review_package_relpath": "review_bundle/review_package.json",
        "source_review_package_sha256": sha256_file(run_dir / "review_bundle/review_package.json"),
        "candidate_feedback_count": sum(1 for row in human_rows if row.get("feedback")),
        "decisions": human_rows,
    }
    write_json(run_dir / "human_decisions.json", normalized)

    decisions_by_id = {row["candidate_id"]: row for row in human_rows}
    low_groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in all_candidates:
        if risk_level(candidate) == "low":
            low_groups.setdefault(stratum_for(candidate), []).append(candidate)
    group_reports: dict[str, Any] = {}
    prediction_rows: list[dict[str, Any]] = []
    for stratum, members in sorted(low_groups.items()):
        sample = [decisions_by_id[item["candidate_id"]] for item in members if item["candidate_id"] in decisions_by_id]
        accepts = sum(item["decision"] == "human_accept" for item in sample)
        rejects = sum(item["decision"] == "human_reject" for item in sample)
        if len(sample) == len(members):
            mode = "ALL_INDIVIDUALLY_REVIEWED"
        elif len(sample) < min(3, len(members)):
            mode = "INSUFFICIENT_DATA"
        elif accepts == len(sample):
            mode = "UNANIMOUS_ACCEPT"
        elif rejects == len(sample):
            mode = "UNANIMOUS_REJECT"
        else:
            mode = "MIXED_ESCALATE"
        group_reports[stratum] = {
            "population": len(members),
            "human_sample": len(sample),
            "human_accept": accepts,
            "human_reject": rejects,
            "outcome": mode,
        }
        for candidate in members:
            candidate_id = candidate["candidate_id"]
            if candidate_id in decisions_by_id:
                route = "human_decision"
                result = decisions_by_id[candidate_id]["decision"]
            elif mode == "UNANIMOUS_ACCEPT":
                route = "machine_prediction"
                result = "machine_proposed_accept"
            elif mode == "UNANIMOUS_REJECT":
                route = "machine_prediction"
                result = "machine_proposed_reject"
            else:
                route = "safety_escalation"
                result = "human_review_required"
            prediction_rows.append(
                {
                    "candidate_id": candidate_id,
                    "candidate_sha256": sha256_bytes(candidate),
                    "risk_level": "low",
                    "stratum": stratum,
                    "decision": result,
                    "decision_provenance": route,
                    "policy_id": autocut_policy.get("id", "NOT_APPROVED"),
                    "policy_status": autocut_policy.get("status", "NOT_APPROVED"),
                    "score": None,
                    "threshold": "unanimous representative sample only",
                }
            )
    for candidate in all_candidates:
        if risk_level(candidate) != "high":
            continue
        candidate_id = candidate["candidate_id"]
        if candidate_id not in decisions_by_id:
            raise DeliveryError(f"high-risk candidate escaped calibration review: {candidate_id}")
        prediction_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_sha256": sha256_bytes(candidate),
                "risk_level": "high",
                "stratum": None,
                "decision": decisions_by_id[candidate_id]["decision"],
                "decision_provenance": "human_decision",
                "policy_id": autocut_policy.get("id", "NOT_APPROVED"),
                "policy_status": autocut_policy.get("status", "NOT_APPROVED"),
                "score": None,
                "threshold": "high-risk must be individually human reviewed",
            }
        )
    report = {
        "schema_version": "delivery-calibration-report-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "reviewer": raw["reviewer"],
        "selection_source_sha256": sha256_file(run_dir / "calibration_source.json"),
        "candidate_feedback_count": sum(1 for row in human_rows if row.get("feedback")),
        "low_risk_strata": group_reports,
        "high_risk": {
            "required": sum(risk_level(item) == "high" for item in all_candidates),
            "reviewed": sum(risk_level(item) == "high" for item in calibration_candidates.values()),
            "status": "PASS",
        },
        "rule": "only unanimous representative low-risk samples produce machine_proposed_accept/reject",
    }
    write_json(run_dir / "calibration_report.json", report)
    prediction = {
        "schema_version": "delivery-prediction-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "calibration_report_relpath": "calibration_report.json",
        "calibration_report_sha256": sha256_file(run_dir / "calibration_report.json"),
        "policy": autocut_policy,
        "predictions": sorted(prediction_rows, key=lambda item: item["candidate_id"]),
    }
    write_json(run_dir / "prediction_manifest.json", prediction)

    source_by_id = {item["candidate_id"]: item for item in all_candidates}
    human_actions: list[dict[str, Any]] = []
    machine_actions: list[dict[str, Any]] = []
    human_gates: list[dict[str, Any]] = []
    machine_gates: list[dict[str, Any]] = []
    for row in prediction["predictions"]:
        candidate = source_by_id[row["candidate_id"]]
        is_source_gate = candidate.get("action_type") == "source_track_gate"
        if row["decision"] == "human_accept":
            if is_source_gate:
                gate = {
                    "action_id": f"gate-{row['candidate_id']}",
                    "action_type": "source_track_gate",
                    "candidate_id": row["candidate_id"],
                    "candidate_sha256": row["candidate_sha256"],
                    "track_id": str(candidate["source_track_id"]),
                    "start_sample": int(candidate["start_sample"]),
                    "end_sample": int(candidate["end_sample"]),
                    "operation": "mute_source_track",
                    "decision": "human_accept",
                    "decision_provenance": "human_individual_review",
                    "risk_level": row["risk_level"],
                }
                human_gates.append(gate)
                machine_gates.append(gate)
            else:
                action = {
                    "action_id": f"cut-{row['candidate_id']}",
                    "action_type": "global_sync_cut",
                    "candidate_id": row["candidate_id"],
                    "candidate_sha256": row["candidate_sha256"],
                    "start_sample": int(candidate["start_sample"]),
                    "end_sample": int(candidate["end_sample"]),
                    "applies_to_all_tracks": True,
                    "decision": "human_accept",
                    "decision_provenance": "human_individual_review",
                    "risk_level": row["risk_level"],
                }
                human_actions.append(action)
                machine_actions.append(action)
        elif row["decision"] == "machine_proposed_accept":
            if is_source_gate:
                raise DeliveryError("source-track gates cannot be machine-proposed without an explicit human decision")
            machine_actions.append(
                {
                    "action_id": f"cut-{row['candidate_id']}",
                    "action_type": "global_sync_cut",
                    "candidate_id": row["candidate_id"],
                    "candidate_sha256": row["candidate_sha256"],
                    "start_sample": int(candidate["start_sample"]),
                    "end_sample": int(candidate["end_sample"]),
                    "applies_to_all_tracks": True,
                    "decision": "machine_proposed_accept",
                    "decision_provenance": "machine_prediction",
                    "risk_level": "low",
                    "calibration_report_sha256": prediction["calibration_report_sha256"],
                    "policy_status": autocut_policy.get("status", "NOT_APPROVED"),
                }
            )
    human_edl = edl_document(
        run_dir=run_dir,
        variant="human_approved",
        actions=human_actions,
        decision_summary={
            "human_accept": len(human_actions),
            "human_source_track_gate": len(human_gates),
            "machine_proposed_accept": 0,
        },
        source_track_gates=human_gates,
    )
    machine_edl = edl_document(
        run_dir=run_dir,
        variant="machine_assisted_draft",
        actions=machine_actions,
        decision_summary={
            "human_accept": len(human_actions),
            "human_source_track_gate": len(machine_gates),
            "machine_proposed_accept": len(machine_actions) - len(human_actions),
        },
        source_track_gates=machine_gates,
    )
    write_json(run_dir / "human_approved.edl.json", human_edl)
    write_json(run_dir / "machine_assisted_draft.edl.json", machine_edl)
    transition(run_dir, "CALIBRATED", "human decisions validated; prediction manifest and dual EDLs created")
    return report, human_edl, machine_edl


def render_filter(
    cuts: list[dict[str, Any]],
    frame_count: int,
    *,
    input_label: str = "[0:a]",
    output_label: str = "[out]",
) -> str:
    if not cuts:
        return f"{input_label}anull{output_label}"
    boundaries: list[tuple[int, int]] = []
    cursor = 0
    for cut in cuts:
        boundaries.append((cursor, int(cut["start_sample"])))
        cursor = int(cut["end_sample"])
    boundaries.append((cursor, frame_count))
    split_labels = [f"[{input_label.strip('[]')}_split{index}]" for index in range(len(boundaries))]
    parts = [f"{input_label}asplit={len(boundaries)}{''.join(split_labels)}"]
    parts.extend(
        f"{split_labels[index]}atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS[s{index}]"
        for index, (start, end) in enumerate(boundaries)
    )
    current = "s0"
    for index, cut in enumerate(cuts, 1):
        output = output_label.strip("[]") if index == len(cuts) else f"x{index}"
        fade = int(cut.get("crossfade_samples") or 0)
        if fade > 0:
            parts.append(f"[{current}][s{index}]acrossfade=ns={fade}:c1=qsin:c2=qsin[{output}]")
        else:
            parts.append(f"[{current}][s{index}]concat=n=2:v=0:a=1[{output}]")
        current = output
    return ";".join(parts)


def source_track_gate_filter(
    gates: list[dict[str, Any]],
    track_id: str,
    frame_count: int,
    *,
    input_label: str = "[0:a]",
    output_label: str = "[gated]",
) -> str:
    """Mute only one physical source track over integer-sample intervals."""

    selected = [
        gate for gate in gates
        if str(gate.get("track_id")) == str(track_id)
    ]
    if not selected:
        return f"{input_label}anull{output_label}"
    selected = sorted(selected, key=lambda gate: int(gate["start_sample"]))
    parts: list[str] = []
    cursor = 0
    segments: list[str] = []
    for index, gate in enumerate(selected):
        start = int(gate["start_sample"])
        end = int(gate["end_sample"])
        if not (0 <= start < end <= frame_count):
            raise DeliveryError(f"source-track gate out of input range: {gate.get('action_id')}")
        if start < cursor:
            raise DeliveryError(f"overlapping source-track gates: {gate.get('action_id')}")
        if cursor < start:
            label = f"gate_pre_{index}"
            parts.append(
                f"{input_label}atrim=start_sample={cursor}:end_sample={start},asetpts=PTS-STARTPTS[{label}]"
            )
            segments.append(f"[{label}]")
        label = f"gate_mute_{index}"
        parts.append(
            f"{input_label}atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS,volume=0[{label}]"
        )
        segments.append(f"[{label}]")
        cursor = end
    if cursor < frame_count:
        label = "gate_tail"
        parts.append(
            f"{input_label}atrim=start_sample={cursor}:end_sample={frame_count},asetpts=PTS-STARTPTS[{label}]"
        )
        segments.append(f"[{label}]")
    parts.append("".join(segments) + f"concat=n={len(segments)}:v=0:a=1{output_label}")
    return ";".join(parts)


def loudnorm_measure(path: Path, ffmpeg: str) -> dict[str, Any]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        "loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise DeliveryError(f"loudness measurement failed: {completed.stderr[-1000:]}")
    matches = re.findall(r"\{[^{}]*?\"input_i\".*?\}", completed.stderr, flags=re.DOTALL)
    if not matches:
        raise DeliveryError("loudness measurement did not produce a JSON report")
    return json.loads(matches[-1])


def loudnorm_two_pass(source: Path, destination: Path, ffmpeg: str) -> dict[str, Any]:
    first = loudnorm_measure(source, ffmpeg)
    filter_value = (
        "loudnorm=I=-16:TP=-1:LRA=11"
        f":measured_I={first['input_i']}"
        f":measured_TP={first['input_tp']}"
        f":measured_LRA={first['input_lra']}"
        f":measured_thresh={first['input_thresh']}"
        f":offset={first['target_offset']}:linear=true:print_format=summary"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-af",
        filter_value,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise DeliveryError(f"loudnorm pass 2 failed: {completed.stderr[-1000:]}")
    actual = loudnorm_measure(destination, ffmpeg)
    return {"target": {"I": -16.0, "TP": -1.0, "LRA": 11.0}, "pass1": first, "actual_output": actual}


def render_one_variant(run_dir: Path, variant: str, ffmpeg: str) -> dict[str, Any]:
    identity = require_identity(run_dir)
    edl = read_json(run_dir / f"{variant}.edl.json")
    if edl.get("variant") != variant:
        raise DeliveryError(f"{variant} EDL variant field is inconsistent")
    if edl.get("run_identity_sha256") != sha256_file(run_dir / "run_identity.json"):
        raise DeliveryError("EDL run identity mismatch")
    input_manifest = read_json(run_dir / "input_manifest.json")
    output_dir = run_dir / f"render_{variant}"
    if output_dir.exists():
        manifest_path = output_dir / "render_manifest.json"
        if manifest_path.is_file():
            return read_json(manifest_path)
        raise DeliveryError(f"partial render directory exists: {output_dir}")
    output_dir.mkdir()
    stems = output_dir / "stems"
    stems.mkdir()
    cuts = edl.get("render_sync_cuts") or []
    source_track_gates = edl.get("source_track_gates") or []
    frame_count = int(input_manifest["frame_count"])
    stem_paths: list[Path] = []
    render_tracks = edl.get("tracks")
    if not isinstance(render_tracks, list) or not render_tracks:
        raise DeliveryError("EDL has no bound render tracks")
    for track in render_tracks:
        source = run_relative_path(run_dir, track.get("input_relpath"), "EDL input_relpath")
        if not source.is_file() or sha256_file(source) != track["audio_sha256"]:
            raise DeliveryError(f"source input changed or is unavailable: {track['track_id']}")
        output = stems / f"{track['track_id']}.edited.wav"
        gate_graph = source_track_gate_filter(
            source_track_gates,
            str(track["track_id"]),
            frame_count,
            input_label="[0:a]",
            output_label="[gated]",
        )
        filter_graph = gate_graph + ";" + render_filter(
            cuts,
            frame_count,
            input_label="[gated]",
            output_label="[out]",
        )
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-filter_complex", filter_graph, "-map", "[out]", "-ar", str(input_manifest["sample_rate_hz"]), "-ac", "1", "-c:a", "pcm_s24le", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise DeliveryError(f"stem render failed for {track['track_id']}: {completed.stderr[-1000:]}")
        stem_paths.append(output)
    plan = read_json(run_dir / "plan.json")
    mixing = plan.get("mixing") or {}
    mix_mode = str(mixing.get("mode") or "direct_mix")
    speech_mix = output_dir / "speech_mix.wav"
    automix_manifest: dict[str, Any] | None = None
    if mix_mode == "automix_v1":
        try:
            automix_manifest = run_automix_speech_mix(
                track_paths=stem_paths,
                output_path=speech_mix,
                tmp_dir=output_dir / "automix_tmp",
                run_id=str(identity["run_id"]),
                run_identity_sha256=sha256_file(run_dir / "run_identity.json"),
                variant=variant,
                edl_path=run_dir / f"{variant}.edl.json",
                source_track_gate_count=len(source_track_gates),
            )
        except Exception as exc:
            fallback = str(mixing.get("fallback_mode") or "")
            if fallback != "direct_mix":
                raise DeliveryError(f"automix stage failed: {exc}") from exc
            mix_mode = "direct_mix_fallback"
            automix_manifest = {
                "status": "FAILED_FALLBACK_TO_DIRECT_MIX",
                "error": str(exc),
            }
    if mix_mode.startswith("direct_mix"):
        mix_command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for stem in stem_paths:
            mix_command += ["-i", str(stem)]
        labels = "".join(f"[{index}:a]" for index in range(len(stem_paths)))
        mix_command += [
            "-filter_complex",
            f"{labels}amix=inputs={len(stem_paths)}:duration=longest:normalize=1,aresample=48000,aformat=channel_layouts=stereo[mix]",
            "-map",
            "[mix]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(speech_mix),
        ]
        completed = subprocess.run(mix_command, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise DeliveryError(f"speech mix failed: {completed.stderr[-1000:]}")
    music = run_dir / "assets/fixed_intro_outro_music.mp3"
    if not music.is_file() or sha256_file(music) != MUSIC_SHA256:
        raise DeliveryError("fixed music is missing or its SHA does not match")
    template = plan["music"]["music_template_id"]
    timing = resolve_run_music_timing(run_dir, plan)
    speech_info = wav_info(speech_mix)
    speech_duration = float(speech_info["duration_seconds"])
    pre_loudnorm = output_dir / "master_pre_loudnorm.wav"
    music_info: dict[str, Any]
    if template == "reference-linear-v1":
        voice_start = float(timing["voice_start_seconds"])
        intro_music_only_end = float(timing["intro_music_only_end_seconds"])
        intro_fade_out_start = float(timing["intro_fade_out_start_seconds"])
        intro_fade_out_end = float(timing["intro_fade_out_end_seconds"])
        outro_fade_in_lead = float(timing["outro_fade_in_lead_seconds"])
        outro_music_tail = float(timing["outro_music_tail_seconds"])
        outro_start = voice_start + speech_duration - outro_fade_in_lead
        outro_delay_ms = max(0, round(outro_start * 1000))
        voice_delay_ms = round(voice_start * 1000)
        intro_duration = intro_fade_out_end
        outro_duration = outro_fade_in_lead + outro_music_tail
        filter_graph_music = (
            f"[0:a]aresample=48000,aformat=channel_layouts=stereo,adelay={voice_delay_ms}|{voice_delay_ms}[voice];"
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={intro_duration:g},volume={float(timing['music_gain_db']):g}dB,afade=t=out:st={intro_fade_out_start:g}:d={intro_fade_out_end - intro_fade_out_start:g}[intro];"
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={outro_duration:g},volume={float(timing['music_gain_db']):g}dB,afade=t=in:st=0:d={outro_fade_in_lead:g},"
            f"adelay={outro_delay_ms}|{outro_delay_ms}[outro];"
            "[intro][voice][outro]amix=inputs=3:duration=longest:normalize=0,alimiter=limit=0.95[out]"
        )
        music_info = {
            "music_template_id": template,
            "voice_start_sample": round(voice_start * 48000),
            "intro_music_start_sample": 0,
            "intro_music_end_sample": round(intro_duration * 48000),
            "outro_music_start_sample": round(outro_start * 48000),
            "outro_music_end_sample": round((outro_start + outro_duration) * 48000),
            "voice_start_seconds": voice_start,
            "intro_music_only_end_seconds": intro_music_only_end,
            "intro_fade_out_start_seconds": intro_fade_out_start,
            "intro_fade_out_end_seconds": intro_fade_out_end,
            "outro_fade_in_lead_seconds": outro_fade_in_lead,
            "outro_music_tail_seconds": outro_music_tail,
            "parameters": {
                "music_gain_db": float(timing["music_gain_db"]),
                "ducking": timing["ducking"],
                "intro_fade_out": f"{intro_fade_out_start:g}s-{intro_fade_out_end:g}s",
                "outro_fade_in": f"{outro_fade_in_lead:g}s",
            },
            "timing_authority": timing.get("timing_authority"),
            "parameter_status": "AUDITION_DEFAULTS_NOT_RELEASE_SPEC",
        }
    elif template == "EP04-v12-crossfade-audition":
        intro_seconds = float(timing.get("intro_music_seconds", 15.0))
        fade = float(timing.get("crossfade_seconds", 3.0))
        voice_start = intro_seconds - fade
        outro_start = voice_start + speech_duration - fade
        filter_graph_music = (
            "[0:a]aresample=48000,aformat=channel_layouts=stereo[voice];"
            "[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration=15,volume=-12dB[intro];"
            "[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=start=0:duration=15,volume=-12dB,afade=t=out:st=12:d=3[outro];"
            "[intro][voice]acrossfade=d=3:c1=qsin:c2=qsin[head];"
            "[head][outro]acrossfade=d=3:c1=qsin:c2=qsin[out]"
        )
        music_info = {
            "music_template_id": template,
            "voice_start_sample": round(voice_start * 48000),
            "intro_music_start_sample": 0,
            "intro_music_end_sample": round(intro_seconds * 48000),
            "outro_music_start_sample": round(outro_start * 48000),
            "outro_music_end_sample": round((outro_start + intro_seconds) * 48000),
            "parameters": {"music_gain_db": -12, "crossfade_seconds": 3, "outro_fade_out_seconds": 3, "ducking": "none"},
            "parameter_status": "EP04_AUDITION_DEFAULTS_NOT_RELEASE_SPEC",
        }
    else:
        raise DeliveryError(f"unsupported music template in plan: {template}")
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(speech_mix), "-i", str(music), "-filter_complex", filter_graph_music, "-map", "[out]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(pre_loudnorm)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise DeliveryError(f"music assembly failed: {completed.stderr[-1000:]}")
    master_wav = output_dir / f"{identity['run_id']}.{variant}.master.wav"
    loudness = loudnorm_two_pass(pre_loudnorm, master_wav, ffmpeg)
    master_mp3 = output_dir / f"{identity['run_id']}.{variant}.master.mp3"
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(master_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(master_mp3)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise DeliveryError(f"MP3 encoding failed: {completed.stderr[-1000:]}")
    manifest = {
        "schema_version": "delivery-render-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "variant": variant,
        "source_edl_relpath": f"{variant}.edl.json",
        "source_edl_sha256": sha256_file(run_dir / f"{variant}.edl.json"),
        "render_method": (
            "local_ffmpeg_from_deepfilternet_run_inputs"
            if read_json(run_dir / "plan.json").get("denoise", {}).get("backend") == "deepfilternet"
            else "local_ffmpeg_from_raw_input_links"
        ),
        "outputs": {
            "stems": [relative_to_run(run_dir, path) for path in stem_paths],
            "speech_mix": relative_to_run(run_dir, speech_mix),
            "master_pre_loudnorm": relative_to_run(run_dir, pre_loudnorm),
            "master_wav": relative_to_run(run_dir, master_wav),
            "master_mp3": relative_to_run(run_dir, master_mp3),
            "master_wav_sha256": sha256_file(master_wav),
            "master_mp3_sha256": sha256_file(master_mp3),
        },
        "source_track_gates": {
            "count": len(source_track_gates),
            "action_ids": [str(gate.get("action_id")) for gate in source_track_gates],
            "applied_before_global_sync_cuts": True,
        },
        "loudness": loudness,
        "music": {
            **music_info,
            "timing": timing,
            "timing_sha256": sha256_bytes(timing),
            "source_asset_relpath": "assets/fixed_intro_outro_music.mp3",
            "source_sha256": MUSIC_SHA256,
        },
    }
    write_json(output_dir / "render_manifest.json", manifest)
    return manifest


def write_normal_music_manifest(run_dir: Path) -> dict[str, Any]:
    """Bind the two normal-render variants to one frozen music configuration."""

    identity = require_identity(run_dir)
    plan = read_json(run_dir / "plan.json")
    timing = resolve_run_music_timing(run_dir, plan)
    template_id = plan["music"]["music_template_id"]
    variants: dict[str, Any] = {}
    for variant in ("human_approved", "machine_assisted_draft"):
        render_path = run_dir / f"render_{variant}/render_manifest.json"
        if not render_path.is_file():
            raise DeliveryError(f"cannot build music manifest without {variant} render manifest")
        render = read_json(render_path)
        render_music = render.get("music") or {}
        if render_music.get("music_template_id") != template_id:
            raise DeliveryError(f"{variant} render uses a different music template")
        if render_music.get("timing_sha256") != sha256_bytes(timing):
            raise DeliveryError(f"{variant} render music timing SHA mismatch")
        if render_music.get("source_sha256") != MUSIC_SHA256:
            raise DeliveryError(f"{variant} render fixed music SHA mismatch")
        variants[variant] = {
            "render_manifest_relpath": relative_to_run(run_dir, render_path),
            "render_manifest_sha256": sha256_file(render_path),
            "voice_start_sample": render_music.get("voice_start_sample"),
            "intro_music_end_sample": render_music.get("intro_music_end_sample"),
            "outro_music_start_sample": render_music.get("outro_music_start_sample"),
            "outro_music_end_sample": render_music.get("outro_music_end_sample"),
        }
    manifest = {
        "schema_version": "delivery-music-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "music_template_id": template_id,
        "asset_relpath": "assets/fixed_intro_outro_music.mp3",
        "asset_sha256": MUSIC_SHA256,
        "timing": timing,
        "timing_sha256": sha256_bytes(timing),
        "variants": variants,
        "parameter_status": timing.get("release_parameter_status", "AUDITION_DEFAULTS_ONLY"),
    }
    write_json(run_dir / "music_manifest.json", manifest)
    return manifest


def qc_and_report(
    run_dir: Path,
    ffmpeg: str | None,
    *,
    decision: str | None = None,
    inherited_loudness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = require_identity(run_dir)
    state = str(read_json(run_dir / "state.json").get("state") or "")
    variants: dict[str, Any] = {}
    failures = identity_errors(run_dir)
    transition_index, transition_errors = transition_qc_report_index(
        run_dir,
        required=transition_qc_required(run_dir, state),
    )
    failures.extend(transition_errors)
    loudness_cache: dict[str, dict[str, Any]] = {}
    loudness_observations: dict[str, Any] = {}
    for variant in ("human_approved", "machine_assisted_draft"):
        manifest_path = run_dir / f"render_{variant}/render_manifest.json"
        if not manifest_path.is_file():
            failures.append(f"render manifest missing: {variant}")
            continue
        manifest = read_json(manifest_path)
        master = run_dir / manifest["outputs"]["master_wav"]
        mp3 = run_dir / manifest["outputs"]["master_mp3"]
        if not master.is_file() or not mp3.is_file():
            failures.append(f"missing master output: {variant}")
            continue
        if sha256_file(master) != manifest["outputs"]["master_wav_sha256"]:
            failures.append(f"WAV hash mismatch: {variant}")
        if sha256_file(mp3) != manifest["outputs"]["master_mp3_sha256"]:
            failures.append(f"MP3 hash mismatch: {variant}")
        variant_qc: dict[str, Any] = {
            "master_wav": manifest["outputs"]["master_wav"],
            "master_mp3": manifest["outputs"]["master_mp3"],
            "wav_probe": audio_probe(master),
            "mp3_probe": audio_probe(mp3),
        }
        if ffmpeg:
            master_sha = sha256_file(master)
            if master_sha not in loudness_cache:
                loudness_cache[master_sha] = loudnorm_measure(master, ffmpeg)
                variant_qc["loudness_measurement_mode"] = "recomputed_current_run"
            else:
                variant_qc["loudness_measurement_mode"] = "reused_current_measurement_for_identical_master"
            variant_qc["loudness_measurement"] = loudness_cache[master_sha]
            measured = loudness_cache[master_sha]
            try:
                observed_i = float(measured["input_i"])
                observed_tp = float(measured["input_tp"])
                loudness_observations[variant] = {
                    "observed_integrated_lufs": observed_i,
                    "observed_true_peak_dbtp": observed_tp,
                    "v12_working_target": {"integrated_lufs": -16.0, "true_peak_dbtp": -1.0},
                    "delta_from_working_target": {
                        "integrated_lu": round(observed_i + 16.0, 2),
                        "true_peak_db": round(observed_tp + 1.0, 2),
                    },
                    "status": "OBSERVED_NOT_A_FROZEN_RELEASE_GATE",
                }
            except (KeyError, TypeError, ValueError):
                loudness_observations[variant] = {"status": "MEASUREMENT_UNPARSEABLE"}
        elif inherited_loudness is not None:
            variant_qc["loudness_measurement"] = inherited_loudness
            variant_qc["loudness_measurement_mode"] = "verified_frozen_source_report_exact_output_hash"
        else:
            failures.append(f"no loudness measurement engine/evidence: {variant}")
        variants[variant] = variant_qc
    result = {
        "schema_version": "delivery-qc-report-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "automatic_qc": "PASS" if not failures else "FAIL",
        "failures": failures,
        "variants": variants,
        "loudness_observations": loudness_observations,
        "transition_qc": transition_index,
        "manual_qc_required": [
            "whole-episode listening",
            "music-vocal balance",
            "candidate coverage gap check",
            "priority transition re-listening from transition_qc.json",
        ],
        "decision_context": decision,
    }
    write_json(run_dir / "qc_report.json", result)
    return result


# write_delivery_report moved to main/orchestrator/write_delivery_report.py
# (registered as  tool; imported at top of file for
#  backward-compatible caller sites).

def resume_after_review(run_dir: Path, *, ffmpeg: str) -> None:
    calibration_and_predictions(run_dir)
    render_one_variant(run_dir, "human_approved", ffmpeg)
    render_one_variant(run_dir, "machine_assisted_draft", ffmpeg)
    write_normal_music_manifest(run_dir)
    transition(run_dir, "MACHINE_ASSISTED_DRAFT_RENDERED", "both audition variants rendered from the current EDLs")
    try:
        generate_transition_qc_reports(run_dir)
    except DeliveryError as exc:
        transition(run_dir, "BLOCKED", f"transition QC failed: {exc}")
        raise
    qc = qc_and_report(run_dir, ffmpeg)
    if qc["automatic_qc"] != "PASS":
        transition(run_dir, "BLOCKED", "automatic QC failed")
        raise DeliveryError("automatic QC failed; see qc_report.json")
    transition(run_dir, "FINAL_QC_REQUIRED", "automatic QC passed; human must listen to the full program")
    write_delivery_report(run_dir, final_status="FINAL_QC_REQUIRED")


def record_final_decision(run_dir: Path, *, decision: str, reviewer: str, note: str) -> None:
    identity = require_identity(run_dir)
    require_state(run_dir, "FINAL_QC_REQUIRED")
    if decision not in {"human_approved_delivery", "REWORK", "HOLD"}:
        raise DeliveryError("decision must be human_approved_delivery, REWORK, or HOLD")
    if not reviewer.strip() or reviewer.upper().startswith(("AUTOMATED_", "LEARNED_", "MACHINE", "AGENT")):
        raise DeliveryError("a real human reviewer is required for the final decision")
    qc = read_json(run_dir / "qc_report.json")
    if decision == "human_approved_delivery" and qc.get("automatic_qc") != "PASS":
        raise DeliveryError("cannot approve delivery while automatic QC is not PASS")
    final = {
        "schema_version": "delivery-final-listening-decision-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "decision": decision,
        "reviewer": reviewer,
        "recorded_at": utc_now(),
        "note": note,
        "qc_report_sha256": sha256_file(run_dir / "qc_report.json"),
        "publish_action": "NOT_REQUESTED",
    }
    write_json(run_dir / "final_listening_decision.json", final)
    transition(run_dir, "DELIVERY_DECISION_RECORDED", decision)
    write_delivery_report(run_dir, final_status=decision)
    decisions_path = run_dir / "human_decisions.json"
    decisions_document = read_json(decisions_path) if decisions_path.is_file() else {}
    candidate_feedback = [
        {
            "candidate_id": row.get("candidate_id"),
            "decision": row.get("decision"),
            "feedback": row.get("feedback", ""),
        }
        for row in decisions_document.get("decisions") or []
        if isinstance(row, dict) and str(row.get("feedback", "")).strip()
    ]
    write_json(
        run_dir / "feedback_bundle.json",
        {
            "schema_version": "delivery-feedback-bundle-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "final_decision_relpath": "final_listening_decision.json",
            "final_decision_sha256": sha256_file(run_dir / "final_listening_decision.json"),
            "candidate_feedback_source_relpath": "human_decisions.json" if decisions_path.is_file() else None,
            "candidate_feedback_source_sha256": sha256_file(decisions_path) if decisions_path.is_file() else None,
            "candidate_feedback_count": len(candidate_feedback),
            "candidate_feedback": candidate_feedback,
            "experience_action": "proposal_only_no_online_policy_change",
        },
    )


def recheck_qc(run_dir: Path, *, ffmpeg: str) -> None:
    """Recompute objective QC without touching approved audio or edit decisions."""

    identity = require_identity(run_dir)
    require_state(run_dir, "FINAL_QC_REQUIRED", "DELIVERY_DECISION_RECORDED")
    previous = run_dir / "qc_report.json"
    if not previous.is_file():
        raise DeliveryError("cannot recheck QC without an existing qc_report.json")
    inherited_copy = run_dir / "qc_report.before_recheck.json"
    if not inherited_copy.exists():
        shutil.copy2(previous, inherited_copy)
    old_sha = sha256_file(previous)
    final = read_json(run_dir / "final_listening_decision.json") if (run_dir / "final_listening_decision.json").is_file() else None
    qc = qc_and_report(run_dir, ffmpeg, decision=final.get("decision") if final else None)
    if qc.get("automatic_qc") != "PASS":
        raise DeliveryError("recomputed automatic QC failed; see qc_report.json")
    version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=False)
    recheck = {
        "schema_version": "delivery-qc-recheck-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "recorded_at": utc_now(),
        "ffmpeg": ffmpeg,
        "ffmpeg_sha256": sha256_file(Path(ffmpeg)),
        "ffmpeg_version": version.stdout.splitlines()[0] if version.returncode == 0 and version.stdout else "UNAVAILABLE",
        "previous_qc_relpath": "qc_report.before_recheck.json",
        "previous_qc_sha256": old_sha,
        "current_qc_relpath": "qc_report.json",
        "current_qc_sha256": sha256_file(run_dir / "qc_report.json"),
        "audio_changed": False,
        "decision_changed": False,
        "purpose": "replace inherited frozen-source loudness evidence with a current local measurement",
    }
    write_json(run_dir / "qc_recheck.json", recheck)
    if final is not None:
        final["qc_report_sha256"] = recheck["current_qc_sha256"]
        final["qc_measurement_mode"] = "recomputed_current_run"
        final["qc_rechecked_at"] = recheck["recorded_at"]
        write_json(run_dir / "final_listening_decision.json", final)
        feedback_path = run_dir / "feedback_bundle.json"
        if feedback_path.is_file():
            feedback = read_json(feedback_path)
            feedback["final_decision_sha256"] = sha256_file(run_dir / "final_listening_decision.json")
            feedback["qc_recheck_relpath"] = "qc_recheck.json"
            write_json(feedback_path, feedback)
        write_delivery_report(run_dir, final_status=str(final.get("decision")), special_scope=(run_dir / "human_approval_scope.json").is_file())


def source_tracks_from_manifest(path: Path) -> list[tuple[str, str, Path]]:
    manifest = read_json(path)
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise DeliveryError("source tracks manifest is empty")
    result: list[tuple[str, str, Path]] = []
    for item in tracks:
        if not isinstance(item, dict):
            raise DeliveryError("invalid source tracks manifest entry")
        source = Path(str(item.get("audio_path", ""))).expanduser()
        result.append((str(item["track_id"]), str(item.get("label") or item["track_id"]), source))
    return result


def promote_v12(
    *,
    episode_id: str,
    run_id: str,
    source_run: Path,
    source_tracks_manifest: Path,
    ffmpeg: str | None,
) -> Path:
    """Create a new, correctly identified delivery run from an explicitly approved v12 audition."""

    source_run = source_run.resolve()
    source_edl = source_run / "EP04-v4.edl.json"
    source_wav = source_run / "EP04-v12.master.wav"
    source_mp3 = source_run / "EP04-v12.master.mp3"
    source_speech = source_run / "EP04-v4.speech-only.wav"
    source_loudness = source_run / "loudnorm_report.json"
    for required in (source_edl, source_wav, source_mp3, source_speech, source_loudness):
        if not required.is_file():
            raise DeliveryError(f"required v12 source artifact is missing: {required}")
    tracks = source_tracks_from_manifest(source_tracks_manifest.resolve())
    run_dir, identity, _ = make_base_run(
        episode_id=episode_id,
        run_id=run_id,
        source_tracks=tracks,
        purpose="promotion of a frozen EP04-v12 audition after explicit whole-episode human approval",
        music_template_id="EP04-v12-crossfade-audition",
        source_audio_mode="frozen_approved_source",
    )
    transition(run_dir, "INPUT_VALIDATED", "source links, PCM metadata, common timeline and fixed music SHA validated")
    transition(run_dir, "TIMELINE_READY", "input sample timeline frozen")
    legacy_edl = read_json(source_edl)
    sync_cuts = legacy_edl.get("sync_cuts_merged")
    gates = legacy_edl.get("gates_by_track")
    if not isinstance(sync_cuts, list) or not isinstance(gates, dict):
        raise DeliveryError("v12 source EDL has no sync_cuts_merged or gates_by_track")
    source_loudness_doc = read_json(source_loudness)
    source_speech_duration = float(wav_info(source_speech)["duration_seconds"])
    source_master_duration = float(wav_info(source_wav)["duration_seconds"])
    if abs(source_master_duration - (source_speech_duration + 24.0)) > 1 / 48000:
        raise DeliveryError(
            "v12 master/speech timing does not match the documented 15s/3s crossfade layout; "
            "do not infer a music manifest"
        )
    inherited_loudness = {
        "source_loudnorm_report_relpath": "source_artifacts/EP04-v12.source.loudnorm_report.json",
        "source_loudnorm_report_sha256": sha256_file(source_loudness),
        "source_speech_only_filename": source_speech.name,
        "source_speech_only_sha256": sha256_file(source_speech),
        "source_speech_only_duration_seconds": source_speech_duration,
        "source_master_duration_seconds": source_master_duration,
        "reported_actual_output": source_loudness_doc.get("actual_output_pass2"),
        "source_master_hash_verified_before_copy": sha256_file(source_wav),
    }
    source_evidence = {
        "source_run_description": "historical v12 audition; its old manifest identity is deliberately not reused",
        "source_run_path_outside_current_run": str(source_run),
        "legacy_edl_filename": source_edl.name,
        "legacy_edl_sha256": sha256_file(source_edl),
        "legacy_edl_claimed_episode_id": legacy_edl.get("episode_id"),
        "approved_master_wav_filename": source_wav.name,
        "approved_master_wav_sha256": sha256_file(source_wav),
        "approved_master_mp3_filename": source_mp3.name,
        "approved_master_mp3_sha256": sha256_file(source_mp3),
        "source_loudnorm_report_sha256": sha256_file(source_loudness),
        "legacy_identity_status": "MISMATCH_RECORDED_NOT_PROPAGATED",
    }
    source_artifacts = run_dir / "source_artifacts"
    source_artifacts.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source_edl, source_artifacts / "EP04-v12.source.edl.json")
    shutil.copy2(source_loudness, source_artifacts / "EP04-v12.source.loudnorm_report.json")
    scope = {
        "schema_version": "human-whole-episode-approval-scope-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "decision": "human_approved_delivery",
        "approval_mode": "human_whole_episode_audition",
        "approval_recorded_at": utc_now(),
        "approval_authority": "USER_AUTHORIZATION_RECORDED_IN_CURRENT_CONVERSATION",
        "authorization_summary": "User stated they listened to the whole EP04 v12 and authorized classifying its complete frozen edit package as human-approved.",
        "scope": {
            "covers": "all synchronized cuts and source-track gates encoded by the frozen v12 source EDL, plus the exact approved v12 master WAV/MP3",
            "does_not_create": ["per-candidate human_accept labels", "training labels", "autocut policy", "cross-episode preference promotion"],
        },
        "source_evidence": source_evidence,
    }
    write_json(run_dir / "human_approval_scope.json", scope)
    scope_rel = "human_approval_scope.json"
    actions = [
        {
            "action_id": f"v12-sync-{index:04d}",
            "action_type": "global_sync_cut",
            "start_sample": int(cut["start_sample"]),
            "end_sample": int(cut["end_sample"]),
            "applies_to_all_tracks": True,
            "decision": "human_whole_episode_approved",
            "decision_provenance": "human_whole_episode_audition",
            "original_provenance": "legacy_v12_machine_or_human_mixed_action; preserved without relabeling",
        }
        for index, cut in enumerate(sync_cuts, 1)
    ]
    gates_out = []
    for track_id, rows in sorted(gates.items()):
        if not isinstance(rows, list):
            raise DeliveryError(f"invalid legacy v12 gates for {track_id}")
        for index, gate in enumerate(rows, 1):
            gates_out.append(
                {
                    "action_id": f"v12-gate-{track_id}-{index:03d}",
                    "action_type": "source_track_gate",
                    "track_id": track_id,
                    "start_sample": int(gate["start_sample"]),
                    "end_sample": int(gate["end_sample"]),
                    "operation": "mute_source_track",
                    "decision": "human_whole_episode_approved",
                    "decision_provenance": "human_whole_episode_audition",
                    "original_provenance": "legacy_v12_machine_gate; preserved without relabeling",
                }
            )
    human_edl = edl_document(
        run_dir=run_dir,
        variant="human_approved",
        actions=actions,
        decision_summary={"human_whole_episode_approved_actions": len(actions), "per_candidate_human_labels": 0},
        whole_episode_scope={"relpath": scope_rel, "sha256": sha256_file(run_dir / scope_rel)},
    )
    human_edl["source_track_gates"] = gates_out
    human_edl["source_audition_evidence"] = {
        "relpath": "source_artifacts/EP04-v12.source.edl.json",
        "sha256": sha256_file(run_dir / "source_artifacts/EP04-v12.source.edl.json"),
    }
    machine_edl = dict(human_edl)
    machine_edl["variant"] = "machine_assisted_draft"
    machine_edl["equivalence_reason"] = "the human approved the complete frozen v12 action scope after whole-episode audition"
    write_json(run_dir / "human_approved.edl.json", human_edl)
    write_json(run_dir / "machine_assisted_draft.edl.json", machine_edl)
    write_json(
        run_dir / "all_candidates.json",
        {
            "schema_version": "delivery-all-candidates-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "status": "SOURCE_CANDIDATE_DETAILS_NOT_RETROACTIVELY_REWRITTEN",
            "frozen_action_counts": {"sync_cuts": len(actions), "source_track_gates": len(gates_out)},
            "source_evidence": source_evidence,
        },
    )
    write_json(
        run_dir / "prediction_manifest.json",
        {
            "schema_version": "delivery-prediction-manifest-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "status": "LEGACY_MACHINE_PROVENANCE_PRESERVED_NOT_USED_AS_HUMAN_LABELS",
            "policy": {"id": "NOT_APPROVED", "status": "NOT_APPROVED"},
            "prediction_source": "legacy v12 source EDL; original machine/human mixed provenance retained in EDL action fields",
        },
    )
    write_json(
        run_dir / "calibration_report.json",
        {
            "schema_version": "delivery-calibration-report-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "status": "NOT_APPLICABLE_FOR_WHOLE_EPISODE_AUDITION_PROMOTION",
            "note": "This run records a human-approved frozen audition, not representative-sample calibration or model learning.",
        },
    )
    write_json(
        run_dir / "preference_application_report.json",
        {
            "schema_version": "preference-application-report-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "profile_id": PREFERENCE_ID,
            "profile_sha256": sha256_file(run_dir / "frozen/editing_preference_profile.md"),
            "music_template_id": "EP04-v12-crossfade-audition",
            "scope": "documented EP04 audition preference only; not promoted to the default release template",
        },
    )
    for variant in ("human_approved", "machine_assisted_draft"):
        output_dir = run_dir / f"render_{variant}"
        output_dir.mkdir()
        wav = output_dir / f"{identity['run_id']}.{variant}.master.wav"
        mp3 = output_dir / f"{identity['run_id']}.{variant}.master.mp3"
        if variant == "human_approved":
            shutil.copy2(source_wav, wav)
            shutil.copy2(source_mp3, mp3)
        else:
            try:
                os.link(run_dir / "render_human_approved" / f"{identity['run_id']}.human_approved.master.wav", wav)
                os.link(run_dir / "render_human_approved" / f"{identity['run_id']}.human_approved.master.mp3", mp3)
            except OSError:
                shutil.copy2(source_wav, wav)
                shutil.copy2(source_mp3, mp3)
        render_manifest = {
            "schema_version": "delivery-render-manifest-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "variant": variant,
            "source_edl_relpath": f"{variant}.edl.json",
            "source_edl_sha256": sha256_file(run_dir / f"{variant}.edl.json"),
            "render_method": "promotion_from_exact_human_approved_frozen_audition",
            "source_evidence": source_evidence,
            "outputs": {
                "master_wav": relative_to_run(run_dir, wav),
                "master_mp3": relative_to_run(run_dir, mp3),
                "master_wav_sha256": sha256_file(wav),
                "master_mp3_sha256": sha256_file(mp3),
            },
        }
        write_json(output_dir / "render_manifest.json", render_manifest)
    speech_duration = source_speech_duration
    music_manifest = {
        "schema_version": "delivery-music-manifest-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "music_template_id": "EP04-v12-crossfade-audition",
        "asset_relpath": "assets/fixed_intro_outro_music.mp3",
        "asset_sha256": MUSIC_SHA256,
        "observed_frozen_timing": {
            "intro_music_seconds": 15.0,
            "speech_start_seconds": 12.0,
            "crossfade_seconds": 3.0,
            "speech_only_duration_seconds": speech_duration,
            "outro_music_start_seconds": 12.0 + speech_duration - 3.0,
            "outro_music_total_seconds": 15.0,
            "outro_fade_out_seconds": 3.0,
        },
        "timing_evidence": {
            "speech_only_relpath": "source_artifacts/EP04-v12.source.edl.json (cut timing) + frozen source speech-only hash in human_approval_scope.json",
            "speech_only_duration_seconds": speech_duration,
            "master_duration_seconds": source_master_duration,
            "validation": "master_duration == speech_only_duration + 24.0 seconds",
            "note": "The actual WAV probe takes precedence over stale prose in historical CUT_DETAILS files.",
        },
        "parameter_status": "EP04_SPECIFIC_HUMAN_APPROVED_AUDITION_NOT_GLOBAL_RELEASE_POLICY",
    }
    write_json(run_dir / "music_manifest.json", music_manifest)
    transition(run_dir, "MACHINE_ASSISTED_DRAFT_RENDERED", "exact approved v12 master copied into correctly identified dual output names")
    qc = qc_and_report(
        run_dir,
        ffmpeg,
        decision="human_approved_delivery",
        inherited_loudness=inherited_loudness,
    )
    if qc["automatic_qc"] != "PASS":
        transition(run_dir, "BLOCKED", "automatic QC failed while promoting v12")
        raise DeliveryError("automatic QC failed while promoting v12")
    final = {
        "schema_version": "delivery-final-listening-decision-v1",
        "episode_id": identity["episode_id"],
        "run_id": identity["run_id"],
        "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
        "decision": "human_approved_delivery",
        "approval_mode": "human_whole_episode_audition",
        "approval_scope_relpath": "human_approval_scope.json",
        "approval_scope_sha256": sha256_file(run_dir / "human_approval_scope.json"),
        "qc_report_sha256": sha256_file(run_dir / "qc_report.json"),
        "recorded_at": utc_now(),
        "publish_action": "NOT_REQUESTED",
        "note": "Recorded from explicit user authorization in the current task after a full EP04 v12 listen; not an invented per-item reviewer record.",
    }
    write_json(run_dir / "final_listening_decision.json", final)
    transition(run_dir, "FINAL_QC_REQUIRED", "automatic QC passed; explicit whole-episode authorization recorded")
    transition(run_dir, "DELIVERY_DECISION_RECORDED", "human_approved_delivery via whole-episode audition scope")
    write_json(
        run_dir / "feedback_bundle.json",
        {
            "schema_version": "delivery-feedback-bundle-v1",
            "episode_id": identity["episode_id"],
            "run_id": identity["run_id"],
            "run_identity_sha256": sha256_file(run_dir / "run_identity.json"),
            "final_decision_relpath": "final_listening_decision.json",
            "final_decision_sha256": sha256_file(run_dir / "final_listening_decision.json"),
            "experience_action": "NO_TRAINING_LABEL_EXPORT_FROM_WHOLE_EPISODE_SCOPE",
        },
    )
    write_delivery_report(run_dir, final_status="human_approved_delivery", special_scope=True)
    return run_dir


def cmd_start(args: argparse.Namespace) -> int:
    input_dir = args.input_dir.expanduser().resolve()
    episode_id = args.episode_id or derive_episode_id(input_dir)
    run_id = args.run_id or derive_run_id(episode_id)
    reuse_analysis_run = getattr(args, "reuse_analysis_run", None)
    reuse_semantic_run = getattr(args, "reuse_semantic_run", None)
    if reuse_analysis_run and (args.model or args.context_prompt):
        raise DeliveryError(
            "--model and --context-prompt cannot be used with --reuse-analysis-run; "
            "reuse means the existing ASR output/configuration is fixed"
        )
    if reuse_semantic_run and not reuse_analysis_run:
        raise DeliveryError("--reuse-semantic-run requires --reuse-analysis-run")
    # Check the renderer dependency before creating any run.  A failed dependency
    # check should not leave a seemingly usable half-run behind.
    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    source_tracks = [
        (f"track_{index:02d}", path.stem, path.resolve())
        for index, path in enumerate(normalized_track_sources(input_dir), 1)
    ]
    run_dir, _, _ = make_base_run(
        episode_id=episode_id,
        run_id=run_id,
        source_tracks=source_tracks,
        purpose="normal audio-to-review delivery run",
        music_template_id=args.music_template,
        candidate_rules_source=args.candidate_rules,
        editing_policy_source=getattr(args, "editing_policy", None),
        review_budget=args.review_budget,
        experience_snapshot_source=getattr(args, "experience_snapshot", None) or resolve_default_experience_snapshot(),
        event_history_runs=getattr(args, "event_history_run", None),
        integration_registry_source=getattr(args, "integration_registry", None),
    )
    try:
        transition(run_dir, "INPUT_VALIDATED", "mono PCM input and fixed music SHA validated")
        transition(run_dir, "TIMELINE_READY", "all input tracks share an integer-sample timeline")
        if reuse_analysis_run:
            reuse_evidence = reuse_denoise_artifacts(
                run_dir,
                reuse_analysis_run.expanduser().resolve(),
                explicit_semantic_run=reuse_semantic_run.expanduser().resolve() if reuse_semantic_run else None,
            )
        else:
            reuse_evidence = None
            run_deepfilternet_denoise(run_dir, python=args.python, ffmpeg=ffmpeg)
        run_local_analysis(
            run_dir,
            model=args.model,
            context_prompt=args.context_prompt,
            python=args.python,
            reuse_source_run=reuse_analysis_run.expanduser().resolve() if reuse_analysis_run else None,
            reuse_semantic_run=reuse_semantic_run.expanduser().resolve() if reuse_semantic_run else None,
            reuse_evidence=reuse_evidence,
        )
        build_candidates_and_review(run_dir, python=args.python, ffmpeg=ffmpeg)
    except Exception as exc:
        if (run_dir / "state.json").is_file():
            transition(run_dir, "FAILED", str(exc))
        raise
    benchmark = (
        refresh_development_benchmark_nonblocking(
            run_dir,
            phase="candidate_frozen",
            python=args.python,
        )
        if benchmark_refresh_enabled(args)
        else {"status": "DISABLED_FOR_DIRECT_FIXTURE_CALL"}
    )
    print(
        json.dumps(
            {
                "status": "CALIBRATION_REVIEW_REQUIRED",
                "run_dir": str(run_dir),
                "next": "run serve-review, then resume",
                "development_benchmark": benchmark,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_serve_review(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    command = review_server_command(run_dir, python=args.python, port=args.port, no_open=args.no_open)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    return 0


def cmd_refresh_review(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    revision = refresh_review_package(
        run_dir,
        python=args.python,
        ffmpeg=resolve_ffmpeg(args.ffmpeg),
        reason=args.reason,
    )
    benchmark = (
        refresh_development_benchmark_nonblocking(
            run_dir,
            phase="review_package_refreshed",
            python=getattr(args, "python", sys.executable),
        )
        if benchmark_refresh_enabled(args)
        else {"status": "DISABLED_FOR_DIRECT_FIXTURE_CALL"}
    )
    print(
        json.dumps(
            {
                "status": "CALIBRATION_REVIEW_REQUIRED",
                "run_dir": str(run_dir),
                "review_manifest_sha256": revision["current_review_manifest_sha256"],
                "superseded_review_manifest_sha256": revision["superseded_review_manifest_sha256"],
                "development_benchmark": benchmark,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    resume_after_review(run_dir, ffmpeg=resolve_ffmpeg(args.ffmpeg))
    benchmark = (
        refresh_development_benchmark_nonblocking(
            run_dir,
            phase="post_render",
            python=getattr(args, "python", sys.executable),
        )
        if benchmark_refresh_enabled(args)
        else {"status": "DISABLED_FOR_DIRECT_FIXTURE_CALL"}
    )
    print(
        json.dumps(
            {"status": "FINAL_QC_REQUIRED", "run_dir": str(run_dir), "development_benchmark": benchmark},
            ensure_ascii=False,
        )
    )
    return 0


def cmd_record_final(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    record_final_decision(run_dir, decision=args.decision, reviewer=args.reviewer, note=args.note)
    benchmark = (
        refresh_development_benchmark_nonblocking(
            run_dir,
            phase="final_decision",
            python=getattr(args, "python", sys.executable),
        )
        if benchmark_refresh_enabled(args)
        else {"status": "DISABLED_FOR_DIRECT_FIXTURE_CALL"}
    )
    print(
        json.dumps(
            {"status": args.decision, "run_dir": str(run_dir), "development_benchmark": benchmark},
            ensure_ascii=False,
        )
    )
    return 0


def cmd_recheck_qc(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    recheck_qc(run_dir, ffmpeg=resolve_ffmpeg(args.ffmpeg))
    print(json.dumps({"status": "QC_RECHECKED", "run_dir": str(run_dir)}, ensure_ascii=False))
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Explicitly refresh development evidence for an existing local run.

    This command is useful after a human fills a QA record by hand.  A failure
    is reported to the caller, but it never changes the run's delivery state or
    invents an EDL/decision merely to make a scorecard look complete.
    """

    run_dir = args.run_dir.expanduser().resolve()
    result = refresh_development_benchmark_nonblocking(
        run_dir,
        phase=args.phase,
        python=getattr(args, "python", sys.executable),
    )
    print(json.dumps({"run_dir": str(run_dir), "development_benchmark": result}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


def cmd_promote_v12(args: argparse.Namespace) -> int:
    ffmpeg = resolve_ffmpeg(args.ffmpeg) if args.ffmpeg or shutil.which("ffmpeg") else None
    run_id = args.run_id or derive_run_id(args.episode_id)
    expected_run = RUNS_ROOT / args.episode_id / run_id
    try:
        run_dir = promote_v12(
            episode_id=args.episode_id,
            run_id=run_id,
            source_run=args.source_run.expanduser(),
            source_tracks_manifest=args.source_tracks_manifest.expanduser(),
            ffmpeg=ffmpeg,
        )
    except DeliveryError as exc:
        # A promotion can fail after the fresh read-only input links exist.  Mark
        # that new run failed rather than leaving it looking resumable.
        state_path = expected_run / "state.json"
        if state_path.is_file():
            try:
                state = read_json(state_path)
                if state.get("state") not in {"DELIVERY_DECISION_RECORDED", "FAILED"}:
                    transition(expected_run, "FAILED", f"promote-v12 failed: {exc}")
            except DeliveryError:
                pass
        raise
    print(json.dumps({"status": "human_approved_delivery", "run_dir": str(run_dir)}, ensure_ascii=False))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    identity = require_identity(run_dir)
    state = read_json(run_dir / "state.json")
    artifacts = delivery_artifact_errors(run_dir)
    print(json.dumps({"episode_id": identity["episode_id"], "run_id": identity["run_id"], "state": state.get("state"), "identity_errors": identity_errors(run_dir), "artifact_errors": artifacts}, ensure_ascii=False, indent=2))
    return 0 if not artifacts else 2


def cmd_verify(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    identity = require_identity(run_dir)
    errors = delivery_artifact_errors(run_dir)
    print(json.dumps({"episode_id": identity["episode_id"], "run_id": identity["run_id"], "status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--python", default=sys.executable, help="Python interpreter used for local challenger tools")
    root.add_argument("--ffmpeg", help="explicit local ffmpeg path")
    root.add_argument(
        "--executor",
        choices=("v1", "v2"),
        default="v1",
        help=(
            "tool 调用后端 · v1=subprocess.run 原路径 (默认) · v2=通过 tool-orchestrator-v2 executor "
            "(拿 provenance + SHA drift 校验 + 未登记即报错)。opt-in · Session 3 后期负责人手动跑 EP04 对比后再默认切 v2。"
            "也可通过 MINGLUE_USE_V2_EXECUTOR=1 环境变量打开。"
        ),
    )
    sub = root.add_subparsers(dest="command", required=True)

    def add_benchmark_mode(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--benchmark-mode",
            choices=("auto", "off"),
            default="auto",
            help=(
                "refresh media-free development benchmark evidence after this stage; "
                "it is non-blocking and cannot create edit decisions (default: auto)"
            ),
        )

    start = sub.add_parser("start", help="new WAV directory -> frozen candidates -> calibration review package")
    start.add_argument("--input-dir", type=Path, required=True)
    start.add_argument("--episode-id")
    start.add_argument("--run-id")
    start.add_argument(
        "--reuse-analysis-run",
        type=Path,
        help="reuse a validated prior run's immutable denoise/ASR/semantic artifacts; skips denoise and ASR",
    )
    start.add_argument(
        "--reuse-semantic-run",
        type=Path,
        help="required when more than one semantic transcript is bound to the reused P0 report",
    )
    start.add_argument("--model")
    start.add_argument("--context-prompt", default="")
    start.add_argument(
        "--music-template",
        default="reference-linear-v1",
        choices=["reference-linear-v1"],
        help="normal production is locked to the five-second-voice-entry template; historical v12 is comparison-only",
    )
    start.add_argument(
        "--candidate-rules",
        type=Path,
        default=DEFAULT_CANDIDATE_RULES,
        help="frozen review-only candidate rules; defaults to the current V18 challenger",
    )
    start.add_argument(
        "--editing-policy",
        type=Path,
        default=DEFAULT_EDITING_POLICY,
        help=(
            "frozen active preserve/review policy guards; the current policy never authorizes automatic semantic deletion"
        ),
    )
    start.add_argument(
        "--integration-registry",
        type=Path,
        default=DEFAULT_INTEGRATION_REGISTRY,
        help="owner-attested component adoption registry; semantic edit approval remains separate",
    )
    start.add_argument(
        "--review-budget",
        type=int,
        default=20,
        help="maximum human calibration labels; fails closed if mandatory high-risk candidates exceed it",
    )
    start.add_argument(
        "--experience-snapshot",
        type=Path,
        default=None,
        help=(
            "explicit immutable label-learning snapshot manifest; omitted means the validated active pointer is used (with legacy fallback), limited to review priority / machine-suggestion sidecars"
        ),
    )
    start.add_argument(
        "--event-history-run",
        type=Path,
        action="append",
        default=[],
        help=(
            "explicit prior same-episode run with itemized human decisions; "
            "used only to create review_bundle/event_routes.json metadata"
        ),
    )
    add_benchmark_mode(start)
    start.set_defaults(func=cmd_start)

    serve = sub.add_parser("serve-review", help="serve the generated calibration review page")
    serve.add_argument("--run-dir", type=Path, required=True)
    serve.add_argument("--port", type=int)
    serve.add_argument("--no-open", action="store_true")
    serve.set_defaults(func=cmd_serve_review)

    refresh = sub.add_parser(
        "refresh-review",
        help="archive an unreviewed review UI bundle, then rebuild it from the same frozen candidates",
    )
    refresh.add_argument("--run-dir", type=Path, required=True)
    refresh.add_argument(
        "--reason",
        required=True,
        help="why a new UI/context/feedback package was needed; audio, ASR and candidate boundaries stay frozen",
    )
    add_benchmark_mode(refresh)
    refresh.set_defaults(func=cmd_refresh_review)

    resume = sub.add_parser("resume", help="validated human review -> calibration -> dual render -> QC")
    resume.add_argument("--run-dir", type=Path, required=True)
    add_benchmark_mode(resume)
    resume.set_defaults(func=cmd_resume)

    final = sub.add_parser("record-final", help="record a real human whole-program decision after QC")
    final.add_argument("--run-dir", type=Path, required=True)
    final.add_argument("--decision", required=True, choices=["human_approved_delivery", "REWORK", "HOLD"])
    final.add_argument("--reviewer", required=True)
    final.add_argument("--note", required=True)
    add_benchmark_mode(final)
    final.set_defaults(func=cmd_record_final)

    recheck = sub.add_parser("recheck-qc", help="recompute QC from existing master files without re-rendering")
    recheck.add_argument("--run-dir", type=Path, required=True)
    recheck.set_defaults(func=cmd_recheck_qc)

    benchmark = sub.add_parser(
        "benchmark",
        help="refresh the non-blocking development benchmark evidence for one existing run",
    )
    benchmark.add_argument("--run-dir", type=Path, required=True)
    benchmark.add_argument(
        "--phase",
        default="manual",
        choices=("candidate_frozen", "review_package_refreshed", "post_render", "final_decision", "manual"),
        help="why this explicit development-only refresh is being run",
    )
    benchmark.set_defaults(func=cmd_benchmark)

    promote = sub.add_parser("promote-v12", help="record explicit whole-episode approval for an exact frozen v12 audition")
    promote.add_argument("--episode-id", required=True)
    promote.add_argument("--run-id")
    promote.add_argument("--source-run", type=Path, required=True)
    promote.add_argument("--source-tracks-manifest", type=Path, required=True)
    promote.set_defaults(func=cmd_promote_v12)

    status = sub.add_parser("status", help="show run state, identity and artifact validator result")
    status.add_argument("--run-dir", type=Path, required=True)
    status.set_defaults(func=cmd_status)
    verify = sub.add_parser("verify", help="fail-closed validation of the current run hash/reference chain")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.set_defaults(func=cmd_verify)
    return root


def main() -> int:
    args = parser().parse_args()
    # v2 executor CLI opt-in · 命令行 flag 优先于环境变量
    if getattr(args, "executor", "v1") == "v2":
        global USE_V2_EXECUTOR
        USE_V2_EXECUTOR = True
        print("[executor] v2 opt-in active · tool 调用尝试走 tool-orchestrator-v2 · fallback v1 subprocess 若不兼容", file=sys.stderr)
    try:
        return int(args.func(args))
    except DeliveryError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"FAILED: external command returned {exc.returncode}: {exc.cmd}", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
