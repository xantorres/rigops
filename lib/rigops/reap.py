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
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rigops.errors import RigopsError
from rigops.reap_guards import (
    PRECIOUS_PATTERNS,
    human,
    is_ignored_untracked,
    orphan_sweep,
    precious_path,
)
from rigops.reap_procs import (  # noqa: F401
    ETIME_RE,
    _alive_for_blocking,
    _descendants,
    _etime_to_seconds,
    _filter_killable,
    _match_tags,
    _ppid_of,
    cwd_map,
    kill_pids,
    log,
    orphan_daemons,
    path_claims,
    process_alive,
    processes_rooted_in,
    protected_pids,
    ps_snapshot,
    removal_blockers,
    verify_pid,
)

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
DEADLINE_SKIP = "skip: deadline"

GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def remaining_after(remaining_s: float | None, started: float) -> float | None:
    """What is left of a budget whose clock started at `started`.

    Negative is a real answer, not an error: clamp_timeout reads anything at
    or below its floor as too thin to spend, so an overspent budget refuses
    the next call rather than being rounded back up to zero.
    """
    if remaining_s is None:
        return None
    return remaining_s - (time.monotonic() - started)


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
    # surrogateescape: a non-UTF-8 path from `status -z` survives and re-encodes
    # to the same bytes when passed back to git as a pathspec.
    with subprocess.Popen(
        argv, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="surrogateescape", env=env,
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


def dir_size(path: Path, timeout: int = 120) -> int:
    """Apparent size on disk. Copy-on-write clones inflate this, so treat the
    total as an upper bound on what a removal actually frees.

    The caller passes a timeout already clamped to the run deadline: `du` on a
    cold or networked tree is as capable of outliving the deadline as any of
    the git calls around it.
    """
    try:
        proc = subprocess.run(
            ["du", "-sk", str(path)], capture_output=True, text=True, timeout=timeout, check=False
        )
        if proc.returncode == 0:
            return int(proc.stdout.split()[0]) * 1024
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return 0


def _has_git_dir(path: Path) -> bool:
    """True when path/.git is a directory.

    A directory the run cannot read is not a repository as far as this walk is
    concerned. Linux raises EACCES on the probe where macOS answers False, and
    one unreadable directory must not abort the scan of an entire root.
    """
    try:
        return (path / ".git").is_dir()
    except OSError:
        return False


def discover_repos(root: Path) -> list[Path]:
    """Primary checkouts one or two levels under root, so a root holding
    checkouts directly and a root holding groups of them both work.

    A worktree or submodule carries a .git file rather than a directory, which
    keeps both out of the list even when they sit alongside real repositories.
    A checkout found at the first level is not descended into: anything nested
    under it belongs to that repository, not beside it.
    """
    repos = []
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return repos
    for entry in entries:
        if _has_git_dir(entry):
            repos.append(entry)
            continue
        try:
            candidates = sorted(p for p in entry.iterdir() if p.is_dir())
        except OSError:
            continue
        for candidate in candidates:
            if _has_git_dir(candidate):
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


def live_worktree_cwds(sessions_dir: Path, strict: bool = False) -> list[Path]:
    """Directories currently occupied by a live process.

    Session files are named for their PID and record the directory the session
    is working in. A file whose PID is gone is stale and carries no claim.
    """
    live: list[Path] = []
    for entry in sessions_dir.glob("*.json"):
        try:
            data = json.loads(entry.read_text())
            pid, cwd = int(data["pid"]), data.get("cwd")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if strict and isinstance(exc, (OSError, ValueError)):
                raise RigopsError(f"unreadable session file {entry}") from exc
            continue
        if not cwd:
            continue
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OverflowError):
            continue
        live.append(Path(cwd))
    return live


def stale_worktree_dirs(repo: Path, registered_paths: set[str],
                        live: list[Path]) -> list[str]:
    """Worktree directories on disk that git no longer registers.

    Kill-only targets: processes rooted here are riding an unregistered
    directory, but the directory itself is never removed by this tool.
    """
    base = repo / ".claude" / "worktrees"
    if not base.is_dir():
        return []
    home = os.path.join(os.path.realpath(repo), ".claude", "worktrees")
    stale: list[str] = []
    for entry in sorted(base.iterdir()):
        # git never creates a worktree as a symlink; following one makes its target a kill zone.
        if entry.is_symlink() or not entry.is_dir():
            continue
        real = os.path.realpath(entry)
        # A symlinked .claude or worktrees dir can land on another repo's trees or this one's src.
        if os.path.dirname(real) != home:
            continue
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


def resolve_target(repo: Path, timeout: int = 120,
                   remaining_s: float | None = None) -> str | None:
    """First merge candidate whose commit exists, within the run deadline.

    Every candidate is re-clamped against what is left: reusing one clamped
    timeout would let a repository whose refs all hang spend the budget once
    per candidate. A budget too thin for the next probe returns None, which
    the caller already reads as "no target, skip the repository".
    """
    started = time.monotonic()
    for ref in MERGE_TARGETS:
        probe_timeout = clamp_timeout(timeout, remaining_after(remaining_s, started))
        if probe_timeout is None:
            return None
        if git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
                  timeout=probe_timeout):
            return ref
    return None


def default_branch(repo: Path, timeout: int = 120) -> str:
    """Resolve the parent repo's default branch for the dirty-diff comparison.

    Independent of MERGE_TARGETS/resolve_target: that mechanism picks whatever
    ref exists from a five-candidate list for the merge-ancestor check. This
    is specifically the remote's advertised default, with a literal fallback
    so the diff check always has something to compare against.
    """
    ref = git_ok(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", timeout=timeout)
    if ref and ref.startswith("refs/remotes/"):
        return ref.removeprefix("refs/remotes/")
    return "main"


def worktree_has_real_changes(path: Path, status: str, default_branch_ref: str,
                              ignored_dirs: set[str], ignored_prefixes: tuple[str, ...],
                              timeout: int = 120) -> bool:
    """Decide whether `git status --porcelain -z` output represents real work.

    Untracked entries matching the ignored dirs/prefixes never count. A
    tracked file that git considers modified only counts if its content
    actually differs from the default branch -- some worktrees show local M
    markers on files whose on-disk content already matches origin's, and
    treating those as dirty blocks removal of a worktree with nothing left
    to lose.
    """
    tracked: list[str] = []
    entries = iter(status.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        code, rest = entry[:2], entry[3:]
        if code == "??":
            if not is_ignored_untracked(rest, ignored_dirs, ignored_prefixes):
                return True
            continue
        tracked.append(rest)
        if "R" in code or "C" in code:
            # A rename or copy carries its source path as the next entry; a rename deletes it.
            source = next(entries, None)
            if "R" in code and source:
                tracked.append(source)

    if not tracked:
        return False

    # Literal: a path such as ":name" would otherwise parse as pathspec magic.
    diff = git_ok(path, "--literal-pathspecs", "diff", default_branch_ref, "--stat",
                  "--", *tracked, timeout=timeout)
    # Unreadable comparison must not clear a dirty flag.
    return diff is None or bool(diff)


def has_unpushed_commits(repo: Path, wt_path: Path, merged: bool,
                         timeout: int = 120) -> bool:
    """True when this worktree holds commits that exist nowhere else.

    `@{u}..` is the direct answer when an upstream is configured. Without
    one, there is nothing to compare against, so a merged branch is trusted
    (its commits are captured by the merge) and an unmerged one is not --
    that is the only case where local-only work would be lost.
    """
    upstream = git_ok(wt_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                      timeout=timeout)
    if upstream:
        ahead = git_ok(wt_path, "log", "@{u}..", "--oneline", timeout=timeout)
        return ahead is None or bool(ahead)
    return not merged


def commit_age_days(repo: Path, sha: str, timeout: int = 120) -> int | None:
    """Days since the branch's tip commit -- independent of the worktree
    directory's filesystem age, which resets if the worktree is recreated
    without the branch itself gaining new work."""
    ts = git_ok(repo, "log", "-1", "--format=%ct", sha, timeout=timeout)
    if not ts:
        return None
    try:
        return int((datetime.now(timezone.utc).timestamp() - int(ts)) / 86400)
    except ValueError:
        return None


def classify(wt: dict, repo: Path, target: str, live: list[Path], self_cwd: Path,
             default_branch_ref: str, grace_days: int, ignored_dirs: set[str],
             ignored_prefixes: tuple[str, ...], remaining_s: float | None = None,
             precious: tuple[str, ...] | list[str] = PRECIOUS_PATTERNS) -> str:
    wt.pop("precious_file", None)
    wt.pop("ignored_files", None)
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

    # Every step below shells out to git. Each call is capped at what is left
    # of the run deadline, so a repository full of slow worktrees cannot carry
    # the run hours past it.
    started = time.monotonic()

    def budget() -> int | None:
        return clamp_timeout(120, remaining_after(remaining_s, started))

    timeout = budget()
    if timeout is None:
        return DEADLINE_SKIP
    try:
        merged = git(repo, "merge-base", "--is-ancestor",
                    f"refs/heads/{wt['branch']}", target, timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:
        return "skip: unreadable"

    timeout = budget()
    if timeout is None:
        return DEADLINE_SKIP
    if has_unpushed_commits(repo, path, merged, timeout=timeout):
        return "needs-push"

    if not merged:
        timeout = budget()
        if timeout is None:
            return DEADLINE_SKIP
        age = commit_age_days(repo, wt["head"], timeout=timeout) if wt["head"] else None
        if age is None or age <= grace_days:
            return "skip: unmerged"

    timeout = budget()
    if timeout is None:
        return DEADLINE_SKIP
    # -uall so build output and other untracked leftovers count as work in
    # progress rather than being silently discarded. -z leaves paths unquoted,
    # so a name with a space or non-ASCII character still works as a pathspec.
    status = git_lines(path, "status", "--porcelain", "-z", "-uall", timeout=timeout)
    if status is None:
        return "skip: unreadable"
    if status:
        timeout = budget()
        if timeout is None:
            return DEADLINE_SKIP
        if worktree_has_real_changes(path, status, default_branch_ref,
                                     ignored_dirs, ignored_prefixes, timeout=timeout):
            return "skip: dirty"

    timeout = budget()
    if timeout is None:
        return DEADLINE_SKIP
    # No --directory: a key inside a wholly ignored directory must be listed too.
    ignored = git_lines(path, "ls-files", "-z", "-oi", "--exclude-standard", timeout=timeout)
    if ignored is None:
        return "skip: unreadable"
    files = [f for f in ignored.split("\0") if f]
    hit = precious_path(files, ignored_dirs, ignored_prefixes, precious)
    if hit:
        wt["precious_file"] = hit
        log(f"SKIP-PRECIOUS tree={wt_path_str} file={hit} key=reaper.precious")
        return "skip: precious"

    wt["ignored_files"] = top_level_entries(files)
    return REAP


IGNORED_LIST_CAP = 10


def top_level_entries(paths: list[str]) -> list[str]:
    """Ignored paths collapsed to their first segment; a directory keeps a trailing slash."""
    return sorted({p.split("/", 1)[0] + "/" if "/" in p.rstrip("/") else p for p in paths})


def scan_repo(repo: Path, live: list[Path], self_cwd: Path, fetch: bool, grace_days: int,
              ignored_dirs: set[str], ignored_prefixes: tuple[str, ...],
              remaining_s: float | None = None,
              precious: tuple[str, ...] | list[str] = PRECIOUS_PATTERNS) -> dict:
    scan_start = time.monotonic()
    result = {"repo": str(repo), "target": None, "default_branch": None, "worktrees": [],
              "note": None, "timed_out": False, "listing_ok": True, "listing_skipped": False}

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

    # Every git call from here on is capped at what is left of the run
    # deadline, so one slow repository cannot carry the run past it.
    def budget() -> int | None:
        return clamp_timeout(120, remaining_after(remaining_s, scan_start))

    def out_of_time(note: str, unjudged: list[dict]) -> dict:
        """Close the scan on the deadline: the worktrees left over stay unjudged."""
        result["timed_out"] = True
        result["note"] = note
        for wt in unjudged:
            wt["verdict"] = DEADLINE_SKIP
            result["worktrees"].append(wt)
        return result

    list_timeout = budget()
    if list_timeout is None:
        # A note may already be set by the fetch-skip branch above; don't
        # clobber it, but the listing must still be skipped and the caller
        # must not be handed a stale worktree registry.
        out_of_time(result["note"] or "deadline: worktree listing skipped", [])
        result["listing_ok"] = False
        # No worktree was ever enumerated, so there is nothing for a
        # skip: deadline verdict to attach to and deadline_skipped stays 0.
        # This flag is the only record that the repository went unscanned.
        result["listing_skipped"] = True
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

    target_timeout = budget()
    if target_timeout is None:
        return out_of_time("deadline: target unresolved", entries[1:])

    target = resolve_target(
        repo, timeout=target_timeout,
        remaining_s=remaining_after(remaining_s, scan_start),
    )
    result["target"] = target
    if target is None:
        result["note"] = "no merge target resolved; repository skipped"
        for wt in entries[1:]:
            wt["verdict"] = PRUNE if wt["prunable"] else "skip: no target"
            result["worktrees"].append(wt)
        return result

    branch_timeout = budget()
    if branch_timeout is None:
        return out_of_time("deadline: default branch unresolved", entries[1:])

    default_branch_ref = default_branch(repo, timeout=branch_timeout)
    result["default_branch"] = default_branch_ref

    # entries[0] is the primary checkout and is never a candidate.
    for wt in entries[1:]:
        wt["verdict"] = classify(
            wt, repo, target, live, self_cwd, default_branch_ref, grace_days,
            ignored_dirs, ignored_prefixes,
            remaining_s=remaining_after(remaining_s, scan_start), precious=precious,
        )
        if wt["verdict"] == DEADLINE_SKIP:
            result["timed_out"] = True
        wt["size"] = 0
        if wt["verdict"] == REAP:
            size_timeout = budget()
            if size_timeout is None:
                result["timed_out"] = True
            else:
                wt["size"] = dir_size(Path(wt["path"]), timeout=size_timeout)
        wt["age_days"] = age_days(Path(wt["path"]))
        result["worktrees"].append(wt)
    return result


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


def worktrees_left(res: dict, preview: bool) -> int:
    """Linked worktrees left once this run's removals and prunes, made or previewed, land."""
    gone = (PRUNE, REAP) if preview else (PRUNE,)
    return sum(1 for w in res["worktrees"]
               if w["verdict"] not in gone and not (w.get("applied") or {}).get("removed"))


def capped(entries: list[str], limit: int = IGNORED_LIST_CAP) -> str:
    shown = ", ".join(entries[:limit])
    return shown if len(entries) <= limit else f"{shown} and {len(entries) - limit} more"


def render(results: list[dict], apply_mode: bool, cap: int = 0) -> str:
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
        if res.get("over_cap"):
            lines.append(f"  ! {res['worktrees_left']} linked worktrees, over the cap of {cap}")
        for wt in interesting:
            counts[wt["verdict"]] = counts.get(wt["verdict"], 0) + 1
            if wt["verdict"] == REAP:
                reclaim += wt.get("size", 0)
            name = Path(wt["path"]).name
            branch = wt["branch"] or "(detached)"
            age = f"{wt['age_days']}d" if wt.get("age_days") is not None else "-"
            size = f"  {human(wt['size'])}" if wt.get("size") else ""
            lines.append(f"  {wt['verdict']:<18} {age:>5}  {name:<34} {branch}{size}")
            if wt.get("precious_file"):
                lines.append(f"      kept because of {wt['precious_file']}; "
                             "change the reaper.precious config key to release it")
            elif wt["verdict"] == REAP and wt.get("ignored_files"):
                lines.append(f"      {'removed' if apply_mode else 'removes'} ignored: "
                             f"{capped(wt['ignored_files'])}")

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


def refresh_verdict(wt: dict, res: dict, opts: Any, self_cwd: Path,
                    remaining_s: float | None) -> str:
    """Re-judge one scanned tree from its current registry record and sessions,
    within the run deadline. Updates wt to that record."""
    started = time.monotonic()
    timeout = clamp_timeout(120, remaining_s)
    if timeout is None:
        return DEADLINE_SKIP
    repo = Path(res["repo"])
    porcelain = git_ok(repo, "worktree", "list", "--porcelain", timeout=timeout)
    if porcelain is None:
        return "skip: unreadable"
    record = next((e for e in parse_worktrees(porcelain) if e["path"] == wt["path"]), None)
    if record is None:
        return "skip: unregistered"
    wt.update(record)
    return classify(
        wt, repo, res["target"], live_worktree_cwds(opts.sessions_dir), self_cwd,
        res["default_branch"], opts.grace_days,
        opts.ignored_untracked_dirs, opts.ignored_untracked_prefixes,
        remaining_s=remaining_after(remaining_s, started),
        precious=getattr(opts, "precious", PRECIOUS_PATTERNS),
    )


def run(opts: Any) -> int:
    """Body of a reap invocation: discover repos, scan, kill, remove, report."""
    if opts.dry_run and not opts.apply:
        print("--dry-run requires --apply", file=sys.stderr)
        return 2
    if shutil.which("git") is None:
        print("git not found on PATH", file=sys.stderr)
        return 2

    if opts.repo:
        repos = []
        seen = set()
        for selected in opts.repo:
            resolved = selected.resolve()
            if resolved not in seen:
                seen.add(resolved)
                repos.append(resolved)
    else:
        repos = []
        for root in opts.roots:
            if not root.is_dir():
                print(f"{root} is not a directory", file=sys.stderr)
                return 2
            repos.extend(discover_repos(root))

    live = live_worktree_cwds(opts.sessions_dir, strict=opts.apply and not opts.dry_run)
    self_cwd = Path.cwd().resolve()
    run_start = time.monotonic()
    results = []
    deadline_hit = False
    for r in repos:
        if time.monotonic() - run_start > opts.deadline_s:
            deadline_hit = True
            skipped = len(repos) - len(results)
            log(f"ABORT deadline elapsed={int(time.monotonic() - run_start)}s "
                f"scanned={len(results)} skipped={skipped}")
            break
        results.append(scan_repo(
            r, live, self_cwd, fetch=not opts.no_fetch, grace_days=opts.grace_days,
            ignored_dirs=opts.ignored_untracked_dirs,
            ignored_prefixes=opts.ignored_untracked_prefixes,
            remaining_s=remaining_after(opts.deadline_s, run_start),
            precious=getattr(opts, "precious", PRECIOUS_PATTERNS),
        ))

    snap = ps_snapshot()
    cwds = cwd_map()
    # An unread process table looks empty; acting on it would kill and remove blind.
    observed = snap is not None and cwds is not None
    if not observed:
        failed = ",".join(n for n, v in (("ps", snap), ("lsof", cwds)) if v is None)
        log(f"SKIP-ALL process observation failed: {failed}")
        print(f"reap: process observation failed ({failed}); nothing signalled or removed",
              file=sys.stderr)
        snap = {} if snap is None else snap
        cwds = {} if cwds is None else cwds
    real_apply = opts.apply and not opts.dry_run and observed
    killed_total = 0
    kill_failed_total = 0

    def emit_preview(kill_result: dict) -> None:
        if real_apply or opts.json:
            return
        for w in kill_result["would"]:
            print(f"WOULD-KILL pid={w['pid']} comm={w['comm']} match={w['match']} tree={w['tree']}")

    def verdict_changed(wt: dict, res: dict, tree: str) -> bool:
        fresh = refresh_verdict(wt, res, opts, self_cwd,
                                remaining_after(opts.deadline_s, run_start))
        if fresh == REAP:
            return False
        wt["verdict"] = fresh
        wt["applied"] = {"removed": False, "detail": f"verdict changed: {fresh}"}
        if not opts.json:
            why = (f" ({wt['precious_file']} matches reaper.precious)"
                   if wt.get("precious_file") else "")
            print(f"skip: {Path(wt['path']).name} -- verdict changed: {fresh}{why}")
        log(f"SKIP-CHANGED tree={tree} verdict={fresh}")
        return True

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
            if opts.apply and not observed:
                wt["applied"] = {"removed": False, "detail": "skip: process observation failed"}
                continue
            # The scan verdict can be an hour old; edits, a checkout or a session
            # since then must win before anything in the tree is signalled.
            if opts.apply and verdict_changed(wt, res, tree):
                continue
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
                if not opts.dry_run and verdict_changed(wt, res, tree):
                    continue  # judged again: the kill wait can leave new work behind
                if not opts.dry_run and wt.get("ignored_files"):
                    log(f"REAP-IGNORED tree={tree} files={','.join(wt['ignored_files'])}")
                ok, detail = reap(repo, wt, opts.dry_run)
                wt["applied"] = {"removed": ok and not opts.dry_run, "detail": detail}
                prefix = "would run" if opts.dry_run else ("ok" if ok else "FAILED")
                if not opts.json:
                    print(f"{prefix}: {Path(wt['path']).name} -- {detail}")
                if ok and not opts.dry_run:
                    removed += 1

        if not res.get("listing_ok", True):
            continue
        # The scan's registry and sessions can be an hour old: a tree added since is not stale.
        t = clamp_timeout(120, remaining_after(opts.deadline_s, run_start))
        porcelain = git_ok(repo, "worktree", "list", "--porcelain", timeout=t) if t else None
        registered = {os.path.realpath(res["repo"])} | {
            os.path.realpath(w["path"]) for w in parse_worktrees(porcelain or "")}
        stale_dirs = [] if porcelain is None else stale_worktree_dirs(
            repo, registered, live_worktree_cwds(opts.sessions_dir))
        for stale_path in stale_dirs:
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

    orphans = _filter_killable(
        orphan_sweep(getattr(opts, "orphan_sweep", False), opts.roots, snap, cwds,
                     opts.orphan_min_age_s), snap)
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

    cap = getattr(opts, "max_worktrees", 0)
    for res in results:
        res["worktrees_left"] = worktrees_left(res, not opts.apply or opts.dry_run)
        res["over_cap"] = cap > 0 and res["worktrees_left"] > cap
    all_worktrees = [w for res in results for w in res["worktrees"]]
    eligible = sum(1 for w in all_worktrees if w["verdict"] == REAP)
    kept_dirty = sum(1 for w in all_worktrees if w["verdict"] == "skip: dirty")
    kept_unpushed = sum(1 for w in all_worktrees if w["verdict"] == "needs-push")
    kept_locked = sum(1 for w in all_worktrees if w["verdict"] == "skip: locked")
    timeouts = sum(1 for res in results if res.get("timed_out"))
    deadline_skipped = sum(1 for w in all_worktrees if w["verdict"] == DEADLINE_SKIP)
    listing_skipped = sum(1 for res in results if res.get("listing_skipped"))
    over_cap = sum(1 for res in results if res["over_cap"])
    log(f"reaper: eligible={eligible} removed={removed} kept_dirty={kept_dirty} "
        f"kept_unpushed={kept_unpushed} kept_locked={kept_locked} timeouts={timeouts} "
        f"deadline_skipped={deadline_skipped} listing_skipped={listing_skipped} "
        f"over_cap={over_cap} "
        f"killed={killed_total} kill_failed={kill_failed_total} "
        f"elapsed={int(time.monotonic() - run_start)}s deadline_hit={int(deadline_hit)}")

    if opts.json:
        print(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "version": 1, "repos": results, "orphan_daemons": orphan_json,
                          "process_observation_ok": observed}, indent=2))
    else:
        print(render(results, apply_mode=opts.apply and not opts.dry_run, cap=cap))

    return 0
