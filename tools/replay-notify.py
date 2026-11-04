#!/usr/bin/env python3
"""Replay doctor events.jsonl through the notify rules and count pages per day.

Events recorded before the "failing" field carry only fail_count and the healed
lines, so the failing set is rebuilt: healed ids are known, the rest of the count
becomes anonymous slots that persist while the count holds. Events with a
"failing" field are used as they are.
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

HEALED_RE = re.compile(r"(?:kickstarted|terminated hung job) (\S+)")


def legacy_due(stamps: dict, keys: set, now: float, window_h: float = 24):
    """The rule before recovery tracking: stamps clear the moment a finding clears."""
    due = any(
        not isinstance(stamps.get(k), (int, float)) or now - stamps[k] >= window_h * 3600
        for k in keys
    )
    return due, ({k: now for k in keys} if due else {k: stamps[k] for k in keys})


def replay(events: list, rule: str) -> collections.Counter:
    per_day, stamps = collections.Counter(), {}
    for e in events:
        now = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        healed = {m.group(1) for h in e.get("healed", []) if (m := HEALED_RE.match(h))}
        if "failing" in e:
            ids = list(e["failing"])
        else:
            slots = max(0, e["fail_count"] - len(healed))
            ids = sorted(healed) + [f"anon{i}" for i in range(slots)]
        jobs = [{"id": i, "status": "failing"} for i in ids]
        if rule == "legacy":
            keys = notify.notify_keys(jobs, [])
            due, stamps = legacy_due(stamps, keys, now)
        else:
            keys = notify.notify_keys(jobs, [], healed_ids=frozenset(healed))
            due, stamps = notify.notify_due(stamps, keys, now)
        if due:
            per_day[e["ts"][:10]] += 1
    return per_day


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("events", type=Path)
    ap.add_argument("--until", default="9999", help="ignore events at or after this ISO timestamp")
    args = ap.parse_args()
    events = [
        e for e in (json.loads(line) for line in args.events.read_text().splitlines() if line)
        if "fail_count" in e and e["ts"] < args.until
    ]
    for rule in ("legacy", "current"):
        per_day = replay(events, rule)
        over = {d: c for d, c in sorted(per_day.items()) if c > 2}
        print(f"{rule}: days with pages {len(per_day)}, total {sum(per_day.values())}, "
              f"max/day {max(per_day.values(), default=0)}, days >2: {over or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
