"""Repeated user corrections across sessions, as candidates for durable memory.

No model call and no write: the miner reads transcripts and the memory index and
prints a report for a human to judge.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from rigops import transcripts
from rigops.mine_redact import redact
from rigops.retrieval import roots, search

MAX_TEXT = 300
KEEP_TEXT = 1000
MIN_TOKENS = 3
JACCARD = 0.4
MIN_SESSIONS = 2
# bm25 rank is negative and lower is better; this is the registry prefetch gate default.
DEDUPE_MAX_RANK = -14.0
SKIP_PREFIXES = ("<system-reminder", "<command-", "Caveat:", "[Request interrupted")
# Spanish statements the shared pattern reads as a leading "no" ("no se", "no hay").
ES_STATEMENT_RE = re.compile(
    r"^\s*no\s+(?P<word>se|sé|es|hay|tengo|puedo|seria|sería|hace|voy|quiero|ire|iré|lo)\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[a-záéíóúñü0-9_]+")
STOPWORDS = frozenset(
    "the and for are was were with this that from into how what where which who why when "
    "does did can should here there then than them they his her its our your about have has "
    "had not but you use too just now please also all any one out get got let make like".split()
    + "que los las una unos unas del por con para como pero mas más muy ese esa eso esto "
    "este esta son era ser hay nos les mis tus sus the tio tío mate man".split()
)


@dataclass(frozen=True)
class Correction:
    session: str
    project: str
    ts: dt.datetime
    text: str


@dataclass
class Harvest:
    files: int = 0
    prompts: int = 0
    detected: int = 0
    kept: list = field(default_factory=list)
    filtered: list = field(default_factory=list)


@dataclass
class Group:
    members: list
    representative: Correction
    sessions: int
    realm: str = "unmapped"
    realms: tuple = ()


@dataclass
class Entry:
    group: Group
    hit: object = None


@dataclass
class Report:
    stats: dict
    kept: list
    discarded: list


def parse_since(value: str, now: dt.datetime) -> dt.datetime:
    m = re.fullmatch(r"-?(\d+)d", value.strip())
    if not m:
        raise ValueError(f"--since expects a day count such as 90d, got {value!r}")
    try:
        return now - dt.timedelta(days=int(m.group(1)))
    except OverflowError:
        raise ValueError(f"--since {value!r} is out of range") from None


def spanish_false_positive(text: str) -> bool:
    return bool(ES_STATEMENT_RE.match(transcripts._first_nonblank_line(text)))


def _text_items(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def collect(since, transcripts_dir=None, until=None) -> Harvest:
    """Corrections from human prompts, walking transcripts the way collect_friction does."""
    root = transcripts._resolve_dir(transcripts_dir)
    harvest = Harvest()
    for path in transcripts.find_transcripts(root):
        if "/subagents/" in path:
            continue
        harvest.files += 1
        project = transcripts.project_label(path, root)
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, RecursionError):
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "user":
                    continue
                ts_raw = obj.get("timestamp")
                if not isinstance(ts_raw, str) or not ts_raw:
                    continue
                try:
                    ts = transcripts.parse_ts(ts_raw)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
                if (since is not None and ts < since) or (until is not None and ts >= until):
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                prompted = False
                for item in _text_items(message.get("content")):
                    if not isinstance(item, dict) or item.get("type") != "text":
                        continue
                    text = item.get("text")
                    if not isinstance(text, str):
                        continue
                    first = transcripts._first_nonblank_line(text)
                    if first.startswith(SKIP_PREFIXES):
                        continue
                    if not prompted:
                        harvest.prompts += 1
                        prompted = True
                    if not transcripts.is_correction(text):
                        continue
                    harvest.detected += 1
                    sid = obj.get("sessionId")
                    found = Correction(
                        session=sid if isinstance(sid, str) and sid else Path(path).stem,
                        project=project, ts=ts, text=first[:KEEP_TEXT],
                    )
                    bucket = harvest.filtered if spanish_false_positive(text) else harvest.kept
                    bucket.append(found)
                    break
    return harvest


def tokens(text: str) -> frozenset:
    words = TOKEN_RE.findall(text.lower())
    return frozenset(w for w in words if len(w) >= 3 and w not in STOPWORDS)


def _jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b)


def _centrality(i, idxs, sets, items):
    overlap = sum(_jaccard(sets[i], sets[j]) for j in idxs if j != i)
    return (overlap, -items[i].ts.timestamp())


def group(items, threshold: float = JACCARD, min_sessions: int = MIN_SESSIONS) -> list:
    """Overlapping corrections as connected components, kept when they span sessions."""
    sets = [tokens(c.text) for c in items]
    usable = [i for i, s in enumerate(sets) if len(s) >= MIN_TOKENS]
    parent = {i: i for i in usable}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for pos, i in enumerate(usable):
        for j in usable[pos + 1:]:
            if _jaccard(sets[i], sets[j]) >= threshold:
                parent[find(i)] = find(j)
    comps = {}
    for i in usable:
        comps.setdefault(find(i), []).append(i)
    out = []
    for idxs in comps.values():
        members = [items[i] for i in idxs]
        sessions = {m.session for m in members}
        if len(sessions) < min_sessions:
            continue
        # Most central member: highest summed overlap with the rest, earliest on a tie.
        best = max(idxs, key=lambda i: _centrality(i, idxs, sets, items))
        out.append(Group(members=members, representative=items[best], sessions=len(sessions)))
    out.sort(key=lambda g: (-g.sessions, -len(g.members), g.representative.ts))
    return out


def realm_for_project(registry, project: str):
    """Realm of the root whose encoded path is the longest prefix of a transcript dir name."""
    best = None
    for root in registry.roots:
        for base in root.bases():
            encoded = roots.encode_project(base)
            if project == encoded or project.startswith(encoded + "-"):
                if best is None or len(encoded) > best[0]:
                    best = (len(encoded), root.realm)
    return best[1] if best else None


def _realm_cwd(registry, realm: str):
    fallback = None
    for root in registry.roots:
        if root.realm != realm or "memory" not in root.cwd_scopes:
            continue
        for base in root.bases():
            if not root.repo:
                return base
            fallback = fallback or base
    return fallback


def nearest_memory(registry, text: str, realms):
    """Best memory hit for `text` across the given realms, or None."""
    best = None
    for realm in realms:
        cwd = _realm_cwd(registry, realm)
        if cwd is None:
            continue
        try:
            result = search.query(registry, text, cwd=cwd, scopes=["memory"], top=5)
        except sqlite3.DatabaseError:
            continue
        live = [h for h in result.hits if search.ARCHIVE_MARKER not in h.path]
        if live and (best is None or live[0].rank < best.rank):
            best = live[0]
    return best


def _label_realms(registry, g: Group) -> None:
    found = {realm_for_project(registry, m.project) for m in g.members}
    mapped = sorted(r for r in found if r)
    g.realms = tuple(mapped)
    g.realm = "+".join(mapped) if mapped else "unmapped"
    if None in found and mapped:
        g.realm += "+unmapped"


def dedupe_gate(registry) -> float:
    return float(registry.raw.get("prefetch", {}).get("min_score", DEDUPE_MAX_RANK))


def run(since, transcripts_dir, registry, max_rank=None) -> Report:
    max_rank = dedupe_gate(registry) if max_rank is None else max_rank
    index_ready = Path(registry.db_path).exists()
    harvest = collect(since, transcripts_dir)
    groups = group(harvest.kept)
    kept, discarded, skipped = [], [], 0
    for g in groups:
        _label_realms(registry, g)
        realms = g.realms or ("personal",)
        if not index_ready or not any(_realm_cwd(registry, r) for r in realms):
            skipped += 1
            hit = None
        else:
            hit = nearest_memory(registry, g.representative.text, realms)
        entry = Entry(group=g, hit=hit)
        (discarded if hit is not None and hit.rank <= max_rank else kept).append(entry)
    stats = {
        "files": harvest.files,
        "prompts": harvest.prompts,
        "detected": harvest.detected,
        "spanish_filtered": len(harvest.filtered),
        "remaining": len(harvest.kept),
        "groups": len(groups),
        "deduped": len(discarded),
        "dedupe_skipped": skipped,
    }
    return Report(stats=stats, kept=kept, discarded=discarded)


def _entry_lines(registry, entry: Entry, n: int) -> list:
    g = entry.group
    text = redact(g.representative.text, registry)[:MAX_TEXT]
    if entry.hit is None:
        nearest = "none"
    else:
        hit = entry.hit
        snippet = redact(hit.snippet.replace("[", "").replace("]", ""), registry)
        where = redact(f"{hit.short_path}:{hit.line}", registry)
        nearest = f"{where} score {hit.rank:.2f} | {snippet}"
    return [f"### {n}. {text}", f"- sessions: {g.sessions}", f"- realm: {g.realm}",
            f"- nearest memory: {nearest}", ""]


def render(report: Report, registry, since_label: str) -> str:
    s = report.stats
    lines = [
        f"# rigops mine --dry-run --since {since_label}", "",
        f"- transcripts walked: {s['files']}",
        f"- human prompts: {s['prompts']}",
        f"- corrections detected: {s['detected']}",
        f"- filtered by the Spanish filter: {s['spanish_filtered']}",
        f"- corrections after the filter: {s['remaining']}",
        f"- groups: {s['groups']}",
        f"- groups dropped by memory dedupe: {s['deduped']}",
        f"- groups not checked against memory (no index or realm cwd): {s['dedupe_skipped']}",
        f"- groups kept: {len(report.kept)}", "",
        "## New candidates", "",
    ]
    for n, entry in enumerate(report.kept, 1):
        lines += _entry_lines(registry, entry, n)
    if not report.kept:
        lines += ["none", ""]
    lines += ["## Dropped as already in memory", ""]
    for n, entry in enumerate(report.discarded, 1):
        lines += _entry_lines(registry, entry, n)
    if not report.discarded:
        lines += ["none", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="rigops mine", description="Mine repeated corrections.")
    ap.add_argument("--dry-run", action="store_true", help="print the report; the only mode")
    ap.add_argument("--since", default="90d", help="window such as 90d (default 90d)")
    ap.add_argument("--transcripts-dir", default=None)
    ap.add_argument("--registry", default=None)
    args = ap.parse_args(argv)
    if not args.dry_run:
        print("rigops mine: only --dry-run is supported; the miner never writes memory",
              file=sys.stderr)
        return 2
    try:
        since = parse_since(args.since, dt.datetime.now(dt.timezone.utc))
    except ValueError as exc:
        print(f"rigops mine: {exc}", file=sys.stderr)
        return 2
    try:
        registry = roots.load(args.registry)
    except (OSError, ValueError, AttributeError) as exc:
        print(f"rigops mine: unreadable registry: {exc}", file=sys.stderr)
        return 2
    if not registry.path.is_file():
        print(f"rigops mine: registry not found: {registry.path}", file=sys.stderr)
        return 2
    report = run(since, args.transcripts_dir, registry)
    sys.stdout.reconfigure(errors="replace")
    print(render(report, registry, args.since))
    return 0
