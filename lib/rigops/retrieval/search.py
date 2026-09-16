"""Query the section index under a realm filter the caller cannot widen.

Two callers, two contracts. An explicit query may name its scopes and may ask
for everything. A prefetch may not: its realm and scopes come from the working
directory, it never reads `mixed`, and it returns nothing at all when the best
row is weak, because a silent miss costs less than a wrong claim in the prompt.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from rigops import core

HOME = str(Path.home())
# Weights chosen by sweeping the probe suite: the path and the store name carry
# more of "where does X live" than the prose does, and a title outranks a body
# line without drowning it.
RANK_SQL = "bm25(docs, 0,0,1.0,0,0,0,2.0,2.0,0,0,8.0)"
SELECT_SQL = f"""
SELECT path, line, section, title, realm, scope, repo, verified,
       snippet(docs, 2, '[', ']', '…', 18) AS snip, {RANK_SQL} AS rank
FROM docs WHERE docs MATCH ?
"""
RX_HYPHENATED = re.compile(r"\b([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)\b")
RX_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
RX_SEP_ROW = re.compile(r"\|[-\s]+\|[-\s|]*\|?")
RX_PIPES = re.compile(r"\s*\|\s*")
RX_SPACES = re.compile(r"\s+")
STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "with", "this", "that", "from", "into",
    "how", "what", "where", "which", "who", "why", "when", "does", "did", "can", "should",
    "here", "there", "then", "than", "them", "they", "his", "her", "its", "our", "your",
    "about", "have", "has", "had", "not", "but", "you", "use", "used", "using", "any",
    "get", "got", "one", "two", "all", "out", "off", "own", "per", "via", "run", "runs",
}
DEFAULT_PREFETCH = {"min_score": -4.0, "max_rows": 3, "budget": 400}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    section: str
    title: str
    realm: str
    scope: str
    repo: str
    verified: str
    snippet: str
    rank: float

    @property
    def short_path(self) -> str:
        return self.path.replace(HOME, "~", 1)

    def claim(self) -> str:
        where = f"{self.short_path}:{self.line}"
        label = f" §{self.section}" if self.section else ""
        stamp = f" (verified {self.verified})" if self.verified else ""
        return f"{where}{label}{stamp}: {self.snippet}"

    def compact(self) -> str:
        label = f" §{self.section}" if self.section else ""
        return f"{self.short_path}:{self.line} [{self.realm}/{self.scope}]{label}: {self.snippet}"

    def as_dict(self) -> dict:
        return {
            "path": self.short_path, "line": self.line, "section": self.section,
            "title": self.title, "realm": self.realm, "scope": self.scope,
            "repo": self.repo, "verified": self.verified, "snippet": self.snippet,
            "rank": round(self.rank, 3),
        }


@dataclass
class Result:
    hits: list = field(default_factory=list)
    realms: tuple = ()
    scopes: tuple = ()
    repo: str = ""
    cwd: str = ""
    query: str = ""
    latency_ms: int = 0
    tokens: int = 0
    silent_reason: str = ""


def clean(text: str, max_len: int = 180) -> str:
    text = RX_SEP_ROW.sub(" ", text)
    text = RX_PIPES.sub(" | ", text)
    text = RX_SPACES.sub(" ", text).strip().strip(" |")
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def normalize_query(q: str) -> str:
    """FTS5 reads a bare hyphen as NOT, so hyphenated words get quoted."""
    if '"' in q:
        parts = q.split('"')
        for i in range(0, len(parts), 2):
            parts[i] = RX_HYPHENATED.sub(lambda m: f'"{m.group(1)}"', parts[i])
        return '"'.join(parts)
    return RX_HYPHENATED.sub(lambda m: f'"{m.group(1)}"', q)


def _content_words(text: str, limit: int = 8) -> list:
    words = []
    for word in RX_WORD.findall(text):
        low = word.lower()
        if low in STOPWORDS or low in words:
            continue
        words.append(low)
    words.sort(key=len, reverse=True)
    return words[:limit]


def and_query(text: str, limit: int = 8) -> str:
    """Every content word, which is precise when the document really is the answer."""
    return " AND ".join(f'"{w}"' for w in _content_words(text, limit))


def loose_query(text: str, limit: int = 8) -> str:
    """A prompt is not a query: keep its content words, OR them, quote each."""
    return " OR ".join(f'"{w}"' for w in _content_words(text, limit))


def _prefetch_cfg(registry, key):
    return registry.raw.get("prefetch", {}).get(key, DEFAULT_PREFETCH[key])


def _plan(registry, cwd, scopes, prefetch):
    """(realms, scopes, repo, profile) for this call, or a reason to stay silent."""
    profile = registry.profile(cwd)
    asked = tuple(scopes or ())
    if "all" in asked:
        if prefetch:
            return None, None, None, None, "prefetch cannot widen to all"
        return tuple(registry.realms), tuple(registry.scopes), "", profile, ""
    if profile is None:
        return None, None, None, None, f"no root maps {cwd}"
    allowed = tuple(profile.scopes)
    use = tuple(s for s in asked if s in allowed) if asked else allowed
    if asked and not use:
        return None, None, None, None, f"scopes {','.join(asked)} not readable from {cwd}"
    realms = (profile.realm,) if prefetch else (profile.realm, "mixed")
    return realms, use, profile.repo, profile, ""


def query(registry, text, cwd=None, scopes=None, top=10, budget=None,
          prefetch=False, max_len=180, db_path=None) -> Result:
    cwd = str(cwd or os.getcwd())
    realms, use_scopes, repo, _profile, reason = _plan(registry, cwd, scopes, prefetch)
    result = Result(cwd=cwd, query=text)
    if reason:
        result.silent_reason = reason
        return result
    result.realms, result.scopes, result.repo = realms, use_scopes, repo or ""

    db = Path(db_path or registry.db_path)
    if not db.exists():
        result.silent_reason = f"index missing: {db}"
        return result

    match = loose_query(text) if prefetch else normalize_query(text)
    if not match.strip():
        result.silent_reason = "empty query"
        return result

    sql = SELECT_SQL + f" AND realm IN ({','.join('?' * len(realms))})"
    params = [match, *realms]
    if use_scopes:
        sql += f" AND scope IN ({','.join('?' * len(use_scopes))})"
        params.extend(use_scopes)
    if repo:
        sql += " AND (scope NOT IN ('repo','memory') OR repo = ?)"
        params.append(repo)
    sql += " ORDER BY rank LIMIT ?"
    params.append(max(top * 5, 25))

    started = time.time()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = []
    try:
        # A question is not an FTS expression: its punctuation is a syntax error and
        # the AND of every word matches only a document that mentions everything,
        # which on this corpus is one sprawling plan. So a rung is accepted only
        # when it returns a real choice of documents, not merely a non-empty one.
        ladder = [match] if prefetch else [match, and_query(text), loose_query(text)]
        attempts, seen_attempts = [], set()
        for attempt in ladder:
            if attempt.strip() and attempt not in seen_attempts:
                seen_attempts.add(attempt)
                attempts.append(attempt)
        wanted = min(max(top, 1), 3)
        for attempt in attempts:
            params[0] = attempt
            try:
                candidate = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                result.silent_reason = f"query error: {exc}"
                continue
            if len(candidate) > len(rows):
                rows = candidate
                result.silent_reason = ""
            if len({row["path"] for row in rows}) >= wanted:
                break
    finally:
        conn.close()
    result.latency_ms = int((time.time() - started) * 1000)

    min_score = _prefetch_cfg(registry, "min_score") if prefetch else None
    if prefetch:
        top = min(top, _prefetch_cfg(registry, "max_rows"))
        budget = budget or _prefetch_cfg(registry, "budget")

    seen, hits, tokens = set(), [], 0
    for row in rows:
        if row["path"] in seen:
            continue
        if min_score is not None and row["rank"] > min_score:
            continue
        hit = Hit(
            path=row["path"], line=int(row["line"] or 1), section=row["section"] or "",
            title=row["title"] or "", realm=row["realm"], scope=row["scope"],
            repo=row["repo"] or "", verified=row["verified"] or "",
            snippet=clean(row["snip"], max_len), rank=float(row["rank"]),
        )
        cost = max(1, len(hit.claim()) // 4)
        if budget and hits and tokens + cost > budget:
            break
        seen.add(row["path"])
        hits.append(hit)
        tokens += cost
        if len(hits) >= top:
            break
    result.hits, result.tokens = hits, tokens
    if not hits and not result.silent_reason:
        result.silent_reason = "below score threshold" if min_score is not None else "no match"
    return result


def render(result: Result, fmt: str = "compact") -> str:
    if fmt == "paths":
        return "\n".join(hit.short_path for hit in result.hits)
    if fmt == "claims":
        return "\n".join(hit.claim() for hit in result.hits)
    if fmt == "snippet":
        return "\n".join(f"{hit.short_path}:{hit.line} §{hit.section}\n  {hit.snippet}"
                         for hit in result.hits)
    return "\n".join(hit.compact() for hit in result.hits)


__all__ = ["query", "render", "Hit", "Result", "clean", "normalize_query", "loose_query",
           "and_query", "core"]
