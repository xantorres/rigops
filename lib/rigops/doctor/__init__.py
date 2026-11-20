"""Config checks, one module per check, discovered by naming convention.

A file named ``check_<name>.py`` in this directory is a check. It exposes
``AREA`` (a backlog area) and ``run(cfg) -> Iterable[Finding]``. Nothing else is
registered anywhere, so adding a check means adding a file.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from rigops import core

from . import ids

PREFIX = "check_"


class _Unimportable:
    """Stands in for a check module that raised on import, so the run can say so."""

    def __init__(self, name: str, exc: Exception) -> None:
        self.name = name
        self.exc = exc

    def run(self, cfg):
        return [core.Finding(
            check=self.name, area="rigops", key="import",
            symptom=f"check `{self.name}` failed to import: {type(self.exc).__name__}",
            evidence=str(self.exc) or type(self.exc).__name__,
            fix="fix the check module; until then it is reporting nothing about its area",
            severity="error",
        )]


def discover() -> list:
    """Return (name, module) for every check module, in stable order."""
    found = []
    for info in sorted(pkgutil.iter_modules([str(Path(__file__).parent)]), key=lambda i: i.name):
        if not info.name.startswith(PREFIX):
            continue
        name = info.name[len(PREFIX):]
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:  # noqa: BLE001 - one broken module must not hide the others
            module = _Unimportable(name, exc)
        found.append((name, module))
    return found


def run_checks(cfg: dict, only=None) -> list:
    """Run every discovered check and return its findings, sorted by id.

    A check that raises becomes a finding of its own rather than a traceback: it
    is one broken module, and letting it abort the run would take the other three
    with it and leave the pre-commit hook with no report to show.
    """
    findings = []
    for name, module in discover():
        if only and name not in only:
            continue
        try:
            findings.extend(module.run(cfg) or [])
        except Exception as exc:  # noqa: BLE001 - a check must not take the run down
            findings.append(core.Finding(
                check=name, area="rigops", key="crashed",
                symptom=f"check `{name}` raised {type(exc).__name__}",
                evidence=str(exc) or type(exc).__name__,
                fix="fix the check; until then it is reporting nothing about its area",
                severity="error",
            ))
    return sorted(findings, key=lambda f: (f.check, f.id))


def names() -> list:
    return [name for name, _ in discover()]


__all__ = ["discover", "run_checks", "names", "core", "ids"]
