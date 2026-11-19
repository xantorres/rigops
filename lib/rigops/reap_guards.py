"""Config and run guards for reap: refuse malformed settings before anything is signalled."""

from __future__ import annotations


def list_setting_error(key: str, value: object) -> str | None:
    if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
        return None
    return f"{key} in config must be a list of non-empty strings"
