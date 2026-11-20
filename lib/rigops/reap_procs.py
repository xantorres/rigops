"""Process table reads, matching and the kill path behind reap; reap.py re-exports every name."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from rigops import state


def log(message: str) -> None:
    log_file = state.state_dir() / "reap.log"
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with log_file.open("a") as fh:
        fh.write(f"{stamp} {message}\n")


ETIME_RE = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def _etime_to_seconds(text: str) -> int | None:
    """[[dd-]hh:]mm:ss -> seconds. macOS ps has no etimes keyword."""
    m = ETIME_RE.match(text.strip())
    if not m:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def ps_snapshot() -> dict[int, dict] | None:
    """One process-table snapshot the whole run reasons from; None when ps failed.

    uid= prints the numeric uid, matching os.getuid() comparisons downstream.
    Elapsed time is normalized to seconds under the "etimes" key.
    """
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,uid=,tty=,etime=,command="],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    snap: dict[int, dict] = {}
    for line in proc.stdout.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        try:
            pid, ppid, pgid, uid = (int(p) for p in parts[:4])
        except ValueError:
            continue
        etimes = _etime_to_seconds(parts[5])
        if etimes is None:
            continue
        snap[pid] = {"ppid": ppid, "pgid": pgid, "uid": uid, "tty": parts[4],
                     "etimes": etimes, "command": parts[6]}
    # Output that parsed to nothing, not even this process, was not read.
    return snap if os.getpid() in snap else None


def cwd_map() -> dict[int, str] | None:
    """pid -> current working directory, from one lsof pass; None when lsof failed.

    lsof exits 1 when it cannot introspect some other-user processes while
    still printing valid records for the rest, so 1 is usable output.
    """
    try:
        proc = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-Fpn"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    cwds: dict[int, str] = {}
    pid: int | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("n") and pid is not None:
            cwds[pid] = line[1:]
    # An exit 1 that listed nothing, not even this process, read nothing.
    return cwds if os.getpid() in cwds else None


def path_claims(path: str | None, target: str) -> bool:
    """True when path is target or lies under it. Trailing-slash-safe.

    Absolute paths only: a relative argv token would otherwise resolve against
    this process's own cwd and match whatever tree the reaper was run from.
    """
    if not path or not os.path.isabs(path):
        return False
    try:
        real_path = os.path.realpath(path)
        real_target = os.path.realpath(target)
    except OSError:
        return False
    return real_path == real_target or real_path.startswith(real_target + os.sep)


def _ppid_of(pid: int) -> int | None:
    """ppid of pid via a direct ps lookup, for an ancestor missing from the
    snapshot. None when ps can't answer (gone, timeout, error)."""
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10,
                             check=False).stdout.strip()
        return int(out)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def protected_pids(snap: dict[int, dict]) -> set[int]:
    """Never-signal set: init, kernel, this process, and its ancestor chain.

    An ancestor missing from the snapshot (dropped line) must not truncate the
    walk and leave the rest of the chain signalable; ask ps directly instead.
    """
    protected = {0, 1, os.getpid()}
    pid = os.getpid()
    seen: set[int] = set()
    walk_truncated = False
    while pid not in (0, 1) and pid not in seen:
        seen.add(pid)
        if pid in snap:
            pid = snap[pid]["ppid"]
        else:
            next_pid = _ppid_of(pid)
            if next_pid is None:
                walk_truncated = True
                break
            pid = next_pid
        protected.add(pid)
    if walk_truncated:
        # A truncated ancestor walk above must not leave the reaper's own
        # process group signalable.
        pgrp = os.getpgrp()
        protected |= {p for p, i in snap.items() if i.get("pgid") == pgrp}
    return protected


def processes_rooted_in(tree: str, snap: dict[int, dict],
                        cwds: dict[int, str]) -> set[int]:
    """Pids whose cwd or argv is rooted in tree, plus all their descendants.

    Argv matching is per whitespace token, never a substring of the whole
    command line, so an editor daemon whose log path merely mentions a parent
    directory does not match.
    """
    roots: set[int] = set()
    for pid, cwd in cwds.items():
        if pid in snap and path_claims(cwd, tree):
            roots.add(pid)
    for pid, info in snap.items():
        if pid in roots:
            continue
        if any(path_claims(tok, tree) for tok in info["command"].split()):
            roots.add(pid)
    if not roots:
        return roots
    return _descendants(roots, snap)


def _filter_killable(pids: set[int], snap: dict[int, dict]) -> set[int]:
    """Own-uid, terminal-less processes only, minus the never-signal set.

    A controlling tty means an interactive session -- a user's shell parked in
    an old worktree directory, not a leaked build daemon. Those are never
    signalled; every orphan this module exists for runs detached (tty `??` on
    macOS, `?` on Linux).
    """
    uid = os.getuid()
    keep = {p for p in pids
            if p in snap and snap[p]["uid"] == uid and snap[p]["tty"].strip("?") == ""}
    return keep - protected_pids(snap)


def owned_pids(pids: set[int], snap: dict[int, dict]) -> set[int]:
    """Matched pids that something alive outside the matched set still owns.

    Walk each pid up through parents that are themselves matched; the top-most
    matched ancestor must have been reparented to init (ppid 1), or a live
    editor or shell outside the tree may still be using the process.
    """
    owned: set[int] = set()
    for pid in pids:
        if pid not in snap:
            continue
        top, seen = pid, {pid}
        while snap[top]["ppid"] in pids and snap[top]["ppid"] in snap \
                and snap[top]["ppid"] not in seen:
            top = snap[top]["ppid"]
            seen.add(top)
        if snap[top]["ppid"] != 1:
            owned.add(pid)
    return owned


def split_owned(rooted: set[int], snap: dict[int, dict]) -> tuple[set[int], set[int]]:
    """(killable, owned): the signalable subset of rooted, split by attribution."""
    killable = _filter_killable(rooted, snap)
    owned = owned_pids(rooted, snap) & killable
    return killable - owned, owned


def owned_entries(label: str, owned: set[int], snap: dict[int, dict]) -> list[dict]:
    entries = []
    for pid in sorted(owned):
        tokens = snap[pid]["command"].split()
        entries.append({"pid": pid, "comm": Path(tokens[0]).name if tokens else "?",
                        "reason": "owned", "parent": snap[pid]["ppid"], "tree": label})
    return entries


def _descendants(roots: set[int], snap: dict[int, dict]) -> set[int]:
    """roots plus every process reachable through ppid chains."""
    children: dict[int, list[int]] = {}
    for pid, info in snap.items():
        children.setdefault(info["ppid"], []).append(pid)
    matched = set(roots)
    queue = list(roots)
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in matched:
                matched.add(child)
                queue.append(child)
    return matched


def _match_tags(tree: str, pids: set[int], snap: dict[int, dict],
                cwds: dict[int, str]) -> dict[int, str]:
    """Why each already-matched pid matched: cwd, argv, or child (closure only)."""
    tags: dict[int, str] = {}
    for pid in pids:
        if path_claims(cwds.get(pid), tree):
            tags[pid] = "cwd"
        elif pid in snap and any(path_claims(tok, tree) for tok in snap[pid]["command"].split()):
            tags[pid] = "argv"
        else:
            tags[pid] = "child"
    return tags


def process_alive(pid: int) -> bool:
    """True while the pid exists and is not a zombie; an unreadable check counts as alive."""
    try:
        proc = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=10, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return True
    stat = proc.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def _alive_for_blocking(pid: int) -> bool:
    """Like process_alive, but fails CLOSED: removal must not proceed on an
    unreadable liveness check, so an unknown pid counts as alive here."""
    try:
        proc = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=10, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return True
    if proc.returncode != 0:
        return False
    stat = proc.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def verify_pid(pid: int, expected_command: str) -> bool:
    """Guard against pid reuse: the command line must still match the snapshot."""
    try:
        proc = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                              capture_output=True, text=True, timeout=10, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0:
        return False
    return proc.stdout.strip() == expected_command.strip()


def kill_pids(label: str, pids: dict[int, str], snap: dict[int, dict], apply: bool,
             max_kills: int, grace_s: int) -> dict:
    """TERM, grace, KILL for an already-matched pid set. Explicit pids only.

    Group signalling is useless here -- the orphans this hunts have dead group
    leaders -- and pattern kills are banned; every signal goes to one verified
    pid. Over the cap means the match is not trusted: no signals at all.
    """

    def comm(pid: int) -> str:
        tokens = snap[pid]["command"].split() if pid in snap else []
        return Path(tokens[0]).name if tokens else "?"

    would = [{"pid": pid, "comm": comm(pid), "match": tag, "tree": label}
             for pid, tag in sorted(pids.items())]
    result = {"would": would, "killed": 0, "kill_failed": 0, "overflow": False, "failed_pids": []}
    if len(pids) > max_kills:
        log(f"FAIL kill-overflow {label} count={len(pids)}")
        result["overflow"] = True
        return result
    if not apply:
        return result

    def send(pid: int, sig: int) -> str:
        try:
            os.kill(pid, sig)
            return "sent"
        except ProcessLookupError:
            return "gone"
        except PermissionError:
            return "denied"

    def fail(pid: int, tag: str, reason: str) -> None:
        # A bare counter is undiagnosable after the fact: record which process
        # survived and why, in the same shape as the KILL lines.
        failed.add(pid)
        log(f"KILL-FAIL pid={pid} comm={comm(pid)} match={tag} tree={label} reason={reason}")

    survivors: list[int] = []
    failed: set[int] = set()
    gone: set[int] = set()  # dead before any signal: neither killed nor failed
    tag_of = dict(pids)
    for pid, tag in sorted(pids.items()):
        if not process_alive(pid):
            gone.add(pid)
            continue
        expected = snap[pid]["command"] if pid in snap else ""
        if not verify_pid(pid, expected):
            fail(pid, tag, "pid-reused")  # alive under a different command
            continue
        outcome = send(pid, signal.SIGTERM)
        if outcome == "gone":
            gone.add(pid)
            continue
        if outcome == "denied":
            fail(pid, tag, "term-denied")
            continue
        log(f"KILL pid={pid} comm={comm(pid)} match={tag} tree={label}")
        survivors.append(pid)

    deadline = time.monotonic() + grace_s
    while survivors and time.monotonic() < deadline:
        time.sleep(0.5)
        survivors = [p for p in survivors if process_alive(p)]

    for pid in list(survivors):
        if not process_alive(pid):
            continue
        expected = snap[pid]["command"] if pid in snap else ""
        if not verify_pid(pid, expected):
            fail(pid, tag_of.get(pid, "?"), "pid-reused")
            continue
        send(pid, signal.SIGKILL)
    if survivors:
        time.sleep(0.3)
    for pid in survivors:
        if pid not in failed and process_alive(pid):
            fail(pid, tag_of.get(pid, "?"), "survived-sigkill")
    result["kill_failed"] = len(failed)
    result["killed"] = len(pids) - len(failed) - len(gone)
    result["failed_pids"] = sorted(failed)
    return result


def removal_blockers(rooted: set, attempted: dict, kill_result: dict,
                     alive=_alive_for_blocking) -> list:
    """Pids that leave a to-be-removed tree not provably clear.

    Removing a directory out from under a live process leaves it on an
    unlinked inode; a tree that could not be fully cleared is not removed.
    """
    blockers = [
        {"pid": pid, "reason": "excluded-by-policy"}
        for pid in sorted(rooted - set(attempted))
        if alive(pid)
    ]
    blockers += [
        {"pid": pid, "reason": "kill-failed"}
        for pid in sorted(kill_result.get("failed_pids", []))
    ]
    return blockers


def orphan_daemons(snap: dict[int, dict], cwds: dict[int, str],
                   root: str, min_age_s: int) -> set[int]:
    """Long-lived build daemons reparented to launchd, rooted in the tree.

    ppid 1 excludes anything a live editor still owns; the node_modules gate
    excludes both system daemons that share tool names and every launchd
    agent of ours; 48h of runtime rules out anything still doing real work.
    """
    uid = os.getuid()
    matched: set[int] = set()
    for pid, info in snap.items():
        if info["uid"] != uid or info["ppid"] != 1:
            continue
        if "/node_modules/" not in info["command"]:
            continue
        if info["etimes"] <= min_age_s:
            continue
        if path_claims(cwds.get(pid), root) or any(
                path_claims(tok, root) for tok in info["command"].split()):
            matched.add(pid)
    if matched:
        # A daemon's own children die with it or leak next; take the tree.
        matched = _descendants(matched, snap)
    return matched - protected_pids(snap)
