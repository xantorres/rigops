from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


def run_group(
    command: str | Sequence[str], *, timeout: float, shell: bool = False,
    cwd: str | Path | None = None, env: Mapping[str, str] | None = None,
    errors: str = "replace",
) -> subprocess.CompletedProcess:
    """Run command in its own process group with captured text output.

    A timeout SIGKILLs the whole group, so a grandchild the command forked dies
    with it, reaps the child and raises TimeoutExpired. The caller's own group
    is a different pgid and is never touched.
    """
    with subprocess.Popen(
        command, shell=shell, start_new_session=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, errors=errors, cwd=cwd, env=env,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise subprocess.TimeoutExpired(command, timeout) from None
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
