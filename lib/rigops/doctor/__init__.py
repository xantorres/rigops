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

PREFIX = "check_"


def discover() -> list:
    """Return (name, module) for every check module, in stable order."""
    found = []
    for info in sorted(pkgutil.iter_modules([str(Path(__file__).parent)]), key=lambda i: i.name):
        if not info.name.startswith(PREFIX):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        found.append((info.name[len(PREFIX):], module))
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
            ))
    return sorted(findings, key=lambda f: (f.check, f.id))


def names() -> list:
    return [name for name, _ in discover()]


__all__ = ["discover", "run_checks", "names", "core"]
