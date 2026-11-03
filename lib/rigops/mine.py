"""Repeated user corrections across sessions, as candidates for durable memory.

No model call and no write: the miner reads transcripts and the memory index and
prints a report for a human to judge.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from rigops import transcripts
from rigops.retrieval import roots, search

MAX_TEXT = 300
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
GENERIC_RULES = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"https?://\S+"), "[url]"),
    (re.compile(r"(?:/Users|/home)/[^/\s]+(?:/\S*)?"), "[path]"),
    (re.compile(r"\b(?:sk|ghp|gho|xox[a-z]|AKIA)[-_A-Za-z0-9]{12,}"), "[key]"),
    (re.compile(r"\b[A-Za-z]{2,10}-\d{2,6}\b"), "[ticket]"),
    (re.compile(r"\b[0-9a-fA-F]{24,}\b"), "[hex]"),
)
PUBLIC_PATTERNS = (Path(__file__).resolve().parent.parent.parent / "tools"
                   / "redaction-patterns.public.txt")
PRIVATE_PATTERNS = "~/.config/rigops/redaction-patterns.txt"


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
    return now - dt.timedelta(days=int(m.group(1)))


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
                except json.JSONDecodeError:
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
                    found = Correction(
                        session=obj.get("sessionId") or Path(path).stem,
                        project=project, ts=ts, text=first[:MAX_TEXT],
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


def _rule_patterns(files) -> list:
    compiled = []
    for path in files:
        try:
            lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            if not raw.strip() or raw.startswith("#"):
                continue
            flag, _, pattern = raw.partition(" ")
            if flag not in ("i", "s") or not pattern:
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE if flag == "i" else 0))
            except re.error:
                continue
    return compiled


def redact(text: str, registry=None, pattern_files=None) -> str:
    """Mask secrets, identifiers and everything the repo's redaction rules or the registry name."""
    for pattern, mask in GENERIC_RULES:
        text = pattern.sub(mask, text)
    if pattern_files is None:
        pattern_files = [PUBLIC_PATTERNS,
                         os.environ.get("RIGOPS_REDACTION_PATTERNS") or PRIVATE_PATTERNS]
    for pattern in _rule_patterns(pattern_files):
        text = pattern.sub("[redacted]", text)
    if registry is not None:
        for token in registry.client_tokens:
            text = re.sub(re.escape(token), "[client]", text, flags=re.IGNORECASE)
    return text


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
        result = search.query(registry, text, cwd=cwd, scopes=["memory"], top=1)
        for hit in result.hits:
            if best is None or hit.rank < best.rank:
                best = hit
    return best


def _label_realms(registry, g: Group) -> None:
    found = {realm_for_project(registry, m.project) for m in g.members}
    mapped = sorted(r for r in found if r)
    g.realms = tuple(mapped)
    g.realm = "+".join(mapped) if mapped else "unmapped"
    if None in found and mapped:
        g.realm += "+unmapped"


def run(since, transcripts_dir, registry, max_rank: float = DEDUPE_MAX_RANK) -> Report:
    harvest = collect(since, transcripts_dir)
    groups = group(harvest.kept)
    kept, discarded = [], []
    for g in groups:
        _label_realms(registry, g)
        realms = g.realms or ("personal",)
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
    }
    return Report(stats=stats, kept=kept, discarded=discarded)


def _entry_lines(registry, entry: Entry, n: int) -> list:
    g = entry.group
    text = redact(g.representative.text, registry)
    if entry.hit is None:
        nearest = "none"
    else:
        snippet = redact(entry.hit.snippet, registry)
        hit = entry.hit
        nearest = f"{hit.short_path}:{hit.line} score {hit.rank:.2f} | {snippet}"
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
        f"- corrections grouped: {s['remaining']}",
        f"- groups: {s['groups']}",
        f"- groups dropped by memory dedupe: {s['deduped']}",
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
    registry = roots.load(args.registry)
    report = run(since, args.transcripts_dir, registry)
    print(render(report, registry, args.since))
    return 0
