"""The always-on instruction surface has a token ceiling.

Measured: the global law file, the rules that carry no ``paths:`` frontmatter
(path-scoped rules load only when a matching file is touched), and the largest
memory index. Tokens are bytes over four, the same arithmetic used to size the
surface in the audit.

A session loads one memory index, the one belonging to the repository it opens
in, so the largest is the worst case rather than an approximation of it. Still
not measured, because the runtime assembles it rather than this tree: the skill
and agent listings. The ceiling is therefore a floor
under the true session cost, not the whole of it.
"""

from __future__ import annotations

from rigops import core

AREA = "docs"
CHECK = "budget"

DEFAULT_CEILING = 6000


def _size(path) -> int:
    """Size, or zero if the file went away between listing it and reading it."""
    try:
        return core.size(path)
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


def _counts(rule) -> bool:
    """True for an always-on rule, and for one that cannot be read to tell."""
    try:
        core.read_bytes(rule)
    except OSError:
        return True
    return core.is_always_on(rule)


def members():
    files = []
    law = core.CLAUDE_DIR / "CLAUDE.md"
    if law.is_file():
        files.append(law)
    for rule in sorted((core.CLAUDE_DIR / "rules").rglob("*.md")):
        if _counts(rule):
            files.append(rule)
    files.extend(_memory_index())
    return files


def _measure(files):
    """(tokens, unreadable files): a member that cannot be read is named, never skipped."""
    total, unreadable = 0, []
    for path in files:
        try:
            total += len(core.read_bytes(path)) // 4
        except OSError:
            unreadable.append(path)
    return total, unreadable


def tokens(cfg):
    return _measure(members())[0]


def _unreadable(files) -> core.Finding:
    names = ", ".join(core.short(p) for p in files)
    return core.Finding(
        check=CHECK, area=AREA, key="unreadable", severity="error",
        symptom=f"{len(files)} always-on file(s) could not be read, so the budget is not measured",
        evidence=names, fix="make the file readable or remove it",
        extra={"paths": [str(p) for p in files]},
    )


def run(cfg):
    ceiling = core.cfg_get(cfg, "doctor.budget.always_on_tokens", DEFAULT_CEILING)
    files = members()
    total, unreadable = _measure(files)
    found = [_unreadable(unreadable)] if unreadable else []
    if total <= ceiling:
        return found
    biggest = sorted(files, key=_size, reverse=True)[:3]
    worst = ", ".join(core.short(p) for p in biggest)
    return found + [core.Finding(
        check=CHECK, area=AREA, key="always_on",
        symptom=f"always-on instruction surface is {total} tokens against a {ceiling} ceiling",
        evidence=f"{len(files)} files, largest: {worst}",
        fix=(
            "move rule text behind `paths:` frontmatter or into references, "
            "or raise the ceiling deliberately"
        ),
        extra={"paths": [str(p) for p in files]},
    )]
