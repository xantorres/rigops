from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import authprobe, levers  # noqa: E402
from rigops import config as rigops_config  # noqa: E402

CFG = {
    "authprobe": {
        "command": ["fake", "cmd"],
        "expect": "ok",
        "timeout_s": 5,
        "path": "/usr/bin:/bin",
        "keychain_service": "",
    },
}


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)


class BuildEnvTests(EnvIsolatedTestCase):
    def test_only_expected_keys_present(self):
        env = authprobe.build_env("/usr/bin:/bin")
        self.assertEqual(set(env), {"HOME", "USER", "PATH", "SHELL", "TERM"})

    def test_token_adds_oauth_var(self):
        env = authprobe.build_env("/usr/bin:/bin", token="tok123")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok123")

    def test_no_leakage_of_host_vars(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://example.invalid"
        env = authprobe.build_env("/usr/bin:/bin")
        self.assertNotIn("ANTHROPIC_BASE_URL", env)


class KeychainTokenTests(unittest.TestCase):
    def test_empty_service_returns_empty(self):
        self.assertEqual(authprobe.keychain_token(""), "")

    def test_no_security_binary_returns_empty(self):
        with mock.patch.object(authprobe.shutil, "which", return_value=None):
            self.assertEqual(authprobe.keychain_token("svc"), "")

    def test_runner_raising_returns_empty(self):
        def boom(*a, **k):
            raise OSError("nope")

        with mock.patch.object(authprobe.shutil, "which", return_value="/usr/bin/security"):
            self.assertEqual(authprobe.keychain_token("svc", runner=boom), "")

    def test_runner_nonzero_rc_returns_empty(self):
        def fake(*a, **k):
            return subprocess.CompletedProcess(a, 1, stdout="", stderr="err")

        with mock.patch.object(authprobe.shutil, "which", return_value="/usr/bin/security"):
            self.assertEqual(authprobe.keychain_token("svc", runner=fake), "")

    def test_runner_ok_returns_stripped_stdout(self):
        def fake(*a, **k):
            return subprocess.CompletedProcess(a, 0, stdout="  secret-token  \n", stderr="")

        with mock.patch.object(authprobe.shutil, "which", return_value="/usr/bin/security"):
            self.assertEqual(authprobe.keychain_token("svc", runner=fake), "secret-token")


def _completed(rc: int, stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["fake"], rc, stdout=stdout, stderr="")


# (name, runner, expected (ok, detail))
PROBE_CASES = [
    ("exit0_exact_expect", lambda *a, **k: _completed(0, "ok\n"), (True, "ok")),
    ("exit0_wrong_output", lambda *a, **k: _completed(0, "nope\n"), (False, "nope")),
    ("nonzero_exit", lambda *a, **k: _completed(1, "boom\n"), (False, "boom")),
]


class ProbeTests(unittest.TestCase):
    def test_decision_table(self):
        for name, runner, expected in PROBE_CASES:
            with self.subTest(name):
                self.assertEqual(authprobe.probe(CFG, runner=runner), expected)

    def test_timeout_returns_false_with_message(self):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="fake", timeout=5)

        ok, detail = authprobe.probe(CFG, runner=boom)
        self.assertFalse(ok)
        self.assertEqual(detail, "timeout after 5s")

    def test_runner_called_with_scrubbed_env_and_tempdir_cwd(self):
        captured = {}

        def fake(command, **kwargs):
            captured.update(kwargs)
            return _completed(0, "ok\n")

        authprobe.probe(CFG, runner=fake)
        self.assertEqual(set(captured["env"]), {"HOME", "USER", "PATH", "SHELL", "TERM"})
        self.assertEqual(captured["cwd"], tempfile.gettempdir())

    def test_string_command_returns_invalid_error(self):
        cfg = {"authprobe": {**CFG["authprobe"], "command": "claude"}}
        self.assertEqual(authprobe.probe(cfg), (False, "invalid authprobe.command"))

    def test_path_none_falls_back_to_default(self):
        cfg = {"authprobe": {**CFG["authprobe"], "path": None}}
        captured = {}

        def fake(command, **kwargs):
            captured.update(kwargs)
            return _completed(0, "ok\n")

        authprobe.probe(cfg, runner=fake)
        self.assertEqual(
            captured["env"]["PATH"], rigops_config.DEFAULTS["authprobe"]["path"]
        )

    def test_invalid_timeout_s_falls_back_to_60(self):
        cfg = {"authprobe": {**CFG["authprobe"], "timeout_s": "abc"}}
        captured = {}

        def fake(command, **kwargs):
            captured.update(kwargs)
            return _completed(0, "ok\n")

        authprobe.probe(cfg, runner=fake)
        self.assertEqual(captured["timeout"], 60)

    def test_nonzero_exit_stdout_empty_falls_back_to_stderr(self):
        def fake(*a, **k):
            return subprocess.CompletedProcess(["fake"], 2, stdout="", stderr="Not logged in\nmore")

        ok, detail = authprobe.probe(CFG, runner=fake)
        self.assertFalse(ok)
        self.assertEqual(detail, "Not logged in")

    def test_string_timeout_reaches_runner_as_float(self):
        cfg = {"authprobe": {**CFG["authprobe"], "timeout_s": "30"}}
        captured = {}

        def fake(command, **kwargs):
            captured.update(kwargs)
            return _completed(0, "ok\n")

        authprobe.probe(cfg, runner=fake)
        self.assertIsInstance(captured["timeout"], float)
        self.assertEqual(captured["timeout"], 30.0)

    def test_infinite_timeout_falls_back_to_60(self):
        cfg = {"authprobe": {**CFG["authprobe"], "timeout_s": float("inf")}}
        captured = {}

        def fake(command, **kwargs):
            captured.update(kwargs)
            return _completed(0, "ok\n")

        authprobe.probe(cfg, runner=fake)
        self.assertEqual(captured["timeout"], 60.0)

    def test_stub_without_stderr_attr_degrades(self):
        class NoErr:
            returncode = 2
            stdout = ""

        ok, detail = authprobe.probe(CFG, runner=lambda *a, **k: NoErr())
        self.assertFalse(ok)
        self.assertEqual(detail, "exit 2")


class LeversSourceAnnotationsTests(unittest.TestCase):
    def test_custom_entry_present_and_builtin_keys_always_present(self):
        cfg = {
            "sources": {
                "custom": [{"name": "probe1", "argv": ["sh", "-c", 'echo \'{"y": 2}\'']}],
            },
        }
        out = levers.source_annotations(cfg, dt.datetime.now(), dt.datetime.now())
        self.assertIn("rtk", out)
        self.assertIn("ccusage", out)
        self.assertEqual(out["probe1"], {"y": 2})

    def test_non_dict_rtk_cfg_no_raise(self):
        cfg = {"sources": {"rtk": "yes"}}
        out = levers.source_annotations(cfg, dt.datetime.now(), dt.datetime.now())
        self.assertIn("rtk", out)
        self.assertTrue(out["rtk"] is None or isinstance(out["rtk"], dict))

    def test_custom_entry_named_rtk_ignored(self):
        cfg = {
            "sources": {
                "custom": [{"name": "rtk", "argv": ["sh", "-c", 'echo \'{"bogus": true}\'']}],
            },
        }
        out = levers.source_annotations(cfg, dt.datetime.now(), dt.datetime.now())
        self.assertNotEqual(out.get("rtk"), {"bogus": True})


class LeversSummaryLineTests(unittest.TestCase):
    def test_non_dict_source_segments_omitted(self):
        row = {
            "date": "2026-08-23",
            "sources": {"rtk": [1, 2], "ccusage": "x"},
        }
        line = levers.summary_line(row, None, None, {})
        self.assertNotIn("rtk saved", line)
        self.assertNotIn("spend $", line)


if __name__ == "__main__":
    unittest.main()
