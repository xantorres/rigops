"""Dev-server sweep: long-running watch servers left behind under reaper.roots."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from rigops.errors import RigopsError
from rigops.reap import clamp_timeout, remaining_after
from rigops.reap_guards import orphan_sweep
from rigops.reap_procs import (
    _descendants,
    _filter_killable,
    cwd_map,
    has_terminal,
    kill_pids,
    log,
    path_claims,
    ps_snapshot,
)

_RUN = (r"^(?:\S*/)?(?:(?:ba|z)?sh\s+-c\s+(?:cd\s+\S+\s+&&\s+)?|npm\s+exec\s+)?"
        r"(?:node\s+(?:-\S+\s+)*)?(?:\S*/)?")
_TOOLS = r"(?:rsbuild|rslib|rspack|vite|next|nuxt|astro|remix|storybook|wrangler|webpack)"

DEFAULT_PATTERNS = [
    _RUN + _TOOLS + r"(?:\.[cm]?js)?\s+(?:dev|mf-dev|serve)(?=\s|$)",
    _RUN + r"vite(?:\.[cm]?js)?(?=\s+--|\s*$)(?!.*\s(?:build|preview|optimize)(?:\s|$))",
    _RUN + r"turbo(?:\.js)?\s+(?:run\s+)?dev(?=\s|$)",
    _RUN + r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev(?::[\w-]+)?|storybook)(?=\s|$)",
    _RUN + r"webpack-dev-server\b",
    _RUN + r"nodemon(?:\.js)?(?=\s|$)",
    _RUN + r"tsx\s+watch(?=\s|$)",
    r"^rsbuild-node\b",
]


class Sweep(NamedTuple):
    stale: dict[int, set[int]]
    kept: dict[int, tuple[str, int]]


def compile_patterns(patterns: list[str]) -> list[re.Pattern] | str:
    """Compiled patterns, or the error text naming the first one that does not compile."""
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            return f"reaper.dev_server_patterns: {pattern!r} is not a valid regex: {exc}"
    return compiled


def _rooted(pid: int, info: dict, cwds: dict[int, str], roots: list[str]) -> bool:
    tokens = info["command"].split()
    return any(path_claims(cwds.get(pid), root) or any(path_claims(t, root) for t in tokens)
               for root in roots)


def _idle_seconds(entry: Path, data: Any) -> int | None:
    if not isinstance(data, dict) or data.get("status") != "idle":
        return None
    try:
        return max(0, int(time.time() - entry.stat().st_mtime))
    except OSError:
        return None


def live_sessions(sessions_dir: Path, strict: bool = False) -> dict[int, int | None]:
    """Live agent session pid -> idle seconds, None when it cannot be shown idle."""
    live: dict[int, int | None] = {}
    for entry in sessions_dir.glob("*.json"):
        try:
            data = json.loads(entry.read_text())
            pid = int(data["pid"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if strict:
                raise RigopsError(f"unreadable session file {entry}") from exc
            continue
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OverflowError):
            continue
        live[pid] = _idle_seconds(entry, data)
    return live


def _ancestors(pid: int, snap: dict[int, dict]) -> list[int]:
    """Parents of pid up to init, nearest first; cycle-safe."""
    chain: list[int] = []
    seen = {pid}
    parent = snap[pid]["ppid"]
    while parent in snap and parent not in seen:
        chain.append(parent)
        seen.add(parent)
        parent = snap[parent]["ppid"]
    return chain


def _terminal_holder(lineage: list[int], tree: set[int], snap: dict[int, dict]) -> int | None:
    for pid in [*lineage, *sorted(tree)]:
        if pid in snap and has_terminal(snap[pid]):
            return pid
    return None


def _session_holder(lineage: list[int], sessions: dict[int, int | None],
                    min_age_s: int) -> int | None:
    for pid in lineage:
        if pid in sessions and (sessions[pid] is None or sessions[pid] <= min_age_s):
            return pid
    return None


def _session_lineage(sessions: dict[int, int | None], snap: dict[int, dict]) -> set[int]:
    """Live session pids present in snap plus all their ancestors."""
    lineage = {s for s in sessions if s in snap}
    for sid in list(lineage):
        lineage.update(_ancestors(sid, snap))
    return lineage


def stale_dev_servers(snap: dict[int, dict], cwds: dict[int, str], roots: list[str],
                      min_age_s: int, patterns: list[re.Pattern],
                      sessions: dict[int, int | None] | None = None) -> Sweep:
    """Topmost matching dev server under roots, split into stale and kept.

    Stale means it ran longer than min_age_s and no terminal (on it, above it or
    below it) and no agent session above it was active for that long.
    """
    sessions = sessions or {}
    barred = _session_lineage(sessions, snap)
    candidates = _filter_killable({
        pid for pid, info in snap.items()
        if pid not in barred
        and info["etimes"] > min_age_s
        and any(p.search(info["command"]) for p in patterns)
        and _rooted(pid, info, cwds, roots)
    }, snap)
    tops = sorted(p for p in candidates if not candidates.intersection(_ancestors(p, snap)))
    stale: dict[int, set[int]] = {}
    kept: dict[int, tuple[str, int]] = {}
    for top in tops:
        lineage = _ancestors(top, snap)
        tree = _descendants({top}, snap)
        terminal = _terminal_holder(lineage, tree, snap)
        session = None if terminal is not None else _session_holder(lineage, sessions, min_age_s)
        if terminal is not None:
            kept[top] = ("terminal", terminal)
        elif session is not None:
            kept[top] = ("session", session)
        else:
            stale[top] = _filter_killable(tree, snap)
    return Sweep(stale, kept)


def _comm(snap: dict[int, dict], pid: int) -> str:
    tokens = snap[pid]["command"].split() if pid in snap else []
    return Path(tokens[0]).name if tokens else "?"


def _kept_entries(kept: dict[int, tuple[str, int]], snap: dict[int, dict],
                  cwds: dict[int, str]) -> list[dict]:
    return [{"pid": top, "comm": _comm(snap, top), "reason": reason, "parent": holder,
             "tree": f"dev-server={cwds.get(top, top)}"}
            for top, (reason, holder) in sorted(kept.items())]


def _kill_groups(opts: Any, groups: list[tuple[str, dict[int, str]]], snap: dict[int, dict],
                 real_apply: bool, started: float) -> tuple[list[dict], bool]:
    results: list[dict] = []
    for index, (label, pids) in enumerate(groups):
        grace = clamp_timeout(opts.kill_grace_s, remaining_after(opts.deadline_s, started))
        if grace is None:
            log(f"SKIP-DEADLINE elapsed={int(time.monotonic() - started)}s "
                f"groups_skipped={len(groups) - index}")
            return results, True
        results.append(kill_pids(label, pids, snap, real_apply, opts.max_kills_per_tree, grace))
    return results, False


def _report(snap: dict[int, dict], cwds: dict[int, str], observed: bool, sweep: Sweep,
            orphans: set[int], results: list[dict], deadline_hit: bool) -> dict:
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": 1, "repos": [],
        "orphan_daemons": [{"pid": pid, "comm": _comm(snap, pid), "match": "daemon",
                            "etimes": snap[pid]["etimes"]}
                           for pid in sorted(orphans) if pid in snap],
        "process_observation_ok": observed,
        "dev_servers": [{"pid": top, "comm": _comm(snap, top), "match": "dev-server",
                         "etimes": snap[top]["etimes"], "cwd": cwds.get(top),
                         "pids": sorted(tree)} for top, tree in sorted(sweep.stale.items())],
        "skipped_owned": _kept_entries(sweep.kept, snap, cwds),
        "kills": results, "deadline_hit": deadline_hit,
    }


def empty_report(observed: bool = True) -> dict:
    return _report({}, {}, observed, Sweep({}, {}), set(), [], False)


def _print_text(real_apply: bool, report: dict, summary: str) -> None:
    for o in report["skipped_owned"]:
        print(f"SKIP-OWNED pid={o['pid']} comm={o['comm']} parent={o['parent']} "
              f"tree={o['tree']}")
    if report["deadline_hit"]:
        print("SKIP-DEADLINE run deadline reached; remaining groups wait for the next run")
    if real_apply:
        print(summary)
        return
    for r in report["kills"]:
        for w in r["would"]:
            print(f"WOULD-KILL pid={w['pid']} comm={w['comm']} match={w['match']} "
                  f"tree={w['tree']}")


def _observation_failed(opts: Any, snap: Any, cwds: Any) -> int:
    failed = ",".join(n for n, v in (("ps", snap), ("lsof", cwds)) if v is None)
    log(f"SKIP-ALL process observation failed: {failed}")
    print(f"reap: process observation failed ({failed}); nothing signalled", file=sys.stderr)
    if opts.json:
        print(json.dumps(empty_report(False), indent=2))
    return 0


def _select(opts: Any, snap: dict[int, dict], cwds: dict[int, str], roots: list[str],
            real_apply: bool) -> tuple[Sweep, set[int]]:
    sweep = Sweep({}, {})
    sessions: dict[int, int | None] = {}
    if opts.dev_server_sweep or opts.orphan_sweep:
        sessions = live_sessions(opts.sessions_dir, strict=real_apply)
    if opts.dev_server_sweep:
        sweep = stale_dev_servers(snap, cwds, roots, opts.dev_server_min_age_s,
                                  opts.dev_server_patterns, sessions)
    orphans = _filter_killable(
        orphan_sweep(opts.orphan_sweep, [Path(r) for r in roots], snap, cwds,
                     opts.orphan_min_age_s), snap)
    orphans -= _session_lineage(sessions, snap)
    for top in sweep.kept:
        orphans -= _descendants({top}, snap)
    return sweep, orphans


def run_procs(opts: Any) -> int:
    """Process-only reap: dev servers and orphan daemons, no git work, cheap enough for minutes."""
    started = time.monotonic()
    snap = ps_snapshot()
    cwds = cwd_map()
    if snap is None or cwds is None:
        return _observation_failed(opts, snap, cwds)
    real_apply = opts.apply and not opts.dry_run
    roots = [str(Path(r).expanduser().resolve()) for r in opts.roots]
    sweep, orphans = _select(opts, snap, cwds, roots, real_apply)
    groups = [(f"dev-server={cwds.get(top, top)}", {pid: "dev-server" for pid in tree})
              for top, tree in sweep.stale.items()]
    groups.append(("orphan-daemon", {pid: "daemon" for pid in orphans}))
    results, deadline_hit = _kill_groups(opts, groups, snap, real_apply, started)

    summary = (f"procs: dev_servers={len(sweep.stale)} orphans={len(orphans)} "
               f"killed={sum(r['killed'] for r in results)} "
               f"kill_failed={sum(r['kill_failed'] for r in results)} "
               f"overflow={sum(1 for r in results if r['overflow'])}")
    log(summary)
    for top, (reason, holder) in sorted(sweep.kept.items()):
        log(f"SKIP-OWNED dev-server pid={top} reason={reason} parent={holder}")
    report = _report(snap, cwds, True, sweep, orphans, results, deadline_hit)
    if opts.json:
        print(json.dumps(report, indent=2))
    else:
        _print_text(real_apply, report, summary)
    return 0
