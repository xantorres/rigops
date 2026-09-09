"""Shared pieces for the render/* modules: the Rendered container, marker
text, and the small text-slicing helpers used to pull sections out of law.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FRONTMATTER_BLOCK_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)


@dataclass
class Rendered:
    files: dict = field(default_factory=dict)
    managed_dirs: list = field(default_factory=list)
    modes: dict = field(default_factory=dict)
    # Pointer repos whose directory doesn't exist: reported by check() as
    # "skipped", never written by apply(), never counted as a drift.
    skipped: list = field(default_factory=list)


def merge(parts) -> Rendered:
    files, modes, managed_dirs, skipped = {}, {}, [], []
    for part in parts:
        files.update(part.files)
        modes.update(part.modes)
        managed_dirs.extend(part.managed_dirs)
        skipped.extend(part.skipped)
    return Rendered(files=files, managed_dirs=managed_dirs, modes=modes, skipped=skipped)


def expand_home(value: str, home: Path) -> Path:
    """Expand a leading ``~`` against the render's own home argument, not the
    OS home -- the render pipeline is exercised against temp homes in tests."""
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value)


def display_src(path: Path, home: Path) -> str:
    try:
        rel = path.relative_to(home)
    except ValueError:
        return str(path)
    return "~" if str(rel) == "." else f"~/{rel}"


def marker_md(source_path: Path, home: Path) -> str:
    return (
        f"<!-- rendered by rigops render from {display_src(source_path, home)}; "
        "edit the source, not this file -->"
    )


def insert_marker(text: str, marker: str) -> str:
    match = FRONTMATTER_BLOCK_RE.match(text)
    if match:
        end = match.end()
        return text[:end] + marker + "\n" + text[end:]
    return marker + "\n" + text


def render_markdown_file(source_path: Path, home: Path) -> bytes:
    text = source_path.read_text()
    return insert_marker(text, marker_md(source_path, home)).encode()


def lines_of(text: str) -> list:
    return text.splitlines()


def section_lines(lines: list, heading: str, include_heading: bool) -> list:
    """Lines from ``heading`` up to (excluding) the next '## ' heading or EOF."""
    start = lines.index(heading)
    body_start = start if include_heading else start + 1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return lines[body_start:end]


def trim_blank(chunk: list) -> list:
    chunk = list(chunk)
    while chunk and not chunk[0].strip():
        chunk.pop(0)
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return chunk


def join_blocks(*blocks) -> str:
    parts = ["\n".join(trim_blank(b)) for b in blocks]
    return "\n\n".join(parts) + "\n"
