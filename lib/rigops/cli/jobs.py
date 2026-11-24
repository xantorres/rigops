from __future__ import annotations

import argparse

from rigops import cli, jobs, launchd
from rigops.errors import RigopsError

CRON_HEADER = "add these lines with `crontab -e` (create {logs} first):"
HEAL_NOTE = "note: --heal applies to launchd jobs only; the cron lines never heal"


def _action(name: str, rendered: bytes) -> str:
    path = jobs.plist_path(name)
    if not path.exists():
        return "installed"
    if path.read_bytes() != rendered:
        return "updated"
    return "unchanged" if launchd.is_loaded(jobs.label(name)) else "loaded"


def _install_cron(names: list[str], heal: bool) -> int:
    lines = jobs.cron_lines(jobs.resolve_executable(), names)
    print(CRON_HEADER.format(logs=jobs.cron_log_dir()))
    for line in lines:
        print(line)
    if heal:
        print(HEAL_NOTE)
    return 0


def _install(args: argparse.Namespace) -> int:
    names = jobs.select_jobs(args.jobs)
    if not jobs.is_macos():
        return _install_cron(names, args.heal)
    rigops_path = jobs.resolve_executable()
    plans = []
    for name in names:
        rendered = jobs.render_plist(name, rigops_path, args.heal)
        plans.append((name, rendered, _action(name, rendered)))
    if not args.apply:
        for name, _, action in plans:
            print(f"plan: {action} {jobs.plist_path(name)} ({jobs.label(name)})")
        print("re-run with --apply to make these changes")
        return 0
    jobs.launch_agents_dir().mkdir(parents=True, exist_ok=True)
    jobs.log_dir().mkdir(parents=True, exist_ok=True)
    for name, rendered, action in plans:
        path, text = jobs.plist_path(name), f"{jobs.plist_path(name)} ({jobs.label(name)})"
        if action in ("installed", "updated"):
            jobs.write_atomic(path, rendered)
        if action == "unchanged":
            print(f"unchanged {text}")
            continue
        print(f"{action} {text} {launchd.bootstrap(jobs.label(name), path)}")
    return 0


def _uninstall(args: argparse.Namespace) -> int:
    if not jobs.is_macos():
        print("remove the rigops lines from your crontab with `crontab -e`")
        return 0
    present = [
        name for name in jobs.JOBS
        if jobs.plist_path(name).exists() or launchd.is_loaded(jobs.label(name))
    ]
    if not present:
        print("nothing installed")
        return 0
    for name in present:
        path, text = jobs.plist_path(name), f"{jobs.plist_path(name)} ({jobs.label(name)})"
        if not args.apply:
            print(f"plan: remove {text}")
            continue
        launchd.bootout(jobs.label(name))
        path.unlink(missing_ok=True)
        print(f"removed {text}")
    if not args.apply:
        print("re-run with --apply to make these changes")
    return 0


def _status(args: argparse.Namespace) -> int:
    if not jobs.is_macos():
        print("cron entries are user-managed and not inspected")
        return 2
    failing = False
    for name in jobs.JOBS:
        path, job_label = jobs.plist_path(name), jobs.label(name)
        installed, loaded = path.exists(), launchd.is_loaded(job_label)
        row = f"{job_label}  installed={'yes' if installed else 'no'}  loaded={'yes' if loaded else 'no'}"
        if installed:
            problem = jobs.program_problem(path)
            row += f"  program={problem or 'ok'}"
            failing = failing or problem is not None or not loaded
        print(row)
    return 1 if failing else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rigops jobs", description=cli.COMMANDS["jobs"])
    sub = ap.add_subparsers(dest="cmd", required=True)

    install_p = sub.add_parser("install", help="schedule the jobs (dry run unless --apply)")
    install_p.add_argument("--apply", action="store_true", help="write and load the jobs")
    install_p.add_argument("--heal", action="store_true", help="let the scheduled doctor heal")
    install_p.add_argument(
        "--jobs", help=f"comma-separated jobs (default doctor,ledger; available {', '.join(jobs.JOBS)})",
    )
    install_p.set_defaults(func=_install)

    uninstall_p = sub.add_parser("uninstall", help="unload and remove the jobs")
    uninstall_p.add_argument("--apply", action="store_true", help="remove for real")
    uninstall_p.set_defaults(func=_uninstall)

    status_p = sub.add_parser("status", help="report whether each job is installed and runnable")
    status_p.set_defaults(func=_status)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        raise RigopsError(str(exc)) from exc
