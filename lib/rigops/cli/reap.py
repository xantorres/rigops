from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from rigops import cli
from rigops import config as rigops_config
from rigops import reap
from rigops import reap_devservers
from rigops import reap_guards
from rigops import reap_selftest
from rigops import state


def _empty_report() -> dict:
    return {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repos": [], "orphan_daemons": [],
            "process_observation_ok": True, "version": 1}


def _relative_config_root(args: argparse.Namespace) -> str | None:
    if args.root:
        return None
    return next((str(r) for r in args.roots if not os.path.isabs(str(r))), None)


def _procs_only(args: argparse.Namespace, cfg: dict) -> int:
    if args.dry_run and not args.apply:
        print("--dry-run requires --apply", file=sys.stderr)
        return 2
    args.dev_server_sweep = rigops_config.get(cfg, "reaper.dev_server_sweep", False)
    problem = reap_guards.bool_setting_error("reaper.dev_server_sweep", args.dev_server_sweep)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    patterns = rigops_config.get(cfg, "reaper.dev_server_patterns",
                                 reap_devservers.DEFAULT_PATTERNS)
    problem = reap_guards.list_setting_error("reaper.dev_server_patterns", patterns)
    compiled = patterns if problem else reap_devservers.compile_patterns(patterns)
    if problem or isinstance(compiled, str):
        print(f"error: {problem or compiled}", file=sys.stderr)
        return 2
    args.dev_server_patterns = compiled
    min_age = args.min_age_s
    if min_age is None:
        min_age = rigops_config.get(cfg, "reaper.dev_server_min_age_s", 10800)
    if type(min_age) is not int or min_age < 0:
        print("error: reaper.dev_server_min_age_s must be a non-negative integer",
              file=sys.stderr)
        return 2
    args.dev_server_min_age_s = min_age
    relative = _relative_config_root(args)
    if relative is not None:
        print(f"error: reaper.roots: '{relative}' must be an absolute path", file=sys.stderr)
        return 2
    if not args.roots:
        print(f"rigops reap: no roots configured; set reaper.roots in "
              f"{rigops_config.config_path()} or pass --root <dir>", file=sys.stderr)
        if args.json:
            print(json.dumps(reap_devservers.empty_report(), indent=2))
        return 0
    if args.apply:
        rigops_config.require_private(rigops_config.config_path())
        with state.locked(state.state_dir() / "reap"):
            return reap_devservers.run_procs(args)
    return reap_devservers.run_procs(args)


def main(argv: list[str] | None = None) -> int:
    # Per-fetch timeouts alone let a run crawl: three slow remotes stretched one
    # run to 2h41m. Scanning stops at the deadline and the run reports what it
    # got; whatever is left is reaped next run.
    try:
        deadline_s = int(os.environ.get("RIGOPS_RUN_DEADLINE_S", "3600"))
    except ValueError:
        deadline_s = 3600

    parser = argparse.ArgumentParser(
        prog="rigops reap",
        description=cli.COMMANDS["reap"],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--selftest", action="store_true",
                        help="exercise the kill module against disposable processes and exit")
    parser.add_argument("--apply", action="store_true",
                        help="remove; without this the run is read-only")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply, print the commands without running them")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--repo", type=Path, action="append",
                        help="scan this repository instead of the whole tree; "
                             "repeatable, absolute or relative path to one repository")
    parser.add_argument("--root", type=Path,
                        help="scan this tree only, overriding the configured reaper.roots list")
    parser.add_argument("--no-fetch", action="store_true",
                        help="skip git fetch; merged-check uses local refs only")
    parser.add_argument("--grace-days", type=int, default=None,
                        help="an unmerged branch becomes eligible once its last commit is older "
                             "than this and it has no unpushed commits "
                             "(default: config reaper.grace_days, else 14)")
    parser.add_argument("--procs-only", action="store_true",
                        help="only sweep stale dev servers and orphan daemons; no git work")
    parser.add_argument("--min-age-s", type=int, default=None,
                        help="with --procs-only, dev-server runtime before it is stale "
                             "(default: config reaper.dev_server_min_age_s, else 10800)")
    args = parser.parse_args(argv)

    cfg = rigops_config.load()
    if args.grace_days is None:
        args.grace_days = rigops_config.get(cfg, "reaper.grace_days", 14)

    if args.selftest:
        return reap_selftest.run_selftest()

    for key, default in (("roots", []), ("ignored_untracked_dirs", []),
                         ("ignored_untracked_prefixes", []),
                         ("precious", [])):
        problem = reap_guards.list_setting_error(
            f"reaper.{key}", rigops_config.get(cfg, f"reaper.{key}", default))
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return 2

    if args.root:
        roots = [args.root]
    else:
        roots = [rigops_config.expand(r) for r in rigops_config.get(cfg, "reaper.roots", [])]

    args.roots = roots
    args.sessions_dir = rigops_config.expand(rigops_config.get(cfg, "reaper.sessions_dir"))
    args.deadline_s = deadline_s
    args.max_kills_per_tree = rigops_config.get(cfg, "reaper.max_kills_per_tree", 20)
    args.orphan_min_age_s = rigops_config.get(cfg, "reaper.orphan_min_age_s", 172800)
    args.kill_grace_s = rigops_config.get(cfg, "reaper.kill_grace_s", 5)
    args.max_worktrees = rigops_config.get(cfg, "reaper.max_worktrees", 5)
    if type(args.max_worktrees) is not int or args.max_worktrees < 0:
        print("error: reaper.max_worktrees in config must be a non-negative integer",
              file=sys.stderr)
        return 2
    args.orphan_sweep = rigops_config.get(cfg, "reaper.orphan_sweep", False)
    problem = reap_guards.bool_setting_error("reaper.orphan_sweep", args.orphan_sweep)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    if args.procs_only:
        return _procs_only(args, cfg)
    if not roots and not args.repo and (args.apply or not args.dry_run):
        print(f"rigops reap: no roots configured; set reaper.roots in "
              f"{rigops_config.config_path()} or pass --root <dir>", file=sys.stderr)
        if args.json:
            print(json.dumps(_empty_report(), indent=2))
        return 0

    args.ignored_untracked_dirs = set(rigops_config.get(cfg, "reaper.ignored_untracked_dirs", []))
    args.ignored_untracked_prefixes = tuple(
        rigops_config.get(cfg, "reaper.ignored_untracked_prefixes", [])
    )

    args.precious = tuple(rigops_config.get(cfg, "reaper.precious", []))

    if args.apply:
        rigops_config.require_private(rigops_config.config_path())
        with state.locked(state.state_dir() / "reap"):
            return reap.run(args)
    return reap.run(args)

