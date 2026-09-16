"""Lever regressions read from the ledger the weekly job already writes.

Every finding here is a warning. A ledger row aggregates last week's sessions,
so a regressed lever is a trend to act on rather than a defect in whatever is
being committed, and a commit gate that tripped on it would be switched off the
first time it got in the way.

Rules live under ``doctor.levers.rules``, one per ledger column:

- ``max``: the latest row must not exceed it (a lever whose right value is zero).
- ``rise_pct`` / ``drop_pct``: the latest row must not move further than this, in
  the worse direction, from the median of the weekly rows before it.

``rigops ledger note "<why>" --accept <lever>`` records a deliberate change: rows
on or before that note's date are not judged for that lever, and a band restarts
its baseline at the accepted row.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, timedelta

from rigops import core

AREA = "metrics"
CHECK = "levers"

# Ledger rows aggregate the seven days before their date; rows closer together
# share sessions and would count one week twice.
WINDOW_DAYS = 7
BASELINE_ROWS = 4
MIN_BASELINE_ROWS = 2
DEFAULT_MAX_AGE_DAYS = 10
RULE_KEYS = ("max", "rise_pct", "drop_pct")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _show(value) -> str:
    return f"{round(value, 1):g}" if isinstance(value, float) else str(value)


def _tilde(path) -> str:
    return str(path).replace(str(core.HOME), "~", 1)


def _warning(key, symptom, evidence, fix, ledger) -> core.Finding:
    return core.Finding(
        check=CHECK, area=AREA, key=key, symptom=symptom, evidence=evidence, fix=fix,
        severity="warn", extra={"paths": [str(ledger)]},
    )


def read_rows(path):
    """(rows, problem): rows that each carry an ISO date, or a reason there are none."""
    try:
        text = core.read_text(path)
    except FileNotFoundError:
        return None, "does not exist"
    except OSError as exc:
        return None, f"cannot be read ({exc.strerror or type(exc).__name__})"
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            date.fromisoformat(row["date"])
        except (ValueError, TypeError, KeyError):
            return None, f"line {lineno} is not a JSON object with an ISO date"
        rows.append(row)
    return rows, None


def weekly(rows) -> list:
    """Rows a full window apart, picked from the newest back, returned oldest first."""
    kept = []
    for row in sorted(rows, key=lambda r: r["date"], reverse=True):
        if not kept or date.fromisoformat(row["date"]) <= (
            date.fromisoformat(kept[-1]["date"]) - timedelta(days=WINDOW_DAYS)
        ):
            kept.append(row)
    return kept[::-1]


def accepted_through(notes, lever):
    dates = [
        n["date"] for n in notes
        if isinstance(n, dict) and isinstance(n.get("date"), str)
        and isinstance(n.get("accept"), list) and lever in n["accept"]
    ]
    return max(dates) if dates else None


def _malformed(rule) -> bool:
    return (
        not isinstance(rule, dict) or not rule
        or any(k not in RULE_KEYS or not _number(v) for k, v in rule.items())
    )


def _judge_lever(lever, rule, series, accepted, ledger) -> list:
    latest = series[-1]
    value = latest.get(lever)
    if not _number(value) or (accepted and latest["date"] <= accepted):
        return []
    fix = (
        "find what moved it, or record a deliberate change with "
        f'`rigops ledger note "<why>" --accept {lever}`'
    )
    found = []
    if "max" in rule and value > rule["max"]:
        found.append(_warning(
            f"{lever}:max",
            f"{lever} is {_show(value)} on the {latest['date']} ledger row, above its limit of "
            f"{_show(rule['max'])}",
            f"{_tilde(ledger)} row {latest['date']}", fix, ledger,
        ))
    earlier = series[:-1]
    if accepted:
        anchor = [r["date"] for r in earlier if r["date"] <= accepted]
        if anchor:
            earlier = [r for r in earlier if r["date"] >= anchor[-1]]
    history = [r for r in earlier if _number(r.get(lever))][-BASELINE_ROWS:]
    if len(history) < MIN_BASELINE_ROWS:
        return found
    baseline = statistics.median(r[lever] for r in history)
    if baseline == 0:
        return found
    change = (value - baseline) / abs(baseline) * 100
    for kind, worse, verb in (("rise_pct", change, "rose"), ("drop_pct", -change, "fell")):
        if kind in rule and worse > rule[kind]:
            found.append(_warning(
                f"{lever}:{kind}",
                f"{lever} {verb} {abs(change):.0f}% to {_show(value)} on the {latest['date']} "
                f"ledger row, past its {_show(rule[kind])}% tolerance",
                f"baseline median {_show(baseline)} over weekly rows "
                f"{history[0]['date']}..{history[-1]['date']}",
                fix, ledger,
            ))
    return found


def judge(rows, notes, rules, today, ledger, max_age_days=DEFAULT_MAX_AGE_DAYS) -> list:
    if not rows:
        return [_warning(
            "empty", "the ledger has no rows, so no lever is judged",
            _tilde(ledger), "run `rigops ledger` or remove doctor.levers.rules", ledger,
        )]
    series = weekly(rows)
    latest = series[-1]
    found = []
    age = (today - date.fromisoformat(latest["date"])).days
    if age > max_age_days:
        found.append(_warning(
            "stale",
            f"the newest ledger row is {age} days old, past the {max_age_days}-day limit",
            f"{_tilde(ledger)} row {latest['date']}",
            "check the job that runs `rigops ledger`; lever findings still describe that row",
            ledger,
        ))
    for lever, rule in sorted(rules.items()):
        if _malformed(rule):
            found.append(_warning(
                f"{lever}:rule", f"doctor.levers.rules.{lever} is not a usable rule",
                json.dumps(rule), f"give it one or more of {', '.join(RULE_KEYS)} as numbers",
                ledger,
            ))
        elif lever not in latest:
            found.append(_warning(
                f"{lever}:column", f"the ledger has no `{lever}` column, so that rule never fires",
                f"{_tilde(ledger)} row {latest['date']}",
                "fix the lever name in doctor.levers.rules", ledger,
            ))
        else:
            found.extend(_judge_lever(lever, rule, series, accepted_through(notes, lever), ledger))
    return found


def run(cfg, today=None):
    rules = core.cfg_get(cfg, "doctor.levers.rules") or {}
    if not rules:
        return []
    state = core.state_dir()
    ledger = state / "ledger.jsonl"
    if not isinstance(rules, dict):
        return [_warning(
            "rules", "doctor.levers.rules is not an object", json.dumps(rules),
            "map each ledger column to its rule", ledger,
        )]
    rows, problem = read_rows(ledger)
    if problem:
        return [_warning(
            "unreadable", f"the ledger {problem}, so no lever is judged", _tilde(ledger),
            "run `rigops ledger`, or repair the named line", ledger,
        )]
    found = []
    notes, notes_problem = read_rows(state / "interventions.jsonl")
    if notes_problem and notes_problem != "does not exist":
        found.append(_warning(
            "notes", f"the intervention notes {notes_problem}, so no acceptance is honoured",
            _tilde(state / "interventions.jsonl"), "repair the named line", ledger,
        ))
    max_age = core.cfg_get(cfg, "doctor.levers.max_age_days", DEFAULT_MAX_AGE_DAYS)
    if not _number(max_age):
        max_age = DEFAULT_MAX_AGE_DAYS
    found.extend(judge(rows, notes or [], rules, today or date.today(), ledger, max_age))
    return found
