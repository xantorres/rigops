from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
