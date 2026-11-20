"""Grouping of similar corrections: token overlap, kept only when it spans sessions."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

MIN_TOKENS = 3
JACCARD = 0.4
MIN_SESSIONS = 2
TOKEN_RE = re.compile(r"[a-záéíóúñü0-9_]+")
STOPWORDS = frozenset(
    "the and for are was were with this that from into how what where which who why when "
    "does did can should here there then than them they his her its our your about have has "
    "had not but you use too just now please also all any one out get got let make like".split()
    + "que los las una unos unas del por con para como pero mas más muy ese esa eso esto "
    "este esta son era ser hay nos les mis tus sus the".split()
)


@dataclass(frozen=True)
class Correction:
    session: str
    project: str
    ts: dt.datetime
    text: str


@dataclass
class Group:
    members: list
    representative: Correction
    sessions: int


def tokens(text: str) -> frozenset:
    words = TOKEN_RE.findall(text.lower())
    return frozenset(w for w in words if len(w) >= 3 and w not in STOPWORDS)


def _jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b)


def _centrality(i, idxs, sets, items):
    overlap = sum(_jaccard(sets[i], sets[j]) for j in idxs if j != i)
    return (overlap, -items[i].ts.timestamp())


def _clique_from(seed, left, near, sets, items):
    """Grow a clique from `seed`, preferring members from sessions it does not cover yet."""
    clique, seen = [seed], {items[seed].session}
    cand = near[seed] & left
    while cand:
        best = min(cand, key=lambda j: (items[j].session in seen,
                                        -_jaccard(sets[seed], sets[j]), j))
        clique.append(best)
        seen.add(items[best].session)
        cand = {j for j in cand if j != best and j in near[best]}
    return clique


def _cliques(usable, sets, items, threshold, min_sessions):
    """Greedy cliques: a member joins only when similar to every member already in.

    Cliques kept are those spanning enough sessions; the members of the rest go around again,
    because a pair split by a closer same-session neighbour can still be a real pair.
    """
    near = {i: set() for i in usable}
    for pos, i in enumerate(usable):
        for j in usable[pos + 1:]:
            if _jaccard(sets[i], sets[j]) >= threshold:
                near[i].add(j)
                near[j].add(i)
    pool, out = set(usable), []
    while pool:
        left, rest = set(pool), []
        while left:
            seed = max(left, key=lambda i: (len(near[i] & left), -i))
            clique = _clique_from(seed, left, near, sets, items)
            left -= set(clique)
            if len({items[i].session for i in clique}) >= min_sessions:
                out.append(clique)
            else:
                rest += clique
        if len(rest) == len(pool):
            break
        pool = set(rest)
    return out


def group(items, threshold: float = JACCARD, min_sessions: int = MIN_SESSIONS) -> list:
    """Mutually similar corrections, kept when they span sessions.

    Complete linkage on purpose: with connected components, A close to B and B close to C
    would also put A and C together without any real overlap.
    """
    sets = [tokens(c.text) for c in items]
    usable = [i for i, s in enumerate(sets) if len(s) >= MIN_TOKENS]
    out = []
    for idxs in _cliques(usable, sets, items, threshold, min_sessions):
        members = [items[i] for i in idxs]
        sessions = {m.session for m in members}
        if len(sessions) < min_sessions:
            continue
        # Most central member: highest summed overlap with the rest, earliest on a tie.
        best = max(idxs, key=lambda i: _centrality(i, idxs, sets, items))
        out.append(Group(members=members, representative=items[best], sessions=len(sessions)))
    out.sort(key=lambda g: (-g.sessions, -len(g.members), g.representative.ts))
    return out
