"""Probe whether the headless `claude -p` credential still works.

A headless (launchd/cron) credential expires independently of interactive
login: interactive sessions keep working while every unattended run fails
silently. This probe runs under a scrubbed environment, because an
interactive Claude Code session exports variables that route auth through
the host session and would produce a false ok.
"""

from __future__ import annotations

import getpass
import math
import os
import shutil
import subprocess
import tempfile

from rigops import config as rigops_config


def build_env(path: str, token: str = "") -> dict:
    """Minimal env for the probe subprocess -- nothing else is inherited."""
    home = os.environ.get("HOME") or os.path.expanduser("~")
    user = os.environ.get("USER") or getpass.getuser()
    env = {"HOME": home, "USER": user, "PATH": path, "SHELL": "/bin/sh", "TERM": "dumb"}
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def keychain_token(service: str, timeout_s: float = 5, runner=subprocess.run) -> str:
    """Reads a long-lived token from the macOS keychain. A keychain item
    added without -T triggers a GUI consent dialog on first read; the
    timeout keeps that from hanging an unattended probe."""
    if not service:
        return ""
    if shutil.which("security") is None:
        return ""
    user = os.environ.get("USER") or getpass.getuser()
    try:
        proc = runner(
            ["security", "find-generic-password", "-a", user, "-s", service, "-w"],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def probe(cfg, runner=subprocess.run) -> tuple:
    defaults = rigops_config.DEFAULTS["authprobe"]
    command = rigops_config.get(cfg, "authprobe.command", defaults["command"])
    expect = rigops_config.get(cfg, "authprobe.expect", defaults["expect"])
    timeout_s = rigops_config.get(cfg, "authprobe.timeout_s", defaults["timeout_s"])
    path = rigops_config.get(cfg, "authprobe.path", defaults["path"])
    keychain_service = rigops_config.get(
        cfg, "authprobe.keychain_service", defaults["keychain_service"]
    )

    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(a, str) for a in command)
    ):
        return False, "invalid authprobe.command"
    if not isinstance(path, str) or not path:
        path = defaults["path"]
    try:
        # The coerced float is what reaches subprocess -- a quoted number in
        # config must not pass validation and then arrive as a string.
        timeout_s = float(timeout_s)
        if timeout_s <= 0 or not math.isfinite(timeout_s):
            timeout_s = 60.0
    except (TypeError, ValueError):
        # Bad timeout still bounds the subprocess rather than hanging or
        # crashing the caller.
        timeout_s = 60.0

    token = keychain_token(keychain_service)
    env = build_env(path, token)

    try:
        proc = runner(
            command, env=env, capture_output=True, text=True, timeout=timeout_s,
            cwd=tempfile.gettempdir(),
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_s:g}s"
    except OSError as exc:
        return False, str(exc)

    output = proc.stdout or ""
    lines = output.splitlines()
    if proc.returncode != 0:
        first_nonempty = next((ln for ln in lines if ln.strip()), "")
        if first_nonempty:
            return False, first_nonempty
        err_lines = (getattr(proc, "stderr", "") or "").splitlines()
        first_err_nonempty = next((ln for ln in err_lines if ln.strip()), "")
        return False, first_err_nonempty or f"exit {proc.returncode}"
    if output.strip() != expect:
        return False, lines[0] if lines else ""
    return True, "ok"
