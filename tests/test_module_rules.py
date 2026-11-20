"""Modular packages stay modular, and no module grows without bound.

The import rule is a package rule: a module inside a package such as
``rigops.doctor`` may import ``rigops.core`` and nothing else from ``rigops``.
``core`` is the single shared hop, and the flat modules are the layer it wraps,
so neither is judged by it.

The line cap is a repository rule: every module under the package root is
judged, package or not. The two flat modules that predate the cap carry a
recorded allowance in ``LINE_ALLOWANCES``: they may shrink, never grow.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "lib" / "rigops"

DEFAULT_MAX_LINES = 400
EXEMPT_PACKAGES = {"core", "sources"}

LINE_ALLOWANCES = {
    "rigops/reap.py": 955,
    "rigops/janitor.py": 422,
}


def _packages(root):
    return {
        p.name: p for p in sorted(root.iterdir())
        if p.is_dir() and (p / "__init__.py").is_file() and p.name not in EXEMPT_PACKAGES
    }


def _siblings(source):
    """Yield (lineno, name) for every import naming something under rigops.

    Parsed rather than matched per line: a dotted submodule, a relative import
    and a parenthesised import list are all ordinary ways to write the violation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "rigops" and len(parts) > 1:
                    yield node.lineno, parts[1]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                continue
            if node.level > 1:
                head = (node.module or "").split(".")[0]
                names = [head] if head else [a.name for a in node.names]
            elif (node.module or "").split(".")[0] == "rigops":
                rest = (node.module or "").split(".")[1:]
                names = rest[:1] or [a.name for a in node.names]
            else:
                continue
            for name in names:
                if name:
                    yield node.lineno, name


def violations(root, allowances=None, max_lines=DEFAULT_MAX_LINES):
    allowances = LINE_ALLOWANCES if allowances is None else allowances
    packages = _packages(root)
    found = []
    for module in sorted(root.rglob("*.py")):
        source = module.read_text(encoding="utf-8")
        where = f"rigops/{module.relative_to(root)}"
        lines = len(source.splitlines())
        limit = allowances.get(where, max_lines)
        if lines > limit:
            found.append(f"size:{where}: {lines} lines against {limit}")
        package = packages.get(module.relative_to(root).parts[0])
        if package is None:
            continue
        for lineno, name in _siblings(source):
            if name in ("core", package.name) or name.startswith("_"):
                continue
            found.append(f"import:{where}:{name}: line {lineno}")
    return found


class RepoTreeTests(unittest.TestCase):
    def test_current_tree_obeys_both_rules(self):
        self.assertEqual(violations(PACKAGE_ROOT), [])

    def test_recorded_allowances_are_unchanged(self):
        self.assertEqual(LINE_ALLOWANCES, {"rigops/reap.py": 955, "rigops/janitor.py": 422})


class ImportRuleTests(unittest.TestCase):
    def _check(self, package, source):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / package
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            (pkg / "mod.py").write_text(source)
            return violations(root)

    def test_core_import_is_allowed(self):
        self.assertEqual(self._check("pkg_a", "from rigops import core\n"), [])

    def test_sibling_import_is_flagged(self):
        found = self._check("pkg_b", "from rigops import janitor\n")
        self.assertEqual([f.rsplit(":", 1)[0] for f in found],
                         ["import:rigops/pkg_b/mod.py:janitor"])

    def test_dotted_relative_and_parenthesised_forms_are_flagged(self):
        forms = {
            "dotted": "from rigops.judge import judge_job\n",
            "plain": "import rigops.janitor\n",
            "climbing": "from ..judge import judge_job\n",
            "parenthesised": "from rigops import (\n    reap,\n)\n",
        }
        for name, source in forms.items():
            with self.subTest(form=name):
                found = self._check("pkg_d", source)
                self.assertTrue(
                    any(f.startswith("import:rigops/pkg_d/mod.py:") for f in found), found)

    def test_import_inside_the_modules_own_package_is_allowed(self):
        found = self._check("pkg_e", "from . import check_budget\nfrom rigops.pkg_e import other\n")
        self.assertEqual(found, [])

    def test_flat_module_may_import_a_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flat.py").write_text("from rigops import janitor\n")
            self.assertEqual(violations(root), [])


class SizeRuleTests(unittest.TestCase):
    def test_package_module_over_the_cap_is_flagged_until_cap_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "pkg_c"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            (pkg / "big.py").write_text("x = 1\n" * 410)
            self.assertEqual(len(violations(root)), 1)
            self.assertEqual(violations(root, max_lines=500), [])

    def test_flat_module_over_the_cap_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flat.py").write_text("x = 1\n" * 410)
            self.assertEqual(len(violations(root)), 1)

    def test_recorded_allowance_flags_only_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / "legacy.py"
            allowances = {"rigops/legacy.py": 500}
            module.write_text("x = 1\n" * 500)
            self.assertEqual(violations(root, allowances), [])
            module.write_text("x = 1\n" * 501)
            self.assertEqual(len(violations(root, allowances)), 1)


if __name__ == "__main__":
    unittest.main()
