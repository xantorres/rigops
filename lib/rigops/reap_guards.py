"""Config and run guards for reap: refuse malformed settings before anything is signalled."""

from __future__ import annotations

from pathlib import Path

from rigops.reap_procs import orphan_daemons


def list_setting_error(key: str, value: object) -> str | None:
    if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
        return None
    return f"{key} in config must be a list of non-empty strings"


def bool_setting_error(key: str, value: object) -> str | None:
    if isinstance(value, bool):
        return None
    return f"{key} in config must be true or false"


def orphan_sweep(enabled: bool, roots: list[Path], snap: dict[int, dict],
                 cwds: dict[int, str], min_age_s: int) -> set[int]:
    """Orphan candidates under every root; empty unless the sweep is switched on."""
    found: set[int] = set()
    if not enabled:
        return found
    for root in roots:
        found |= orphan_daemons(snap, cwds, str(root.resolve()), min_age_s)
    return found
