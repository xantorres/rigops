from __future__ import annotations

import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
