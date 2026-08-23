from __future__ import annotations

import json
import math
import shutil
import subprocess


def run_json(argv: list, timeout_s: float = 30) -> object | None:
    """Run argv, parse stdout as JSON. Any failure along the way -- missing
    binary, timeout, nonzero exit, invalid JSON -- returns None."""
    if not argv or not all(isinstance(a, str) for a in argv):
        return None
    if shutil.which(argv[0]) is None:
        return None
    try:
        try:
            timeout = float(timeout_s)
            if timeout <= 0 or not math.isfinite(timeout):
                timeout = 30.0
        except (TypeError, ValueError):
            # Bad/missing timeout still bounds the subprocess -- a hung
            # probe must not hang a scheduled ledger run.
            timeout = 30.0
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError, TypeError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def custom_annotations(specs: list, timeout_s_default: float = 30) -> dict:
    """Run each configured sources.custom[] entry; result[name] is whatever
    run_json returned (dict, list, or None). Malformed entries are skipped
    silently so one bad config item never blocks the rest."""
    out = {}
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        argv = spec.get("argv")
        if not name or not isinstance(argv, list) or not argv:
            continue
        timeout_s = spec.get("timeout_s", timeout_s_default)
        out[name] = run_json(argv, timeout_s=timeout_s)
    return out
