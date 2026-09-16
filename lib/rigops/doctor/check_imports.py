"""Modular packages stay modular, and no module grows without bound.

The import rule is a package rule: a module inside a package such as
``rigops.doctor`` may import ``rigops.core`` and nothing else from ``rigops``.
``core`` is the single shared hop, and the flat modules are the layer it wraps,
so neither is judged by it -- a flat module reaching for a sibling is the
legacy layout, not a defect in it.

The line cap is a repository rule: size rot is size rot wherever it happens, so
every module under the package root is judged, package or not. The two flat
modules that predate the cap carry a recorded allowance in ``LINE_ALLOWANCES``:
they may shrink, never grow, and a new module is capped like any other. Keeping
the cap a package rule would have left them advertising a limit they are twice
over.
"""

from __future__ import annotations

import ast

from rigops import core

AREA = "rigops"
CHECK = "imports"

DEFAULT_MAX_LINES = 400
EXEMPT_PACKAGES = {"core", "sources"}

# Modules that were already over the cap when it became a repository rule. The
# recorded number is their size on the day it was recorded, which is the most
# they are ever allowed to be again.
LINE_ALLOWANCES = {
    "rigops/reap.py": 1292,
    "rigops/janitor.py": 422,
}


def _package_root():
    return core.expand(core.__file__).parent.parent


def _packages(root):
    return [
        p for p in sorted(root.iterdir())
        if p.is_dir() and (p / "__init__.py").is_file() and p.name not in EXEMPT_PACKAGES
    ]


def _rel(path, root):
    return f"rigops/{path.relative_to(root)}"


def _siblings(source):
    """Yield (lineno, name) for every import naming something under rigops.

    Parsed rather than matched line by line: a dotted submodule, a relative
    import and a parenthesised import list are all ordinary ways to write the
    violation, and a regex over source lines recognised none of them.
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
                continue  # inside the module's own package, which the rule allows
            if node.level > 1:
                # `from ..judge import x` climbs out of the package to a sibling.
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


def _size_finding(where, line_count, max_lines):
    allowance = LINE_ALLOWANCES.get(where)
    if line_count <= (allowance or max_lines):
        return None
    if allowance:
        return core.Finding(
            check=CHECK, area=AREA, key=f"size:{where}",
            symptom=f"module is {line_count} lines against its recorded {allowance} line allowance",
            evidence=where,
            fix="split it: a module that predates the cap may shrink, never grow",
        )
    return core.Finding(
        check=CHECK, area=AREA, key=f"size:{where}",
        symptom=f"module is {line_count} lines against a {max_lines} line cap",
        evidence=where, fix="split it, or raise doctor.imports.max_lines deliberately",
    )


def run(cfg):
    root = _package_root()
    if not root.is_dir():
        return []
    max_lines = core.cfg_get(cfg, "doctor.imports.max_lines", DEFAULT_MAX_LINES)
    packages = {p.name: p for p in _packages(root)}
    findings = []
    for module in sorted(root.rglob("*.py")):
        try:
            source = core.read_text(module)
        except OSError:
            continue
        where = _rel(module, root)
        size = _size_finding(where, len(source.splitlines()), max_lines)
        if size:
            findings.append(size)
        package = packages.get(module.relative_to(root).parts[0])
        if package is None:
            continue
        for lineno, name in _siblings(source):
            if name in ("core", package.name) or name.startswith("_"):
                continue
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"import:{where}:{name}",
                symptom=f"`{package.name}` module imports sibling `rigops.{name}`",
                evidence=f"{where}:{lineno}",
                fix="route it through rigops.core instead",
            ))
    return findings
