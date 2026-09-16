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


def local_state_path(name: str) -> Path:
    """XDG state path for `name`, outside `~/.claude` even when RIGOPS_STATE_DIR
    points inside it: retrieval's log and doctor's gate record both need state
    that never lands under a directory a shared-state override might sync."""
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    path = Path(os.path.expanduser(str(Path(base) / "rigops" / name)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def percentile(sorted_vals, p) -> float:
    return _transcripts.percentile(sorted_vals, p)


def find_transcripts(transcripts_dir=None) -> list:
    return _transcripts.find_transcripts(transcripts_dir)


def parse_ts(value):
    return _transcripts.parse_ts(value)


def run(argv, timeout: int = 10) -> str:
    """Run a command and return stdout, or an empty string on any failure."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout


_OVERLAY: dict = {}


def set_overlay(files) -> None:
    """Serve this content instead of what is on disk, keyed by path.

    The staged gate judges the index rather than the working tree, so the checks
    read every file through ``read_text``/``read_bytes`` and none of them has to
    know which of the two it was handed.
    """
    global _OVERLAY
    _OVERLAY = {str(expand(path)): data for path, data in (files or {}).items()}


def read_bytes(path) -> bytes:
    """Contents of a file, from the overlay when it covers that path."""
    resolved = expand(path)
    data = _OVERLAY.get(str(resolved))
    return resolved.read_bytes() if data is None else data


def read_text(path) -> str:
    return read_bytes(path).decode("utf-8", errors="replace")


def size(path) -> int:
    resolved = expand(path)
    data = _OVERLAY.get(str(resolved))
    return len(data) if data is not None else resolved.stat().st_size


def git(cwd, *argv) -> str:
    return run(["git", "-C", str(cwd), *argv])


def staged_index(cwd=None):
    """Return (repository root, {path: staged bytes}) for what a commit would add.

    Deletions carry no content and are left out: the file is already gone from
    the working tree the checks resolve their pointers against.
    """
    root = git(cwd or Path.cwd(), "rev-parse", "--show-toplevel").strip()
    if not root:
        return None, {}
    names = git(root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    files = {}
    for name in (n for n in names.split("\0") if n):
        try:
            blob = subprocess.run(
                ["git", "-C", root, "show", f":{name}"],
                capture_output=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if blob.returncode == 0:
            files[str(expand(Path(root) / name))] = blob.stdout
    return expand(root), files


def finding_id(check: str, key: str) -> str:
    return hashlib.sha1(f"{check}\x00{key}".encode()).hexdigest()[:8]


@dataclass(frozen=True)
class Finding:
    """One defect, rendered as a backlog.md line.

    ``key`` is what makes the id stable across runs: it must identify the defect
    itself, never the run that found it, so a line can be deduplicated against an
    existing backlog entry. A ``warn`` finding is reported but never fails the run.
    """

    check: str
    area: str
    symptom: str
    evidence: str
    fix: str
    key: str = ""
    severity: str = "fail"
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
