from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "release-check.py"

CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [{heading}] - 2026-01-01\n\n- {body}\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Alice", "-c", "user.email=alice@example.com", *args],
        cwd=root, check=True, capture_output=True,
    )


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        _git(self.repo, "init", "-q")

    def _write(self, rel: str, text: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _commit(self, version: str, *, plugin: str | None = None, market: str | None = None,
                heading: str | None = None, body: str = "first", source: str = "x = 1\n") -> None:
        self._write("lib/rigops/__init__.py",
                    f'from __future__ import annotations\n\n__version__ = "{version}"\n')
        self._write("plugin/.claude-plugin/plugin.json",
                    json.dumps({"name": "rigops", "version": plugin or version}, indent=2) + "\n")
        self._write(".claude-plugin/marketplace.json", json.dumps(
            {"name": "rigops", "plugins": [{"name": "rigops", "version": market or version}]},
            indent=2) + "\n")
        self._write("CHANGELOG.md", CHANGELOG.format(heading=heading or version, body=body))
        self._write("src/app.py", source)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", f"release {version}")

    def _tag(self, name: str, annotated: bool = False) -> None:
        if annotated:
            _git(self.repo, "tag", "-a", name, "-m", name)
        else:
            _git(self.repo, "tag", name)

    def _check(self, tag: str, repo: Path | None = None):
        return subprocess.run(
            [sys.executable, str(TOOL), tag, "--repo", str(repo or self.repo)],
            capture_output=True, text=True, check=False, timeout=30,
        )

    def test_agreeing_versions_pass(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc1", annotated=True)

        proc = self._check("v0.13.0rc1")

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_tag_that_differs_from_the_package_version_fails(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc2")

        proc = self._check("v0.13.0rc2")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("error:", proc.stderr + proc.stdout)
        self.assertIn("0.13.0rc1", proc.stderr + proc.stdout)

    def test_plugin_version_mismatch_fails(self):
        self._commit("0.13.0rc1", plugin="0.12.0")
        self._tag("v0.13.0rc1")

        proc = self._check("v0.13.0rc1")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("plugin.json", proc.stderr + proc.stdout)

    def test_marketplace_version_mismatch_fails(self):
        self._commit("0.13.0rc1", market="0.12.0")
        self._tag("v0.13.0rc1")

        proc = self._check("v0.13.0rc1")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("marketplace.json", proc.stderr + proc.stdout)

    def test_release_candidate_tag_skips_the_diff_check(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc1")
        self._commit("0.13.0rc2", source="x = 2\n")
        self._tag("v0.13.0rc2")

        proc = self._check("v0.13.0rc2")

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_final_with_version_and_heading_changes_only_passes(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc1", annotated=True)
        self._commit("0.13.0", heading="0.13.0")
        self._tag("v0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_final_with_a_changed_source_line_fails(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc1")
        self._commit("0.13.0", source="x = 2\n")
        self._tag("v0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("src/app.py", proc.stderr + proc.stdout)

    def test_final_with_a_changed_changelog_body_line_fails(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc1")
        self._commit("0.13.0", body="second")
        self._tag("v0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("CHANGELOG.md", proc.stderr + proc.stdout)

    def test_final_with_a_version_line_that_changes_more_than_the_version_fails(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc1")
        self._commit("0.13.0", heading="0.13.0")
        self._write("lib/rigops/__init__.py",
                    'from __future__ import annotations\n\n__version__ = "0.13.0"\nEXTRA = 1\n')
        _git(self.repo, "commit", "-q", "-am", "extra")
        self._tag("v0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("lib/rigops/__init__.py", proc.stderr + proc.stdout)

    def test_final_compares_against_the_highest_candidate(self):
        self._commit("0.13.0rc1")
        self._tag("v0.13.0rc1")
        self._commit("0.13.0rc2", source="x = 2\n")
        self._tag("v0.13.0rc2")
        self._commit("0.13.0", source="x = 2\n")
        self._tag("v0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_candidate_ten_outranks_candidate_nine(self):
        self._commit("0.13.0rc9")
        self._tag("v0.13.0rc9")
        self._commit("0.13.0rc10", source="x = 2\n")
        self._tag("v0.13.0rc10")
        self._commit("0.13.0", source="x = 2\n")
        self._tag("v0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_final_without_a_candidate_passes_with_a_note(self):
        self._commit("0.13.0")
        self._tag("v0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("note: no release candidate for 0.13.0; diff check skipped", proc.stdout)

    def test_a_directory_that_is_not_a_repository_exits_2(self):
        with tempfile.TemporaryDirectory() as other:
            proc = self._check("v0.13.0", repo=Path(other))

        self.assertEqual(proc.returncode, 2)

    def test_a_tag_without_the_v_prefix_exits_2(self):
        self._commit("0.13.0")
        self._tag("0.13.0")

        proc = self._check("0.13.0")

        self.assertEqual(proc.returncode, 2)

    def test_an_unknown_tag_exits_2(self):
        self._commit("0.13.0")

        proc = self._check("v0.13.0")

        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
