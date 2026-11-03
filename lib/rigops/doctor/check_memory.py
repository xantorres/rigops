"""A memory store's index must match the notes beside it, and stay loadable.

The index is read into every session, so a pointer to a note that is gone wastes
a line and misleads, a note the index does not name is never recalled, and an
index past the load limit is silently cut. Findings are warnings: they are
reported, never fail the run and never page.

A ``[[name]]`` link with no note behind it is not checked. It marks something
worth writing later, so it is allowed by convention.
"""

from __future__ import annotations

import re
from pathlib import Path

from rigops import core

from .check_pointers import _finding, _sources
from .pointer_grammar import MEMORY_NOTE_RE

AREA = "docs"
CHECK = "memory"

INDEX = "MEMORY.md"
DEFAULT_MAX_LINES = 150
DEFAULT_MAX_BYTES = 20000
DEFAULT_SOURCES = ["~/.claude/projects/*/memory/*.md"]

_POINTER_RE = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)\s]*)?\)")


def _warn(kind, value, where, fix, symptom):
    finding = _finding(kind, value, where, fix)
    return core.Finding(
        check=CHECK, area=AREA, key=finding.key, symptom=symptom,
        evidence=where, fix=fix, severity="warn",
    )


def _cap(cfg, key, default):
    value = core.cfg_get(cfg, f"doctor.memory.{key}", None)
    return default if value is None else value


def _store_findings(mem, max_lines, max_bytes):
    out = []
    label = str(mem / INDEX).replace(str(core.HOME), "~")
    text = core.read_text(mem / INDEX)
    linked = set()
    for match in _POINTER_RE.finditer(text):
        target = match.group(1)
        if "://" in target:
            continue
        linked.add(Path(target).name)
        if not (mem / target).is_file():
            out.append(_warn(
                "pointer", target, label,
                "repoint the index entry at a note that exists or drop it",
                f"index pointer `{target}` has no note",
            ))
    for note in sorted(mem.glob("*.md")):
        if note.name != INDEX and note.name not in linked:
            out.append(_warn(
                "unindexed", note.name, label,
                "add a one-line index entry or retire the note",
                f"note `{note.name}` is missing from the index",
            ))
    lines, size = len(text.splitlines()), len(text.encode("utf-8"))
    if lines > max_lines or size > max_bytes:
        out.append(_warn(
            "index-size", label, label,
            "fold index lines together or retire stale notes",
            f"index is {lines} lines and {size} bytes (caps {max_lines} and {max_bytes})",
        ))
    return out


def run(cfg):
    configured = core.cfg_get(cfg, "doctor.memory.sources", DEFAULT_SOURCES)
    paths, _ = _sources({"doctor": {"pointers": {"sources": configured}}})
    max_lines = _cap(cfg, "index_max_lines", DEFAULT_MAX_LINES)
    max_bytes = _cap(cfg, "index_max_bytes", DEFAULT_MAX_BYTES)
    stores = sorted({p.parent for p in paths if MEMORY_NOTE_RE.search(str(p))})
    out = []
    for mem in stores:
        if (mem / INDEX).is_file():
            try:
                out.extend(_store_findings(mem, max_lines, max_bytes))
            except OSError:
                continue
    return out

