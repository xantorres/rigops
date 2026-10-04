from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import render  # noqa: E402

LAW_MD = """# Law

Six critical rules, zero tolerance, every project.

1. **Rule one.** Full: x.
2. **Rule two.** Full: x.
3. **Rule three.** Full: x.
4. **Rule four.** Full: x.
5. **Rule five.** Full: x.
6. **Rule six.** Full: x.

## Gates

gate body

## Voice

voice body line one
voice body line two

## Knowledge routing

routing body

## Where things live

where body

## Search

Never plain grep -r or find. Content: rg.

## rtk

rtk body

## Token discipline

td body

## Compact instructions

ci body
"""

AGENT_MD = """---
name: demo
purpose: does the thing, quite a useful thing to have around
tier: sonnet
tools: [read, shell]
memory: project
voice: caveman
---
Body text of the agent prompt.
"""

INHERIT_AGENT_MD = """---
name: plain
purpose: a plain agent
tools: read
---
Plain body.
"""

ALL_TOOLS_AGENT_MD = """---
name: broad
purpose: an agent that keeps every tool
tier: sonnet
---
Broad body.
"""


class RenderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = self.tmp / "rig"
        self.home = self.tmp / "home"
        self.source.mkdir()
        self.home.mkdir()
        (self.source / "law.md").write_text(LAW_MD)

        (self.source / "rules" / "sub").mkdir(parents=True)
        (self.source / "rules" / "sub" / "a.md").write_text(
            '---\npaths: ["src/**"]\n---\nrule body\n'
        )

        (self.source / "agents").mkdir()
        (self.source / "agents" / "demo.md").write_text(AGENT_MD)
        (self.source / "agents" / "plain.md").write_text(INHERIT_AGENT_MD)
        (self.source / "agents" / "broad.md").write_text(ALL_TOOLS_AGENT_MD)

        skill_dir = self.source / "skills" / "myskill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: myskill\n---\nskill body\n")
        self.script = skill_dir / "run.sh"
        self.script.write_text("#!/bin/sh\necho hi\n")
        os.chmod(self.script, 0o755)

        (self.source / "registry").mkdir()
        self.pointer_repo = self.home / "projects" / "personal" / "opsdeck"
        self.pointer_repo.mkdir(parents=True)
        (self.pointer_repo / "CLAUDE.md").write_text("# stack\n")
        self.readme_repo = self.home / "projects" / "personal" / "readme-only"
        self.readme_repo.mkdir(parents=True)
        (self.source / "registry" / "roots.json").write_text(json.dumps({
            "roots": [{"path": "~/projects/personal/*", "realm": "personal", "repo": True}],
            "pointer_repos": [
                "~/projects/personal/opsdeck",
                "~/projects/personal/readme-only",
                "~/projects/personal/does-not-exist",
            ],
        }))

    def tearDown(self):
        self._tmp.cleanup()


class MarkerPlacementTests(RenderTestCase):
    def test_marker_lands_after_frontmatter_for_rule_files(self):
        rendered = render.render_claude(self.source, self.home)
        target = self.home / ".claude" / "rules" / "sub" / "a.md"
        text = rendered.files[target].decode()
        lines = text.splitlines()
        self.assertEqual(lines[0], "---")
        self.assertEqual(lines[2], "---")
        self.assertTrue(lines[3].startswith("<!-- rendered by rigops render from"))

    def test_marker_is_line_one_when_no_frontmatter(self):
        rendered = render.render_claude(self.source, self.home)
        text = rendered.files[self.home / ".claude" / "CLAUDE.md"].decode()
        self.assertTrue(text.splitlines()[0].startswith("<!-- rendered by rigops render from"))


class ClaudeAgentTests(RenderTestCase):
    def test_tools_order_and_optional_fields(self):
        rendered = render.render_claude(self.source, self.home)
        text = rendered.files[self.home / ".claude" / "agents" / "demo.md"].decode()
        self.assertIn("tools: Read, Bash", text)
        self.assertIn("model: sonnet", text)
        self.assertIn("memory: project", text)

    def test_inherit_tier_and_none_memory_are_omitted(self):
        rendered = render.render_claude(self.source, self.home)
        text = rendered.files[self.home / ".claude" / "agents" / "plain.md"].decode()
        self.assertNotIn("model:", text)
        self.assertNotIn("memory:", text)

    def test_agent_without_tool_classes_omits_the_tools_line(self):
        rendered = render.render_claude(self.source, self.home)
        text = rendered.files[self.home / ".claude" / "agents" / "broad.md"].decode()
        self.assertNotIn("tools:", text)
        self.assertIn("model: sonnet", text)


class SkillFileTests(RenderTestCase):
    def test_script_keeps_exec_bit(self):
        rendered = render.render_claude(self.source, self.home)
        target = self.home / ".claude" / "skills" / "myskill" / "run.sh"
        self.assertEqual(rendered.modes[target], stat.S_IMODE(self.script.stat().st_mode))


class CodexTests(RenderTestCase):
    def test_agents_md_line_count(self):
        rendered = render.render_codex(self.source, self.home)
        text = rendered.files[self.home / ".codex" / "AGENTS.md"].decode()
        self.assertLessEqual(len(text.splitlines()), 25)

    def test_toml_content_and_model_omitted_for_inherit(self):
        rendered = render.render_codex(self.source, self.home)
        demo = rendered.files[self.home / ".codex" / "agents" / "demo.toml"].decode()
        self.assertIn('name = "demo"', demo)
        self.assertIn('description = "does the thing', demo)
        self.assertIn('model = "sonnet"', demo)
        self.assertIn("developer_instructions = '''", demo)

        plain = rendered.files[self.home / ".codex" / "agents" / "plain.toml"].decode()
        self.assertNotIn("model =", plain)


class LocalTests(RenderTestCase):
    def test_prompt_has_only_law_and_voice(self):
        rendered = render.render_local(self.source, self.home)
        text = rendered.files[self.home / ".config" / "ai-agent" / "system-prompt.md"].decode()
        self.assertIn("# Law", text)
        self.assertIn("## Voice", text)
        self.assertNotIn("## Gates", text)
        self.assertNotIn("## Knowledge routing", text)


class AgentsDocTests(RenderTestCase):
    def test_table_has_a_row_per_agent(self):
        rendered = render.render_agents(self.source, self.home)
        text = rendered.files[self.home / ".claude" / "references" / "agents.md"].decode()
        self.assertIn("| demo | sonnet | project | caveman |", text)
        self.assertIn("| plain | inherit | none |", text)


class ReposTests(RenderTestCase):
    def test_pointer_repo_line_count_and_claude_entry(self):
        rendered = render.render_repos(self.source, self.home)
        target = self.pointer_repo / "AGENTS.md"
        text = rendered.files[target].decode()
        self.assertLessEqual(len(text.splitlines()), 25)
        self.assertIn("`CLAUDE.md`", text)
        self.assertIn("repo `opsdeck`", text)
        self.assertIn("realm `personal`", text)

    def test_readme_fallback_when_claude_md_absent(self):
        rendered = render.render_repos(self.source, self.home)
        text = rendered.files[self.readme_repo / "AGENTS.md"].decode()
        self.assertIn("`README.md`", text)

    def test_missing_repo_dir_is_skipped_not_rendered(self):
        rendered = render.render_repos(self.source, self.home)
        missing = self.home / "projects" / "personal" / "does-not-exist" / "AGENTS.md"
        self.assertNotIn(missing, rendered.files)
        self.assertIn(missing, rendered.skipped)


class ApplyAndCheckTests(RenderTestCase):
    def test_check_is_empty_after_apply_except_skipped(self):
        rendered = render.render_all(self.source, self.home)
        render.apply(rendered, self.home)
        results = render.check(rendered)
        states = {r["state"] for r in results}
        self.assertEqual(states, {"skipped"})

    def test_editing_a_rendered_file_reports_differs(self):
        rendered = render.render_all(self.source, self.home)
        render.apply(rendered, self.home)
        target = self.home / ".claude" / "CLAUDE.md"
        target.write_text("tampered\n")
        results = render.check(rendered)
        self.assertIn({"path": str(target), "state": "differs"}, results)

    def test_stale_file_in_managed_dir_is_reported_then_removed_on_reapply(self):
        rendered = render.render_all(self.source, self.home)
        render.apply(rendered, self.home)
        stray = self.home / ".claude" / "rules" / "stray.md"
        stray.write_text("junk\n")
        results = render.check(rendered)
        self.assertIn({"path": str(stray), "state": "stale"}, results)

        render.apply(rendered, self.home)
        self.assertFalse(stray.exists())

    def test_file_outside_managed_dirs_is_never_deleted(self):
        rendered = render.render_all(self.source, self.home)
        render.apply(rendered, self.home)
        outsider = self.home / ".claude" / "settings.json"
        outsider.write_text("{}\n")
        render.apply(rendered, self.home)
        self.assertTrue(outsider.exists())

    def test_only_changed_or_symlinked_targets_are_rewritten(self):
        unchanged = self.home / "unchanged.md"
        unchanged.write_bytes(b"same\n")
        os.chmod(unchanged, 0o644)
        os.utime(unchanged, (1_000_000_000, 1_000_000_000))
        changed = self.home / "changed.md"
        changed.write_bytes(b"old\n")
        elsewhere = self.tmp / "elsewhere.md"
        elsewhere.write_bytes(b"same\n")
        linked = self.home / "linked.md"
        linked.symlink_to(elsewhere)
        rendered = render.Rendered(
            files={unchanged: b"same\n", changed: b"new\n", linked: b"same\n"},
            modes={unchanged: 0o755},
        )

        log = render.apply(rendered, self.home)

        self.assertEqual(unchanged.stat().st_mtime, 1_000_000_000)
        self.assertEqual(stat.S_IMODE(unchanged.stat().st_mode), 0o755)
        self.assertEqual(changed.read_bytes(), b"new\n")
        self.assertFalse(linked.is_symlink())
        self.assertEqual(linked.read_bytes(), b"same\n")
        self.assertEqual(log, [f"wrote {changed}", f"wrote {linked}"])

    def test_symlinked_target_in_managed_dir_is_rewritten_not_removed(self):
        managed = self.home / "skills"
        managed.mkdir()
        elsewhere = self.tmp / "elsewhere.sh"
        elsewhere.write_bytes(b"echo hi\n")
        target = managed / "run.sh"
        target.symlink_to(elsewhere)
        rendered = render.Rendered(files={target: b"echo hi\n"}, managed_dirs=[managed])

        log = render.apply(rendered, self.home)

        self.assertEqual(log, [f"wrote {target}"])
        self.assertTrue(target.is_file())
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), b"echo hi\n")


class PluginTypeFileTests(RenderTestCase):
    def setUp(self):
        super().setUp()
        manifest = self.source / "skills" / "myskill" / ".claude-plugin"
        manifest.mkdir()
        (manifest / "plugin.json").write_text('{"name": "myskill"}\n')
        self.rendered = render.render_all(self.source, self.home)
        render.apply(self.rendered, self.home)
        self.plugin = self.home / ".claude" / "skills" / "myskill"

    def write(self, rel, text="x\n"):
        path = self.plugin / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def drift(self):
        return [r for r in render.check(self.rendered) if r["state"] != "skipped"]

    def test_types_tree_under_the_plugin_dir_is_not_reported_stale(self):
        self.write(".claude-plugin/types/.gitignore", "*\n")
        self.write(".claude-plugin/types/tsconfig.json")
        self.write(".claude-plugin/types/sdk/index.d.ts")
        self.assertEqual(self.drift(), [])

    def test_tsconfig_beside_the_plugin_dir_is_not_reported_stale(self):
        self.write("tsconfig.json", '{"extends": "./.claude-plugin/types/tsconfig.json"}\n')
        self.assertEqual(self.drift(), [])

    def test_engine_files_survive_a_reapply(self):
        kept = [
            self.write("tsconfig.json"),
            self.write(".claude-plugin/types/.gitignore"),
            self.write(".claude-plugin/types/sdk/index.d.ts"),
        ]
        render.apply(self.rendered, self.home)
        self.assertEqual([p.is_file() for p in kept], [True, True, True])

    def test_tsconfig_with_no_plugin_dir_beside_it_is_stale_and_removed(self):
        stray = self.write("hooks/tsconfig.json")
        self.assertEqual(self.drift(), [{"path": str(stray), "state": "stale"}])
        render.apply(self.rendered, self.home)
        self.assertFalse(stray.exists())

    def test_types_dir_outside_the_plugin_dir_is_stale_and_removed(self):
        stray = self.write("types/index.d.ts")
        self.assertEqual(self.drift(), [{"path": str(stray), "state": "stale"}])
        render.apply(self.rendered, self.home)
        self.assertFalse(stray.exists())

    def test_other_stray_file_in_the_plugin_dir_is_stale_and_removed(self):
        stray = self.write(".claude-plugin/stray.json")
        self.assertEqual(self.drift(), [{"path": str(stray), "state": "stale"}])
        render.apply(self.rendered, self.home)
        self.assertFalse(stray.exists())

    def test_source_provided_files_in_those_places_are_still_rendered(self):
        skill = self.source / "skills" / "myskill"
        (skill / "tsconfig.json").write_text('{"strict": true}\n')
        (skill / ".claude-plugin" / "types").mkdir()
        (skill / ".claude-plugin" / "types" / "index.d.ts").write_text("export {};\n")
        rendered = render.render_all(self.source, self.home)
        render.apply(rendered, self.home)

        tsconfig = self.plugin / "tsconfig.json"
        types = self.plugin / ".claude-plugin" / "types" / "index.d.ts"
        self.assertEqual(tsconfig.read_text(), '{"strict": true}\n')
        self.assertEqual(types.read_text(), "export {};\n")
        tsconfig.write_text("tampered\n")
        self.assertIn({"path": str(tsconfig), "state": "differs"}, render.check(rendered))


if __name__ == "__main__":
    unittest.main()


class StaleScanIgnoresCaches(unittest.TestCase):
    def test_pycache_in_managed_dir_is_neither_stale_nor_deleted(self):
        import tempfile
        from pathlib import Path

        from rigops.render import combine
        from rigops.render.common import Rendered

        with tempfile.TemporaryDirectory() as tmp:
            managed = Path(tmp) / "skills"
            (managed / "one").mkdir(parents=True)
            keep = managed / "one" / "SKILL.md"
            cache = managed / "one" / "__pycache__" / "x.cpython-314.pyc"
            cache.parent.mkdir()
            cache.write_bytes(b"cache")
            rendered = Rendered(files={keep: b"body"}, managed_dirs=[managed])
            combine.apply(rendered, Path(tmp))
            self.assertTrue(cache.exists())
            self.assertEqual(combine.check(rendered), [])
