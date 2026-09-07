from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import rigops  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RIGOPS = REPO_ROOT / "bin" / "rigops"


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(RIGOPS), *args], capture_output=True, text=True, check=False, timeout=30,
    )


class VersionTests(unittest.TestCase):
    def test_version_subcommand_prints_the_package_version(self):
        proc = _run("version")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), rigops.__version__)

    def test_version_flag_matches_the_subcommand(self):
        proc = _run("--version")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), rigops.__version__)

    def test_version_is_listed_in_usage(self):
        proc = _run("help")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("version", proc.stdout)

    def test_unknown_command_still_fails(self):
        proc = _run("--nope")

        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown command", proc.stderr)


if __name__ == "__main__":
    unittest.main()
