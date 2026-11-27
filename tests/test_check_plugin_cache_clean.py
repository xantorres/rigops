from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rigops import core
from rigops.doctor import check_plugin_cache_clean


def _aged(path: Path, days: float) -> None:
    path.mkdir(parents=True)
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


class PluginCacheCleanTests(unittest.TestCase):
    def _run(self, make):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "plugins" / "cache"
            cache.mkdir(parents=True)
            make(cache)
            with mock.patch.object(core, "CLAUDE_DIR", Path(tmp)):
                return list(check_plugin_cache_clean.run({}))

    def test_a_temp_git_dir_older_than_the_janitor_cadence_fails(self):
        found = self._run(lambda cache: _aged(cache / "temp_git_123_abc", 9))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check, "plugin_cache_clean")
        self.assertEqual(found[0].severity, "fail")
        self.assertIn("temp_git_123_abc", found[0].evidence)

    def test_a_fresh_temp_git_dir_passes(self):
        self.assertEqual(self._run(lambda cache: _aged(cache / "temp_git_1_x", 0.1)), [])

    def test_a_temp_git_dir_inside_the_janitor_cadence_passes(self):
        self.assertEqual(self._run(lambda cache: _aged(cache / "temp_git_2_y", 6)), [])

    def test_other_old_dirs_pass(self):
        self.assertEqual(self._run(lambda cache: _aged(cache / "marketplace", 30)), [])

    def test_a_missing_cache_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(core, "CLAUDE_DIR", Path(tmp)):
                self.assertEqual(list(check_plugin_cache_clean.run({})), [])


if __name__ == "__main__":
    unittest.main()
