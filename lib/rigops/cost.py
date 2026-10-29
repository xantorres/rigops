"""Input-token-equivalent cost of transcript turns, split by usage class."""

from __future__ import annotations

COST_WEIGHTS = {
    "input": 1.0, "cache_write": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1, "output": 5.0,
}


def split(turns: list[dict]) -> dict:
    """Input-token-equivalent cost per usage class at list-price ratios: output 5x input,
    cache writes 1.25x for the 5-minute lifetime and 2x for the 1-hour one."""
    writes_1h = [min(t.get("cache_write_1h", 0), t["cache_creation"]) for t in turns]
    return {
        "input": sum(t["input"] for t in turns) * COST_WEIGHTS["input"],
        "cache_write": (sum(t["cache_creation"] for t in turns) - sum(writes_1h))
        * COST_WEIGHTS["cache_write"] + sum(writes_1h) * COST_WEIGHTS["cache_write_1h"],
        "cache_read": sum(t["cache_read"] for t in turns) * COST_WEIGHTS["cache_read"],
        "output": sum(t["output"] for t in turns) * COST_WEIGHTS["output"],
    }


def by_surface(turns: list[dict]) -> dict:
    """Share of input-equivalent cost per surface, and each one's split by usage class."""
    groups: dict[str, list[dict]] = {}
    for t in turns:
        groups.setdefault(t.get("surface") or "unknown", []).append(t)
    splits = {k: split(v) for k, v in groups.items()}
    grand = sum(sum(s.values()) for s in splits.values())
    out = {}
    for k, parts in sorted(splits.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(parts.values())
        out[k] = {
            "pct": round(total / grand * 100, 1) if grand else None,
            **{f"{c}_pct": round(v / total * 100, 1) if total else None for c, v in parts.items()},
        }
    return out
