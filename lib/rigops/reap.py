"""Reap merged, unused git worktrees across a projects tree.

Worktrees accumulate silently: every tool that creates one either releases it
only on a clean exit path, or leaves it behind entirely. Nothing sweeps. This
walks each repository, enumerates its worktrees through git itself, and removes
only those that are provably safe to remove.

Enumeration goes through `git worktree list --porcelain` rather than walking
directories, so worktrees registered outside the repository tree are covered by
the same code path, and lock state is visible.

A worktree is removed only when every one of these holds:

  * it is not the primary checkout
  * it is not locked
  * it has a branch (detached HEADs are left alone; their commits are
    reachable only through the reflog and cannot be merge-checked)
  * its branch is an ancestor of the repository's merge target, OR its tip
    commit is older than --grace-days
  * it carries no commits that exist only here (see --grace-days below)
  * its working tree is clean: untracked build/type-gen output is ignored,
    and a tracked file counts as dirty only if it actually differs from the
    default branch
  * no live process is working inside it
  * it is not an ancestor of this process's own directory

A worktree with commits found nowhere else -- no upstream and unmerged, or
ahead of its upstream -- is never removed regardless of age; it is reported
under `needs-push` instead.

Registrations whose directory is already gone are pruned unconditionally;
there is nothing on disk left to lose.

Default mode is a read-only report. Removal requires an explicit --apply.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rigops import config, state

# Candidate merge targets, most authoritative first. A repository that resolves
# none of these is skipped whole: without a target there is no merged-check, and
# without a merged-check there is no safe removal.
MERGE_TARGETS = (
    "origin/HEAD",
    "origin/main",
    "origin/master",
    "main",
    "master",
)

REAP = "reap"
PRUNE = "prune"

GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def clamp_timeout(base_s: int, remaining_s: float | None, floor_s: int = 5) -> int | None:
    """Cap a git timeout to what's left of the run deadline.

    None means no deadline is in play, so base_s passes through unchanged. A
    remaining budget at or under floor_s is too thin to risk the call at all;
    the caller must skip it rather than race a doomed subprocess.
    """
    if remaining_s is None:
        return int(base_s)
    if remaining_s <= floor_s:
        return None
    return int(min(base_s, remaining_s))


def _run_captured(
    argv: list[str], timeout: int, env: dict[str, str],
) -> subprocess.CompletedProcess:
    """Run argv with captured output; a timeout SIGKILLs the whole process group.

    start_new_session makes the child its own group leader (pgid == pid), so
    the SIGKILL reaches git's ssh/credential-helper children too -- those are
    exactly what a plain terminate leaves behind. The reaper's own process
    group is a different pgid and is never touched.
    """
    with subprocess.Popen(
        argv, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
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
            raise subprocess.TimeoutExpired(argv, timeout) from None
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run git against a repository with hooks and prompts disabled."""
    env = {**os.environ, **GIT_ENV}
    return _run_captured(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args], timeout, env
    )


def git_ok(repo: Path, *args: str, timeout: int = 120) -> str | None:
    """Return stripped stdout, or None if git exited non-zero."""
    try:
        proc = git(repo, *args, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_lines(repo: Path, *args: str, timeout: int = 120) -> str | None:
    """Like git_ok, but only the trailing newline is trimmed. A whole-string
    .strip() silently eats the leading space of column-formatted output --
    `git status --porcelain`'s first status char is literally a space for
    "unmodified in index", and losing it shifts every column by one."""
    try:
        proc = git(repo, *args, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else None


def git_error(stderr: str) -> str:
    """Pick the informative line out of git's stderr.

    Failures often end with continuation text ("and the repository exists.")
    that says nothing on its own, so prefer the line git marked as the cause.
    """
    lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    if not lines:
        return "unknown"
    for line in lines:
        if line.startswith(("fatal:", "error:")):
            return line
    return lines[-1]


def human(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def dir_size(path: Path) -> int:
    """Apparent size on disk. Copy-on-write clones inflate this, so treat the
    total as an upper bound on what a removal actually frees."""
    try:
        proc = subprocess.run(
            ["du", "-sk", str(path)], capture_output=True, text=True, timeout=120, check=False
        )
        if proc.returncode == 0:
            return int(proc.stdout.split()[0]) * 1024
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return 0


def discover_repos(root: Path) -> list[Path]:
    """Primary checkouts two levels under root. A worktree carries a .git file
    rather than a directory, which keeps linked worktrees out of the list even
    when they sit alongside real repositories."""
    repos = []
    try:
        groups = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return repos
    for group in groups:
        try:
            candidates = sorted(p for p in group.iterdir() if p.is_dir())
        except OSError:
            continue
        for candidate in candidates:
            if (candidate / ".git").is_dir():
                repos.append(candidate)
    return repos


def parse_worktrees(porcelain: str) -> list[dict]:
    """Parse `git worktree list --porcelain` into records.

    Records are blank-line separated. `bare`, `detached`, `locked` and
    `prunable` appear as valueless or reason-carrying flag lines.
    """
    entries: list[dict] = []
    current: dict = {}
    for line in porcelain.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = {"path": value, "branch": None, "head": None,
                       "bare": False, "detached": False, "locked": False, "prunable": False}
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key in ("bare", "detached", "locked", "prunable"):
            current[key] = True
    if current:
        entries.append(current)
    return entries


def live_worktree_cwds(sessions_dir: Path) -> list[Path]:
    """Directories currently occupied by a live process.

    Session files are named for their PID and record the directory the session
    is working in. A file whose PID is gone is stale and carries no claim.
    """
    live: list[Path] = []
    for entry in sessions_dir.glob("*.json"):
        try:
            data = json.loads(entry.read_text())
            pid, cwd = int(data["pid"]), data.get("cwd")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not cwd:
            continue
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OverflowError):
            continue
        live.append(Path(cwd))
    return live


ETIME_RE = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def _etime_to_seconds(text: str) -> int | None:
    """[[dd-]hh:]mm:ss -> seconds. macOS ps has no etimes keyword."""
    m = ETIME_RE.match(text.strip())
    if not m:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def ps_snapshot() -> dict[int, dict]:
    """One process-table snapshot the whole run reasons from.

    uid= prints the numeric uid, matching os.getuid() comparisons downstream.
    Elapsed time is normalized to seconds under the "etimes" key.
    """
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,uid=,tty=,etime=,command="],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if proc.returncode != 0:
        return {}
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
    return snap


def cwd_map() -> dict[int, str]:
    """pid -> current working directory, from one lsof pass.

    lsof exits 1 when it cannot introspect some other-user processes while
    still printing valid records for the rest, so 1 is usable output.
    """
    try:
        proc = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-Fpn"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if proc.returncode not in (0, 1):
        return {}
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
    return cwds


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
    """True while the pid exists and is not a zombie."""
    try:
        proc = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=10, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
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

    deadline = time.time() + grace_s
    while survivors and time.time() < deadline:
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


def stale_worktree_dirs(repo: Path, registered_paths: set[str],
                        live: list[Path]) -> list[str]:
    """Worktree directories on disk that git no longer registers.

    Kill-only targets: processes rooted here are riding an unregistered
    directory, but the directory itself is never removed by this tool.
    """
    base = repo / ".claude" / "worktrees"
    if not base.is_dir():
        return []
    stale: list[str] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        real = os.path.realpath(entry)
        if real in registered_paths:
            continue
        if any(path_claims(str(cwd), real) for cwd in live):
            continue
        stale.append(real)
    return stale


def age_days(path: Path) -> int | None:
    """Days since the worktree directory was created.

    Taken from the filesystem rather than a creation log: it needs no hook, it
    cannot drift out of sync, and it already covers worktrees that existed
    before this tool did. Purely informational -- age never authorizes a
    removal, so falling back to None costs only a column in the report.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    created = getattr(st, "st_birthtime", st.st_mtime)
    return int((datetime.now(timezone.utc).timestamp() - created) / 86400)


def resolve_target(repo: Path) -> str | None:
    for ref in MERGE_TARGETS:
        if git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    return None


def is_ignored_untracked(path: str, ignored_dirs: set[str],
                         ignored_prefixes: tuple[str, ...]) -> bool:
    """True when any path segment is generated noise (build output, type
    codegen): a file under an ignored directory is noise wherever it sits."""
    for part in path.split("/"):
        if part in ignored_dirs or part.startswith(ignored_prefixes):
            return True
    return False


def default_branch(repo: Path) -> str:
    """Resolve the parent repo's default branch for the dirty-diff comparison.

    Independent of MERGE_TARGETS/resolve_target: that mechanism picks whatever
    ref exists from a five-candidate list for the merge-ancestor check. This
    is specifically the remote's advertised default, with a literal fallback
    so the diff check always has something to compare against.
    """
    ref = git_ok(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if ref and ref.startswith("refs/remotes/"):
        return ref.removeprefix("refs/remotes/")
    return "main"


def worktree_has_real_changes(path: Path, status: str, default_branch_ref: str,
                              ignored_dirs: set[str], ignored_prefixes: tuple[str, ...]) -> bool:
    """Decide whether porcelain output represents real work.

    Untracked entries matching the ignored dirs/prefixes never count. A
    tracked file that git considers modified only counts if its content
    actually differs from the default branch -- some worktrees show local M
    markers on files whose on-disk content already matches origin's, and
    treating those as dirty blocks removal of a worktree with nothing left
    to lose.
    """
    tracked: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        code, rest = line[:2], line[3:]
        if code == "??":
            if not is_ignored_untracked(rest, ignored_dirs, ignored_prefixes):
                return True
            continue
        tracked.append(rest.split(" -> ", 1)[-1])

    if not tracked:
        return False

    diff = git_ok(path, "diff", default_branch_ref, "--stat", "--", *tracked)
    # Unreadable comparison must not clear a dirty flag.
    return diff is None or bool(diff)


def has_unpushed_commits(repo: Path, wt_path: Path, merged: bool) -> bool:
    """True when this worktree holds commits that exist nowhere else.

    `@{u}..` is the direct answer when an upstream is configured. Without
    one, there is nothing to compare against, so a merged branch is trusted
    (its commits are captured by the merge) and an unmerged one is not --
    that is the only case where local-only work would be lost.
    """
    upstream = git_ok(wt_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        ahead = git_ok(wt_path, "log", "@{u}..", "--oneline")
        return ahead is None or bool(ahead)
    return not merged


def commit_age_days(repo: Path, sha: str) -> int | None:
    """Days since the branch's tip commit -- independent of the worktree
    directory's filesystem age, which resets if the worktree is recreated
    without the branch itself gaining new work."""
    ts = git_ok(repo, "log", "-1", "--format=%ct", sha)
    if not ts:
        return None
    try:
        return int((datetime.now(timezone.utc).timestamp() - int(ts)) / 86400)
    except ValueError:
        return None


def classify(wt: dict, repo: Path, target: str, live: list[Path], self_cwd: Path,
             default_branch_ref: str, grace_days: int, ignored_dirs: set[str],
             ignored_prefixes: tuple[str, ...]) -> str:
    if wt["prunable"]:
        return PRUNE
    if wt["bare"]:
        return "skip: bare"
    if wt["locked"]:
        return "skip: locked"
    if wt["detached"] or not wt["branch"]:
        return "skip: detached"

    path = Path(wt["path"])
    wt_path_str = str(path)
    if path_claims(str(self_cwd), wt_path_str):
        return "skip: self"
    if any(path_claims(str(c), wt_path_str) for c in live):
        return "skip: in-use"
    if not path.is_dir():
        return PRUNE

    try:
        merged = git(repo, "merge-base", "--is-ancestor",
                    f"refs/heads/{wt['branch']}", target).returncode == 0
    except subprocess.TimeoutExpired:
        return "skip: unreadable"

    if has_unpushed_commits(repo, path, merged):
        return "needs-push"

    if not merged:
        age = commit_age_days(repo, wt["head"]) if wt["head"] else None
        if age is None or age <= grace_days:
            return "skip: unmerged"

    # -uall so build output and other untracked leftovers count as work in
    # progress rather than being silently discarded.
    status = git_lines(path, "status", "--porcelain", "-uall")
    if status is None:
        return "skip: unreadable"
    if status and worktree_has_real_changes(path, status, default_branch_ref,
                                            ignored_dirs, ignored_prefixes):
        return "skip: dirty"

    return REAP


def scan_repo(repo: Path, live: list[Path], self_cwd: Path, fetch: bool, grace_days: int,
              ignored_dirs: set[str], ignored_prefixes: tuple[str, ...],
              remaining_s: float | None = None) -> dict:
    scan_start = time.time()
    result = {"repo": str(repo), "target": None, "worktrees": [], "note": None,
              "timed_out": False, "listing_ok": True}

    if fetch:
        fetch_timeout = clamp_timeout(180, remaining_s)
        if fetch_timeout is None:
            result["timed_out"] = True
            result["note"] = "deadline: fetch skipped"
            log(f"deadline: fetch skipped: {repo}")
        else:
            try:
                proc = git(repo, "fetch", "--prune", "--quiet", timeout=fetch_timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                result["note"] = "fetch timeout, using local refs"
                log(f"fetch timeout: {repo}")
            else:
                if proc.returncode != 0:
                    result["note"] = f"fetch failed, using local refs: {git_error(proc.stderr)}"

    list_remaining = (
        remaining_s - (time.time() - scan_start) if remaining_s is not None else None
    )
    list_timeout = clamp_timeout(120, list_remaining)
    if list_timeout is None:
        # A note may already be set by the fetch-skip branch above; don't
        # clobber it, but the listing must still be skipped and the caller
        # must not be handed a stale worktree registry.
        if result["note"] is None:
            result["note"] = "deadline: worktree listing skipped"
        result["listing_ok"] = False
        return result

    porcelain = git_ok(repo, "worktree", "list", "--porcelain", timeout=list_timeout)
    if porcelain is None:
        result["note"] = "cannot list worktrees"
        # Without the registry the stale-dir sweep cannot tell stale from live;
        # a transient git failure must not grant kill authority over all of them.
        result["listing_ok"] = False
        return result

    entries = parse_worktrees(porcelain)
    if len(entries) <= 1:
        return result

    target = resolve_target(repo)
    result["target"] = target
    if target is None:
        result["note"] = "no merge target resolved; repository skipped"
        for wt in entries[1:]:
            wt["verdict"] = PRUNE if wt["prunable"] else "skip: no target"
            result["worktrees"].append(wt)
        return result

    default_branch_ref = default_branch(repo)

    # entries[0] is the primary checkout and is never a candidate.
    for wt in entries[1:]:
        wt["verdict"] = classify(wt, repo, target, live, self_cwd, default_branch_ref, grace_days,
                                  ignored_dirs, ignored_prefixes)
        wt["size"] = dir_size(Path(wt["path"])) if wt["verdict"] == REAP else 0
        wt["age_days"] = age_days(Path(wt["path"]))
        result["worktrees"].append(wt)
    return result


def log(message: str) -> None:
    log_file = state.state_dir() / "reap.log"
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with log_file.open("a") as fh:
        fh.write(f"{stamp} {message}\n")


def reap(repo: Path, wt: dict, dry_run: bool) -> tuple[bool, str]:
    """Remove one worktree and its branch.

    Runs from the repository root, never from inside the target: removing the
    directory a process is sitting in leaves it on an unlinked inode.

    --force is required on remove because a clean check and a removal are not
    atomic, and generated files reappear between them. `branch -d` is then
    deliberately the lowercase form: it re-runs the merged-check independently,
    so losing that race costs a leftover branch rather than commits.
    """
    path, branch, sha = wt["path"], wt["branch"], wt["head"]
    if dry_run:
        return True, (
            f"git -C {repo} worktree remove --force {path} && "
            f"git -C {repo} branch -d {branch}"
        )

    try:
        removal = git(repo, "worktree", "remove", "--force", path)
    except subprocess.TimeoutExpired:
        return False, "git timeout (worktree remove)"
    if removal.returncode != 0:
        detail = git_error(removal.stderr)
        log(f"FAIL remove repo={repo} path={path} branch={branch} err={detail}")
        return False, detail

    try:
        branch_removal = git(repo, "branch", "-d", branch)
    except subprocess.TimeoutExpired:
        # Worktree is already gone; a hung branch delete must not turn a real
        # removal into a reported failure.
        note = " (branch kept: git timeout)"
        log(f"REAP repo={repo} path={path} branch={branch} sha={sha}{note}")
        return True, f"removed{note}"
    branch_note = "" if branch_removal.returncode == 0 else " (branch kept: not merged)"
    log(f"REAP repo={repo} path={path} branch={branch} sha={sha}{branch_note}")
    return True, f"removed{branch_note}"


def render(results: list[dict], apply_mode: bool) -> str:
    lines: list[str] = []
    counts: dict[str, int] = {}
    reclaim = 0

    for res in results:
        interesting = [w for w in res["worktrees"] if w["verdict"] != "skip: bare"]
        if not interesting and not res["note"]:
            continue
        repo_label = str(Path(res["repo"])).replace(str(Path.home()), "~")
        header = f"\n{repo_label}"
        if res["target"]:
            header += f"  (target: {res['target']})"
        lines.append(header)
        if res["note"]:
            lines.append(f"  ! {res['note']}")
        for wt in interesting:
            counts[wt["verdict"]] = counts.get(wt["verdict"], 0) + 1
            if wt["verdict"] == REAP:
                reclaim += wt.get("size", 0)
            name = Path(wt["path"]).name
            branch = wt["branch"] or "(detached)"
            age = f"{wt['age_days']}d" if wt.get("age_days") is not None else "-"
            size = f"  {human(wt['size'])}" if wt.get("size") else ""
            lines.append(f"  {wt['verdict']:<18} {age:>5}  {name:<34} {branch}{size}")

    lines.append("")
    if counts:
        summary = "  ".join(f"{v}={counts[v]}" for v in sorted(counts))
        lines.append(f"totals: {summary}")
    else:
        lines.append("totals: nothing found")
    if reclaim:
        verb = "reclaimed" if apply_mode else "reclaimable"
        lines.append(
            f"{verb}: {human(reclaim)} (apparent size; copy-on-write means actual is lower)"
        )
    return "\n".join(lines)


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


def run(opts: Any) -> int:
    """Body of a reap invocation: discover repos, scan, kill, remove, report."""
    if opts.dry_run and not opts.apply:
        print("--dry-run requires --apply", file=sys.stderr)
        return 64
    if shutil.which("git") is None:
        print("git not found on PATH", file=sys.stderr)
        return 2

    if opts.repo:
        repos = [opts.repo.resolve()]
    else:
        repos = []
        for root in opts.roots:
            if not root.is_dir():
                print(f"{root} is not a directory", file=sys.stderr)
                return 2
            repos.extend(discover_repos(root))

    live = live_worktree_cwds(opts.sessions_dir)
    self_cwd = Path.cwd().resolve()
    run_start = time.time()
    results = []
    deadline_hit = False
    for r in repos:
        if time.time() - run_start > opts.deadline_s:
            deadline_hit = True
            skipped = len(repos) - len(results)
            log(f"ABORT deadline elapsed={int(time.time() - run_start)}s "
                f"scanned={len(results)} skipped={skipped}")
            break
        results.append(scan_repo(
            r, live, self_cwd, fetch=not opts.no_fetch, grace_days=opts.grace_days,
            ignored_dirs=opts.ignored_untracked_dirs,
            ignored_prefixes=opts.ignored_untracked_prefixes,
            remaining_s=opts.deadline_s - (time.time() - run_start),
        ))

    snap = ps_snapshot()
    cwds = cwd_map()
    real_apply = opts.apply and not opts.dry_run
    killed_total = 0
    kill_failed_total = 0

    def emit_preview(kill_result: dict) -> None:
        if real_apply or opts.json:
            return
        for w in kill_result["would"]:
            print(f"WOULD-KILL pid={w['pid']} comm={w['comm']} match={w['match']} tree={w['tree']}")

    removed = 0
    for res in results:
        repo = Path(res["repo"])
        res["kills"] = []
        targets = [w for w in res["worktrees"] if w["verdict"] == REAP]
        for wt in targets:
            tree = str(Path(wt["path"]))
            # Uniform report shape for every reap-verdict worktree; the
            # branches below only ever overwrite these.
            wt["applied"] = None
            wt["blockers"] = []
            rooted_all = processes_rooted_in(tree, snap, cwds)
            rooted = _filter_killable(rooted_all, snap)
            tags = _match_tags(tree, rooted, snap, cwds)
            kr = kill_pids(tree, tags, snap, real_apply, opts.max_kills_per_tree, opts.kill_grace_s)
            res["kills"].append(kr)
            killed_total += kr["killed"]
            kill_failed_total += kr["kill_failed"]
            emit_preview(kr)
            if kr["overflow"]:
                continue  # tree survives to next run; classification is not trusted
            if opts.apply:
                blockers = removal_blockers(rooted_all, tags, kr)
                if blockers:
                    pids = ",".join(str(b["pid"]) for b in blockers)
                    wt["blockers"] = [b["pid"] for b in blockers]
                    if not opts.json:
                        print(f"skip: {Path(wt['path']).name} -- "
                              f"{len(blockers)} live process(es) block removal (pids: {pids})")
                    log(f"SKIP-LIVE tree={tree} pids={pids}")
                    continue
                ok, detail = reap(repo, wt, opts.dry_run)
                wt["applied"] = {"removed": ok and not opts.dry_run, "detail": detail}
                prefix = "would run" if opts.dry_run else ("ok" if ok else "FAILED")
                if not opts.json:
                    print(f"{prefix}: {Path(wt['path']).name} -- {detail}")
                if ok and not opts.dry_run:
                    removed += 1

        if not res.get("listing_ok", True):
            continue
        registered = {os.path.realpath(res["repo"])} | {
            os.path.realpath(w["path"]) for w in res["worktrees"]}
        for stale_path in stale_worktree_dirs(repo, registered, live):
            rooted = _filter_killable(processes_rooted_in(stale_path, snap, cwds), snap)
            tags = _match_tags(stale_path, rooted, snap, cwds)
            kr = kill_pids(f"stale={stale_path}", tags, snap, real_apply,
                           opts.max_kills_per_tree, opts.kill_grace_s)
            res["kills"].append(kr)
            killed_total += kr["killed"]
            kill_failed_total += kr["kill_failed"]
            emit_preview(kr)

        wt_verdicts = (w["verdict"] for w in res["worktrees"])
        if opts.apply and not opts.dry_run and any(v in (REAP, PRUNE) for v in wt_verdicts):
            try:
                git(repo, "worktree", "prune")
            except subprocess.TimeoutExpired:
                pass

    # Orphan sweep is independent of --repo: it walks every configured root,
    # not just the repository (if any) selected for the worktree scan above.
    orphan_raw: set[int] = set()
    for root in opts.roots:
        orphan_raw |= orphan_daemons(snap, cwds, str(root.resolve()), opts.orphan_min_age_s)
    orphans = _filter_killable(orphan_raw, snap)
    orphan_kr = kill_pids("orphan-daemon", {pid: "daemon" for pid in orphans}, snap, real_apply,
                          opts.max_kills_per_tree, opts.kill_grace_s)
    killed_total += orphan_kr["killed"]
    kill_failed_total += orphan_kr["kill_failed"]
    emit_preview(orphan_kr)
    orphan_json = [
        {"pid": pid,
         "comm": (Path(snap[pid]["command"].split()[0]).name
                 if snap[pid]["command"].split() else "?"),
         "match": "daemon", "etimes": snap[pid]["etimes"]}
        for pid in sorted(orphans) if pid in snap
    ]

    all_worktrees = [w for res in results for w in res["worktrees"]]
    eligible = sum(1 for w in all_worktrees if w["verdict"] == REAP)
    kept_dirty = sum(1 for w in all_worktrees if w["verdict"] == "skip: dirty")
    kept_unpushed = sum(1 for w in all_worktrees if w["verdict"] == "needs-push")
    kept_locked = sum(1 for w in all_worktrees if w["verdict"] == "skip: locked")
    timeouts = sum(1 for res in results if res.get("timed_out"))
    log(f"reaper: eligible={eligible} removed={removed} kept_dirty={kept_dirty} "
        f"kept_unpushed={kept_unpushed} kept_locked={kept_locked} timeouts={timeouts} "
        f"killed={killed_total} kill_failed={kill_failed_total} "
        f"elapsed={int(time.time() - run_start)}s deadline_hit={int(deadline_hit)}")

    if opts.json:
        print(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "repos": results,
                          "orphan_daemons": orphan_json}, indent=2))
    else:
        print(render(results, apply_mode=opts.apply and not opts.dry_run))

    return 0
