#!/usr/bin/env python3
"""Validated configuration shared by the episode review server and renderer."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_FRONTEND = PROJECT_ROOT / "审核前端/challenger-review-product-v1/mvp.html"
DEFAULT_FFMPEG = Path(
    os.environ.get(
        "PODCAST_FFMPEG",
        str(PROJECT_ROOT / ".tools/bin/ffmpeg"),
    )
)
EPISODE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(value: str, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class EpisodeConfig:
    config_path: Path
    episode_id: str
    source_package: Path
    previews_dir: Path
    tracks_manifest: Path
    frontend: Path
    run_dir: Path
    ffmpeg: Path
    port: int

    @property
    def bundle_dir(self) -> Path:
        return self.run_dir / "review_bundle"

    @property
    def review_package(self) -> Path:
        return self.bundle_dir / "review_package.json"

    @property
    def decisions_path(self) -> Path:
        return self.run_dir / "human_decisions.json"

    @property
    def edl_path(self) -> Path:
        return self.run_dir / "approved.edl.draft.json"


def load_episode_config(
    config_path: Path,
    *,
    project_root: Path | None = None,
) -> EpisodeConfig:
    """Load a config and reject cross-episode or out-of-project output paths."""
    root = (project_root or PROJECT_ROOT).resolve()
    path = config_path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"config does not exist: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "review-episode-config-v1":
        raise ValueError("config schema_version must be review-episode-config-v1")

    episode_id = str(raw.get("episode_id", "")).strip()
    if not EPISODE_ID_RE.fullmatch(episode_id):
        raise ValueError("episode_id must contain only letters, numbers, dot, underscore or hyphen")

    required = ("source_package", "tracks_manifest", "run_dir")
    missing = [key for key in required if not str(raw.get(key, "")).strip()]
    if missing:
        raise ValueError("missing config paths: " + ", ".join(missing))

    source_package = resolve_path(str(raw["source_package"]), path)
    tracks_manifest = resolve_path(str(raw["tracks_manifest"]), path)
    run_dir = resolve_path(str(raw["run_dir"]), path)
    previews_dir = resolve_path(
        str(raw.get("previews_dir") or source_package.parent / "previews"), path
    )
    frontend = resolve_path(str(raw.get("frontend") or DEFAULT_FRONTEND), path)
    ffmpeg = resolve_path(str(raw.get("ffmpeg") or DEFAULT_FFMPEG), path)

    for label, candidate in (
        ("source_package", source_package),
        ("tracks_manifest", tracks_manifest),
        ("frontend", frontend),
        ("ffmpeg", ffmpeg),
    ):
        if not candidate.is_file():
            raise ValueError(f"{label} does not exist: {candidate}")

    runs_root = (root / "main/runs").resolve()
    if run_dir == runs_root or not is_within(run_dir, runs_root):
        raise ValueError(f"run_dir must be inside {runs_root}")
    if not run_dir.name.startswith(f"{episode_id}-"):
        raise ValueError("run_dir basename must start with '<episode_id>-' to prevent cross-episode writes")

    manifest = json.loads(tracks_manifest.read_text(encoding="utf-8"))
    if manifest.get("episode_id") != episode_id:
        raise ValueError("tracks_manifest episode_id does not match config episode_id")
    if not manifest.get("tracks"):
        raise ValueError("tracks_manifest has no tracks")

    source = json.loads(source_package.read_text(encoding="utf-8"))
    source_episode = source.get("episode_id")
    if source_episode is not None and source_episode != episode_id:
        raise ValueError("source_package episode_id does not match config episode_id")
    if not isinstance(source.get("candidates"), list):
        raise ValueError("source_package candidates must be an array")

    port = int(raw.get("port", 8768))
    if not 1 <= port <= 65535:
        raise ValueError("port must be within 1..65535")

    return EpisodeConfig(
        config_path=path,
        episode_id=episode_id,
        source_package=source_package,
        previews_dir=previews_dir,
        tracks_manifest=tracks_manifest,
        frontend=frontend,
        run_dir=run_dir,
        ffmpeg=ffmpeg,
        port=port,
    )


def package_identity_errors(config: EpisodeConfig, package: dict) -> list[str]:
    """Confirm an existing bundle belongs to exactly this immutable input set."""
    errors = []
    expected = {
        "episode_id": config.episode_id,
        "source_package_path": str(config.source_package),
        "source_package_sha256": sha_file(config.source_package),
        "tracks_manifest_path": str(config.tracks_manifest),
        "tracks_manifest_sha256": sha_file(config.tracks_manifest),
        "ui_sha256": sha_file(config.frontend),
    }
    for key, value in expected.items():
        if package.get(key) != value:
            errors.append(f"existing package {key} does not match config")
    return errors
