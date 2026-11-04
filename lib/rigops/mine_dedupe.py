"""Realm labels and the memory dedupe: is a repeated correction already a stored fact."""

from __future__ import annotations

import copy
import sqlite3

from rigops.retrieval import roots, search

# bm25 rank is negative and lower is better; this is the registry prefetch gate default.
DEDUPE_MAX_RANK = -14.0


def _inherits(base, rest: str) -> bool:
    """Whether an encoded remainder below `base` is a hidden dir or a real child of it."""
    if not rest or rest.startswith("--"):
        return True
    try:
        children = [roots.encode_project(c.name) for c in base.iterdir() if c.is_dir()]
    except OSError:
        return False
    tail = rest[1:]
    return any(tail == c or tail.startswith(c + "-") for c in children)


def realm_for_project(registry, project: str):
    """Realm of the root whose encoded path is the longest prefix of a transcript dir name.

    The encoding turns "/" and "-" into the same character, so a sibling such as `repo-x`
    looks like a child of `repo`. A remainder inherits only when it is a hidden dir (the
    worktrees) or a directory that exists below the root.
    """
    best = None
    for root in registry.roots:
        for base in root.bases():
            encoded = roots.encode_project(base)
            if project != encoded and not project.startswith(encoded + "-"):
                continue
            if not _inherits(base, project[len(encoded):]):
                continue
            if best is None or len(encoded) > best[0]:
                best = (len(encoded), root.realm)
    return best[1] if best else None


def realm_cwds(registry, realm: str) -> list:
    """Working dirs whose memory scope covers every memory note of the realm."""
    eligible = [r for r in registry.roots if r.realm == realm and "memory" in r.cwd_scopes]
    for root in eligible:
        if not root.repo:
            for base in root.bases():
                return [base]
    withmem = {repo for _, r, repo in registry.memory_dirs() if r == realm}
    bases = [base for root in eligible for base in root.bases()]
    return [b for b in bases if b.name in withmem] or bases[:1]


def nearest_memory(registry, text: str, realms):
    """Best live memory hit for `text` across the given realms, or None."""
    best = None
    for realm in realms:
        for cwd in realm_cwds(registry, realm):
            try:
                result = search.query(registry, text, cwd=cwd, scopes=["memory"], top=5)
            except sqlite3.DatabaseError:
                continue
            live = [h for h in result.hits if search.ARCHIVE_MARKER not in h.path]
            if live and (best is None or live[0].rank < best.rank):
                best = live[0]
    return best


def dedupe_gate(registry) -> float:
    return float(registry.raw.get("prefetch", {}).get("min_score", DEDUPE_MAX_RANK))


def _gated(registry, max_rank: float):
    gated = copy.copy(registry)
    gated.raw = {**registry.raw, "prefetch": {**registry.raw.get("prefetch", {}),
                                              "min_score": max_rank}}
    return gated


def check(registry, text: str, realms, max_rank: float):
    """(hit, duplicate): the nearest note, and whether a prefetch would already surface it.

    Duplicate means the full prefetch gate passes: score at the cutoff plus the term overlap
    gate, so one shared word is not enough.
    """
    nearest = nearest_memory(registry, text, realms)
    gated, found = _gated(registry, max_rank), None
    for realm in realms:
        for cwd in realm_cwds(registry, realm):
            try:
                result = search.query(gated, text, cwd=cwd, scopes=["memory"], prefetch=True,
                                      top=1)
            except sqlite3.DatabaseError:
                continue
            if result.hits and (found is None or result.hits[0].rank < found.rank):
                found = result.hits[0]
    return (found or nearest), found is not None
