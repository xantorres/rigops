from __future__ import annotations

import shutil

from rigops.sources.command import run_json

DEFAULT_ARGV = ["rtk", "gain", "--format", "json"]


def probe() -> bool:
    return shutil.which("rtk") is not None


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def window(since=None, until=None, argv=None, timeout_s=20) -> dict | None:
    """rtk gain reports all-time totals, not a windowed slice -- since/until
    are accepted for adapter-contract symmetry and ignored."""
    payload = run_json(argv or DEFAULT_ARGV, timeout_s=timeout_s)
    if not isinstance(payload, dict):
        return None

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = payload  # older/newer rtk may flatten the summary to the root

    commands = summary.get("total_commands")
    saved = summary.get("total_saved")
    saved_pct = summary.get("avg_savings_pct")
    if not (_numeric(commands) and _numeric(saved) and _numeric(saved_pct)):
        return None

    return {
        "commands": commands,
        "saved_tokens": saved,
        "saved_pct": round(saved_pct, 1),
        "snapshot": True,
    }
