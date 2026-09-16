"""The SessionStart card: realm, prefetch scopes, a tripped doctor gate, a
preload budget check -- everything a session needs before its first turn,
never more than `context.card.max_tokens`.

The registry and the config are handed in already loaded; this module never
reaches past `rigops.core`, so the file-arithmetic estimate below reads the
instruction surface directly rather than through `rigops.doctor.check_budget`
(a sibling package it cannot import). The two checks measure the same kind of
surface for different reasons: the doctor's is a static ceiling for the
whole repository, this one is what one concrete cwd would actually load.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from rigops import core

from . import util

DEFAULT_MAX_TOKENS = 250
DEFAULT_DOCTOR_MAX_AGE_H = 2
GATES_STATE_NAME = "doctor/gates.json"
SEARCH_LINE = ('search: rigops retrieval search "<q>" '
               '(this realm + mixed; --scope all only on an explicit ask)')

# Mirrors rigops.doctor.check_budget: a rule with no `paths:` key in its
# frontmatter loads in every session, scoped or not.
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
PATHS_RE = re.compile(r"^paths:", re.MULTILINE)


def _abspath(cwd) -> Path:
    """Absolute, not resolved: `registry.profile` matches a root the same way,
    so a cwd reached through a symlink (macOS `/tmp`) must not desync from it."""
    return Path(os.path.abspath(os.path.expanduser(str(cwd))))


def _short(path) -> str:
    home = str(core.HOME)
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home) else text


def _is_unscoped(path) -> bool:
    try:
        head = core.read_text(path)[:2000]
    except OSError:
        return False
    match = FRONTMATTER_RE.match(head)
    return not (match and PATHS_RE.search(match.group(1)))


def _git_toplevel(cwd: Path):
    """Nearest ancestor with a `.git` entry, file or directory -- a worktree's
    own root, not the repository it was cut from."""
    d = cwd
    while True:
        if (d / ".git").exists():
            return d
        if d.parent == d:
            return None
        d = d.parent


_WORKTREE_GITDIR_RE = re.compile(r"gitdir:\s*(.*)/\.git/worktrees/[^/]+/?\s*$")


def _main_root(cwd: Path) -> Path:
    """A worktree's memory shares its main checkout's project slug.

    Walks to the nearest `.git`: a directory means cwd's ancestor already is
    the main checkout; a file (`gitdir: <main>/.git/worktrees/<name>`) redirects
    there. No `.git` at all leaves the memory index keyed on cwd itself.
    """
    d = cwd
    while True:
        entry = d / ".git"
        if entry.is_dir():
            return d
        if entry.is_file():
            try:
                content = core.read_text(entry).strip()
            except OSError:
                return d
            m = _WORKTREE_GITDIR_RE.match(content)
            return Path(m.group(1)) if m else d
        if d.parent == d:
            return cwd
        d = d.parent


_SLUG_RE = re.compile(r"[^A-Za-z0-9]")


def _memory_slug(main_root: Path) -> str:
    return _SLUG_RE.sub("-", str(main_root))


def _project_instruction_files(cwd: Path) -> list:
    """CLAUDE.md, CLAUDE.local.md and .claude/CLAUDE.md, cwd up to `/`."""
    out = []
    d = cwd
    while True:
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            candidate = d / name
            if candidate.is_file():
                out.append(candidate)
        nested = d / ".claude" / "CLAUDE.md"
        if nested.is_file():
            out.append(nested)
        if d.parent == d:
            return out
        d = d.parent


def _unscoped_rules(rules_dir: Path) -> list:
    if not rules_dir.is_dir():
        return []
    return [p for p in sorted(rules_dir.rglob("*.md")) if _is_unscoped(p)]


def _preload_components(cfg, cwd: Path) -> list:
    """Every file-arithmetic component but the card's own text (added by the
    caller once the card's non-budget lines are known)."""
    seen, out = set(), []

    def add(label, path):
        resolved = core.expand(path)
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        out.append({"label": label, "path": _short(resolved), "tokens": core.size(resolved) // 4})

    add("law", core.cfg_get(cfg, "context.preload.law", "~/.claude/CLAUDE.md"))
    for rule in _unscoped_rules(core.CLAUDE_DIR / "rules"):
        add("rule", rule)
    for instructions in _project_instruction_files(cwd):
        add("project", instructions)
    toplevel = _git_toplevel(cwd)
    if toplevel:
        for rule in _unscoped_rules(toplevel / ".claude" / "rules"):
            add("rule", rule)

    slug = _memory_slug(_main_root(cwd))
    memory_index = core.CLAUDE_DIR / "projects" / slug / "memory" / "MEMORY.md"
    if memory_index.is_file():
        try:
            head = "\n".join(core.read_text(memory_index).splitlines()[:200])
        except OSError:
            head = ""
        out.append({"label": "memory", "path": _short(memory_index),
                    "tokens": len(head.encode("utf-8")) // 4})

    fixed_tokens = int(core.cfg_get(cfg, "context.preload.fixed_tokens", 0) or 0)
    out.append({"label": "fixed", "path": None, "tokens": fixed_tokens})
    return out


def _resolve_budget(cfg, realm):
    value = core.cfg_get(cfg, "context.preload.budget_tokens")
    if value is None:
        return None
    return value.get(realm) if isinstance(value, dict) else value


def _budget_line(components: list, budget_tokens) -> str:
    total = sum(c["tokens"] for c in components)
    if budget_tokens is None or total <= budget_tokens:
        return ""
    # The fixed share is not a file anyone can trim from here, so name the largest file instead.
    files = [c for c in components if c.get("label") != "fixed"] or components
    largest = max(files, key=lambda c: c["tokens"])
    return (f"budget: preload ~{total} tok over {budget_tokens} "
            f"(largest: {largest['label']} {largest['tokens']})")


def _read_gates() -> dict:
    path = core.local_state_path(GATES_STATE_NAME)
    if not path.is_file():
        return None
    try:
        return json.loads(core.read_text(path))
    except (OSError, ValueError):
        return None


def _visible_gates(record, cwd_realm) -> list:
    if not record:
        return []
    return [g for g in record.get("gates", []) if g.get("realm") in (None, cwd_realm)]


def _age_hours(ts):
    if not ts:
        return None
    try:
        stamp = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 3600


def _render_check_gate(gate: dict) -> str:
    # An untagged check may name another realm's things in free text, so only
    # a realm-matched check gets to show its detail.
    if gate.get("realm") is not None:
        return f"{gate.get('name')}: {gate.get('detail')}"
    return f"{gate.get('name')} {gate.get('status')}"


def _truncate_tokens(text: str, max_tokens: int) -> str:
    limit = max_tokens * 4
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _stale_line(record, visible: list, cfg) -> str:
    if record is None:
        return None
    items = []
    max_age_h = core.cfg_get(cfg, "context.card.doctor_max_age_h", DEFAULT_DOCTOR_MAX_AGE_H)
    age_h = _age_hours(record.get("ts"))
    if age_h is not None and age_h > max_age_h:
        items.append(f"doctor gates last recorded {round(age_h)}h ago")

    # An overdue job is a trend the doctor never pages on, so only a broken one trips the card.
    failing = sum(1 for g in visible if g.get("kind") == "job" and g.get("status") in ("failing", "hung"))
    if failing:
        items.append(f"jobs: {failing} failing")

    for gate in visible:
        if gate.get("kind") == "check":
            items.append(_render_check_gate(gate))
        elif gate.get("kind") == "config":
            items.append(f"config {gate.get('name')} {gate.get('status')}")

    if not items:
        return None
    line = "stale: " + "; ".join(items) + " (rigops doctor --report)"
    return _truncate_tokens(line, 60)


def _scope_lines(registry, scopes) -> list:
    notes = (registry.raw or {}).get("scope_notes", {}) or {}
    return [f"- {scope}: {notes[scope]}" for scope in scopes if notes.get(scope)]


def build(registry, cfg, cwd) -> dict:
    cwd = _abspath(cwd)
    max_tokens = int(core.cfg_get(cfg, "context.card.max_tokens", DEFAULT_MAX_TOKENS))
    gates_record = _read_gates()
    profile = registry.profile(str(cwd))
    cwd_realm = profile.realm if profile else None
    visible = _visible_gates(gates_record, cwd_realm)
    stale = _stale_line(gates_record, visible, cfg)

    if profile is None:
        where = _short(cwd)
        header = (f"rig card · {where} is unmapped · no realm, no prefetch · "
                  f"rigops retrieval roots --cwd {where} explains")
        lines = [header] + ([stale] if stale else [])
        return {
            "lines": lines, "tokens": util.tokens("\n".join(lines)), "realm": None,
            "scopes": [], "preload": {}, "gates": visible,
        }

    name = profile.repo or Path(profile.root).name
    header = f"rig card · {name} · realm {profile.realm} · prefetch {', '.join(profile.scopes)}"
    scope_lines = _scope_lines(registry, profile.scopes)

    partial = [header, SEARCH_LINE, *scope_lines]
    if stale:
        partial.append(stale)
    components = _preload_components(cfg, cwd)
    components.append({"label": "card", "path": None, "tokens": util.tokens("\n".join(partial))})
    budget_tokens = _resolve_budget(cfg, profile.realm)
    budget_line = _budget_line(components, budget_tokens)

    def assemble():
        out = [header, SEARCH_LINE, *scope_lines]
        if stale:
            out.append(stale)
        if budget_line:
            out.append(budget_line)
        return out

    lines = assemble()
    while util.tokens("\n".join(lines)) > max_tokens and scope_lines:
        scope_lines.pop()
        lines = assemble()

    return {
        "lines": lines, "tokens": util.tokens("\n".join(lines)), "realm": profile.realm,
        "scopes": list(profile.scopes),
        "preload": {"total": sum(c["tokens"] for c in components), "budget": budget_tokens,
                    "components": components},
        "gates": visible,
    }


__all__ = ["build"]
