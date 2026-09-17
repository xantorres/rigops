"""UserPromptSubmit nudges: a declared regex fires a short reminder, scoped by
the cwd's realm and rate-limited per session so a long conversation is not
reminded of the same thing on every turn.

The registry, the retrieval index and the retrieval log all belong to the
caller (`libexec/rigops-nudge`); this module only ever reaches `rigops.core`,
so it takes a `registry_path` (for the default nudges-file location) and a
`cwd_realm` (already resolved by the caller) rather than a registry object.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from rigops import core

from . import util

DEFAULT_BUDGET = 400
DEFAULT_REPEAT_AFTER = 20
MIN_PREFETCH_BUDGET = 40
PRUNE_MAX_AGE_DAYS = 7
REPLAY_MARKERS = ("<task-notification>", "[SYSTEM NOTIFICATION", "<system-reminder>")


def is_replay(prompt: str) -> bool:
    """Agent output replayed back through the same channel is not the user asking."""
    text = prompt or ""
    return any(marker in text for marker in REPLAY_MARKERS)


def declarations_path(cfg, registry_path) -> Path:
    configured = core.cfg_get(cfg, "context.nudges_path")
    if configured:
        return core.expand(configured)
    return core.expand(Path(registry_path).parent / "nudges.json")


def _count(data: dict, key: str, default: int, path) -> int:
    """`data[key]` when it's a non-negative int, else `default` with a warning
    naming `key` -- a bool is not an int here, only a missing key is silent."""
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        print(f"warning: nudges file {path} {key} must be a non-negative int, "
              f"using default {default}: {value!r}", file=sys.stderr)
        return default
    return value


def _compile(entry):
    """The compiled nudge dict for a well-formed entry, else None with a warning."""
    if not isinstance(entry, dict):
        print(f"warning: nudge entry is not an object, skipping: {entry!r}", file=sys.stderr)
        return None
    name, pattern, say = entry.get("name"), entry.get("pattern"), entry.get("say")
    if not (isinstance(name, str) and name and isinstance(pattern, str) and pattern
            and isinstance(say, str) and say):
        print(f"warning: nudge entry missing name/pattern/say: {entry}", file=sys.stderr)
        return None
    flags_raw = entry.get("flags")
    if flags_raw is not None and not isinstance(flags_raw, str):
        print(f"warning: nudge {name!r} flags must be a string, skipping", file=sys.stderr)
        return None
    realms = entry.get("realms")
    if realms is not None and not (isinstance(realms, list)
                                    and all(isinstance(r, str) for r in realms)):
        print(f"warning: nudge {name!r} realms must be a list of strings, skipping",
              file=sys.stderr)
        return None
    flags = re.IGNORECASE if "i" in (flags_raw or "") else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        print(f"warning: nudge {name!r} pattern does not compile: {exc}", file=sys.stderr)
        return None
    return {"name": name, "regex": compiled, "say": say, "realms": realms}


def load_declarations(cfg, registry_path):
    """(nudges, budget, repeat_after); a missing file means no nudges, not an
    error, and a hand-edited file never raises -- anything malformed is
    defaulted or skipped with a warning on stderr instead."""
    path = declarations_path(cfg, registry_path)
    if not path.is_file():
        return [], DEFAULT_BUDGET, DEFAULT_REPEAT_AFTER
    try:
        data = json.loads(core.read_text(path))
    except (OSError, ValueError) as exc:
        print(f"warning: nudges file {path} unreadable: {exc}", file=sys.stderr)
        return [], DEFAULT_BUDGET, DEFAULT_REPEAT_AFTER
    if not isinstance(data, dict):
        print(f"warning: nudges file {path} is not a JSON object, ignoring", file=sys.stderr)
        return [], DEFAULT_BUDGET, DEFAULT_REPEAT_AFTER

    budget = _count(data, "budget", DEFAULT_BUDGET, path)
    repeat_after = _count(data, "repeat_after", DEFAULT_REPEAT_AFTER, path)

    raw_nudges = data.get("nudges", [])
    if not isinstance(raw_nudges, list):
        print(f"warning: nudges file {path} nudges must be a list, ignoring", file=sys.stderr)
        raw_nudges = []

    nudges = [compiled for entry in raw_nudges if (compiled := _compile(entry)) is not None]
    return nudges, budget, repeat_after


def match(nudges: list, prompt: str, cwd_realm) -> list:
    """Regex + realm matches, declaration order, deduped by `say` text."""
    text = prompt or ""
    hits, seen_say = [], set()
    for entry in nudges:
        realms = entry.get("realms")
        if realms and cwd_realm not in realms:
            continue
        if not entry["regex"].search(text):
            continue
        if entry["say"] in seen_say:
            continue
        seen_say.add(entry["say"])
        hits.append(entry)
    return hits


_SESSION_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def _sanitize_session_id(session_id: str) -> str:
    return _SESSION_ID_RE.sub("_", session_id)


def _session_state_path(session_id: str) -> Path:
    return core.local_state_path(f"nudge/{_sanitize_session_id(session_id)}.json")


def _prune_session_states(state_dir: Path, max_age_days: int = PRUNE_MAX_AGE_DAYS) -> None:
    """A session that has not prompted in a week never will again; the same
    mtime-age sweep `ctx-nudge.sh` runs over its own state directory."""
    cutoff = time.time() - max_age_days * 86400
    try:
        entries = list(state_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def apply_repeat_suppression(session_id, matched: list, repeat_after: int, budget=None):
    """(firing, suppressed_names); without a session id nothing is persisted --
    there is no session to rate-limit against. A budget trims `firing` before
    it is recorded, so a nudge cut for space stays due."""
    if session_id:
        path = _session_state_path(session_id)
        try:
            state = json.loads(core.read_text(path)) if path.is_file() else {}
        except (OSError, ValueError):
            state = {}
    else:
        state = {}
    prompts = int(state.get("prompts", 0)) + 1
    fired_at = dict(state.get("fired") or {})

    firing, suppressed = [], []
    for entry in matched:
        last = fired_at.get(entry["name"])
        if last is None or prompts - last >= repeat_after:
            firing.append(entry)
        else:
            suppressed.append(entry["name"])
    if budget is not None:
        firing, _ = select_within_budget(firing, budget)

    if session_id:
        for entry in firing:
            fired_at[entry["name"]] = prompts
        path.write_text(json.dumps({"prompts": prompts, "fired": fired_at}))
        _prune_session_states(path.parent)

    return firing, suppressed


def select_within_budget(firing: list, budget: int):
    """Declaration order, costed as the joined block `render_output` prints; the
    first nudge always survives even if it alone is over budget, matching how
    `retrieval.search` never rejects its first hit."""
    kept, text = [], ""
    for entry in firing:
        joined = f"{text}\n{entry['say']}" if kept else entry["say"]
        if kept and util.tokens(joined) > budget:
            break
        kept.append(entry)
        text = joined
    return kept, util.tokens(text)


def render_output(say_lines: list, claims_text: str, budget: int) -> str:
    parts = [line for line in (*say_lines, claims_text) if line]
    if not parts:
        return ""
    text = "\n".join(parts)
    return text if util.tokens(text) <= budget else util.clip(text, budget)


@dataclass
class Plan:
    say: list = field(default_factory=list)
    fired: list = field(default_factory=list)
    suppressed: list = field(default_factory=list)
    tokens: int = 0
    budget: int = DEFAULT_BUDGET

    @property
    def remaining(self) -> int:
        return self.budget - self.tokens


def plan(cfg, registry_path, prompt: str, cwd_realm, session_id, budget_override=None) -> Plan:
    nudges, file_budget, repeat_after = load_declarations(cfg, registry_path)
    budget = budget_override if budget_override is not None else file_budget
    matched = match(nudges, prompt, cwd_realm)
    firing, suppressed = apply_repeat_suppression(session_id, matched, repeat_after, budget)
    say = [entry["say"] for entry in firing]
    fired = [entry["name"] for entry in firing]
    return Plan(
        say=say, fired=fired, suppressed=suppressed,
        tokens=util.tokens("\n".join(say)), budget=budget,
    )


__all__ = ["is_replay", "load_declarations", "match", "apply_repeat_suppression",
           "select_within_budget", "render_output", "plan", "Plan"]
