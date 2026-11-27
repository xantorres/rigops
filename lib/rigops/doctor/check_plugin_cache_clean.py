"""A failed plugin clone left in the plugin cache is dead weight that nothing removes.

The janitor rule that deletes `temp_git_*` directories runs weekly; a directory
older than eight days means that rule is missing or has not run. A directory is
as old as the newest thing under it, as the janitor judges it.
"""

from __future__ import annotations

import os
import time

from rigops import core

AREA = "rigops"
CHECK = "plugin_cache_clean"

MAX_AGE_DAYS = 8


def _newest_mtime(entry):
    """None when any part cannot be read: an unknown age is never old."""
    try:
        newest = os.lstat(entry).st_mtime
        for root, dirnames, filenames in os.walk(entry, followlinks=False, onerror=_raise):
            for name in dirnames + filenames:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
    except OSError:
        return None
    return newest


def _raise(exc):
    raise exc


def run(cfg):
    cache = core.CLAUDE_DIR / "plugins" / "cache"
    if not cache.is_dir():
        return []
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    out = []
    for entry in sorted(cache.glob("temp_git_*")):
        newest = _newest_mtime(entry)
        if entry.is_dir() and newest is not None and newest < cutoff:
            out.append(core.Finding(
                check=CHECK, area=AREA, key=f"stale:{entry.name}",
                symptom=f"failed plugin clone {entry.name} is over {MAX_AGE_DAYS} days old",
                evidence=core.short(entry),
                fix="add a janitor delete rule for temp_git_* in the plugin cache and run it",
                extra={"paths": [str(entry)]},
            ))
    return out
