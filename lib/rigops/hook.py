from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import rigops
from rigops import core
from rigops import state as rigops_state
from rigops.errors import RigopsError

MAX_OUTPUT_BYTES = 1000
TIME_BUDGET_S = 1.0
MAX_SESSION_ID = 100

DEFAULT_DOCTOR_MAX_AGE_H = 2
DEFAULT_TIERS = (250000, 350000, 500000)
DEFAULT_REARM = 50000
TAIL_BYTES = 1 << 20
TAIL_LINES = 400
STATE_MAX_AGE_S = 7 * 86400

USAGE = """usage: rigops hook <event>

Plugin plumbing: reads the hook JSON payload on stdin, prints at most
1000 bytes, always exits 0.

events:
  session-start        stale doctor gates, or why there is none, plus a
                       second line when plugin and CLI versions differ
  user-prompt-submit   context-size nudge when the transcript crosses a tier
"""


def _cap(text: str) -> str:
    raw = (text + "\n").encode("utf-8")
    if len(raw) <= MAX_OUTPUT_BYTES:
        return text
    room = MAX_OUTPUT_BYTES - len("…\n".encode())
    return raw[:room].decode("utf-8", errors="ignore") + "…"


def _emit(text: str) -> None:
    """The only writer to stdout: capped, and a dead stdout never fails the hook."""
    try:
        sys.stdout.write(_cap(text.rstrip("\n")) + "\n")
        sys.stdout.flush()
    except BaseException:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
        except OSError:
            pass


def _read_gates(path):
    try:
        record = json.loads(core.read_text(path))
    except (OSError, ValueError):
        return None
    if not (isinstance(record, dict) and isinstance(record.get("gates"), list)
            and all(isinstance(g, dict) for g in record["gates"])):
        return None
    return record


def _age_hours(ts):
    stamp = core.parse_utc_stamp(ts)
    if stamp is None:
        return None
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 3600


def _stale_line(record, cfg):
    gates = record["gates"]
    items = []
    max_age_h = core.cfg_get(cfg, "context.card.doctor_max_age_h", DEFAULT_DOCTOR_MAX_AGE_H)
    age_h = _age_hours(record.get("ts"))
    if age_h is not None and age_h > max_age_h:
        items.append(f"doctor gates last recorded {round(age_h)}h ago")
    failing = sum(1 for g in gates
                  if g.get("kind") == "job" and g.get("status") in ("failing", "hung"))
    if failing:
        items.append(f"jobs: {failing} failing")
    for gate in gates:
        if gate.get("kind") == "check":
            items.append(f"{gate.get('name')} {gate.get('status')}")
        elif gate.get("kind") == "config":
            items.append(f"config {gate.get('name')} {gate.get('status')}")
    if not items:
        return ""
    return "stale: " + "; ".join(items) + " (rigops doctor --report)"


def _session_start_line() -> str:
    path = core.local_state_path(core.GATES_STATE_NAME)
    if not path.exists():
        return "rigops: no doctor gates recorded yet (run rigops doctor)"
    record = _read_gates(path)
    if record is None:
        return f"rigops: gate state unreadable at {core.short(path)} (run rigops doctor)"
    return _stale_line(record, core.load_config())


def _major_minor(version: str):
    match = re.match(r"(\d+)\.(\d+)", version)
    return (match.group(1), match.group(2)) if match else None


def _version_line() -> str:
    plugin = _major_minor(os.environ.get("RIGOPS_PLUGIN_VERSION", ""))
    cli = _major_minor(rigops.__version__)
    if plugin is None or cli is None or plugin == cli:
        return ""
    return (f"rigops: plugin {'.'.join(plugin)} and CLI {'.'.join(cli)} differ; "
            "upgrade the older one (see docs/INSTALL.md#upgrade)")


def _session_start() -> str:
    try:
        line = _session_start_line()
    except RigopsError as exc:
        line = f"rigops: config unreadable: {exc}"
    except Exception as exc:
        line = f"rigops: session-start hook failed ({type(exc).__name__})"
    return "\n".join(part for part in (line, _version_line()) if part)


def _valid_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _limits():
    try:
        cfg = core.load_config()
    except RigopsError:
        return DEFAULT_TIERS, DEFAULT_REARM
    tiers = core.cfg_get(cfg, "context.nudge_tiers")
    if not (isinstance(tiers, list) and len(tiers) >= 3 and all(_valid_int(t) for t in tiers[:3])):
        tiers = DEFAULT_TIERS
    rearm = core.cfg_get(cfg, "context.rearm_tokens")
    return tuple(tiers[:3]), rearm if _valid_int(rearm) else DEFAULT_REARM


def _context_tokens(transcript: str):
    with open(transcript, "rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    found = None
    for line in lines:
        try:
            row = json.loads(line)
            usage = row["message"]["usage"] if row.get("type") == "assistant" else None
            total = sum(usage.get(k) or 0 for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if isinstance(total, int) and total > 0:
            found = total
    return found


def _prune(directory: Path) -> None:
    cutoff = time.time() - STATE_MAX_AGE_S
    for entry in directory.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def _message(ctx: int, tiers) -> str:
    tier1, tier2, tier3 = tiers
    k = ctx // 1000
    if ctx >= tier3:
        return (f"[ctx-nudge] Context {k}k: past the point where this session is worth "
                "continuing. Take no new work. Finish or hand off the step in flight, write "
                "down what is left, and tell the user to start a fresh chat now.")
    if ctx >= tier2:
        return (f"[ctx-nudge] Context {k}k: heavy. Finish and verify the current step ONLY; "
                "at the next natural stopping point, tell the user: fresh chat now. "
                "All reads/edits via subagents.")
    return (f"[ctx-nudge] Context {k}k (>{tier1 // 1000}k). Wrap the current task; delegate "
            "mechanical edits and broad reads to subagents; then suggest a fresh chat.")


def _user_prompt_submit() -> str:
    try:
        payload = json.loads(sys.stdin.read())
    except (ValueError, OSError):
        return ""
    if not isinstance(payload, dict):
        return ""
    transcript, sid = payload.get("transcript_path"), payload.get("session_id")
    if not (isinstance(transcript, str) and transcript and isinstance(sid, str) and sid):
        return ""
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:MAX_SESSION_ID]

    directory = core.local_state_path("ctx-nudge")
    directory.mkdir(parents=True, exist_ok=True)
    _prune(directory)
    state = directory / sid

    ctx = _context_tokens(transcript)
    tiers, rearm = _limits()
    prev = None
    try:
        prev = int(state.read_text().strip())
    except (OSError, ValueError):
        pass

    # Re-arm only below the lowest tier: microcompaction drops tokens but stays above it.
    if ctx is not None and prev is not None and ctx < tiers[0]:
        state.unlink(missing_ok=True)
        prev = None
    if ctx is None or ctx < tiers[0]:
        return ""
    if prev is None or ctx >= prev + rearm:
        rigops_state.atomic_write(state, f"{ctx}\n")
        return _message(ctx, tiers)
    return ""


EVENTS = {"session-start": _session_start, "user-prompt-submit": _user_prompt_submit}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        _emit(USAGE)
        return 0
    handler = EVENTS.get(argv[0]) if argv else None
    if handler is None:
        return 0
    try:
        text = handler()
        if text:
            _emit(text)
    except BaseException:
        pass
    return 0
