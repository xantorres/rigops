from __future__ import annotations

import json
import math
import os
from pathlib import Path


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
    if not isinstance(data, dict):
        return {}, "top level is not an object"
    if numeric_values and not all(_finite_number(v) for v in data.values()):
        return {}, "holds a value that is not a finite number"
    return data, ""


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
