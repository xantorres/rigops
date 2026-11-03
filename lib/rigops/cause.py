from __future__ import annotations

import re
from pathlib import Path

CAUSES = ("auth", "quota", "network", "deterministic", "unknown")

_PROBE_FAIL_RE = re.compile(r"claude-auth-probe: FAIL \((auth|quota|network)\)")
_PROBE_EXIT_RE = re.compile(r"probe exit ([123])\b")
_PROBE_CODES = {"1": "auth", "2": "quota", "3": "network"}
_AUTH_RE = re.compile(r"Not logged in|Please run /login|auth-probe-failed", re.I)
_QUOTA_RE = re.compile(r"hit your|usage limit|\brate.?limit|\bquota\b", re.I)
_NETWORK_RE = re.compile(
    r"nodename nor servname|ENOTFOUND|ECONNREFUSED|ETIMEDOUT|Unable to connect|API Error: 5\d\d",
    re.I,
)
_DETERMINISTIC_RE = re.compile(r"command not found|commit\.subject-type", re.I)
_DETERMINISTIC_EXITS = (126, 127)


def classify(exit_code, log_tail) -> str:
    """Name why a job failed from its exit status and the tail of its current run's log.

    Pure. A cause that no signal names is "unknown", which keeps today's behaviour.
    """
    if isinstance(exit_code, int) and exit_code > 255 and exit_code % 256 == 0:
        exit_code //= 256  # launchctl reports the raw wait status
    text = log_tail or ""
    if exit_code in _DETERMINISTIC_EXITS or _DETERMINISTIC_RE.search(text):
        return "deterministic"
    last_probe = None
    for m in _PROBE_FAIL_RE.finditer(text):
        last_probe = (m.start(), m.group(1))
    for m in _PROBE_EXIT_RE.finditer(text):
        if last_probe is None or m.start() > last_probe[0]:
            last_probe = (m.start(), _PROBE_CODES[m.group(1)])
    if last_probe:
        return last_probe[1]
    if _AUTH_RE.search(text):
        return "auth"
    if _QUOTA_RE.search(text):
        return "quota"
    if _NETWORK_RE.search(text):
        return "network"
    return "unknown"


def tail_of(path, *, now: float, max_age_h: float, lines: int = 40, max_bytes: int = 16384) -> str:
    """Last lines of a job log, or "" when it is missing or older than max_age_h.

    A log untouched for longer than the job's cadence cannot describe the run
    that just failed, so it is ignored instead of misclassifying it.
    """
    try:
        p = Path(path)
        st = p.stat()
        if now - st.st_mtime > max_age_h * 3600:
            return ""
        with p.open("rb") as fh:
            fh.seek(max(0, st.st_size - max_bytes))
            data = fh.read()
    except OSError:
        return ""
    kept = data.decode("utf-8", "replace").splitlines()[-lines:]
    for i in range(len(kept) - 1, -1, -1):
        if kept[i].startswith("====="):
            kept = kept[i + 1:] or kept
            break
    return "\n".join(kept)


HELD_CAUSES = ("auth", "quota", "network")
HOLD_RETRY_H = {"auth": 6.0, "quota": 4.0, "network": 1.0}
HOLD_NOTIFY_AFTER_H = {"auth": 0.0, "quota": 24.0, "network": 24.0}
NETWORK_MAX_RELEASES_PER_DAY = 3


def clean_holds(raw) -> dict:
    """Keep only well-formed hold entries; anything else is dropped."""
    if not isinstance(raw, dict):
        return {}
    num = (int, float)
    out = {}
    for k, v in raw.items():
        if (
            isinstance(v, dict) and v.get("cause") in HELD_CAUSES
            and isinstance(v.get("since"), num) and isinstance(v.get("last_try"), num)
            and isinstance(v.get("releases", []), list)
            and all(isinstance(t, num) for t in v.get("releases", []))
        ):
            out[k] = {**v, "releases": list(v.get("releases", []))}
    return out


def log_paths(plist_path) -> list:
    """StandardErrorPath then StandardOutPath of a launchd plist; [] when unreadable."""
    import plistlib

    try:
        with Path(plist_path).expanduser().open("rb") as fh:
            data = plistlib.load(fh)
        if not isinstance(data, dict):
            return []
    except Exception:
        return []
    keys = ("StandardErrorPath", "StandardOutPath")
    return [data[k] for k in keys if isinstance(data.get(k), str)]


def update_hold(holds: dict, job_id: str, cause: str, now: float, probe=None) -> bool:
    """Advance one job's hold and say whether it may be kicked now.

    Mutates holds. A new hold, or one for a different cause, starts held. After
    the cause's retry interval the optional probe decides: a failing probe waits
    a full interval again, a passing one (or none) releases a single kick.
    Network releases are capped per day.
    """
    entry = holds.get(job_id)
    if not isinstance(entry, dict):
        holds[job_id] = {"cause": cause, "since": now, "last_try": now, "releases": []}
        return False
    entry["cause"] = cause  # a flapping cause keeps its clock, or the hold never expires
    if now - entry["last_try"] < HOLD_RETRY_H[cause] * 3600:
        return False
    recent = [t for t in entry.get("releases", []) if now - t < 86400]
    entry["releases"] = recent
    if cause == "network" and len(recent) >= NETWORK_MAX_RELEASES_PER_DAY:
        return False
    entry["last_try"] = now
    if probe is not None and not probe():
        return False
    recent.append(now)
    return True


def hold_is_silent(holds: dict, job_id: str, cause: str, now: float) -> bool:
    """True while a held job is still inside its quiet period before it may page."""
    entry = holds.get(job_id)
    if not isinstance(entry, dict) or entry.get("cause") != cause:
        return False
    return now - entry["since"] < HOLD_NOTIFY_AFTER_H.get(cause, 0.0) * 3600
