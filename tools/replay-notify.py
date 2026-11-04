#!/usr/bin/env python3
"""Replay doctor events.jsonl through the notify rules and count pages per day.

Events recorded before the "failing" field carry only fail_count and the healed
lines, so the failing set is rebuilt. Events with a "failing" field are used as
they are. Run with no pipe to read the model limits it prints.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import notify  # noqa: E402

KICKED_RE = re.compile(r"kickstarted (\S+)")
KILLED_RE = re.compile(r"terminated hung job (\S+)")
LIMITS = (
    "model limits: events before the failing field carry only fail_count and the healed lines, "
    "so failing ids are rebuilt (kicked ids first, the rest anonymous slots that persist while "
    "the count holds; of the kicked ids, stale_count are assumed stale, not failing); checks are "
    "known only when they paged (notified keys); anonymous slots reuse ids, so real "
    "identity churn can page more than 'uncapped' shows. The ceiling holds regardless."
)


def legacy_due(stamps: dict, keys: set, now: float, window_h: float = 24):
    """The rule before recovery tracking: stamps clear the moment a finding clears."""
    due = any(
        not isinstance(stamps.get(k), (int, float)) or now - stamps[k] >= window_h * 3600
        for k in keys
    )
    return due, ({k: now for k in keys} if due else {k: stamps[k] for k in keys})


def failing_of(e: dict) -> tuple:
    """(failing ids, kicked ids, killed ids) of one event."""
    kicked = sorted(m.group(1) for h in e.get("healed", []) if (m := KICKED_RE.match(h)))
    killed = sorted(m.group(1) for h in e.get("healed", []) if (m := KILLED_RE.match(h)))
    if "failing" in e:
        return list(e["failing"]), kicked, killed
    failing_kicked = kicked[: max(0, len(kicked) - e.get("stale_count", 0))]
    slots = max(0, e["fail_count"] - len(failing_kicked) - len(killed))
    return failing_kicked + killed + [f"anon{i}" for i in range(slots)], kicked, killed


def replay(events: list, rule: str, with_checks: bool = True) -> tuple:
    """(pages per day, passes held by the ceiling). Runs the production functions."""
    per_day, stamps, marked, held = collections.Counter(), {}, set(), 0
    for e in events:
        now = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        ids, kicked, killed = failing_of(e)
        jobs = [{"id": i, "status": "hung" if i in killed else "failing"} for i in ids]
        checks = [
            {"name": k.split(":", 1)[1], "status": "fail"}
            for k in e.get("notified", []) if with_checks and k.startswith("check:")
        ]
        if rule == "legacy":
            due, stamps = legacy_due(stamps, notify.notify_keys(jobs, checks), now)
        else:
            silenced, marked = notify.kick_suppressed(marked, set(kicked), set(ids))
            keys = notify.notify_keys(jobs, checks, healed_ids=frozenset(silenced))
            cap = float("inf") if rule == "uncapped" else notify.MAX_PAGES_PER_DAY
            due, stamps, over = notify.notify_due(stamps, keys, now, max_pages=cap)
            held += bool(over)
        if due:
            per_day[e["ts"][:10]] += 1
    return per_day, held


def line(name: str, per_day: collections.Counter, extra: str = "") -> str:
    over = {d: c for d, c in sorted(per_day.items()) if c > 2}
    return (f"{name}: days with pages {len(per_day)}, total {sum(per_day.values())}, "
            f"max/day {max(per_day.values(), default=0)}, days >2: {over or 'none'}{extra}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("events", type=Path)
    ap.add_argument("--until", default="9999", help="ignore events at or after this ISO timestamp")
    args = ap.parse_args()
    events = [
        e for e in (json.loads(line) for line in args.events.read_text().splitlines() if line)
        if "fail_count" in e and e["ts"] < args.until
    ]
    print(line("legacy", replay(events, "legacy")[0]))
    print(line("uncapped (upper bound before the ceiling)", replay(events, "uncapped")[0]))
    print(line("jobs only (no checks, ceiling)", replay(events, "current", with_checks=False)[0]))
    per_day, held = replay(events, "current")
    print(line("current (ceiling)", per_day, f", passes the ceiling delays {held}"))
    print(LIMITS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
