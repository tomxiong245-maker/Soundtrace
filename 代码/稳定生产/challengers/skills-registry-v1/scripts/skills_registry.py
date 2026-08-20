"""skills_registry · single source of truth for SKILL.md discovery.

Sister module to `main/tools/tool_lookup.py`. Where tool_lookup answers "which
script does tool X map to?", this module answers "which skills exist, what's
their status, and which tools do they call into?"

Data sources merged in this order:
    1. Every `SKILL.md` in the project (project-maintained skills).
    2. `稳定生产/challengers/skills-registry-v1/external_skills.json` (mentor /
       third-party skills whose SKILL.md is read-only).

Contract for SKILL.md frontmatter fields: see
`docs/skill_frontmatter_contract.md`.

Public API:
    all_skills()                  -> list[dict]   every skill entry
    by_name(name)                 -> dict         raise if unknown
    by_entry_tool(tool_name)      -> list[dict]   skills whose entry_tool == name
    assert_reachable()            -> list[str]    return list of contract errors
                                                  (empty = healthy)
    clear_cache()                 -> None         drop caches for tests

Every dict has the canonical shape:
    {
        "name": str,
        "description": str,
        "status": "active" | "deprecated" | "experimental" | "external",
        "owner": str,
        "entry_tool": str | None,
        "related_tools": list[str],
        "preconditions": list[str],
        "postconditions": list[str],
        "supersedes": list[str],
        "superseded_by": str | None,
        "path": str,          # project-root-relative path to SKILL.md
        "source": "frontmatter" | "sidecar",
    }
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[4]  # …/剪辑项目
SIDECAR_JSON = (
    PROJECT_ROOT / "稳定生产/challengers/skills-registry-v1/external_skills.json"
)

# tools.json access (via P1 sibling module)
_TOOLS_JSON = PROJECT_ROOT / "main/tools/tools.json"

VALID_STATUS = {"active", "deprecated", "experimental", "external"}
VALID_OWNER_PREFIXES = ("champion", "challenger:", "mentor", "external")

# Directories to skip when globbing SKILL.md files
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages"}

# Mentor SKILL.md are read-only; they must be registered via sidecar, not
# glob-discovered. If we discover a SKILL.md under mentor的成果/ during glob we
# **ignore** it and rely on the sidecar (fail-closed if not registered there).
_MENTOR_ROOT_NAME = "mentor的成果"


class SkillRegistryError(RuntimeError):
    """Raised when the registry cannot be resolved (missing fields, name clash,
    invalid enum, etc.)."""


def _parse_frontmatter(md_path: Path) -> dict[str, Any]:
    """Extract the YAML frontmatter block from a SKILL.md. Returns {} if none."""
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    # find the closing ---
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}
    head = parts[0][3:]  # strip the leading ---
    data = yaml.safe_load(head)
    if not isinstance(data, dict):
        return {}
    return data


def _canonicalize(entry: dict[str, Any], path: str, source: str) -> dict[str, Any]:
    """Fill defaults & validate types. Fail-closed on missing required fields."""
    for req in ("name", "description", "status", "owner"):
        if req not in entry or entry[req] in (None, ""):
            raise SkillRegistryError(
                f"skill at {path}: missing required frontmatter field {req!r}"
            )
    if entry["status"] not in VALID_STATUS:
        raise SkillRegistryError(
            f"skill {entry['name']!r} at {path}: invalid status "
            f"{entry['status']!r}; must be one of {sorted(VALID_STATUS)}"
        )
    owner = entry["owner"]
    if not (
        owner in ("champion", "mentor", "external")
        or owner.startswith("challenger:")
    ):
        raise SkillRegistryError(
            f"skill {entry['name']!r} at {path}: invalid owner {owner!r}; "
            f"must be one of champion/mentor/external or challenger:<name>"
        )
    if "entry_tool" not in entry:
        raise SkillRegistryError(
            f"skill {entry['name']!r} at {path}: frontmatter is missing "
            f"entry_tool (use null explicitly if no tool)"
        )
    return {
        "name": entry["name"],
        "description": entry["description"],
        "status": entry["status"],
        "owner": owner,
        "entry_tool": entry.get("entry_tool"),  # may be None
        "related_tools": list(entry.get("related_tools") or []),
        "preconditions": list(entry.get("preconditions") or []),
        "postconditions": list(entry.get("postconditions") or []),
        "supersedes": list(entry.get("supersedes") or []),
        "superseded_by": entry.get("superseded_by"),
        "path": path,
        "source": source,
    }


def _iter_skill_md() -> list[Path]:
    """Enumerate every SKILL.md under the project, excluding mentor/read-only
    trees (those go through the sidecar) and typical junk dirs."""
    out: list[Path] = []
    for p in PROJECT_ROOT.rglob("SKILL.md"):
        rel = p.relative_to(PROJECT_ROOT)
        parts = set(rel.parts)
        if parts & _SKIP_DIRS:
            continue
        if _MENTOR_ROOT_NAME in rel.parts:
            continue  # mentor read-only; use sidecar
        out.append(p)
    return sorted(out)


@lru_cache(maxsize=1)
def _load_all() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    # 1. project-maintained SKILL.md
    for md in _iter_skill_md():
        fm = _parse_frontmatter(md)
        if not fm:
            raise SkillRegistryError(
                f"skill at {md.relative_to(PROJECT_ROOT)}: no YAML frontmatter"
            )
        rel = str(md.relative_to(PROJECT_ROOT))
        entries.append(_canonicalize(fm, rel, "frontmatter"))

    # 2. sidecar external skills
    if SIDECAR_JSON.exists():
        sidecar = json.loads(SIDECAR_JSON.read_text(encoding="utf-8"))
        for raw in sidecar.get("skills", []):
            rel = raw.get("path", "<sidecar>")
            # Contract: description may be omitted in sidecar and fall back to
            # the underlying SKILL.md frontmatter.
            if not raw.get("description") and raw.get("path"):
                md = PROJECT_ROOT / raw["path"]
                if md.exists():
                    fm = _parse_frontmatter(md)
                    if fm.get("description"):
                        raw = {**raw, "description": fm["description"]}
            entries.append(_canonicalize(raw, rel, "sidecar"))

    # name uniqueness across both sources
    seen: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e["name"] in seen:
            raise SkillRegistryError(
                f"duplicate skill name {e['name']!r}: "
                f"first at {seen[e['name']]['path']}, "
                f"again at {e['path']}"
            )
        seen[e["name"]] = e
    return entries


# ---- public API -----------------------------------------------------------

def all_skills() -> list[dict[str, Any]]:
    """Return every registered skill (project + sidecar) as canonical dicts."""
    return list(_load_all())


def by_name(name: str) -> dict[str, Any]:
    """Return the skill entry by name; raise if unknown."""
    for e in _load_all():
        if e["name"] == name:
            return e
    known = sorted(e["name"] for e in _load_all())
    raise SkillRegistryError(f"unknown skill {name!r}; known: {known}")


def by_entry_tool(tool_name: str) -> list[dict[str, Any]]:
    """Return all skills whose entry_tool == tool_name (usually 0 or 1)."""
    return [e for e in _load_all() if e["entry_tool"] == tool_name]


def _tools_json_names() -> set[str]:
    """Return the set of tool names declared in tools.json."""
    manifest = json.loads(_TOOLS_JSON.read_text(encoding="utf-8"))
    return {t["name"] for t in manifest.get("tools", []) if "name" in t}


def assert_reachable() -> list[str]:
    """Check contract invariants. Return list of human-readable error strings
    (empty = healthy). Does NOT raise — callers (contract tests) decide."""
    errors: list[str] = []

    try:
        skills = _load_all()
    except SkillRegistryError as exc:
        return [str(exc)]

    try:
        tool_names = _tools_json_names()
    except Exception as exc:
        errors.append(f"could not load tools.json: {exc}")
        return errors

    active_names = {s["name"] for s in skills if s["status"] == "active"}

    for s in skills:
        # 1. entry_tool must be null or in tools.json
        et = s["entry_tool"]
        if et is not None and et not in tool_names:
            errors.append(
                f"skill {s['name']!r}: entry_tool {et!r} not in tools.json"
            )
        # 2. related_tools must all exist in tools.json
        for rt in s["related_tools"]:
            if rt not in tool_names:
                errors.append(
                    f"skill {s['name']!r}: related_tool {rt!r} not in tools.json"
                )
        # 3. supersedes/superseded_by must reference existing skill names
        for sup in s["supersedes"]:
            if sup not in {x["name"] for x in skills}:
                errors.append(
                    f"skill {s['name']!r}: supersedes {sup!r} not a known skill"
                )
        if s["superseded_by"] is not None:
            if s["superseded_by"] not in {x["name"] for x in skills}:
                errors.append(
                    f"skill {s['name']!r}: superseded_by "
                    f"{s['superseded_by']!r} not a known skill"
                )
        # 4. deprecated skill requires superseded_by unless self-declared archive
        if s["status"] == "deprecated" and not s["superseded_by"]:
            errors.append(
                f"skill {s['name']!r}: status=deprecated but no superseded_by "
                f"declared"
            )

    # 5. no active skill may claim to supersede another active skill
    for s in skills:
        if s["status"] != "active":
            continue
        for sup in s["supersedes"]:
            if sup in active_names:
                errors.append(
                    f"active skill {s['name']!r} supersedes another "
                    f"active skill {sup!r}; the superseded one should be "
                    f"marked deprecated"
                )

    return errors


def clear_cache() -> None:
    """For tests: drop caches so a fresh read picks up changes."""
    _load_all.cache_clear()


if __name__ == "__main__":
    # Sanity dump for interactive use
    for s in all_skills():
        et = s["entry_tool"] or "-"
        print(
            f"  {s['status']:<12}  {s['name']:<32}  entry={et:<28}  "
            f"path={s['path']}"
        )
    errs = assert_reachable()
    if errs:
        print("\nCONTRACT VIOLATIONS:")
        for e in errs:
            print(f"  - {e}")
    else:
        print("\nOK  every skill's entry_tool + related_tools resolve.")
