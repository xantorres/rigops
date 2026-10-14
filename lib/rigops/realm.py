"""Client realm lookup: the dir behind the nearest trusted `.claude/realm` link at or above a path.

A link is trusted only when the dir holding it is a non-personal registry root, so a cloned
repo cannot bring its own realm. Mirrors `scripts/realm.sh`, including `REALM_REGISTRY`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

GLOB_CUT = re.compile(r"/?[*?\[].*$")


def registry():
    """The registry that decides trust, or None when it is missing or unreadable."""
    # Lazy: roots imports core, and core imports this module.
    from rigops.retrieval import roots

    try:
        return roots.load(os.environ.get("REALM_REGISTRY") or roots.DEFAULT_REGISTRY)
    except (OSError, ValueError, AttributeError):
        return None


def _cut(path: str) -> str:
    return GLOB_CUT.sub("", os.path.expanduser(path))


def mounts(reg) -> list[str]:
    """Where a realm link may sit: each non-personal root's path, cut at the first glob char."""
    cuts = (_cut(root.path) for root in reg.roots
            if root.realm != "personal" and root.path)
    return [cut for cut in cuts if cut]


def find(start, reg=None) -> Path | None:
    """First trusted `<dir>/.claude/realm` directory walking up from `start`, resolved."""
    reg = reg or registry()
    if reg is None:
        return None
    trusted = set(mounts(reg))
    here = Path(os.path.abspath(start))
    for candidate in (here, *here.parents):
        link = candidate / ".claude" / "realm"
        if str(candidate) in trusted and link.is_dir():
            return link.resolve()
    return None


def from_registry(reg) -> list[Path]:
    """Every realm reachable from the registry's non-personal roots, deduped, in order."""
    found = []
    for mount in mounts(reg):
        realm = find(mount, reg)
        if realm is not None and realm not in found:
            found.append(realm)
    return found
