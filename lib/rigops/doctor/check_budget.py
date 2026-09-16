"""The always-on instruction surface has a token ceiling.

Measured: the global law file, the rules that carry no ``paths:`` frontmatter
(path-scoped rules load only when a matching file is touched), the memory index,
and the instruction file of the largest registered repository. Tokens are bytes
over four, the same arithmetic used to size the surface in the audit.

A session loads one memory index and one project file, the ones belonging to the
repository it opens in, so the largest of each is the worst case rather than an
approximation of it. Still not measured, because the runtime assembles it rather
than this tree: the skill and agent listings. The ceiling is therefore a floor
under the true session cost, not the whole of it.
"""

from __future__ import annotations

import json
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
        head = core.read_text(path)[:2000]
    except OSError:
        return False
    match = FRONTMATTER_RE.match(head)
    return not (match and PATHS_RE.search(match.group(1)))


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


def _registered_repos(cfg):
    """The repositories the control plane knows about, from its own root list."""
    source = core.expand(core.cfg_get(cfg, "render.source", str(core.HOME / "rig")))
    try:
        data = json.loads(core.read_text(source / "registry" / "roots.json"))
    except (OSError, ValueError):
        return []
    return [core.expand(raw) for raw in data.get("pointer_repos", [])]


def _project_instructions(cfg):
    """The largest registered repository's own instruction file."""
    files = [repo / "CLAUDE.md" for repo in _registered_repos(cfg)]
    return sorted((f for f in files if f.is_file()), key=_size, reverse=True)[:1]


def members(cfg):
    files = []
    law = core.CLAUDE_DIR / "CLAUDE.md"
    if law.is_file():
        files.append(law)
    for rule in sorted((core.CLAUDE_DIR / "rules").rglob("*.md")):
        if _is_always_on(rule):
            files.append(rule)
    files.extend(_memory_index())
    files.extend(_project_instructions(cfg))
    return files


def _total(files):
    total = 0
    for path in files:
        try:
            total += len(core.read_bytes(path)) // 4
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
        extra={"paths": [str(p) for p in files]},
    )]
