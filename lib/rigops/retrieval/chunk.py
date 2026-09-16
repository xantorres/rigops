"""Split a document into the unit a reader actually reads: one section.

A whole file is the wrong row. The client knowledge tree holds single documents
over 100 KB, and returning one as a hit costs more than the answer is worth. An
h2 is where these documents already break, so that is the cut; an oversized
section falls back to its h3s and then to a hard cut, so one runaway section
cannot reintroduce whole-file rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
DATE_RE = re.compile(r"(?:verified|distilled|last_updated|updated)\s*:\s*\"?(\d{4}-\d{2}-\d{2})")
DEFAULT_MAX_CHARS = 12000


@dataclass(frozen=True)
class Section:
    heading: str
    line: int
    text: str
    verified: str = ""


def _frontmatter(text: str):
    """Return (frontmatter, body, body_offset_lines)."""
    if not text.startswith("---"):
        return "", text, 0
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text, 0
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:]), i + 1
    return "", text, 0


def title_of(text: str, fallback: str = "") -> str:
    match = H1_RE.search(text)
    return match.group(1).strip() if match else fallback


def _date_in(text: str, default: str = "") -> str:
    match = DATE_RE.search(text)
    return match.group(1) if match else default


def _blocks(lines, start_line, marker):
    """Cut a run of lines into (heading, first_line, lines) blocks at `marker`."""
    blocks = []
    current, heading, first = [], "", start_line
    for offset, line in enumerate(lines):
        if line.startswith(marker):
            if any(item.strip() for item in current):
                blocks.append((heading, first, current))
            heading = line[len(marker):].strip()
            first = start_line + offset
            current = [line]
        else:
            current.append(line)
    if any(item.strip() for item in current):
        blocks.append((heading, first, current))
    return blocks


def _split_large(heading, first, lines, max_chars, default_date):
    """An oversized h2 falls back to its h3s, then to a hard cut."""
    out = []
    parts = _blocks(lines, first, "### ")
    if len(parts) > 1:
        for sub_heading, sub_first, sub_lines in parts:
            label = f"{heading} / {sub_heading}" if sub_heading else heading
            out.extend(_emit(label, sub_first, sub_lines, max_chars, default_date, split=False))
        return out
    return _emit(heading, first, lines, max_chars, default_date, split=False)


def _emit(heading, first, lines, max_chars, default_date, split=True):
    text = "\n".join(lines).strip("\n")
    if not text.strip():
        return []
    if len(text) <= max_chars:
        return [Section(heading=heading, line=first, text=text,
                        verified=_date_in(text, default_date))]
    if split:
        return _split_large(heading, first, lines, max_chars, default_date)
    out, buffer, buffer_line = [], [], first
    for offset, line in enumerate(lines):
        buffer.append(line)
        if sum(len(item) + 1 for item in buffer) >= max_chars:
            chunk = "\n".join(buffer).strip("\n")
            if chunk.strip():
                out.append(Section(heading=heading, line=buffer_line, text=chunk,
                                   verified=_date_in(chunk, default_date)))
            buffer, buffer_line = [], first + offset + 1
    chunk = "\n".join(buffer).strip("\n")
    if chunk.strip():
        out.append(Section(heading=heading, line=buffer_line, text=chunk,
                           verified=_date_in(chunk, default_date)))
    return out


def split(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list:
    """Sections of one document, in file order, frontmatter stripped."""
    front, body, offset = _frontmatter(text)
    default_date = _date_in(front)
    sections = []
    for heading, first, lines in _blocks(body.splitlines(), offset + 1, "## "):
        sections.extend(_emit(heading, first, lines, max_chars, default_date))
    return sections


__all__ = ["Section", "split", "title_of", "DEFAULT_MAX_CHARS"]
