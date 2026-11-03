from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops.cause import (  # noqa: E402
    CAUSES,
    classify,
    hold_is_silent,
    log_paths,
    tail_of,
    update_hold,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cause"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class ClassifyTests(unittest.TestCase):
    def test_fixture_per_cause(self):
        cases = [
            ("auth.txt", 1, "auth"),
            ("auth-probe.txt", 1, "auth"),
            ("quota.txt", 1, "quota"),
            ("network-dns.txt", 1, "network"),
            ("network-5xx.txt", 1, "network"),
            ("deterministic.txt", 1, "deterministic"),
            ("unknown.txt", 1, "unknown"),
        ]
        for name, code, expected in cases:
            with self.subTest(name):
                self.assertEqual(classify(code, fixture(name)), expected)

    def test_all_causes_are_covered(self):
        self.assertEqual(set(CAUSES), {"auth", "quota", "network", "deterministic", "unknown"})

    def test_exit_126_127_are_deterministic_whatever_the_log(self):
        for code in (126, 127):
            self.assertEqual(classify(code, "Not logged in"), "deterministic")
            self.assertEqual(classify(code, ""), "deterministic")

    def test_command_not_found_is_deterministic(self):
        self.assertEqual(classify(1, "run.sh: line 3: jq: command not found"), "deterministic")

    def test_probe_exit_codes_map_to_causes(self):
        for code, cause in ((1, "auth"), (2, "quota"), (3, "network")):
            line = f"ABORTED: headless claude whatever (probe exit {code})"
            self.assertEqual(classify(1, line), cause)

    def test_probe_fail_line_names_the_cause(self):
        self.assertEqual(classify(1, "claude-auth-probe: FAIL (network): x"), "network")

    def test_empty_or_missing_log_is_unknown(self):
        self.assertEqual(classify(1, ""), "unknown")
        self.assertEqual(classify(1, None), "unknown")

    def test_quota_wins_over_network_like_the_probe(self):
        self.assertEqual(classify(1, "rate limit; API Error: 503"), "quota")


class TailTests(unittest.TestCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(tail_of(Path("/nonexistent/x.txt"), now=0.0, max_age_h=1.0), "")

    def test_stale_log_is_ignored(self):
        path = FIXTURES / "auth.txt"
        mtime = path.stat().st_mtime
        self.assertIn("Not logged in", tail_of(path, now=mtime + 60, max_age_h=1.0))
        self.assertEqual(tail_of(path, now=mtime + 2 * 3600, max_age_h=1.0), "")

    def test_only_the_last_lines_are_kept(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_text("\n".join(f"line{i}" for i in range(500)) + "\n")
            out = tail_of(path, now=path.stat().st_mtime, max_age_h=1.0, lines=5)
            self.assertEqual(out.splitlines(), [f"line{i}" for i in range(495, 500)])


H = 3600


class UpdateHoldTests(unittest.TestCase):
    def test_first_sight_holds(self):
        holds = {}
        self.assertFalse(update_hold(holds, "j", "auth", 1000.0))
        self.assertEqual(holds["j"]["since"], 1000.0)

    def test_inside_retry_interval_stays_held(self):
        holds = {}
        update_hold(holds, "j", "auth", 0.0)
        self.assertFalse(update_hold(holds, "j", "auth", 5 * H))

    def test_release_after_interval_without_probe(self):
        holds = {}
        update_hold(holds, "j", "auth", 0.0)
        self.assertTrue(update_hold(holds, "j", "auth", 6 * H))
        self.assertFalse(update_hold(holds, "j", "auth", 7 * H))

    def test_failing_probe_waits_another_interval(self):
        holds = {}
        update_hold(holds, "j", "quota", 0.0)
        self.assertFalse(update_hold(holds, "j", "quota", 4 * H, probe=lambda: False))
        self.assertFalse(update_hold(holds, "j", "quota", 7 * H, probe=lambda: True))
        self.assertTrue(update_hold(holds, "j", "quota", 8 * H, probe=lambda: True))

    def test_probe_only_runs_once_the_interval_elapsed(self):
        holds = {}
        calls = []
        update_hold(holds, "j", "auth", 0.0)
        update_hold(holds, "j", "auth", 1 * H, probe=lambda: calls.append(1) or True)
        self.assertEqual(calls, [])

    def test_cause_change_restarts_the_hold(self):
        holds = {}
        update_hold(holds, "j", "auth", 0.0)
        self.assertFalse(update_hold(holds, "j", "network", 10 * H))
        self.assertEqual(holds["j"]["cause"], "network")

    def test_network_releases_capped_per_day(self):
        holds = {}
        update_hold(holds, "j", "network", 0.0)
        released = [update_hold(holds, "j", "network", (i + 1) * H) for i in range(6)]
        self.assertEqual(released, [True, True, True, False, False, False])
        self.assertTrue(update_hold(holds, "j", "network", 25 * H))

    def test_corrupt_entry_is_replaced(self):
        holds = {"j": "garbage"}
        self.assertFalse(update_hold(holds, "j", "auth", 0.0))


class HoldSilenceTests(unittest.TestCase):
    def test_quota_is_silent_for_24h(self):
        holds = {"j": {"cause": "quota", "since": 0.0}}
        self.assertTrue(hold_is_silent(holds, "j", "quota", 23 * H))
        self.assertFalse(hold_is_silent(holds, "j", "quota", 24 * H))

    def test_auth_is_never_silent(self):
        holds = {"j": {"cause": "auth", "since": 0.0}}
        self.assertFalse(hold_is_silent(holds, "j", "auth", 0.0))

    def test_unheld_job_is_not_silent(self):
        self.assertFalse(hold_is_silent({}, "j", "quota", 0.0))


class LogPathsTests(unittest.TestCase):
    def test_reads_stderr_then_stdout(self):
        import plistlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.plist"
            p.write_bytes(
                plistlib.dumps({"StandardOutPath": "/o.txt", "StandardErrorPath": "/e.txt"})
            )
            self.assertEqual(log_paths(p), ["/e.txt", "/o.txt"])

    def test_missing_or_broken_plist_is_empty(self):
        self.assertEqual(log_paths("/nonexistent.plist"), [])


if __name__ == "__main__":
    unittest.main()
