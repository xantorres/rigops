"""Realm separation is a build-time property, so it is checked like one.

Two ways it rots: the registry stops describing the trees (a root with no realm,
a working directory granted a scope nothing produces), or the index stops
matching the registry (a row whose realm or scope no longer exists), which is how
a walker change would quietly widen what a prefetch can reach.

Shells out to the CLI for the same reason ``check_render`` does: a finding here
reproduces with the command it recommends, and a doctor module may not import a
sibling package.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rigops import core

AREA = "docs"
CHECK = "realms"


def _cli_path() -> Path:
    return Path(core.__file__).resolve().parents[3] / "libexec" / "rigops-retrieval"


def run(cfg):
    default = str(core.HOME / "rig" / "registry" / "roots.json")
    registry = core.expand(core.cfg_get(cfg, "retrieval.registry", default))
    if not registry.is_file():
        return []

    cli = _cli_path()
    if not cli.is_file():
        return [core.Finding(
            check=CHECK, area=AREA, key="cli",
            symptom="the retrieval CLI is missing while a roots registry exists",
            evidence=str(cli), fix="reinstall rigops",
        )]
    try:
        proc = subprocess.run(
            [sys.executable, str(cli), "roots", "--json"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        payload = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return [core.Finding(
            check=CHECK, area=AREA, key="cli",
            symptom="`rigops retrieval roots --json` did not report",
            evidence=str(cli), fix="run it by hand and fix what it prints",
        )]

    return [
        core.Finding(
            check=CHECK, area=AREA, key=problem.get("key", ""),
            symptom=problem.get("symptom", ""),
            evidence=problem.get("evidence", str(registry)),
            fix=problem.get("fix", "fix registry/roots.json"),
        )
        for problem in payload.get("problems", [])
    ]
