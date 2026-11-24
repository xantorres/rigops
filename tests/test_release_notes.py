from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "release-notes.py"

CHANGELOG = """# Changelog

## [Unreleased]

## [0.13.0] - 2026-02-01

### Added

- first

- second

## [0.12.0] - 2025-12-01

- old

## [0.11.0] - 2025-11-01

"""


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "CHANGELOG.md"
        self.path.write_text(CHANGELOG, encoding="utf-8")

    def _run(self, version: str):
        return subprocess.run(
            [sys.executable, str(TOOL), version, "--changelog", str(self.path)],
            capture_output=True, text=True, check=False, timeout=30,
        )

    def test_prints_the_body_up_to_the_next_heading_without_outer_blank_lines(self):
        proc = self._run("0.13.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "### Added\n\n- first\n\n- second\n")

    def test_the_last_section_is_read_to_the_end_of_the_file(self):
        self.path.write_text(CHANGELOG.replace("## [0.11.0] - 2025-11-01\n\n", "")
                             + "## [0.11.0] - 2025-11-01\n\n- tail\n", encoding="utf-8")

        proc = self._run("0.11.0")

        self.assertEqual(proc.stdout, "- tail\n")

    def test_a_missing_heading_exits_2(self):
        self.assertEqual(self._run("9.9.9").returncode, 2)

    def test_an_empty_body_exits_2(self):
        self.assertEqual(self._run("0.11.0").returncode, 2)

    def test_a_missing_changelog_exits_2(self):
        self.path.unlink()

        self.assertEqual(self._run("0.13.0").returncode, 2)


if __name__ == "__main__":
    unittest.main()
