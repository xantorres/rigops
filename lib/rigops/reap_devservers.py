"""Dev-server sweep: long-running watch servers left behind under reaper.roots."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rigops.reap_guards import orphan_sweep
from rigops.reap_procs import (
    _descendants,
    _filter_killable,
    cwd_map,
    kill_pids,
    log,
    path_claims,
    ps_snapshot,
)

DEFAULT_PATTERNS = [
    r"\b(rsbuild|rslib|rspack|vite|next|nuxt|astro|remix|storybook|wrangler|webpack)"
    r"(\.[cm]?js)?\s+(dev|mf-dev|serve|start|preview)\b",
    r"(^|/)vite(\.[cm]?js)?(\s+--|\s*$)",
    r"\bturbo(\.js)?\s+(run\s+)?dev\b",
    r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(dev|start|serve|storybook)\b",
    r"\bwebpack-dev-server\b",
    r"\bnodemon\b",
    r"\btsx\s+watch\b",
    r"^rsbuild-node\b",
]


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


def stale_dev_servers(snap: dict[int, dict], cwds: dict[int, str], roots: list[str],
                      min_age_s: int, patterns: list[re.Pattern]) -> dict[int, set[int]]:
    """Topmost matching dev server -> its killable tree, whoever the parent is.

    The parent is ignored on purpose: a server an agent started stays parented to
    a live app for days, so ppid 1 alone misses most leaks. The no-tty rule in
    _filter_killable keeps a server the user runs in a terminal.
    """
    candidates = {
        pid for pid, info in snap.items()
        if info["etimes"] > min_age_s
        and any(p.search(info["command"]) for p in patterns)
        and _rooted(pid, info, cwds, roots)
    }
    candidates = _filter_killable(candidates, snap)

    def has_candidate_ancestor(pid: int) -> bool:
        seen = {pid}
        parent = snap[pid]["ppid"]
        while parent in snap and parent not in seen:
            if parent in candidates:
                return True
            seen.add(parent)
            parent = snap[parent]["ppid"]
        return False

    tops = sorted(p for p in candidates if not has_candidate_ancestor(p))
    return {top: _filter_killable(_descendants({top}, snap), snap) for top in tops}


def run_procs(opts: Any) -> int:
    """Process-only reap: dev servers and orphan daemons, no git work, cheap enough for minutes."""
    snap = ps_snapshot()
    cwds = cwd_map()
    if snap is None or cwds is None:
        failed = ",".join(n for n, v in (("ps", snap), ("lsof", cwds)) if v is None)
        log(f"SKIP-ALL process observation failed: {failed}")
        print(f"reap: process observation failed ({failed}); nothing signalled", file=sys.stderr)
        return 0
    real_apply = opts.apply and not opts.dry_run
    roots = [str(Path(r).expanduser().resolve()) for r in opts.roots]

    groups = stale_dev_servers(snap, cwds, roots, opts.dev_server_min_age_s,
                               opts.dev_server_patterns) if opts.dev_server_sweep else {}
    results = []
    for top, tree in groups.items():
        label = f"dev-server={cwds.get(top, top)}"
        results.append(kill_pids(label, {pid: "dev-server" for pid in tree}, snap, real_apply,
                                 opts.max_kills_per_tree, opts.kill_grace_s))
    orphans = _filter_killable(
        orphan_sweep(opts.orphan_sweep, [Path(r) for r in roots], snap, cwds,
                     opts.orphan_min_age_s), snap)
    results.append(kill_pids("orphan-daemon", {pid: "daemon" for pid in orphans}, snap,
                             real_apply, opts.max_kills_per_tree, opts.kill_grace_s))

    killed = sum(r["killed"] for r in results)
    kill_failed = sum(r["kill_failed"] for r in results)
    overflow = sum(1 for r in results if r["overflow"])
    log(f"procs: dev_servers={len(groups)} orphans={len(orphans)} killed={killed} "
        f"kill_failed={kill_failed} overflow={overflow}")

    if opts.json:
        print(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "version": 1, "process_observation_ok": True,
                          "kills": results}, indent=2))
    elif not real_apply:
        for r in results:
            for w in r["would"]:
                print(f"WOULD-KILL pid={w['pid']} comm={w['comm']} match={w['match']} "
                      f"tree={w['tree']}")
    else:
        print(f"procs: dev_servers={len(groups)} orphans={len(orphans)} killed={killed} "
              f"kill_failed={kill_failed} overflow={overflow}")
    return 0
