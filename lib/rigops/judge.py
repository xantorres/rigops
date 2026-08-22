from __future__ import annotations

STATUSES = ("ok", "stale", "hung", "failing", "unknown")
ACTIONS = ("none", "kill", "kickstart", "notify")


def judge_job(
    *,
    runtime_h,
    max_runtime_h,
    last_exit_status,
    evidence_age_h,
    expected_interval_h,
    heal_cooldown_active,
):
    """Judge one job's health from point-in-time signals. No I/O, no side effects.

    A kill stamps the same cooldown as a kickstart: the supervisor's next
    scheduled fire restarts the job regardless, so an immediate kickstart
    right after a kill risks re-hanging the run that was just cleared (intended).

    Decision table (first matching row wins):

    | runtime_h | exit / evidence                            | status  | action        |
    |-----------|---------------------------------------------|---------|---------------|
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

    A live runtime_h always wins over exit status and evidence age: launchd
    never starts a scheduled job whose previous run is still alive, so a hung
    run must be judged -- and killed -- before anything else is considered.
    """
    if runtime_h is not None:
        if runtime_h > max_runtime_h:
            return "hung", "kill"
        return "ok", "none"

    exit_fail = last_exit_status is not None and last_exit_status != 0

    stale = False
    if expected_interval_h is not None and evidence_age_h is not None:
        stale = evidence_age_h > expected_interval_h * 1.5

    if exit_fail or stale:
        status = "failing" if exit_fail else "stale"
        action = "notify" if heal_cooldown_active else "kickstart"
        return status, action

    if last_exit_status is None and evidence_age_h is None:
        return "unknown", "none"

    return "ok", "none"
