"""Drift between ~/rig and its rendered surfaces is a defect like any other.

Shells out to the CLI rather than importing rigops.render directly: the CLI is
the same path an operator runs by hand, so a finding here reproduces exactly
with the fix it recommends.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rigops import core

AREA = "docs"
CHECK = "render"


def _cli_path() -> Path:
    return Path(core.__file__).resolve().parents[3] / "libexec" / "rigops-render"


def run(cfg):
    source = core.expand(core.cfg_get(cfg, "render.source", str(core.HOME / "rig")))
    if not source.is_dir():
        return []

    cli = _cli_path()
    try:
        proc = subprocess.run(
            [sys.executable, str(cli), "all", "--check", "--json"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return [core.Finding(
            check=CHECK, area=AREA, key="cli",
            symptom="`rigops render all --check` failed to run",
            evidence=str(cli), fix="run rigops render all",
        )]

    try:
        items = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        if proc.returncode == 0:
            return []
        return [core.Finding(
            check=CHECK, area=AREA, key="cli",
            symptom=f"`rigops render all --check` exited {proc.returncode} with unparsable output",
            evidence=(proc.stderr or proc.stdout).strip()[:200],
            fix="run rigops render all",
        )]

    return [
        core.Finding(
            check=CHECK, area=AREA, key=f"{item['state']}:{item['path']}",
            symptom=f"rendered surface {item['state']}: {item['path']}",
            evidence=item["path"], fix="run rigops render all",
        )
        for item in items
    ]
