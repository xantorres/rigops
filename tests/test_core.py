from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core  # noqa: E402


class ShortTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(core, "HOME", Path("/home/alice"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_path_under_home_collapses_to_tilde(self):
        self.assertEqual(core.short(Path("/home/alice/rig/law.md")), "~/rig/law.md")

    def test_home_itself_is_a_bare_tilde(self):
        self.assertEqual(core.short("/home/alice"), "~")

    def test_path_outside_home_is_unchanged(self):
        self.assertEqual(core.short("/opt/rig/law.md"), "/opt/rig/law.md")

    def test_home_text_inside_a_path_is_not_collapsed(self):
        outside = "/mnt/backup/home/alice/law.md"
        self.assertEqual(core.short(outside), outside)


class UtcStampTests(unittest.TestCase):
    def test_a_written_stamp_parses_back_to_the_same_utc_second(self):
        moment = datetime(2026, 11, 18, 9, 30, 5, tzinfo=timezone.utc)
        self.assertEqual(core.parse_utc_stamp(moment.strftime(core.UTC_STAMP)), moment)

    def test_anything_but_a_stamp_is_none(self):
        for value in (None, "", "2026-11-18 09:30:05", "2026-11-18T09:30:05+00:00", 5):
            with self.subTest(value=value):
                self.assertIsNone(core.parse_utc_stamp(value))


class AlwaysOnTests(unittest.TestCase):
    def test_only_a_top_level_paths_key_in_the_leading_frontmatter_scopes_a_rule(self):
        cases = {
            "no frontmatter": ("# Rule\n", True),
            "frontmatter without paths": ("---\nname: x\n---\n# Rule\n", True),
            "top-level paths": ("---\npaths:\n  - '**/*.py'\n---\n", False),
            "crlf paths": ("---\r\npaths: ['*.py']\r\n---\r\n", False),
            "globs is not a scope": ("---\nglobs: '*.py'\n---\n", True),
            "indented paths is not a scope": ("---\nmeta:\n  paths: x\n---\n", True),
            "paths after the frontmatter": ("---\nname: x\n---\npaths: x\n", True),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, (text, expected) in cases.items():
                with self.subTest(name):
                    path = Path(tmp) / "rule.md"
                    path.write_text(text)
                    self.assertIs(core.is_always_on(path), expected)
            self.assertFalse(core.is_always_on(Path(tmp) / "gone.md"))


if __name__ == "__main__":
    unittest.main()
