from __future__ import annotations

import math
import time

from rigops import cause as rigops_cause

NOTIFY_WINDOW_H = 24
RECOVERY_H = 6
MAX_PAGES_PER_DAY = 2
PAGES_KEY = "_pages"


def _entry(raw, now: float) -> dict | None:
    """A stamp as {sent, seen}; a bare number is the legacy shape. A stamp that is
    not a finite time in the past cannot be trusted, so it counts as no stamp."""
    num = (int, float)

    def ok(v) -> bool:
        return isinstance(v, num) and not isinstance(v, bool) and math.isfinite(v) and v <= now

    if ok(raw):
        return {"sent": raw, "seen": raw}
    if isinstance(raw, dict) and ok(raw.get("sent")) and ok(raw.get("seen")):
        return {"sent": raw["sent"], "seen": raw["seen"]}
    return None


def _pages_today(stamps: dict, now: float) -> int:
    raw = stamps.get(PAGES_KEY)
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if isinstance(raw, dict) and raw.get("day") == day:
        n = raw.get("n")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            return n
    return 0


def notify_due(
    stamps: dict, keys: set, now: float,
    window_h: float = NOTIFY_WINDOW_H, recovery_h: float = RECOVERY_H,
    max_pages: float = MAX_PAGES_PER_DAY,
) -> tuple:
    """Returns (due, stamps', held). A key is fresh when it is new, was last sent over
    window_h ago, or came back after being absent for recovery_h; a failure that flaps
    inside recovery_h is the same finding, not a new one. At most max_pages pages go
    out per local day: fresh keys over that ceiling are held, left unstamped so they
    page the next day if still failing."""
    entries = {k: e for k, v in stamps.items() if (e := _entry(v, now)) is not None}
    fresh = {
        k for k in keys
        if (e := entries.get(k)) is None
        or now - e["seen"] >= recovery_h * 3600 or now - e["sent"] >= window_h * 3600
    }
    used = _pages_today(stamps, now)
    due = bool(fresh) and used < max_pages
    out = {
        k: e for k, e in entries.items()
        if k not in keys and now - e["seen"] < recovery_h * 3600
    }
    for k in keys:
        e = entries.get(k)
        if due:
            out[k] = {"sent": now, "seen": now}
        elif k in fresh:
            if e is not None:
                out[k] = e
        else:
            out[k] = {"sent": e["sent"], "seen": now}
    if due or used:
        out[PAGES_KEY] = {
            "day": time.strftime("%Y-%m-%d", time.localtime(now)), "n": used + (1 if due else 0),
        }
    return due, out, (sorted(fresh) if fresh and not due else [])


def kick_suppressed(marked: set, kicked: set, failing: set) -> tuple:
    """Returns (ids this run's kick may silence, ids to remember). A kick silences a
    job's page once per failing episode: kicked again while still marked, it pages."""
    silenced = set(kicked) - set(marked)
    return silenced, (set(marked) & set(failing)) | silenced


def notify_keys(
    jobs: list, checks: list, holds: dict | None = None, now: float = 0.0,
    healed_ids: frozenset = frozenset(),
) -> set:
    """Findings worth a page. A kick is not a finding, and neither is the job it just
    restarted: if the kick did not work the next run sees it failing with its heal
    cooldown active and pages then. A stale row is a trend to read, so it never pages."""
    keys = {
        f"job:{j['id']}:{j['status']}" for j in jobs
        if j["status"] in ("failing", "hung")
        and j["id"] not in healed_ids
        and not (
            holds is not None and j.get("cause") in rigops_cause.HELD_CAUSES
            and rigops_cause.hold_is_silent(holds, j["id"], j.get("cause"), now)
        )
    }
    keys |= {f"check:{c['name']}" for c in checks if c["status"] == "fail"}
    return keys
