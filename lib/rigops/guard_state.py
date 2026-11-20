from __future__ import annotations

import json
import math
import os
from pathlib import Path

from rigops import state


def unwrap(data):
    """(entries, version): the versioned wrapper is exactly {"version": int, "entries": ...}."""
    if (isinstance(data, dict) and set(data) == {"version", "entries"}
            and isinstance(data["version"], int) and not isinstance(data["version"], bool)):
        return data["entries"], data["version"]
    return data, 0


def wrap(entries) -> dict:
    return {"version": state.VERSION, "entries": entries}


def load_guard_state(path, numeric_values: bool = False) -> tuple:
    """(data, reason) for a state file that guards an action; reason is "" when usable.

    A missing file is usable and empty. Anything unreadable or malformed yields ({}, reason)
    so the caller can refuse to act on it instead of reading it as "nothing recorded".
    """
    path = Path(path)
    if not path.exists():
        return {}, ""
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        return {}, f"unreadable ({type(exc).__name__})"
    except ValueError:
        return {}, "not valid JSON"
    data, version = unwrap(data)
    if version > state.VERSION:
        return {}, f"written by a newer rigops (version {version})"
    if not isinstance(data, dict):
        return {}, "top level is not an object"
    if numeric_values and not all(_finite_number(v) for v in data.values()):
        return {}, "holds a value that is not a finite number"
    return data, ""


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def move_aside(path, stamp: str):
    """Rename `path` to `<name>.corrupt-<stamp>` beside it; the new path, or None on failure."""
    path = Path(path)
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
    try:
        os.rename(path, target)
    except OSError:
        return None
    return target
