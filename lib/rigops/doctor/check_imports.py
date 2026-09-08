"""Modular packages stay modular.

A module inside a package such as ``rigops.doctor`` may import ``rigops.core``
and nothing else from ``rigops``; ``core`` is the single shared hop and is itself
exempt. A module over its line cap is the other way modularity rots, so the same
check owns both limits.
"""

from __future__ import annotations

import re

from rigops import core

AREA = "rigops"
CHECK = "imports"

DEFAULT_MAX_LINES = 400
EXEMPT_PACKAGES = {"core", "sources"}
FROM_RE = re.compile(r"^\s*from\s+rigops(?:\.([a-z_]+))?\s+import\s+(.+)$")
IMPORT_RE = re.compile(r"^\s*import\s+rigops\.([a-z_]+)")


def _package_root():
    return core.expand(core.__file__).parent.parent


def _packages(root):
    return [
        p for p in sorted(root.iterdir())
        if p.is_dir() and (p / "__init__.py").is_file() and p.name not in EXEMPT_PACKAGES
    ]


def _rel(path, root):
    return f"rigops/{path.relative_to(root)}"


def run(cfg):
    root = _package_root()
    if not root.is_dir():
        return []
    max_lines = core.cfg_get(cfg, "doctor.imports.max_lines", DEFAULT_MAX_LINES)
    findings = []
    for package in _packages(root):
        for module in sorted(package.rglob("*.py")):
            try:
                lines = module.read_text(errors="replace").splitlines()
            except OSError:
                continue
            where = _rel(module, root)
            if len(lines) > max_lines:
                findings.append(core.Finding(
                    check=CHECK, area=AREA, key=f"size:{where}",
                    symptom=f"module is {len(lines)} lines against a {max_lines} line cap",
                    evidence=where, fix="split it, or raise doctor.imports.max_lines deliberately",
                ))
            for lineno, line in enumerate(lines, 1):
                match = FROM_RE.match(line)
                if match:
                    sibling = match.group(1)
                    names = [n.strip().split(" as ")[0] for n in match.group(2).split(",")]
                    imported = [sibling] if sibling else names
                else:
                    plain = IMPORT_RE.match(line)
                    imported = [plain.group(1)] if plain else []
                for name in imported:
                    if name in ("core", package.name) or name.startswith("_"):
                        continue
                    findings.append(core.Finding(
                        check=CHECK, area=AREA, key=f"import:{where}:{name}",
                        symptom=f"`{package.name}` module imports sibling `rigops.{name}`",
                        evidence=f"{where}:{lineno}",
                        fix="route it through rigops.core instead",
                    ))
    return findings
