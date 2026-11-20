from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from rigops import config  # noqa: E402

REAP = REPO / "libexec" / "rigops-reap"


class DefaultsTests(unittest.TestCase):
    def test_reaper_roots_default_is_empty(self):
        self.assertEqual(config.DEFAULTS["reaper"]["roots"], [])

    def test_ignored_untracked_dirs_default_is_generic(self):
        self.assertEqual(config.DEFAULTS["reaper"]["ignored_untracked_dirs"],
                         ["node_modules", "dist", ".turbo"])


class ReapNoRootsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "config.json"
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.tmp),
            "RIGOPS_CONFIG": str(self.config),
            "RIGOPS_STATE_DIR": str(self.tmp / "state"),
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run([sys.executable, str(REAP), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_no_roots_prints_one_actionable_line_and_exits_zero(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stderr,
            f"rigops reap: no roots configured; set reaper.roots in {self.config} "
            "or pass --root <dir>\n")
        self.assertEqual(proc.stdout, "")

    def test_json_keeps_the_empty_payload_on_stdout(self):
        proc = self._run("--json")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr.count("\n"), 1)
        payload = json.loads(proc.stdout)
        self.assertEqual(
            sorted(payload),
            ["generated", "orphan_daemons", "process_observation_ok", "repos", "version"])
        self.assertEqual(payload["repos"], [])
        self.assertEqual(payload["orphan_daemons"], [])

    def test_invalid_max_worktrees_exits_two_even_with_no_roots(self):
        self.config.write_text(json.dumps({"reaper": {"max_worktrees": -1}}))
        proc = self._run()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("reaper.max_worktrees", proc.stderr)

    def test_invalid_orphan_sweep_exits_two_even_with_no_roots(self):
        self.config.write_text(json.dumps({"reaper": {"orphan_sweep": "yes"}}))
        proc = self._run()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("reaper.orphan_sweep", proc.stderr)

    def test_explicit_root_still_scans(self):
        root = self.tmp / "tree"
        root.mkdir()
        proc = self._run("--root", str(root), "--json")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("no roots configured", proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["repos"], [])

    def test_configured_roots_still_scan(self):
        root = self.tmp / "tree"
        root.mkdir()
        self.config.write_text(json.dumps({"reaper": {"roots": [str(root)]}}))
        proc = self._run("--json")
        self.assertNotIn("no roots configured", proc.stderr)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
