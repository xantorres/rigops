from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_jobs import REPO, home_dir, script, write_plist


class DoctorCheckTests(unittest.TestCase):
    def findings(self, platform):
        from rigops.doctor import check_jobs

        with mock.patch.object(sys, "platform", platform):
            return list(check_jobs.run({}))

    def test_a_broken_plist_is_a_finding_on_darwin(self):
        with home_dir() as home:
            plist = write_plist(home, "doctor", [str(home / "gone"), "doctor"])

            found = self.findings("darwin")

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check, "jobs")
        self.assertEqual(found[0].severity, "fail")
        self.assertIn(str(plist), found[0].evidence)
        self.assertEqual(found[0].fix, "rigops jobs install --apply")

    def test_a_plist_that_cannot_be_read_is_a_finding_not_a_crash(self):
        denied = PermissionError(13, "Permission denied")
        with home_dir() as home:
            plist = write_plist(home, "doctor", [str(home / "gone"), "doctor"])
            with mock.patch.object(Path, "read_bytes", side_effect=denied):
                found = self.findings("darwin")

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "fail")
        self.assertIn(str(plist), found[0].evidence)

    def test_a_working_plist_and_no_plist_are_clean(self):
        with home_dir() as home:
            self.assertEqual(self.findings("darwin"), [])
            prog = script(home / "rigops", '[ "$1" = version ] || exit 9')
            write_plist(home, "doctor", [str(prog), "doctor"])

            self.assertEqual(self.findings("darwin"), [])

    def test_linux_reports_nothing_and_never_reads_the_plists(self):
        with home_dir() as home:
            write_plist(home, "doctor", [str(home / "gone"), "doctor"])

            self.assertEqual(self.findings("linux"), [])

    @unittest.skipUnless(sys.platform == "darwin", "the check reports on macOS only")
    def test_doctor_config_only_exits_1_with_a_broken_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            write_plist(home, "doctor", [str(home / "gone"), "doctor"])
            env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(REPO / "lib")}

            proc = subprocess.run(
                [sys.executable, "-m", "rigops", "doctor", "--config-only", "--check", "jobs"],
                capture_output=True, text=True, check=False, env=env, timeout=60,
            )

        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("com.rigops.doctor", proc.stdout)
