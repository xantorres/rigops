from __future__ import annotations

import json
import unittest
from pathlib import Path

from rigops import frontmatter

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugin"

ALWAYS_LOADED_FILES = [
    "commands/backlog.md",
    "commands/doctor.md",
    "commands/ledger.md",
    "commands/tax.md",
    "commands/week.md",
    "skills/fleet-triage/SKILL.md",
    "skills/ops-loop/SKILL.md",
]
ALWAYS_LOADED_BYTES = 1409


def always_loaded_bytes() -> dict[str, int]:
    sizes = {}
    for path in [*PLUGIN.glob("skills/*/SKILL.md"), *PLUGIN.glob("commands/*.md")]:
        fields, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
        stem = path.parent.name if path.name == "SKILL.md" else path.stem
        text = fields.get("name", stem) + fields.get("description", "")
        sizes[path.relative_to(PLUGIN).as_posix()] = len(text.encode("utf-8"))
    return sizes


class PluginSurfaceTests(unittest.TestCase):
    def test_contributing_files_are_the_pinned_list(self):
        self.assertEqual(sorted(always_loaded_bytes()), ALWAYS_LOADED_FILES)

    def test_always_loaded_bytes_are_pinned(self):
        sizes = always_loaded_bytes()
        self.assertEqual(
            sum(sizes.values()), ALWAYS_LOADED_BYTES,
            f"the plugin's always-loaded bytes (skill and command name plus description) "
            f"changed; per file: {sizes}. Update ALWAYS_LOADED_BYTES in this test and add a "
            f"CHANGELOG.md line saying why the fixed context cost moved.")

    def test_hooks_register_only_the_two_events_through_the_shim(self):
        hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(sorted(hooks), ["SessionStart", "UserPromptSubmit"])
        for event, groups in hooks.items():
            for group in groups:
                for entry in group["hooks"]:
                    with self.subTest(event=event):
                        self.assertIn("/hooks/rigops-hook.sh", entry["command"])


if __name__ == "__main__":
    unittest.main()
