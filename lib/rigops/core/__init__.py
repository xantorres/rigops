"""Shared primitives for the modular rigops packages.

Every module inside a package such as ``rigops.doctor`` imports this package and
nothing else from ``rigops``; ``check_imports`` enforces that. ``core`` itself is
the one place allowed to wrap the flat legacy modules, so the rule stays a single
hop rather than a web.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rigops import config as _config
from rigops import state as _state
from rigops import transcripts as _transcripts

HOME = Path(os.path.expanduser("~"))
CLAUDE_DIR = HOME / ".claude"
DOCS_DIR = HOME / "docs"

VALID_AREAS = (
    "brain", "launchd", "hooks", "skills", "memory",
    "permissions", "metrics", "repos", "docs", "rigops", "plans",
)


def expand(value) -> Path:
    """Expand ``~`` and environment variables into an absolute path."""
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve(strict=False)


def load_config() -> dict:
    return _config.load()


def cfg_get(cfg: dict, dotted: str, default=None):
    return _config.get(cfg, dotted, default)


def state_dir() -> Path:
    return _state.state_dir()


def percentile(sorted_vals, p) -> float:
    return _transcripts.percentile(sorted_vals, p)


def run(argv, timeout: int = 10) -> str:
    """Run a command and return stdout, or an empty string on any failure."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout


def finding_id(check: str, key: str) -> str:
    return hashlib.sha1(f"{check}\x00{key}".encode()).hexdigest()[:8]


@dataclass(frozen=True)
class Finding:
    """One defect, rendered as a backlog.md line.

    ``key`` is what makes the id stable across runs: it must identify the defect
    itself, never the run that found it, so a line can be deduplicated against an
    existing backlog entry.
    """

    check: str
    area: str
    symptom: str
    evidence: str
    fix: str
    key: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def id(self) -> str:
        return finding_id(self.check, self.key or f"{self.symptom}|{self.evidence}")

    def line(self, today=None) -> str:
        stamp = (today or date.today()).isoformat()
        return (
            f"- [ ] {stamp} {self.area}: {self.symptom}. "
            f"Evidence: {self.evidence}. Fix: {self.fix}. [doctor:{self.id}]"
        )
