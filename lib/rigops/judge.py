from __future__ import annotations

import copy
import glob
import hashlib
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from rigops import cause as rigops_cause
from rigops import config as rigops_config
from rigops import core as rigops_core
from rigops import launchd

STATUSES = ("ok", "stale", "hung", "failing", "unknown")
ACTIONS = ("none", "kill", "kickstart", "notify", "hold")

CADENCE_EVENT_RE = re.compile(r"\bevent\b", re.IGNORECASE)
CADENCE_DAILY_HHMM_RE = re.compile(r"daily\s+(\d{1,2}):(\d{2})")
CADENCE_EVERY_MIN_RE = re.compile(r"every\s+(\d+)\s*min", re.IGNORECASE)
CADENCE_KEEPALIVE_RE = re.compile(r"always-on|keepalive", re.IGNORECASE)
CADENCE_WEEKLY_RE = re.compile(r"weekly", re.IGNORECASE)
CADENCE_MONTHLY_RE = re.compile(r"monthly", re.IGNORECASE)
CADENCE_QUARTERLY_RE = re.compile(r"quarterly", re.IGNORECASE)
CADENCE_ONDEMAND_RE = re.compile(r"on-demand|manual|deadline", re.IGNORECASE)
HEALTH_TRAILING_NOTE_RE = re.compile(r"\s*\([^)]*\)\s*$")
GLOB_CHARS_RE = re.compile(r"[*?\[]")

def judge_job(
    *,
    runtime_h,
    max_runtime_h,
    last_exit_status,
    evidence_age_h,
    expected_interval_h,
    heal_cooldown_active,
    cause=None,
    hold_release=False,
    keepalive=False,
):
    """Judge one job's health from point-in-time signals. No I/O, no side effects.

    A kill stamps the same cooldown as a kickstart: the supervisor's next
    scheduled fire restarts the job regardless, so an immediate kickstart
    right after a kill risks re-hanging the run that was just cleared (intended).

    Decision table (first matching row wins):

    | runtime_h | exit / evidence                            | status  | action        |
    |-----------|---------------------------------------------|---------|---------------|
    | live      | keepalive cadence (a daemon never finishes)  | ok      | none          |
    | > max_h   | (a live run always wins over anything else)  | hung    | kill          |
    | <= max_h  | (live, within its ceiling)                   | ok      | none          |
    | None      | last_exit_status not in (None, 0)            | failing | kickstart/notify* |
    | None      | evidence_age_h > expected_interval_h * 1.5   | stale   | kickstart/notify* |
    | None      | last_exit_status is None and no evidence     | unknown | none          |
    | None      | else (exit 0 and/or evidence fresh/absent)   | ok      | none          |

    * kickstart when heal_cooldown_active is False, notify when True: cooldown
      suppresses the repeat auto-heal but still surfaces the problem instead
      of going silent.

    Staleness only applies when expected_interval_h is not None (on-demand,
    event, and keepalive cadences are never staleness-checked) and
    evidence_age_h is not None (no evidence yet is "unknown", not "stale" --
    the job may simply not have fired since it was registered). Both
    boundaries are strict `>`: runtime_h == max_runtime_h is still "ok", and
    evidence_age_h == expected_interval_h * 1.5 is still not stale.

    cause (from rigops.cause, None when unclassified or the policy is off)
    only rewrites a kickstart: auth, quota and network hold instead, unless
    hold_release says the hold has run its course; deterministic never
    kickstarts and notifies instead. Other causes keep the table above.

    A live runtime_h always wins over exit status and evidence age: launchd
    never starts a scheduled job whose previous run is still alive, so a hung
    run must be judged -- and killed -- before anything else is considered.
    """
    if runtime_h is not None:
        if runtime_h > max_runtime_h and not keepalive:
            return "hung", "kill"
        return "ok", "none"

    exit_fail = last_exit_status is not None and last_exit_status != 0

    stale = False
    if expected_interval_h is not None and evidence_age_h is not None:
        stale = evidence_age_h > expected_interval_h * 1.5

    if exit_fail or stale:
        status = "failing" if exit_fail else "stale"
        action = "notify" if heal_cooldown_active else "kickstart"
        if exit_fail and cause is not None:
            if cause == "deterministic":
                action = "notify"
            elif cause in ("auth", "quota", "network") and not hold_release:
                action = "hold"
        return status, action

    if last_exit_status is None and evidence_age_h is None:
        return "unknown", "none"

    return "ok", "none"


def cadence_interval_hours(cadence: str):
    """Expected interval in hours, or None if the cadence isn't staleness-checkable.

    Interval words win over the uncheckable ones: cadences routinely name
    both ("manual, expected at least monthly", "daily 08:30 (manual
    kickstart ok)"), and the interval half is the one that can be checked.
    """
    if not cadence:
        return None
    if CADENCE_WEEKLY_RE.search(cadence):
        return 7 * 24
    if CADENCE_MONTHLY_RE.search(cadence):
        return 30 * 24
    if CADENCE_QUARTERLY_RE.search(cadence):
        return 91 * 24
    times = CADENCE_DAILY_HHMM_RE.findall(cadence)
    if times:
        occurrences = len(times) if len(times) > 1 else 1
        return 24 / occurrences
    m = CADENCE_EVERY_MIN_RE.search(cadence)
    if m:
        minutes = int(m.group(1))
        return minutes / 60 if minutes > 0 else None
    if CADENCE_ONDEMAND_RE.search(cadence):
        return None
    if CADENCE_EVENT_RE.search(cadence):
        return None
    if CADENCE_KEEPALIVE_RE.search(cadence):
        return None
    return None


def newest_evidence_mtime(health_field: str):
    """health_field may be a single path, a brace-expansion like foo.{log,err.log},
    a glob (* ? [), a path with a trailing '(note)' annotation, or '-' (no
    evidence). Returns (path_used, mtime) of the freshest match; a glob with no
    match is no evidence."""
    if not health_field or health_field.strip() == "-":
        return None, None
    cleaned = HEALTH_TRAILING_NOTE_RE.sub("", health_field.strip())
    candidates = []
    m = re.match(r"^(.*)\{([^}]+)\}(.*)$", cleaned)
    if m:
        prefix, alts, suffix = m.groups()
        for alt in alts.split(","):
            candidates.append(rigops_config.expand(prefix + alt + suffix))
    else:
        candidates.append(rigops_config.expand(cleaned))
    candidates = [
        Path(hit) for c in candidates
        for hit in (glob.glob(str(c)) if GLOB_CHARS_RE.search(str(c)) else [c])
    ]

    best_path, best_mtime = None, None
    for c in candidates:
        try:
            mt = c.stat().st_mtime
        except OSError:
            continue
        if best_mtime is None or mt > best_mtime:
            best_path, best_mtime = c, mt
    return best_path, best_mtime


def _evidence_age_h(ev_mtime):
    """Hours since ev_mtime, or None if there's no evidence file at all.

    mtime 0 (the epoch) is a valid timestamp, not "no evidence" -- only
    None means that. A negative age (clock skew, future mtime) clamps to 0.
    """
    if ev_mtime is None:
        return None
    return max((time.time() - ev_mtime) / 3600, 0.0)

def _resolve_label(candidate: str, prefixes: list):
    """candidate only counts as this item's label when it's both actually
    loaded and matches a configured prefix -- prefixes are config-owned,
    the registry is data, so an operator-supplied `plist:` path can't by
    itself point doctor at an unrelated already-loaded daemon."""
    if launchd.is_loaded(candidate) and any(candidate.startswith(p) for p in prefixes):
        return candidate
    return None


def _label_for_item(item: dict, prefixes: list):
    plist = item.get("plist")
    if plist:
        return _resolve_label(Path(plist).expanduser().stem, prefixes)
    job_id = item.get("id", "")
    for prefix in prefixes:
        label = _resolve_label(f"{prefix}{job_id}", prefixes)
        if label:
            return label
    return None


def _judge_launchd_item(
    item: dict, prefixes: list, heal_cooldown_active: bool, cause_ctx: dict | None = None,
) -> dict:
    job_id = item.get("id", "unknown")
    seen = cause_ctx["labels"] if cause_ctx else {}
    label = seen[id(item)] if id(item) in seen else _label_for_item(item, prefixes)
    if label is None:
        # No label is not "no information": the item's own evidence file still
        # says whether it ran. Unknown means neither a label nor evidence.
        return _judge_evidence_only_item(item, heal_cooldown_active)

    _, pid, last_exit = launchd.job_snapshot(label)
    if pid == os.getpid():
        return {
            "id": job_id, "status": "ok", "action": "none",
            "runtime_h": None, "evidence_age_h": None, "label": label, "pid": pid,
        }
    runtime_h = launchd.runtime_hours(pid) if pid else None
    interval_h = cadence_interval_hours(item.get("cadence", ""))
    _, ev_mtime = newest_evidence_mtime(item.get("health", ""))
    evidence_age_h = _evidence_age_h(ev_mtime)

    status, action = judge_job(
        runtime_h=runtime_h, max_runtime_h=item["max_runtime_h"], last_exit_status=last_exit,
        evidence_age_h=evidence_age_h, expected_interval_h=interval_h,
        heal_cooldown_active=heal_cooldown_active,
        keepalive=bool(CADENCE_KEEPALIVE_RE.search(item.get("cadence", ""))),
    )
    job = {
        "id": job_id, "status": status, "action": action, "runtime_h": runtime_h,
        "evidence_age_h": evidence_age_h, "label": label, "pid": pid,
    }
    if cause_ctx and runtime_h is None and status == "failing":
        _apply_cause(job, item, label, last_exit, interval_h, cause_ctx, heal_cooldown_active)
    return job


def _apply_cause(job, item, label, last_exit, interval_h, ctx, heal_cooldown_active) -> None:
    try:
        _apply_cause_unsafe(job, item, label, last_exit, interval_h, ctx, heal_cooldown_active)
    except Exception as exc:  # advisory: a classifier fault must never stop the supervisor
        job.pop("cause", None)
        job.pop("would", None)
        for key in ("exit", "tail_sha", "log"):
            job.pop(key, None)
        print(f"warning: cause classification skipped for {job['id']}: {exc}", file=sys.stderr)


def _apply_cause_unsafe(job, item, label, last_exit, interval_h, ctx, heal_cooldown_active) -> None:
    """Classify a failing job and judge it again with the cause.

    Shadow records the verdict as job["would"] and leaves job["action"] alone;
    on adopts it.
    """
    plist = item.get("plist") or f"~/Library/LaunchAgents/{label}.plist"
    window_h = interval_h * 1.5 if interval_h else 24.0
    tails = [
        (path, rigops_cause.tail_of(path, now=ctx["now"], max_age_h=window_h))
        for path in rigops_cause.log_paths(plist)
    ]
    tail = "\n".join(t for _, t in tails)
    cause = rigops_cause.classify(last_exit, tail)
    release = False
    before = copy.deepcopy(ctx["holds"].get(job["id"]))
    if cause in rigops_cause.HELD_CAUSES:
        release = rigops_cause.update_hold(
            ctx["holds"], job["id"], cause, ctx["now"], ctx["probe"],
            _label_kicks(ctx, label, job["id"]),
        )
        if release and job["id"] in ctx["killed"]:
            release = False  # a kill's cooldown outranks a released hold
            ctx["undo"][job["id"]] = before
    _, would = judge_job(
        runtime_h=None, max_runtime_h=item["max_runtime_h"], last_exit_status=last_exit,
        evidence_age_h=job["evidence_age_h"], expected_interval_h=interval_h,
        heal_cooldown_active=heal_cooldown_active and not release, cause=cause,
        hold_release=release,
    )
    if release:
        ctx["undo"][job["id"]] = before
    job["cause"] = cause
    if ctx["policy"] == "on":
        job["action"] = would
    else:
        job["would"] = would
        # Never the raw tail: it can carry secrets. The hash lets two reviews compare tails.
        shown = next((p for p, t in tails if t), tails[0][0] if tails else "")
        job["exit"] = last_exit
        job["tail_sha"] = hashlib.sha256(tail.encode()).hexdigest()[:12]
        job["log"] = rigops_core.short(shown)


def _label_kicks(ctx: dict, label: str, job_id: str) -> list:
    """When this label was last kicked: every id's heal stamp, and the siblings' released holds."""
    times = []
    for member in ctx["label_ids"].get(label, ()):
        times.append(ctx["heal_stamps"].get(member))
        if member != job_id:
            times += ctx["holds"].get(member, {}).get("releases", [])
    return [
        t for t in times
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t <= ctx["now"]
    ]


def _run_probe(command: str, warnings: list):
    """A probe passes on exit 0; any other outcome keeps the hold. It runs at most once
    per doctor run, and a probe that cannot run is recorded in warnings."""
    outcome = []

    def probe() -> bool:
        if not outcome:
            try:
                outcome.append(subprocess.run(
                    shlex.split(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=120,
                ).returncode == 0)
            except Exception as exc:
                warnings.append(f"cause probe could not run: {type(exc).__name__}")
                outcome.append(False)
        return outcome[0]
    return probe


def _judge_evidence_only_item(item: dict, heal_cooldown_active: bool) -> dict:
    job_id = item.get("id", "unknown")
    interval_h = cadence_interval_hours(item.get("cadence", ""))
    _, ev_mtime = newest_evidence_mtime(item.get("health", ""))
    evidence_age_h = _evidence_age_h(ev_mtime)

    status, action = judge_job(
        runtime_h=None, max_runtime_h=item["max_runtime_h"], last_exit_status=None,
        evidence_age_h=evidence_age_h, expected_interval_h=interval_h,
        heal_cooldown_active=heal_cooldown_active,
    )
    if action == "kickstart":
        # No label means no supervisor to kickstart: report only, status stays stale.
        action = "none"
    return {
        "id": job_id, "status": status, "action": action, "runtime_h": None,
        "evidence_age_h": evidence_age_h, "label": None, "pid": None,
    }
