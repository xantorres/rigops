from __future__ import annotations

import shutil

from rigops.sources.command import run_json

DEFAULT_ARGV = ["ccusage", "daily", "--json"]
COST_KEYS = ("totalCost", "total_cost_usd", "totalCostUSD", "costUSD")
TOKEN_KEYS = ("totalTokens", "total_tokens")


def probe() -> bool:
    return shutil.which("ccusage") is not None


def _first_numeric(d: dict, keys) -> float | None:
    for key in keys:
        value = d.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def window(since=None, until=None, argv=None, timeout_s=60) -> dict | None:
    call_argv = list(argv or DEFAULT_ARGV)
    if since is not None and until is not None:
        call_argv += ["--since", since.strftime("%Y%m%d"), "--until", until.strftime("%Y%m%d")]

    payload = run_json(call_argv, timeout_s=timeout_s)
    totals = payload.get("totals") if isinstance(payload, dict) else None
    if not isinstance(totals, dict):
        return None

    cost = _first_numeric(totals, COST_KEYS)
    tokens = _first_numeric(totals, TOKEN_KEYS)
    if cost is None and tokens is None:
        return None

    daily = payload.get("daily")
    return {
        "cost_usd": round(cost, 2) if cost is not None else None,
        "tokens": int(tokens) if tokens is not None else None,
        "days": len(daily) if isinstance(daily, list) else None,
    }
