from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOVED = (
    "lib/rigops/retrieval",
    "lib/rigops/render",
    "lib/rigops/context",
    "lib/rigops/mine_dedupe.py",
    "lib/rigops/mine_es.py",
    "lib/rigops/doctor/check_plans.py",
    "lib/rigops/doctor/check_realms.py",
    "lib/rigops/doctor/check_render.py",
    "libexec/rigops-card",
    "libexec/rigops-nudge",
    "libexec/rigops-render",
    "libexec/rigops-retrieval",
)
LEAVING_MODULES = (
    "rigops.retrieval",
    "rigops.render",
    "rigops.realm",
    "rigops.mine_dedupe",
    "rigops.mine_es",
)


def _package(rel: str) -> list:
    parts = rel[len("lib/"):].split("/")
    return parts[:-1]


def _imported(tree: ast.AST, package: list) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def _violations(rel: str) -> list:
    path = ROOT / rel
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    package = _package(rel) if rel.startswith("lib/") else []
    return sorted(
        name for name in _imported(tree, package)
        if any(name == m or name.startswith(m + ".") for m in LEAVING_MODULES)
    )


def _kept_modules() -> list:
    return [
        str(p.relative_to(ROOT))
        for p in sorted((ROOT / "lib" / "rigops").rglob("*.py"))
        if p.is_file()
    ]


class KeptImportsTests(unittest.TestCase):
    def test_kept_code_does_not_import_leaving_modules(self):
        bad = {}
        for rel in _kept_modules():
            found = _violations(rel)
            if found:
                bad[rel] = found
        self.assertEqual(bad, {})

    def test_removed_paths_stay_removed(self):
        present = [p for p in REMOVED if (ROOT / p).exists()]
        self.assertEqual(present, [])


if __name__ == "__main__":
    unittest.main()
