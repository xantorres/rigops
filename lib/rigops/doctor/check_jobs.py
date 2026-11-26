"""A scheduled launchd job whose program is gone or broken never runs, and says nothing.

An interpreter upgrade or a removed install leaves the plist in place with a
path that no longer works; `launchd` then fails the job on every tick. Only
macOS schedules with launchd, so elsewhere the check reports nothing.
"""

from __future__ import annotations

import sys

from rigops import core

AREA = "rigops"
CHECK = "jobs"


def run(cfg):
    if sys.platform != "darwin":
        return []
    return [
        _program(path, problem) if readable else _unreadable(path, problem)
        for path, problem, readable in core.job_problems()
    ]


def _program(path, problem) -> core.Finding:
    return core.Finding(
        check=CHECK, area=AREA, key=f"program:{path.name}",
        symptom=f"scheduled job {path.stem} cannot run its program: {problem}",
        evidence=str(path),
        fix="rigops jobs install --apply",
        extra={"paths": [str(path)]},
    )


def _unreadable(path, problem) -> core.Finding:
    return core.Finding(
        check=CHECK, area=AREA, key=f"unreadable:{path.name}", severity="error",
        symptom=f"scheduled job {path.stem} cannot be judged: {problem}",
        evidence=str(path),
        fix="make the plist readable, or reinstall it: rigops jobs install --apply",
        extra={"paths": [str(path)]},
    )
