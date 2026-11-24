from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "check-wheel.py"

ENTRY_POINTS = "[console_scripts]\nrigops = rigops.cli:main\n"
METADATA = "Metadata-Version: 2.4\nName: rigops\nVersion: 1.0.0\nRequires-Python: >=3.11\n"
FILES = {
    "lib/rigops/__init__.py": "",
    "lib/rigops/templates/launchd/a.plist.in": "x",
    "lib/rigops/redaction-patterns.public.txt": "x",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class CheckWheelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "repo"
        for rel, text in FILES.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        _git(self.repo, "init", "-q")
        _git(self.repo, "add", ".")

    def _wheel(self, *, skip=(), entry_points=ENTRY_POINTS, metadata=METADATA) -> Path:
        wheel = self.tmp / "rigops-1.0.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            for rel, text in FILES.items():
                if rel not in skip:
                    zf.writestr(rel.removeprefix("lib/"), text)
            zf.writestr("rigops-1.0.0.dist-info/entry_points.txt", entry_points)
            zf.writestr("rigops-1.0.0.dist-info/METADATA", metadata)
        return wheel

    def _check(self, wheel: Path):
        return subprocess.run(
            [sys.executable, str(TOOL), str(wheel), str(self.repo)],
            capture_output=True, text=True, check=False, timeout=30,
        )

    def test_a_complete_wheel_passes(self):
        proc = self._check(self._wheel())

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_missing_template_fails(self):
        proc = self._check(self._wheel(skip=["lib/rigops/templates/launchd/a.plist.in"]))

        self.assertEqual(proc.returncode, 1)
        self.assertIn("rigops/templates/launchd/a.plist.in", proc.stdout)

    def test_a_missing_entry_point_fails(self):
        proc = self._check(self._wheel(entry_points="[console_scripts]\n"))

        self.assertEqual(proc.returncode, 1)
        self.assertIn("entry point", proc.stdout)

    def test_a_requires_dist_fails(self):
        proc = self._check(self._wheel(metadata=METADATA + "Requires-Dist: other\n"))

        self.assertEqual(proc.returncode, 1)
        self.assertIn("Requires-Dist", proc.stdout)

    def test_a_wrong_python_floor_fails(self):
        proc = self._check(self._wheel(metadata=METADATA.replace(">=3.11", ">=3.9")))

        self.assertEqual(proc.returncode, 1)
        self.assertIn("Requires-Python", proc.stdout)

    def test_an_unreadable_wheel_exits_2(self):
        bad = self.tmp / "bad.whl"
        bad.write_text("not a zip")

        self.assertEqual(self._check(bad).returncode, 2)
        self.assertEqual(self._check(self.tmp / "missing.whl").returncode, 2)

    def test_the_tool_is_importable_without_running(self):
        spec = importlib.util.spec_from_file_location("check_wheel", TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main()
