from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import launchd  # noqa: E402

# Real child processes, no mocks: these guards signal actual pids/pgids, so
# the only trustworthy check is that a real process does or doesn't die.
GRACE_S = 0.5


def _spawn(cmd: list, **kwargs) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


def _wait_gone(proc: subprocess.Popen, timeout: float) -> bool:
    # proc.poll() reaps our own child via waitpid(WNOHANG); os.kill(pid, 0)
    # can't tell a reaped-but-zombie child from a live one -- the pid stays
    # valid (and signalable) until something waits on it.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


class TerminateProcessGroupTests(unittest.TestCase):
    def test_sigterm_kills_own_pgid_child_within_grace(self):
        proc = _spawn(["sleep", "30"], start_new_session=True)
        try:
            self.assertTrue(launchd.terminate_process_group(proc.pid, GRACE_S))
            self.assertTrue(_wait_gone(proc, timeout=GRACE_S + 5))
        finally:
            proc.wait(timeout=5)

    def test_sigterm_ignored_child_is_sigkilled_after_grace(self):
        proc = _spawn(["sh", "-c", 'trap "" TERM; sleep 30'], start_new_session=True)
        try:
            start = time.time()
            result = launchd.terminate_process_group(proc.pid, GRACE_S)
            elapsed = time.time() - start
            self.assertTrue(result)
            self.assertGreaterEqual(elapsed, GRACE_S)
            self.assertTrue(_wait_gone(proc, timeout=5))
        finally:
            proc.wait(timeout=5)

    def test_pid_1_never_signalled(self):
        self.assertFalse(launchd.terminate_process_group(1, GRACE_S))

    def test_pid_0_never_signalled(self):
        self.assertFalse(launchd.terminate_process_group(0, GRACE_S))

    def test_own_pid_never_signalled(self):
        self.assertFalse(launchd.terminate_process_group(os.getpid(), GRACE_S))

    def test_own_pgid_never_signalled(self):
        # No start_new_session: this child stays in the test runner's own
        # process group, so terminate_process_group must refuse it even
        # though its pid is neither 1, 0, nor our own.
        proc = _spawn(["sleep", "30"])
        try:
            self.assertEqual(os.getpgid(proc.pid), os.getpgid(0))
            self.assertFalse(launchd.terminate_process_group(proc.pid, GRACE_S))
            self.assertIsNone(proc.poll())  # nothing signalled it
        finally:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
