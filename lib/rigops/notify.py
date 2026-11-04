from __future__ import annotations

from rigops import cause as rigops_cause

NOTIFY_WINDOW_H = 24
RECOVERY_H = 6


def _entry(raw) -> dict | None:
    """A stamp as {sent, seen}; a bare number is the legacy shape."""
    num = (int, float)
    if isinstance(raw, num) and not isinstance(raw, bool):
        return {"sent": raw, "seen": raw}
    if isinstance(raw, dict) and all(isinstance(raw.get(f), num) for f in ("sent", "seen")):
        return {"sent": raw["sent"], "seen": raw["seen"]}
    return None


def notify_due(
    stamps: dict, keys: set, now: float,
    window_h: float = NOTIFY_WINDOW_H, recovery_h: float = RECOVERY_H,
) -> tuple:
    """Returns (due, stamps'). Due when a key is new, was last sent over window_h
    ago, or came back after being absent for recovery_h. A failure that flaps
    inside recovery_h is the same finding, not a new one."""
    entries = {k: e for k, v in stamps.items() if (e := _entry(v)) is not None}
    due = False
    for k in keys:
        e = entries.get(k)
        if e is None or now - e["seen"] >= recovery_h * 3600 or now - e["sent"] >= window_h * 3600:
            due = True
    out = {
        k: e for k, e in entries.items()
        if k not in keys and now - e["seen"] < recovery_h * 3600
    }
    for k in keys:
        e = entries.get(k)
        sent = now if due or e is None else e["sent"]
        out[k] = {"sent": sent, "seen": now}
    return due, out


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
