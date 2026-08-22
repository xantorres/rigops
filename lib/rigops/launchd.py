from __future__ import annotations

import os
import re
import signal
import subprocess
import time

_ETIME_RE = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def _uid() -> str:
    return str(os.getuid())


def _launchctl_list_output(label: str):
    """One `launchctl list <label>` call, parsed into (loaded, pid, exit_status).

    loaded is False (pid, exit_status both None) when the label isn't
    loaded, the command errors, or it times out.
    """
    try:
        out = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, None, None
    if out.returncode != 0:
        return False, None, None
    pid_m = re.search(r'"PID"\s*=\s*(\d+);', out.stdout)
    exit_m = re.search(r'"LastExitStatus"\s*=\s*(-?\d+);', out.stdout)
    pid = int(pid_m.group(1)) if pid_m else None
    exit_status = int(exit_m.group(1)) if exit_m else None
    return True, pid, exit_status


def is_loaded(label: str) -> bool:
    """Whether label is currently loaded in launchd, regardless of run state."""
    loaded, _, _ = _launchctl_list_output(label)
    return loaded


def job_pid(label: str):
    """Running PID for a loaded launchd label, or None if not running/not loaded."""
    _, pid, _ = _launchctl_list_output(label)
    return pid


def last_exit_status(label: str):
    """Last exit status for a loaded launchd label, or None if never run/not loaded."""
    _, _, exit_status = _launchctl_list_output(label)
    return exit_status


def job_snapshot(label: str):
    """(loaded, pid, last_exit_status) from a single `launchctl list` call.

    Callers needing more than one of is_loaded/job_pid/last_exit_status for
    the same label: three separate calls are three separate `launchctl
    list` invocations of that label, each its own point in time. This
    reads all three from one snapshot instead.
    """
    return _launchctl_list_output(label)


def kickstart(label: str) -> bool:
    try:
        out = subprocess.run(
            ["launchctl", "kickstart", f"gui/{_uid()}/{label}"],
            capture_output=True, text=True, timeout=15,
        )
        return out.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def runtime_hours(pid: int):
    """Wall-clock age of a live process from `ps etime` ([[dd-]hh:]mm:ss); None if gone."""
    try:
        out = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = _ETIME_RE.match(out.stdout.strip())
    if not m:
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups())
    return days * 24 + hours + minutes / 60 + seconds / 3600


def _process_alive(pid: int) -> bool:
    """True while pid is in the process table and not a zombie."""
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    stat = out.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def terminate_process_group(pid: int, grace_s: float) -> bool:
    """SIGTERM a hung job, SIGKILL after grace_s if it lingers; True once TERM was delivered.

    Safety guards, ported verbatim from the personal watchdog this adapter
    replaces: a pid that is not its own process-group leader gets the signal
    alone, never `killpg` (a leaderless killpg would mean every process in
    that group, which may include unrelated siblings); and a group id of 0
    or 1 is never signalled -- `killpg(-1, ...)` means every process this
    user owns, and 0/1 can only appear here as a lookup artifact, never a
    real job's group.
    """
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return False
    if pgid <= 1 or pgid == os.getpgid(0):
        return False
    group = pgid == pid and pgid > 1

    def send(sig: int) -> None:
        if group:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)

    try:
        send(signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    deadline = time.time() + grace_s
    while time.time() < deadline:
        time.sleep(0.5)
        if not _process_alive(pid):
            return True
    try:
        send(signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # nothing signalable is left, which is the outcome we wanted
    return True
