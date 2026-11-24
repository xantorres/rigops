from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "lib" / "rigops"


def _project() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _tracked(path: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", path], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [REPO_ROOT / line for line in out.splitlines()]


class PyprojectTests(unittest.TestCase):
    def test_python_floor_is_3_11(self):
        self.assertEqual(_project()["project"]["requires-python"], ">=3.11")

    def test_there_are_no_runtime_dependencies(self):
        self.assertEqual(_project()["project"]["dependencies"], [])

    def test_console_script_is_rigops(self):
        self.assertEqual(_project()["project"]["scripts"], {"rigops": "rigops.cli:main"})

    def test_version_is_read_from_the_package(self):
        data = _project()
        self.assertIn("version", data["project"]["dynamic"])
        self.assertEqual(data["tool"]["setuptools"]["dynamic"]["version"],
                         {"attr": "rigops.__version__"})

    def test_build_backend_needs_a_setuptools_with_license_expressions(self):
        build = _project()["build-system"]
        self.assertEqual(build["requires"], ["setuptools>=77"])
        self.assertEqual(build["build-backend"], "setuptools.build_meta")

    def test_every_data_file_is_matched_by_a_package_data_glob(self):
        globs = _project()["tool"]["setuptools"]["package-data"]["rigops"]
        matched = {p for g in globs for p in PACKAGE.glob(g) if p.is_file()}
        data_files = {p for p in _tracked("lib/rigops") if p.suffix != ".py"}

        self.assertTrue(data_files)
        self.assertEqual(sorted(data_files - matched), [])

    def test_every_directory_holding_python_files_is_a_package(self):
        dirs = {p.parent for p in _tracked("lib/rigops") if p.suffix == ".py"}

        self.assertEqual(sorted(str(d) for d in dirs if not (d / "__init__.py").exists()), [])

    def test_no_library_file_edits_sys_path(self):
        offenders = [
            str(p.relative_to(REPO_ROOT)) for p in _tracked("lib")
            if p.suffix == ".py" and "sys.path.insert" in p.read_text(encoding="utf-8")
        ]

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
