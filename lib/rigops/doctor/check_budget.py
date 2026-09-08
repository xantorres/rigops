"""The always-on instruction surface has a token ceiling.

Measured: the global law file, the rules that carry no ``paths:`` frontmatter
(path-scoped rules load only when a matching file is touched), and the memory
index. Tokens are bytes over four, the same arithmetic used to size the surface
in the audit.

Not measured, because neither is discoverable from this tree: the per-project
instruction file of whatever repository the session opens in, and the skill and
agent listings the runtime assembles. This ceiling therefore guards the part of
the surface that is authored here, and is a floor under the true session cost
rather than the whole of it.
"""

from __future__ import annotations

import re

from rigops import core

AREA = "docs"
CHECK = "budget"

DEFAULT_CEILING = 6000
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
PATHS_RE = re.compile(r"^paths:", re.MULTILINE)


def _is_always_on(path) -> bool:
    """A rule without ``paths:`` frontmatter loads in every session."""
    try:
        head = path.read_text(errors="replace")[:2000]
    except OSError:
        return False
    match = FRONTMATTER_RE.match(head)
    return not (match and PATHS_RE.search(match.group(1)))


def _size(path) -> int:
    """Size, or zero if the file went away between listing it and reading it."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _memory_index():
    """The largest per-project memory index.

    A session loads exactly one of them, the one belonging to the project it
    opens in, so the largest is the worst case rather than an approximation of it.
    """
    indexes = sorted(
        (core.CLAUDE_DIR / "projects").glob("*/memory/MEMORY.md"),
        key=_size, reverse=True,
    )
    return indexes[:1]


def members(cfg):
    files = []
    law = core.CLAUDE_DIR / "CLAUDE.md"
    if law.is_file():
        files.append(law)
    for rule in sorted((core.CLAUDE_DIR / "rules").rglob("*.md")):
        if _is_always_on(rule):
            files.append(rule)
    files.extend(_memory_index())
    return files


def _total(files):
    total = 0
    for path in files:
        try:
            total += len(path.read_bytes()) // 4
        except OSError:
            continue
    return total


def tokens(cfg):
    return _total(members(cfg))


def run(cfg):
    ceiling = core.cfg_get(cfg, "doctor.budget.always_on_tokens", DEFAULT_CEILING)
    files = members(cfg)
    total = _total(files)
    if total <= ceiling:
        return []
    biggest = sorted(files, key=_size, reverse=True)[:3]
    worst = ", ".join(str(p).replace(str(core.HOME), "~") for p in biggest)
    return [core.Finding(
        check=CHECK, area=AREA, key="always_on",
        symptom=f"always-on instruction surface is {total} tokens against a {ceiling} ceiling",
        evidence=f"{len(files)} files, largest: {worst}",
        fix=(
            "move rule text behind `paths:` frontmatter or into references, "
            "or raise the ceiling deliberately"
        ),
    )]
