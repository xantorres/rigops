from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rigops import cli, reap_devservers, state
from rigops.cli import reap as reap_cli


class ProcsCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.config = self.home / "config.json"
        env = {"RIGOPS_CONFIG": str(self.config), "RIGOPS_STATE_DIR": str(self.home / "state")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        run = mock.patch.object(reap_devservers, "run_procs", return_value=0)
        self.run_procs = run.start()
        self.addCleanup(run.stop)

    def write_config(self, reaper: dict, mode: int = 0o600) -> None:
        self.config.write_text(json.dumps({"reaper": reaper}))
        os.chmod(self.config, mode)

    def call(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.run(lambda: reap_cli.main(["--procs-only", *argv]))
        return code, out.getvalue(), err.getvalue()

    def test_bad_sweep_type_exits_2(self):
        self.write_config({"roots": ["/w/root"], "dev_server_sweep": "yes"})
        self.assertEqual(self.call()[0], 2)
        self.run_procs.assert_not_called()

    def test_bad_regex_exits_2(self):
        self.write_config({"roots": ["/w/root"], "dev_server_patterns": ["("]})
        self.assertEqual(self.call()[0], 2)

    def test_negative_min_age_exits_2(self):
        self.write_config({"roots": ["/w/root"]})
        self.assertEqual(self.call("--min-age-s", "-1")[0], 2)

    def test_zero_min_age_allowed(self):
        self.write_config({"roots": ["/w/root"]})
        self.assertEqual(self.call("--min-age-s", "0")[0], 0)
        self.assertEqual(self.run_procs.call_args.args[0].dev_server_min_age_s, 0)

    def test_relative_config_root_exits_2(self):
        self.write_config({"roots": ["projects/app"]})
        code, _, err = self.call()
        self.assertEqual(code, 2)
        self.assertIn("error: reaper.roots: 'projects/app' must be an absolute path", err)
        self.run_procs.assert_not_called()

    def test_relative_cli_root_accepted(self):
        self.write_config({"roots": ["projects/app"]})
        self.assertEqual(self.call("--root", "rel/dir")[0], 0)
        self.run_procs.assert_called_once()

    def test_no_roots_exits_0_with_empty_json_object(self):
        self.write_config({"roots": []})
        code, out, _ = self.call("--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["repos"], [])
        self.assertEqual(report["orphan_daemons"], [])
        self.assertTrue(report["process_observation_ok"])
        self.assertEqual(report["version"], 1)
        for key in ("dev_servers", "skipped_owned", "kills", "deadline_hit"):
            self.assertIn(key, report)
        self.run_procs.assert_not_called()

    def test_no_roots_without_json_prints_no_object(self):
        self.write_config({"roots": []})
        self.assertEqual(self.call()[1], "")

    def test_apply_takes_the_full_reap_lock(self):
        self.write_config({"roots": ["/w/root"]})
        with mock.patch.object(state, "locked") as locked:
            self.assertEqual(self.call("--apply")[0], 0)
        locked.assert_called_once_with(state.state_dir() / "reap")

    def test_preview_takes_no_lock(self):
        self.write_config({"roots": ["/w/root"]})
        with mock.patch.object(state, "locked") as locked:
            self.call()
        locked.assert_not_called()

    def test_apply_refuses_group_writable_config(self):
        self.write_config({"roots": ["/w/root"]}, mode=0o660)
        code, _, err = self.call("--apply")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        self.run_procs.assert_not_called()

    def test_dry_run_without_apply_exits_2(self):
        self.write_config({"roots": ["/w/root"]})
        self.assertEqual(self.call("--dry-run")[0], 2)

    def test_session_read_error_maps_to_exit_2(self):
        self.write_config({"roots": ["/w/root"]})
        self.run_procs.side_effect = reap_devservers.RigopsError("unreadable session file x")
        code, _, err = self.call("--apply")
        self.assertEqual(code, 2)
        self.assertIn("unreadable session file", err)


if __name__ == "__main__":
    unittest.main()
