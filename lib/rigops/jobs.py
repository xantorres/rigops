from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from xml.parsers.expat import ExpatError

from rigops import procgroup
from rigops.errors import RigopsError

LABEL_PREFIX = "com.rigops"
VERSION_TIMEOUT_S = 10
EPHEMERAL_DIRS = frozenset({".cache", "Caches"})
EXTRA_ENV = ("RIGOPS_CONFIG", "RIGOPS_STATE_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME")
_PLACEHOLDER = re.compile(r"@[A-Z_]+@")


@dataclass(frozen=True)
class Job:
    name: str
    schedule: str
    cron_args: tuple[str, ...]


JOBS: dict[str, Job] = {
    job.name: job for job in (
        Job("doctor", "*/30 * * * *", ("doctor", "--supervisor", "none", "--exit-zero")),
        Job("ledger", "5 9 * * 1", ("ledger", "write")),
        Job("janitor", "0 10 * * 0", ("janitor", "--apply")),
        Job("reap", "30 8 * * *", ("reap", "--apply")),
    )
}
DEFAULT_JOBS = ("doctor", "ledger")


def is_macos() -> bool:
    return sys.platform == "darwin"


def label(name: str) -> str:
    return f"{LABEL_PREFIX}.{name}"


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "rigops"


def plist_path(name: str) -> Path:
    return launch_agents_dir() / f"{label(name)}.plist"


def select_jobs(raw: str | None) -> list[str]:
    available = ", ".join(JOBS)
    if raw is None:
        return list(DEFAULT_JOBS)
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [name for name in wanted if name not in JOBS]
    if not wanted or unknown:
        shown = ", ".join(unknown) if unknown else "(empty)"
        raise RigopsError(f"unknown job(s) {shown}; available: {available}")
    return [name for name in JOBS if name in wanted]


def resolve_executable() -> str:
    found = shutil.which("rigops")
    if found is None:
        raise RigopsError(
            "no `rigops` on PATH; install it persistently (for example with `uv tool install` "
            "or `pipx install`) so the scheduled jobs have a program to run"
        )
    real, running = os.path.realpath(found), os.path.realpath(sys.argv[0])
    if real != running:
        raise RigopsError(
            f"the `rigops` on PATH ({found}) is not the running executable ({running}); "
            "run the `rigops` found on PATH, which `python -m rigops` is not"
        )
    parts = set(Path(os.path.abspath(found)).parts) | set(Path(real).parts)
    if parts & EPHEMERAL_DIRS:
        raise RigopsError(
            f"{found} lives in an ephemeral runner's cache; ephemeral runners are for trying "
            "the tool, install it persistently to schedule it"
        )
    return os.path.abspath(found)


def _template(name: str) -> str:
    source = resources.files("rigops") / "templates" / "launchd" / f"{name}.plist.in"
    return source.read_text(encoding="utf-8")


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _extra_env() -> str:
    lines = []
    for name in EXTRA_ENV:
        value = os.environ.get(name)
        if value:
            lines += [f"\t\t<key>{name}</key>", f"\t\t<string>{_escape(value)}</string>"]
    return "\n".join(lines)


def render_plist(name: str, rigops_path: str, heal: bool) -> bytes:
    heal_arg = "\t\t<string>--heal</string>\n" if heal and name == "doctor" else ""
    values = {
        "@LABEL@": _escape(label(name)),
        "@RIGOPS@": _escape(rigops_path),
        "@HOME@": _escape(str(Path.home())),
        "@LOG_DIR@": _escape(str(log_dir())),
        "@EXTRA_ENV@": _extra_env(),
        "@HEAL_ARG@": heal_arg,
    }
    text = _template(name)
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    leftover = sorted(set(_PLACEHOLDER.findall(text)))
    if leftover:
        raise RigopsError(
            f"unresolved placeholder(s) in the {name} template: {', '.join(leftover)}"
        )
    data = text.encode("utf-8")
    try:
        plistlib.loads(data)
    except (ValueError, ExpatError) as exc:
        raise RigopsError(f"the rendered {name} plist is not valid: {exc}") from exc
    return data


def read_plist(path: Path) -> bytes:
    # Reading a FIFO would block until a writer appears.
    if path.exists() and not path.is_file():
        raise RigopsError(f"cannot read {path}: not a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RigopsError(f"cannot read {path}: {exc}") from exc


def program_problem(path: Path) -> str | None:
    path = Path(path)
    raw = read_plist(path)
    try:
        data = plistlib.loads(raw)
    except (ValueError, ExpatError) as exc:
        raise RigopsError(f"cannot parse {path}: {exc}") from exc
    args = data.get("ProgramArguments") if isinstance(data, dict) else None
    if not isinstance(args, list) or not args or not isinstance(args[0], str):
        return f"{path} has no ProgramArguments"
    program = args[0]
    if not os.path.isfile(program):
        return f"{program} is missing"
    if not os.access(program, os.X_OK):
        return f"{program} is not executable"
    try:
        done = procgroup.run_group(
            [program, "version"], timeout=VERSION_TIMEOUT_S, cwd="/"
        )
    except subprocess.TimeoutExpired:
        return f"`{program} version` timed out after {VERSION_TIMEOUT_S}s"
    except OSError as exc:
        return f"`{program} version` could not run: {exc}"
    if done.returncode != 0:
        return f"`{program} version` exited {done.returncode}"
    return None


def installed_problems() -> list[tuple[Path, str, bool]]:
    found = []
    for name in JOBS:
        path = plist_path(name)
        if path.exists():
            try:
                problem = program_problem(path)
            except RigopsError as exc:
                found.append((path, str(exc), False))
                continue
            if problem is not None:
                found.append((path, problem, True))
    return found


def cron_log_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "rigops"


def cron_lines(rigops_path: str, names: list[str]) -> list[str]:
    logs = cron_log_dir()
    return [
        f"{JOBS[name].schedule} {shlex.quote(rigops_path)} {' '.join(JOBS[name].cron_args)}"
        f" >> {shlex.quote(str(logs / f'{name}.cron.log'))} 2>&1"
        for name in names
    ]


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
