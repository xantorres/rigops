from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBEXEC = REPO_ROOT / "libexec"


class CliContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.state = self.home / "state"
        self.config = self.home / "config.json"

    def run_cmd(self, cmd, *args, config=None):
        env = {
            **os.environ,
            "HOME": str(self.home),
            "RIGOPS_STATE_DIR": str(self.state),
            "RIGOPS_CONFIG": str(self.config),
            "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
            "XDG_STATE_HOME": str(self.home / "xdg-state"),
        }
        if config is not None:
            self.config.write_text(config if isinstance(config, str) else json.dumps(config))
        return subprocess.run(
            [sys.executable, str(LIBEXEC / f"rigops-{cmd}"), *args],
            capture_output=True, text=True, check=False, timeout=60, env=env,
            stdin=subprocess.DEVNULL,
        )

    def test_reap_help_works_with_a_broken_config(self):
        proc = self.run_cmd("reap", "--help", config="{not json")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage:", proc.stdout)

    def json_out(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_tax_json_is_valid_when_fixed_tax_is_not_configured(self):
        payload = self.json_out(self.run_cmd("tax", "--json"))

        self.assertEqual(payload, {"files": [], "total_bytes": 0})

    def test_tax_json_is_valid_when_no_file_matches(self):
        config = {"fixed_tax": {"paths": [str(self.home / "missing.md")]}}
        payload = self.json_out(self.run_cmd("tax", "--json", config=config))

        self.assertEqual(payload, {"files": [], "total_bytes": 0})

    def test_tax_history_json_is_an_empty_list_without_ledger_rows(self):
        self.assertEqual(self.json_out(self.run_cmd("tax", "--history", "--json")), [])

    def test_ledger_diff_json_is_valid_with_fewer_than_two_rows(self):
        payload = self.json_out(self.run_cmd("ledger", "diff", "--json"))

        self.assertEqual(payload, {
            "baseline": None, "latest": None, "deltas": {}, "interventions": [], "derived": [],
        })

    def test_backlog_lint_json_is_valid_without_a_backlog_file(self):
        missing = str(self.home / "none.md")
        payload = self.json_out(self.run_cmd("backlog", "lint", "--json", "--file", missing))

        self.assertEqual(payload, {
            "open": 0, "done": 0, "malformed": [], "unknown_area": [], "ok": True,
        })

    def test_janitor_json_is_valid_when_nothing_is_configured(self):
        payload = self.json_out(self.run_cmd("janitor", "--json"))

        self.assertEqual(payload, {
            "rules": [], "total_deleted": 0, "watermarks": [], "apply": False,
        })

    def test_render_json_without_check_is_a_usage_error(self):
        proc = self.run_cmd("render", "claude", "--json", "--home", str(self.home))

        self.assertEqual(proc.returncode, 2)
        self.assertIn("--json", proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
