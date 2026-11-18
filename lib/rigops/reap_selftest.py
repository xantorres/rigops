"""reap --selftest: prove the kill path hits only the processes it should."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

from rigops import config
from rigops.reap_procs import (
    _filter_killable,
    cwd_map,
    kill_pids,
    process_alive,
    processes_rooted_in,
    ps_snapshot,
)


def run_selftest() -> int:
    """Spawn disposable processes and prove the kill module hits only them."""
    fixture = os.path.realpath(tempfile.mkdtemp(prefix="reaper-selftest-"))
    elsewhere = os.path.realpath(tempfile.mkdtemp(prefix="reaper-bystander-"))
    procs: list[subprocess.Popen] = []
    failures: list[str] = []
    try:
        # start_new_session detaches from any controlling tty, matching the
        # daemon shape the filter accepts and real orphans actually have.
        target = subprocess.Popen(["sleep", "300"], cwd=fixture, start_new_session=True,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shell = subprocess.Popen(["sh", "-c", "sleep 300"], cwd=fixture, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        bystander = subprocess.Popen(["sleep", "300"], cwd=elsewhere, start_new_session=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs = [target, shell, bystander]
        time.sleep(1.0)  # let ps/lsof observe them

        snap = ps_snapshot()
        cwds = cwd_map()
        if snap is None or cwds is None:
            failures.append("process observation failed (ps or lsof)")
            snap, cwds = snap or {}, cwds or {}
        matched = _filter_killable(processes_rooted_in(fixture, snap, cwds), snap)

        if target.pid not in matched:
            failures.append(f"target sleep {target.pid} not matched")
        if shell.pid not in matched:
            failures.append(f"shell {shell.pid} not matched")
        if bystander.pid in matched:
            failures.append(f"bystander {bystander.pid} wrongly matched")
        shell_children = [p for p, i in snap.items() if i["ppid"] == shell.pid]
        for child in shell_children:
            if child not in matched:
                failures.append(f"shell child {child} missed by closure")

        if not failures:
            tags = {pid: "test" for pid in matched}
            kill_result = kill_pids(
                fixture, tags, snap, apply=True,
                max_kills=config.DEFAULTS["reaper"]["max_kills_per_tree"],
                grace_s=config.DEFAULTS["reaper"]["kill_grace_s"],
            )
            if process_alive(target.pid):
                failures.append(f"target {target.pid} still alive after kill")
            if process_alive(shell.pid):
                failures.append(f"shell {shell.pid} still alive after kill")
            for child in shell_children:
                if process_alive(child):
                    failures.append(f"shell child {child} still alive after kill")
            if not process_alive(bystander.pid):
                failures.append(f"bystander {bystander.pid} died")
            if kill_result["overflow"]:
                failures.append("unexpected overflow")
    finally:
        for p in procs:
            try:
                p.kill()
            except OSError:
                pass
            try:
                p.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        shutil.rmtree(fixture, ignore_errors=True)
        shutil.rmtree(elsewhere, ignore_errors=True)

    if failures:
        for f in failures:
            print(f"FAIL selftest: {f}")
        return 1
    print("PASS selftest: match, closure, bystander isolation, kill, cleanup")
    return 0
