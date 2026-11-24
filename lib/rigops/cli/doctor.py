from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rigops import cause as rigops_cause
from rigops import cli
from rigops import config as rigops_config
from rigops import core as rigops_core
from rigops import doctor as config_checks
from rigops import fmt, guard_state, launchd, procgroup, state
from rigops import notify as rigops_notify
from rigops import registry as rigops_registry
from rigops.judge import (  # noqa: F401
    _judge_evidence_only_item,
    _judge_launchd_item,
    _label_for_item,
    _run_probe,
    cadence_interval_hours,
    newest_evidence_mtime,
)

EVIDENCE_LINE_RE = re.compile(r":\d+$")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        return default


def _load_entries(path: Path, default):
    data, version = guard_state.unwrap(_load_json(path, default))
    return default if version > state.VERSION else data


def _load_dict(path: Path) -> dict:
    data = _load_entries(path, {})
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data) -> None:
    state.atomic_write(path, json.dumps(data, indent=2, sort_keys=True))


def _save_entries(path: Path, entries) -> None:
    _save_json(path, guard_state.wrap(entries))


NOTIFY_TIMEOUT_S = 60
KILL_PREFIX = "kill:"


def _guard_finding(path: Path, reason: str, outcome: str) -> rigops_core.Finding:
    return rigops_core.Finding(
        check="heal-state", area="doctor", symptom=f"{path.name} rejected: {reason}",
        evidence=str(path), fix=outcome, key=path.name,
    )


def _cooldown_active(heal_stamps: dict, job_id: str, cooldown_h: float, now: float) -> bool:
    last = heal_stamps.get(job_id)
    return last is not None and (now - last) < cooldown_h * 3600



def _first_stdout_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def run_custom_check(check_cfg: dict) -> dict:
    name = check_cfg.get("name", "custom")
    command = check_cfg.get("command", "")
    timeout_s = check_cfg.get("timeout_s", 10)
    warn_exit = check_cfg.get("warn_exit")
    fail_exit = check_cfg.get("fail_exit")
    try:
        out = procgroup.run_group(command, shell=True, timeout=timeout_s)
        code = out.returncode
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "warn", "detail": f"timed out after {timeout_s}s"}
    except OSError as exc:
        return {"name": name, "status": "fail", "detail": str(exc)}
    if fail_exit is not None and code == fail_exit:
        return {"name": name, "status": "fail",
                "detail": _first_stdout_line(out.stdout) or f"exit {code}"}
    if warn_exit is not None and code == warn_exit:
        return {"name": name, "status": "warn",
                "detail": _first_stdout_line(out.stdout) or f"exit {code}"}
    if code == 0:
        return {"name": name, "status": "ok", "detail": "exit 0"}
    return {"name": name, "status": "warn",
            "detail": _first_stdout_line(out.stdout) or f"unexpected exit {code}"}


def run_disk_free_check(check_cfg: dict) -> dict:
    # detail keeps the configured string: reports get pasted around, and the
    # expanded path would put the home directory into every one of them.
    raw_path = check_cfg.get("path", "/")
    warn_gb = check_cfg.get("warn_gb", 25)
    fail_gb = check_cfg.get("fail_gb", 10)
    try:
        free_gb = shutil.disk_usage(str(rigops_config.expand(raw_path))).free / (1024 ** 3)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        detail = f"cannot stat {raw_path}: {reason}"
        return {"name": "disk-free", "status": "fail", "detail": detail}
    if free_gb < fail_gb:
        status = "fail"
    elif free_gb < warn_gb:
        status = "warn"
    else:
        status = "ok"
    return {"name": "disk-free", "status": status, "detail": f"{free_gb:.1f}GB free on {raw_path}"}


def _notify(command: str, report_path: Path) -> str:
    """Runs the configured notify command; returns an event message on
    timeout/failure, or "" once it ran (or there's no command configured)."""
    if not command:
        return ""
    cmd = f"{command} {shlex.quote(str(report_path))}"
    try:
        proc = procgroup.run_group(cmd, shell=True, timeout=NOTIFY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"notify command timed out after {NOTIFY_TIMEOUT_S}s"
    except OSError as exc:
        return f"notify command failed: {exc}"
    if proc.returncode != 0:
        return f"notify command exited {proc.returncode}"
    return ""


def _is_unjudged(job: dict) -> bool:
    """No label and no evidence: the row would carry nothing but its own id."""
    return (
        job["status"] == "unknown"
        and job["label"] is None
        and job["evidence_age_h"] is None
    )


def render_report(ts: str, jobs: list, checks: list, heal_log: list, supervisor: str,
                  red_for: dict | None = None) -> str:
    red_for = red_for or {}
    lines = [f"rigops doctor report {ts}", f"supervisor: {supervisor}", "", "== jobs =="]
    unjudged = [j for j in jobs if _is_unjudged(j)]
    job_rows = [
        [
            j["id"], j["status"],
            f"{j['runtime_h']:.1f}h" if j["runtime_h"] is not None else "-",
            f"{j['evidence_age_h']:.1f}h" if j["evidence_age_h"] is not None else "-",
            j["action"], red_for.get(("job", j["id"]), ""),
        ]
        for j in jobs if not _is_unjudged(j)
    ]
    if job_rows:
        lines.append(fmt.table(
            job_rows, ["id", "status", "runtime", "evidence age", "action", "red for"]))
    elif not jobs:
        lines.append("no registry items")
    if unjudged:
        ids = ", ".join(j["id"] for j in unjudged)
        lines.append(f"not judged (no launchd label, no evidence): {ids}")
    lines.append("")
    lines.append("== checks ==")
    check_rows = [[c["name"], c["status"], red_for.get(("check", c["name"]), ""), c["detail"]]
                  for c in checks]
    lines.append(
        fmt.table(check_rows, ["name", "status", "red for", "detail"])
        if check_rows else "none configured"
    )
    if heal_log:
        lines.append("")
        lines.append("== heal actions ==")
        lines.extend(heal_log)
    return "\n".join(lines) + "\n"


RED = config_checks.ids.EXIT_1_STATUSES


def render_summary(jobs: list, checks: list, findings: list, gates: list, ts: str) -> str:
    counts = [
        (sum(1 for j in jobs if j["status"] in ("failing", "hung")), "failing/hung job"),
        (sum(1 for c in checks if c["status"] == "fail"), "failing check"),
        (len(_failing(findings)), "failing config finding"),
        (len(_errors(findings)), "config check error"),
    ]
    summary = ", ".join(f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in counts if n)
    lines = [f"rigops doctor: {summary or 'OK'} ({len(jobs)} jobs checked)"]
    red = [g for g in gates if g["status"] in RED]
    if red:
        lines.append("red for: " + ", ".join(
            f"{g['name']} {_red_for(g['since'], ts)}" for g in red))
    return "\n".join(lines) + "\n"


def _failing(findings: list) -> list:
    return [f for f in findings if f.severity not in ("warn", "error")]


def _errors(findings: list) -> list:
    return [f for f in findings if f.severity == "error"]


def default_supervisor() -> str:
    return "launchd" if sys.platform == "darwin" else "none"


def default_registry() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return rigops_config.expand(Path(xdg) / "rigops" / "registry.md")


def exit_code(findings: list, verdict: bool, exit_zero: bool) -> int:
    if _errors(findings):
        return 2
    return 1 if verdict and not exit_zero else 0


def render_unknown_keys(keys: list) -> str:
    if not keys:
        return ""
    noun = "key" if len(keys) == 1 else "keys"
    return f"info: {len(keys)} unknown config {noun}: {', '.join(keys)} (rigops config check)\n"


def render_findings(findings: list) -> str:
    failing = _failing(findings)
    warnings = [f for f in findings if f.severity == "warn"]
    lines = [f"config checks: {len(failing)} findings"]
    lines.extend(f.line() for f in failing)
    errors = _errors(findings)
    if errors:
        lines.append(f"errors: {len(errors)} (a check could not measure; the run exits 2)")
        lines.extend(f.line() for f in errors)
    if warnings:
        lines.append(f"warnings: {len(warnings)} (reported, never fail the run)")
        lines.extend(f.line() for f in warnings)
    return "\n".join(lines) + "\n"


def _finding_paths(finding) -> set:
    """The files a finding is about, absolute, for the staged filter to match on.

    Checks that measure several files at once list them in ``extra``; everything
    else carries one path as its evidence, with a line number the filter drops.
    """
    listed = finding.extra.get("paths")
    if listed:
        return {str(rigops_config.expand(p)) for p in listed}
    return {str(rigops_config.expand(EVIDENCE_LINE_RE.sub("", finding.evidence)))}


def report_config_checks(cfg: dict, args) -> int:
    # --staged asserts "this commit is sound" rather than "the tree is quiet":
    # the checks read the index of every staged file instead of the working tree
    # copy, and a finding about a file this commit does not touch is somebody
    # else's in-flight edit, which the unscoped fleet run still reports.
    staged, staged_files = None, {}
    if args.staged:
        _, staged_files = rigops_core.staged_index()
        staged = set(staged_files)
    # Set either way: the overlay belongs to this run, not to the process.
    rigops_core.set_overlay(staged_files)
    findings = config_checks.run_checks(cfg, only=set(args.check or []) or None)
    if staged is not None:
        findings = [f for f in findings if _finding_paths(f) & staged]
    if args.json:
        print(json.dumps(
            {"version": 1, "findings": [
                {"id": f.id, "check": f.check, "area": f.area, "severity": f.severity,
                 "symptom": f.symptom, "evidence": f.evidence, "fix": f.fix} for f in findings]},
            indent=2,
        ))
    else:
        print(render_findings(findings), end="")
    return exit_code(findings, bool(_failing(findings)), args.exit_zero)


def _job_gate_entries(jobs: list) -> list:
    return [
        {"kind": "job", "name": j["id"], "status": j["status"], "detail": ""}
        for j in jobs if j["status"] in ("failing", "hung", "stale")
    ]


def _check_gate_entries(checks: list) -> list:
    return [
        {"kind": "check", "name": c["name"], "status": c["status"],
         "detail": c["detail"]}
        for c in checks if c["status"] != "ok"
    ]


def _config_gate_entries(findings: list) -> list:
    by_check: dict = {}
    for f in findings:
        by_check.setdefault(f.check, []).append(f)
    return [
        {"kind": "config", "name": check, "detail": f"{len(group)} findings",
         "status": "fail" if any(f.severity != "warn" for f in group) else "warn"}
        for check, group in sorted(by_check.items())
    ]


def _with_since(gates: list, previous, ts: str) -> list:
    """A gate keeps its first ts while it stays in the record at the same red-or-not level."""
    known = {}
    if isinstance(previous, dict) and isinstance(previous.get("gates"), list):
        known = {(g.get("kind"), g.get("name"), g.get("status") in RED): g["since"]
                 for g in previous["gates"]
                 if isinstance(g, dict) and isinstance(g.get("since"), str)}
    return [{**g, "since": known.get((g["kind"], g["name"], g["status"] in RED), ts)}
            for g in gates]


def _red_for(since: str, ts: str) -> str:
    start, end = rigops_core.parse_utc_stamp(since), rigops_core.parse_utc_stamp(ts)
    if start is None or end is None:
        return "?"
    hours = max(0.0, (end - start).total_seconds() / 3600)
    return f"{int(hours // 24)}d" if hours >= 24 else f"{int(hours)}h"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rigops doctor", description=cli.COMMANDS["doctor"])
    ap.add_argument("--report", action="store_true", help="print the full report to stdout")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--list", action="store_true", help="list registry items and exit")
    ap.add_argument(
        "--heal", action="store_true",
        help=(
            "allow kill/kickstart side effects; default is report-only: no "
            "kill/kickstart (custom checks and notify still execute their "
            "configured commands)"
        ),
    )
    ap.add_argument(
        "--exit-zero", action="store_true",
        help="exit 0 even when a finding fails; output and findings are unchanged",
    )
    ap.add_argument(
        "--supervisor", choices=["launchd", "none"], default=default_supervisor(),
        help=(
            "'none' skips launchctl entirely and judges from evidence file ages only "
            "(default: launchd on macOS, none elsewhere)"
        ),
    )
    ap.add_argument("--registry", help="registry file path (default: config doctor.registry, else $XDG_CONFIG_HOME/rigops/registry.md)")
    ap.add_argument(
        "--config-only", action="store_true",
        help=(
            "run only the config checks (agents, budget, jobs, levers, mcp, memory, "
            "pointers) and skip the fleet"
        ),
    )
    ap.add_argument(
        "--no-config", action="store_true", help="skip the config checks",
    )
    ap.add_argument(
        "--staged", action="store_true",
        help=(
            "judge the git index of the files staged in the current repository "
            "instead of the working tree, and report only findings about them; "
            "for a pre-commit hook. Requires --config-only"
        ),
    )
    # choices, not a free string: an unrecognised name would otherwise build an
    # empty filter, run nothing and report a clean tree, so a typo in the name of
    # a check would read as "all clear".
    ap.add_argument(
        "--check", action="append", metavar="NAME", choices=config_checks.names(),
        help=f"limit config checks to NAME (repeatable); one of {', '.join(config_checks.names())}",
    )
    args = ap.parse_args(argv)
    if args.staged and not args.config_only:
        ap.error("--staged applies to the config checks only: add --config-only")

    cfg = rigops_config.load()

    if args.config_only:
        return report_config_checks(cfg, args)

    configured = rigops_config.get(cfg, "doctor.registry")
    is_default = configured == rigops_config.DEFAULTS["doctor"]["registry"]
    registry_path = rigops_config.expand(
        args.registry or (default_registry() if is_default else configured))
    if not args.registry and is_default and not registry_path.exists():
        print(f"note: no job registry at {rigops_core.short(registry_path)}; "
              "job checks skipped", file=sys.stderr)
        items = []
    else:
        items = rigops_registry.load_registry(registry_path)

    if args.list:
        if args.json:
            print(json.dumps({"version": 1, "items": items}, indent=2, default=str))
        else:
            rows = [
                [it.get("id", ""), it.get("domain", ""), it.get("trigger", ""),
                 it.get("cadence", "")]
                for it in items
            ]
            print(fmt.table(rows, ["id", "domain", "trigger", "cadence"]))
        return 0

    rigops_config.require_private(rigops_config.config_path(), registry_path)
    doctor_state_dir = state.state_dir() / "doctor"
    with state.locked(doctor_state_dir / "doctor"):
        return _fleet_run(args, cfg, items)


def _fleet_run(args, cfg: dict, items: list) -> int:
    doctor_state_dir = state.state_dir() / "doctor"
    heal_stamps_path = doctor_state_dir / "heal-stamps.json"
    events_path = doctor_state_dir / "events.jsonl"
    heal_stamps, stamps_bad = guard_state.load_guard_state(heal_stamps_path, numeric_values=True)
    guard_problems = [(heal_stamps_path, stamps_bad)] if stamps_bad else []
    cooldown_h = rigops_config.get(cfg, "doctor.heal_cooldown_h", 12)
    kill_grace_s = rigops_config.get(cfg, "doctor.kill_grace_s", 5)
    prefixes = rigops_config.get(cfg, "doctor.label_prefix", ["local."])

    now = time.time()
    ts = datetime.now(timezone.utc).strftime(rigops_core.UTC_STAMP)
    cause_policy = rigops_config.get(cfg, "doctor.cause_policy", "off")
    holds_path = doctor_state_dir / "holds.json"
    cause_ctx = None
    probe_warnings = []
    if cause_policy in ("shadow", "on") and args.supervisor == "launchd":
        probe_command = rigops_config.get(cfg, "doctor.cause_probe_command", "")
        if not isinstance(probe_command, str):
            probe_warnings.append(
                f"cause_probe_command ignored: not a string ({type(probe_command).__name__})"
            )
        probe_command = probe_command.strip() if isinstance(probe_command, str) else ""
        # A run that cannot kick must not spend a hold's retry, and a shadow run must not
        # run the probe: it would be work the unchanged supervisor never did.
        holds_raw, holds_bad = guard_state.load_guard_state(holds_path)
        if holds_bad:
            guard_problems.append((holds_path, holds_bad))
        cause_ctx = {
            "policy": cause_policy, "now": now, "undo": {},
            "holds": rigops_cause.clean_holds(holds_raw),
            "probe": (
                _run_probe(probe_command, probe_warnings)
                if probe_command and cause_policy == "on" and args.heal else None
            ),
            "heal_stamps": heal_stamps, "label_ids": {}, "labels": {},
            "killed": {
                k[len(KILL_PREFIX):] for k in heal_stamps
                if k.startswith(KILL_PREFIX) and _cooldown_active(heal_stamps, k, cooldown_h, now)
            },
        }
        for item in items:
            label = cause_ctx["labels"][id(item)] = _label_for_item(item, prefixes)
            if label:
                cause_ctx["label_ids"].setdefault(label, []).append(item.get("id", "unknown"))
    jobs = []
    for item in items:
        job_id = item.get("id", "unknown")
        cooldown_active = _cooldown_active(heal_stamps, job_id, cooldown_h, now)
        if args.supervisor == "none":
            job = _judge_evidence_only_item(item, cooldown_active)
        else:
            job = _judge_launchd_item(item, prefixes, cooldown_active, cause_ctx)
        jobs.append(job)

    would_log = []
    if cause_ctx and cause_policy == "shadow":
        would_log = [
            {"id": j["id"], "cause": j["cause"], "action": j["action"], "would": j["would"],
             "exit": j["exit"], "tail_sha": j["tail_sha"], "log": j["log"]}
            for j in jobs if "would" in j
        ]

    fail_closed = bool(guard_problems) and args.heal and args.supervisor == "launchd"
    guard_findings = []
    stamps_pinned = False
    for path, reason in guard_problems:
        moved = guard_state.move_aside(path, ts) if fail_closed else None
        if not fail_closed:
            outcome = "file left in place; a --heal run under launchd restarts nothing and moves it aside"
        elif moved:
            outcome = f"no job restarted and no hold released this run; moved aside to {moved.name}"
        else:
            outcome = "no job restarted and no hold released this run; file still in place"
        stamps_pinned = stamps_pinned or (path == heal_stamps_path and not moved)
        guard_findings.append(_guard_finding(path, reason, outcome))

    heal_log = []
    kicked_labels = set()
    kicked_ids = set()
    if args.heal and args.supervisor == "launchd":
        for job in jobs:
            if job["action"] == "kill" and job["pid"]:
                current_pid = launchd.job_pid(job["label"])
                if current_pid != job["pid"]:
                    heal_log.append(f"pid changed for {job['id']}, skip kill")
                elif launchd.terminate_process_group(job["pid"], kill_grace_s):
                    heal_stamps[job["id"]] = heal_stamps[KILL_PREFIX + job["id"]] = now
                    heal_log.append(f"terminated hung job {job['id']} (pid {job['pid']})")
                    if not stamps_pinned:
                        _save_entries(heal_stamps_path, heal_stamps)
            elif job["action"] == "kickstart" and job["label"] and not fail_closed:
                if job["label"] in kicked_labels:
                    # One launchd label runs every job sharing it: a second kick is a double run.
                    heal_stamps[job["id"]] = now
                    kicked_ids.add(job["id"])
                    _save_entries(heal_stamps_path, heal_stamps)
                elif launchd.kickstart(job["label"]):
                    kicked_labels.add(job["label"])
                    sharing = [j["id"] for j in jobs if j["label"] == job["label"]]
                    kicked_ids.update(sharing)
                    for sibling in sharing:
                        hold = cause_ctx["holds"].get(sibling) if cause_ctx else None
                        if hold and now not in hold["releases"]:
                            hold["releases"].append(now)
                    heal_stamps[job["id"]] = now
                    heal_log.append(f"kickstarted {job['id']} ({job['label']})")
                    _save_entries(heal_stamps_path, heal_stamps)

    if cause_ctx and args.heal and not fail_closed:
        for job_id, previous in cause_ctx["undo"].items():
            if job_id not in kicked_ids:
                if previous is None:
                    cause_ctx["holds"].pop(job_id, None)
                else:
                    cause_ctx["holds"][job_id] = previous
        keep = {
            j["id"]: j.get("cause") for j in jobs
            if j["runtime_h"] is not None or j["status"] == "failing"
        }
        cause_ctx["holds"] = {
            k: v for k, v in cause_ctx["holds"].items()
            if k in keep and (keep[k] is None or keep[k] == v["cause"])
        }
        _save_entries(holds_path, cause_ctx["holds"])

    checks = []
    for check_cfg in rigops_config.get(cfg, "doctor.checks.custom", []) or []:
        checks.append(run_custom_check(check_cfg))
    disk_free_cfg = rigops_config.get(cfg, "doctor.checks.disk_free")
    if disk_free_cfg:
        checks.append(run_disk_free_check(disk_free_cfg))
    only = set(args.check or []) or None
    findings = guard_findings + ([] if args.no_config else config_checks.run_checks(cfg, only=only))

    fail_count = sum(1 for j in jobs if j["status"] in ("failing", "hung"))
    # Reported, never paged: a stale row is a trend to read in the events log,
    # not a nightly interruption, so it stays out of the notify predicate.
    stale_count = sum(1 for j in jobs if j["status"] == "stale")
    gates_path = rigops_core.local_state_path(rigops_core.GATES_STATE_NAME)
    gates = _with_since(
        _job_gate_entries(jobs) + _check_gate_entries(checks) + _config_gate_entries(findings),
        _load_json(gates_path, None), ts,
    )
    red_for = {(g["kind"], g["name"]): _red_for(g["since"], ts) for g in gates}
    report_text = render_report(ts, jobs, checks, heal_log, args.supervisor, red_for)

    notify_command = rigops_config.get(cfg, "doctor.notify_command", "")
    notify_stamps_path = doctor_state_dir / "notified.json"
    kicked_path = doctor_state_dir / "kicked.json"
    marked = _load_entries(kicked_path, {})
    if isinstance(marked, list):  # the shape before kicks were timed
        marked = dict.fromkeys([m for m in marked if isinstance(m, str)], now)
    silenced, marked = rigops_notify.kick_suppressed(
        {k: v for k, v in marked.items() if isinstance(k, str)} if isinstance(marked, dict) else {},
        kicked_ids, now,
    )
    notify_keys = (
        rigops_notify.notify_keys(
            jobs, checks, cause_ctx["holds"] if cause_policy == "on" and cause_ctx else None, now,
            frozenset(silenced),
        )
        if notify_command else set()
    )
    due, stamps, held = rigops_notify.notify_due(
        _load_dict(notify_stamps_path), notify_keys, now,
    )

    event = {
        "ts": ts, "jobs_checked": len(jobs), "fail_count": fail_count,
        "stale_count": stale_count, "healed": heal_log,
        "failing": sorted(j["id"] for j in jobs if j["status"] in ("failing", "hung")),
        "notified": [k for k in sorted(notify_keys) if k not in held] if due else [],
    }
    if held:
        event["held"] = held
    if cause_policy == "shadow":
        event["would"] = would_log
    state.append_jsonl(events_path, event)
    for finding in guard_findings:
        state.append_jsonl(events_path, {"ts": ts, "event": f"{finding.symptom}; {finding.fix}"})
    for message in probe_warnings:
        state.append_jsonl(events_path, {"ts": ts, "event": message})

    # Saved before the send: a failed send must not hand the next kick a free silence.
    if notify_command:
        _save_entries(kicked_path, marked)

    notify_error = ""
    if due:
        report_path = doctor_state_dir / "report-latest.txt"
        state.atomic_write(report_path, report_text)
        notify_error = _notify(notify_command, report_path)
        if notify_error:
            state.append_jsonl(events_path, {"ts": ts, "event": notify_error})
    # A failed send leaves the keys unstamped so the next tick tries again.
    if notify_command and not notify_error:
        _save_entries(notify_stamps_path, stamps)

    # Written every run, gates or none: an empty list still proves the doctor ran,
    # which is what the session-start hook reading it needs to tell "clean" from "never checked".
    try:
        _save_json(gates_path, {"version": 1, "ts": ts, "gates": gates})
    except OSError as exc:
        print(f"warning: gates record not written: {exc}", file=sys.stderr)

    unknown_keys = [key for key, note in rigops_config.unknown_keys(cfg) if note is None]
    if args.json:
        payload = {
            "version": 1, "ts": ts, "supervisor": args.supervisor, "jobs": jobs, "checks": checks,
            "healed": heal_log, "fail_count": fail_count, "stale_count": stale_count,
            "gates": gates,
            "unknown_config_keys": unknown_keys,
            "findings": [
                {"id": f.id, "check": f.check, "area": f.area, "severity": f.severity,
                 "symptom": f.symptom, "evidence": f.evidence, "fix": f.fix}
                for f in findings
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
    elif args.report:
        print(report_text, end="")
        print(render_findings(findings), end="")
        print(render_unknown_keys(unknown_keys), end="")
    else:
        print(render_summary(jobs, checks, findings, gates, ts), end="")
        print(render_findings(findings), end="")
        print(render_unknown_keys(unknown_keys), end="")

    # Scheduled runs pass --exit-zero so the supervisor does not read a verdict as a crash.
    failed_checks = any(c["status"] == "fail" for c in checks)
    verdict = bool(fail_count or failed_checks or _failing(findings))
    if guard_findings:
        return 2
    return exit_code(findings, verdict, args.exit_zero)

