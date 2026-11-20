"""Repeated user corrections across sessions, as candidates for durable memory.

No model call and no write: the miner reads transcripts and the memory index and
prints a report for a human to judge.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rigops import config as rigops_config
from rigops import transcripts
from rigops.detector import BUILTIN, Detector, first_nonblank_line
from rigops.mine_dedupe import (  # noqa: F401
    DEDUPE_MAX_RANK,
    check,
    dedupe_gate,
    nearest_memory,
    realm_cwds,
    realm_for_project,
)
from rigops.mine_group import (  # noqa: F401
    JACCARD,
    MIN_SESSIONS,
    MIN_TOKENS,
    Correction,
    Group,
    group,
    tokens,
)
from rigops.mine_redact import broken_rules, redact
from rigops.retrieval import roots, search  # noqa: F401

MAX_TEXT = 300
KEEP_TEXT = 1000


@dataclass
class Harvest:
    files: int = 0
    prompts: int = 0
    detected: int = 0
    kept: list = field(default_factory=list)
    filtered: list = field(default_factory=list)
    extra_added: int = 0


@dataclass
class Entry:
    group: Group
    hit: object = None
    duplicate: bool = False


@dataclass
class Report:
    stats: dict
    kept: list
    discarded: list
    broken_rules: int = 0


def parse_since(value: str, now: dt.datetime) -> dt.datetime:
    m = re.fullmatch(r"-?(\d+)d", value.strip())
    if not m:
        raise ValueError(f"--since expects a day count such as 90d, got {value!r}")
    try:
        return now - dt.timedelta(days=int(m.group(1)))
    except OverflowError:
        raise ValueError(f"--since {value!r} is out of range") from None


def _text_items(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def collect(since, transcripts_dir=None, until=None, detector=BUILTIN) -> Harvest:
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
                    first = first_nonblank_line(text)
                    if first.startswith(transcripts.SKIP_PREFIXES):
                        continue
                    if not prompted:
                        harvest.prompts += 1
                        prompted = True
                    found_by = detector.classify(text)
                    if found_by == "none":
                        continue
                    harvest.detected += 1
                    harvest.extra_added += found_by == "extra"
                    sid = obj.get("sessionId")
                    found = Correction(
                        session=sid if isinstance(sid, str) and sid else Path(path).stem,
                        project=project, ts=ts, text=first[:KEEP_TEXT],
                    )
                    bucket = harvest.filtered if found_by == "excluded" else harvest.kept
                    bucket.append(found)
                    break
    return harvest


def _label_realms(registry, g: Group) -> None:
    found = {realm_for_project(registry, m.project) for m in g.members}
    mapped = sorted(r for r in found if r)
    g.realms = tuple(mapped)
    g.realm = "+".join(mapped) if mapped else "unmapped"
    if None in found and mapped:
        g.realm += "+unmapped"


def run(since, transcripts_dir, registry, max_rank=None, min_sessions=MIN_SESSIONS,
        detector=BUILTIN) -> Report:
    max_rank = dedupe_gate(registry) if max_rank is None else max_rank
    index_ready = Path(registry.db_path).exists()
    harvest = collect(since, transcripts_dir, detector=detector)
    groups = group(harvest.kept, min_sessions=min_sessions)
    kept, discarded, skipped = [], [], 0
    for g in groups:
        _label_realms(registry, g)
        realms = g.realms or ("personal",)
        if not index_ready or not any(realm_cwds(registry, r) for r in realms):
            skipped += 1
            hit, duplicate = None, False
        else:
            hit, duplicate = check(registry, g.representative.text, realms, max_rank)
            # singles are listed with their nearest memory, never dropped, even at --min-sessions 1
            duplicate = duplicate and g.sessions >= MIN_SESSIONS
        (discarded if duplicate else kept).append(Entry(group=g, hit=hit, duplicate=duplicate))
    stats = {
        "files": harvest.files,
        "prompts": harvest.prompts,
        "detected": harvest.detected,
        "extra_added": harvest.extra_added,
        "excluded": len(harvest.filtered),
        "remaining": len(harvest.kept),
        "groups": len(groups),
        "deduped": len(discarded),
        "dedupe_skipped": skipped,
    }
    return Report(stats=stats, kept=kept, discarded=discarded, broken_rules=broken_rules())


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
        f"- of those, found only by extra correction patterns: {s['extra_added']}",
        f"- dropped by correction exclude patterns: {s['excluded']}",
        f"- corrections after the filter: {s['remaining']}",
        f"- groups: {s['groups']}",
        f"- groups dropped by memory dedupe: {s['deduped']}",
        f"- groups not checked against memory (no index or realm cwd): {s['dedupe_skipped']}",
        f"- groups kept: {len(report.kept)}",
        f"- redaction rules skipped as broken or unreadable: {report.broken_rules}", "",
        "## New candidates", "",
    ]
    for n, entry in enumerate(report.kept, 1):
        lines += _entry_lines(registry, entry, n)
    if not report.kept:
        lines += ["none", ""]
    lines += ["## Dropped as already in memory (groups only, single corrections are never dropped)",
              ""]
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
    ap.add_argument("--min-sessions", type=int, default=MIN_SESSIONS,
                    help="sessions a group must span; 1 lists single corrections, never dropped")
    ap.add_argument("--transcripts-dir", default=None)
    ap.add_argument("--registry", default=None)
    args = ap.parse_args(argv)
    if not args.dry_run:
        print("rigops mine: only --dry-run is supported; the miner never writes memory",
              file=sys.stderr)
        return 2
    detector = Detector.from_config(rigops_config.load())
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
    report = run(since, args.transcripts_dir, registry, min_sessions=args.min_sessions,
                 detector=detector)
    sys.stdout.reconfigure(errors="replace")
    print(render(report, registry, args.since))
    return 0
