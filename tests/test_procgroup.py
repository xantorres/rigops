from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import procgroup  # noqa: E402

from tests.test_doctor import doctor  # noqa: E402


def pid_is_gone(pid: int, wait_s: float = 5.0) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        probe = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False,
        )
        if not probe.stdout.strip() or probe.stdout.strip().startswith("Z"):
            return True
        time.sleep(0.1)
    return False


def sleeper_command(pid_file: Path) -> str:
    return f"sleep 300 & echo $! > '{pid_file}'; wait"


def wait_for_pid(pid_file: Path, wait_s: float = 5.0) -> int | None:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        text = pid_file.read_text().strip() if pid_file.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    return None


class SleeperCleanup:
    def track(self, test: unittest.TestCase, pid_file: Path) -> None:
        def kill() -> None:
            text = pid_file.read_text().strip() if pid_file.exists() else ""
            if text:
                try:
                    os.kill(int(text), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
        test.addCleanup(kill)


class RunGroupTests(unittest.TestCase):
    def test_success_returns_captured_text_output(self):
        proc = procgroup.run_group("echo out; echo err >&2", shell=True, timeout=10)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "out\n", "err\n"))

    def test_argv_list_runs_without_a_shell(self):
        proc = procgroup.run_group(["printf", "%s", "a b"], timeout=10)
        self.assertEqual(proc.stdout, "a b")

    def test_non_zero_exit_is_returned_not_raised(self):
        proc = procgroup.run_group("exit 3", shell=True, timeout=10)
        self.assertEqual(proc.returncode, 3)

    def test_cwd_and_env_pass_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = procgroup.run_group(
                ["/bin/sh", "-c", "pwd; echo $PG_PROBE"], timeout=10,
                cwd=tmp, env={"PG_PROBE": "seen", "PATH": "/usr/bin:/bin"},
            )
        lines = proc.stdout.split()
        self.assertEqual(lines[1], "seen")
        self.assertEqual(os.path.realpath(lines[0]), os.path.realpath(tmp))

    def test_timeout_kills_grandchildren_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "pid"
            SleeperCleanup().track(self, pid_file)
            with self.assertRaises(subprocess.TimeoutExpired):
                procgroup.run_group(sleeper_command(pid_file), shell=True, timeout=1)
            pid = wait_for_pid(pid_file)
            self.assertIsNotNone(pid)
            self.assertTrue(pid_is_gone(pid), f"pid {pid} survived the timeout")

    def test_child_gets_its_own_process_group(self):
        proc = procgroup.run_group("ps -o pgid= -p $$", shell=True, timeout=10)
        self.assertNotEqual(int(proc.stdout.strip()), os.getpgrp())


class CustomCheckTimeoutTests(unittest.TestCase):
    def test_a_custom_check_that_forks_a_sleeper_leaves_no_process_after_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "pid"
            SleeperCleanup().track(self, pid_file)
            result = doctor.run_custom_check(
                {"name": "slow", "command": sleeper_command(pid_file), "timeout_s": 1},
            )
            self.assertEqual(result["status"], "warn")
            self.assertEqual(result["detail"], "timed out after 1s")
            pid = wait_for_pid(pid_file)
            self.assertIsNotNone(pid)
            self.assertTrue(pid_is_gone(pid), f"pid {pid} survived the timeout")


class JanitorCommandTimeoutTests(unittest.TestCase):
    def test_a_janitor_command_rule_that_forks_a_sleeper_leaves_no_process_after_timeout(self):
        from rigops import janitor

        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "pid"
            SleeperCleanup().track(self, pid_file)
            rule = {"name": "slow", "action": "command", "path": tmp,
                    "command": sleeper_command(pid_file), "timeout_s": 1}
            result = janitor.run_rules([rule], apply=True, max_delete=200)
            self.assertEqual(result["rules"][0]["errors"], ["timed out after 1s"])
            pid = wait_for_pid(pid_file)
            self.assertIsNotNone(pid)
            self.assertTrue(pid_is_gone(pid), f"pid {pid} survived the timeout")

