"""CLI-layer contract for `rigops card` and `rigops nudge`: hook-mode JSON
parsing, the always-exit-0 and print-nothing-on-error rule, and the
replayed-prompt guard. The pure decision logic has its own table tests in
test_context_card.py and test_context_nudge.py; this file only exercises the
libexec scripts as subprocesses.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CARD_SCRIPT = REPO_ROOT / "libexec" / "rigops-card"
NUDGE_SCRIPT = REPO_ROOT / "libexec" / "rigops-nudge"


def _env(tmp: Path) -> dict:
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp / "xdg")
    env["RIGOPS_CONFIG"] = str(tmp / "no-such-config.json")
    return env


def _registry(tmp: Path, cwd_name="acme-app") -> tuple:
    cwd = tmp / "repos" / cwd_name
    cwd.mkdir(parents=True)
    path = tmp / "roots.json"
    path.write_text(json.dumps({
        "realms": ["work"], "scopes": ["repo"],
        "roots": [{"path": str(cwd), "realm": "work", "scope": "repo", "repo": True,
                   "cwd_scopes": ["repo"]}],
    }))
    return path, cwd


def _run(script: Path, args: list, env: dict, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], input=stdin,
        env=env, capture_output=True, text=True, check=False, timeout=30,
    )


# The host's own doctor record and nudge state must never leak into these tests.
_STATE_HOME = tempfile.TemporaryDirectory()
_STATE_ENV = mock.patch.dict(os.environ, {"XDG_STATE_HOME": _STATE_HOME.name})


def setUpModule():
    _STATE_ENV.start()


def tearDownModule():
    _STATE_ENV.stop()
    _STATE_HOME.cleanup()


class CardHookModeTests(unittest.TestCase):
    def test_valid_hook_input_prints_lines_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path, cwd = _registry(tmp)
            stdin = json.dumps({"cwd": str(cwd)})
            result = _run(CARD_SCRIPT, ["--hook", "--registry", str(registry_path)],
                           _env(tmp), stdin)
        self.assertEqual(result.returncode, 0)
        self.assertIn("realm work", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_malformed_hook_input_exits_zero_prints_nothing_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            result = _run(CARD_SCRIPT, ["--hook"], _env(tmp), "{not json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("error:", result.stderr)

    def test_missing_cwd_falls_back_to_process_cwd(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            # Resolved: the subprocess's own os.getcwd() always returns the
            # OS-canonical path, so the registry must be built against that
            # same form or the fallback would look unmapped on a symlinked
            # temp tree (e.g. macOS /var -> /private/var).
            tmp = Path(tmp_s).resolve()
            registry_path, cwd = _registry(tmp)
            result = subprocess.run(
                [sys.executable, str(CARD_SCRIPT), "--hook", "--registry", str(registry_path)],
                input="{}", cwd=str(cwd), env=_env(tmp),
                capture_output=True, text=True, check=False, timeout=30,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("realm work", result.stdout)


class NudgeHookModeTests(unittest.TestCase):
    def test_replayed_prompt_prints_nothing(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path, cwd = _registry(tmp)
            stdin = json.dumps({
                "prompt": "<task-notification>done</task-notification>",
                "cwd": str(cwd), "session_id": "sess-1",
            })
            result = _run(NUDGE_SCRIPT, ["--hook", "--no-log", "--registry", str(registry_path)],
                           _env(tmp), stdin)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_malformed_hook_input_exits_zero_prints_nothing_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            result = _run(NUDGE_SCRIPT, ["--hook", "--no-log"], _env(tmp), "{not json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("error:", result.stderr)

    def test_no_nudges_file_and_no_index_is_silent_but_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path, cwd = _registry(tmp)
            stdin = json.dumps({"prompt": "hello there", "cwd": str(cwd), "session_id": "s"})
            result = _run(NUDGE_SCRIPT, ["--hook", "--no-log", "--registry", str(registry_path)],
                           _env(tmp), stdin)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_json_flag_reports_the_documented_keys(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path, cwd = _registry(tmp)
            result = _run(NUDGE_SCRIPT, ["--cwd", str(cwd), "--no-log", "--json",
                                         "--registry", str(registry_path), "hello"],
                           _env(tmp))
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(
            set(payload),
            {"silent", "tokens", "nudges", "suppressed", "prefetch", "ms", "realm"},
        )
        self.assertEqual(payload["realm"], "work")

    def test_unwritable_log_dir_still_prints_the_nudge(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path, cwd = _registry(tmp)
            (tmp / "nudges.json").write_text(json.dumps({"nudges": [
                {"name": "pr", "pattern": "pr", "say": "careful with PRs"},
            ]}))
            env = _env(tmp)
            # XDG_STATE_HOME (env["XDG_STATE_HOME"] == tmp/"xdg") as a plain file:
            # the retrieval log's write can't create its directory.
            Path(env["XDG_STATE_HOME"]).write_text("not a directory")
            stdin = json.dumps({"prompt": "open a pr", "cwd": str(cwd), "session_id": "sess-1"})
            result = _run(NUDGE_SCRIPT, ["--hook", "--registry", str(registry_path)],
                           env, stdin)
        self.assertEqual(result.returncode, 0)
        self.assertIn("careful with PRs", result.stdout)

    def test_non_hook_direct_text_is_never_treated_as_a_session(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path, cwd = _registry(tmp)
            result = _run(NUDGE_SCRIPT, ["--cwd", str(cwd), "--no-log",
                                         "--registry", str(registry_path), "hello"],
                           _env(tmp))
        self.assertEqual(result.returncode, 0)
        self.assertFalse((tmp / "xdg" / "rigops" / "nudge").exists())


if __name__ == "__main__":
    unittest.main()
