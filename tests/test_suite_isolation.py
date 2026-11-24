from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rigops import config, launchd, state

import tests

REPO = Path(__file__).resolve().parent.parent

CHILD = """
import os, sys
import tests
from pathlib import Path
from rigops import config, state
root = Path(os.environ["HOME"]).resolve().parent
state_file = state.state_dir() / "probe.json"
state.atomic_write(state_file, "{}")
print(state.state_dir().resolve())
print(config.config_path().resolve())
print(root)
print("RIGOPS_CONFIG" in os.environ)
"""


def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


class SuiteIsolationInProcess(unittest.TestCase):
    def test_home_is_not_the_real_home(self):
        self.assertNotEqual(Path(os.environ["HOME"]).resolve(), _real_home())

    def test_home_state_and_config_share_one_throwaway_root(self):
        root = Path(os.environ["HOME"]).resolve().parent
        self.assertNotEqual(root, _real_home())
        for path in (state.state_dir().resolve(), config.config_path().resolve()):
            self.assertIn(root, path.parents)
        self.assertNotEqual(root, Path(root.anchor))

    def test_config_override_is_removed(self):
        self.assertFalse("RIGOPS_CONFIG" in os.environ)


class LaunchctlFakeTests(unittest.TestCase):
    def test_mutating_call_through_launchd_lands_in_the_fake_log(self):
        before = tests.LAUNCHCTL_LOG.read_text() if tests.LAUNCHCTL_LOG.exists() else ""

        ok = launchd.kickstart("com.rigops.isolation-probe")

        self.assertFalse(ok)
        after = tests.LAUNCHCTL_LOG.read_text()
        self.assertEqual(
            after[len(before):], f"kickstart gui/{os.getuid()}/com.rigops.isolation-probe\n"
        )

    def test_launchctl_on_path_is_the_fake(self):
        self.assertEqual(shutil.which("launchctl"), str(tests._bin / "launchctl"))


class SuiteIsolationSubprocess(unittest.TestCase):
    def _run_child(self, sentinel: Path):
        env = dict(os.environ)
        env.update(
            HOME=str(sentinel),
            RIGOPS_STATE_DIR=str(sentinel / "state"),
            XDG_STATE_HOME=str(sentinel / "xdg-state"),
            XDG_CONFIG_HOME=str(sentinel / "xdg-config"),
            RIGOPS_CONFIG=str(sentinel / "config.json"),
        )
        done = subprocess.run(
            [sys.executable, "-c", CHILD],
            cwd=REPO, env=env, capture_output=True, text=True, check=True,
        )
        return done.stdout.splitlines()

    def test_import_leaves_the_inherited_locations_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp).resolve() / "sentinel"
            sentinel.mkdir()
            before = os.stat(sentinel).st_mtime_ns
            lines = self._run_child(sentinel)
            self.assertEqual(list(sentinel.iterdir()), [])
            self.assertEqual(os.stat(sentinel).st_mtime_ns, before)
            state_dir, cfg, _root, has_cfg = lines
            for printed in (state_dir, cfg):
                self.assertNotIn(sentinel, Path(printed).parents)
            self.assertEqual(has_cfg, "False")

    def test_throwaway_root_is_removed_at_interpreter_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp).resolve() / "sentinel"
            sentinel.mkdir()
            root = self._run_child(sentinel)[2]
            self.assertFalse(os.path.exists(root))


if __name__ == "__main__":
    unittest.main()
