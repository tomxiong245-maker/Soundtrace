"""Contract: skill discovery MUST be complete, unambiguous, and reachable.

Sister to test_orchestrator_uses_tools_json.py in the tool layer. Where that
test guards "every orchestrator subprocess resolves through tools.json", this
test guards "every SKILL.md registers through skills_registry and its declared
entry_tool / related_tools resolve to tools.json".

The invariants this locks in:

    1. skills_registry loads without exception (no missing required fields).
    2. Every entry_tool that is not null MUST exist in tools.json.
    3. Every related_tool MUST exist in tools.json.
    4. No duplicate skill names across SKILL.md frontmatter + sidecar.
    5. Every deprecated skill has a superseded_by pointer.
    6. No active skill claims to supersede another *active* skill.
    7. Skills under mentor的成果/ MUST be registered via sidecar
       (not glob-discovered), because those files are read-only.
    8. by_name / by_entry_tool APIs behave.

If a new SKILL.md gets added and forgets these fields, this test fails at CI
time — same fail-closed pattern as tool_lookup vs tools.json.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]  # …/剪辑项目
_REGISTRY_DIR = PROJECT_ROOT / "稳定生产/challengers/skills-registry-v1/scripts"

# Put the registry scripts dir on sys.path so `import skills_registry` works
sys.path.insert(0, str(_REGISTRY_DIR))

import skills_registry  # noqa: E402


TOOLS_JSON = PROJECT_ROOT / "main/tools/tools.json"


def _tool_names_in_manifest() -> set[str]:
    data = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    return {t["name"] for t in data.get("tools", []) if "name" in t}


class SkillsRegistryContract(unittest.TestCase):

    def setUp(self) -> None:
        skills_registry.clear_cache()

    def test_all_skills_load_without_error(self) -> None:
        skills = skills_registry.all_skills()
        self.assertGreater(
            len(skills),
            0,
            "skills_registry loaded zero skills — did anything match SKILL.md glob?",
        )

    def test_every_entry_tool_exists_in_tools_json(self) -> None:
        tool_names = _tool_names_in_manifest()
        offenders: list[str] = []
        for s in skills_registry.all_skills():
            et = s["entry_tool"]
            if et is not None and et not in tool_names:
                offenders.append(f"{s['name']} -> entry_tool={et}")
        self.assertEqual(
            offenders,
            [],
            "skills declaring entry_tool not in tools.json:\n  "
            + "\n  ".join(offenders),
        )

    def test_every_related_tool_exists_in_tools_json(self) -> None:
        tool_names = _tool_names_in_manifest()
        offenders: list[str] = []
        for s in skills_registry.all_skills():
            for rt in s["related_tools"]:
                if rt not in tool_names:
                    offenders.append(f"{s['name']} -> related_tool={rt}")
        self.assertEqual(
            offenders,
            [],
            "skills declaring related_tools not in tools.json:\n  "
            + "\n  ".join(offenders),
        )

    def test_no_duplicate_skill_names(self) -> None:
        names = [s["name"] for s in skills_registry.all_skills()]
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [], f"duplicate skill names: {dupes}")

    def test_deprecated_skills_have_superseded_by(self) -> None:
        offenders = [
            s["name"]
            for s in skills_registry.all_skills()
            if s["status"] == "deprecated" and not s["superseded_by"]
        ]
        self.assertEqual(
            offenders,
            [],
            "deprecated skills missing superseded_by pointer: "
            + ", ".join(offenders),
        )

    def test_active_skill_doesnt_supersede_active(self) -> None:
        skills = skills_registry.all_skills()
        active = {s["name"] for s in skills if s["status"] == "active"}
        offenders: list[str] = []
        for s in skills:
            if s["status"] != "active":
                continue
            for sup in s["supersedes"]:
                if sup in active:
                    offenders.append(f"active {s['name']} supersedes active {sup}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_assert_reachable_is_healthy(self) -> None:
        errors = skills_registry.assert_reachable()
        self.assertEqual(
            errors,
            [],
            "skills_registry.assert_reachable() reported:\n  "
            + "\n  ".join(errors),
        )

    def test_by_name_roundtrip(self) -> None:
        for s in skills_registry.all_skills():
            self.assertEqual(skills_registry.by_name(s["name"]), s)

    def test_by_name_unknown_raises(self) -> None:
        with self.assertRaises(skills_registry.SkillRegistryError):
            skills_registry.by_name("this-skill-does-not-exist")

    def test_by_entry_tool_returns_only_matching(self) -> None:
        for s in skills_registry.all_skills():
            et = s["entry_tool"]
            if et is None:
                continue
            matches = skills_registry.by_entry_tool(et)
            self.assertIn(s, matches)
            for m in matches:
                self.assertEqual(m["entry_tool"], et)

    def test_mentor_skills_registered_via_sidecar_not_frontmatter(self) -> None:
        """SKILL.md under mentor的成果/ are read-only. They MUST appear in the
        registry via the sidecar, and MUST NOT be glob-discovered from
        frontmatter (which would violate the read-only boundary)."""
        skills = skills_registry.all_skills()
        by_path = {s["path"]: s for s in skills}
        mentor_entries = [
            s for s in skills if s["path"].startswith("mentor的成果/")
        ]
        self.assertGreater(
            len(mentor_entries),
            0,
            "Expected at least one mentor skill registered via sidecar; found none.",
        )
        for m in mentor_entries:
            self.assertEqual(
                m["source"],
                "sidecar",
                f"mentor skill {m['name']} at {m['path']} came from "
                f"frontmatter — this violates the read-only mentor boundary; "
                f"it must be registered in external_skills.json",
            )
            # And its owner should be mentor or external
            self.assertIn(m["owner"], {"mentor", "external"})

    def test_registry_covers_every_skill_md_on_disk(self) -> None:
        """Enumerate SKILL.md on disk; every one MUST be reachable through the
        registry (either via frontmatter glob or explicitly via sidecar).
        Skips the same junk dirs as the registry itself."""
        skip_dirs = skills_registry._SKIP_DIRS
        on_disk = sorted(
            str(p.relative_to(PROJECT_ROOT))
            for p in PROJECT_ROOT.rglob("SKILL.md")
            if not (set(p.parts) & skip_dirs)
        )
        registered = {s["path"] for s in skills_registry.all_skills()}
        missing = [p for p in on_disk if p not in registered]
        self.assertEqual(
            missing,
            [],
            "SKILL.md on disk not registered anywhere:\n  " + "\n  ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
