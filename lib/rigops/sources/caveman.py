from __future__ import annotations

import shutil

from rigops.sources.command import run_json

DEFAULT_ARGV = ["caveman", "stats", "--json", "--days", "7"]


def probe() -> bool:
    return shutil.which("caveman") is not None


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def window(since=None, until=None, argv=None, timeout_s=20) -> dict | None:
    """caveman stats only windows by trailing UTC days ending now, so the row is
    a snapshot; saved_tokens is caveman's own o200k estimate, not API-counted."""
    payload = run_json(argv or DEFAULT_ARGV, timeout_s=timeout_s)
    totals = payload.get("totals") if isinstance(payload, dict) else None
    if not isinstance(totals, dict):
        return None
    requests = totals.get("requests")
    saved = totals.get("saved_tokens")
    before = totals.get("before_tokens")
    if not (_numeric(requests) and _numeric(saved)):
        return None
    return {
        "requests": requests,
        "saved_tokens": saved,
        "saved_pct": round(saved / before * 100, 1) if _numeric(before) and before else None,
        "estimate": True,
        "snapshot": True,
    }
