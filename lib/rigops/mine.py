"""Repeated user corrections across sessions, as candidates for durable memory.

No model call and no write: the miner reads transcripts and prints a report for a human to judge.
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
class Report:
    stats: dict
    kept: list
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


def run(since, transcripts_dir, min_sessions=MIN_SESSIONS, detector=BUILTIN) -> Report:
    harvest = collect(since, transcripts_dir, detector=detector)
    groups = group(harvest.kept, min_sessions=min_sessions)
    stats = {
        "files": harvest.files,
        "prompts": harvest.prompts,
        "detected": harvest.detected,
        "extra_added": harvest.extra_added,
        "excluded": len(harvest.filtered),
        "remaining": len(harvest.kept),
        "groups": len(groups),
    }
    return Report(stats=stats, kept=groups, broken_rules=broken_rules())


def render(report: Report, since_label: str) -> str:
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
        f"- groups kept: {len(report.kept)}",
        f"- redaction rules skipped as broken or unreadable: {report.broken_rules}", "",
        "## New candidates", "",
    ]
    for n, g in enumerate(report.kept, 1):
        text = redact(g.representative.text)[:MAX_TEXT]
        lines += [f"### {n}. {text}", f"- sessions: {g.sessions}", ""]
    if not report.kept:
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
    report = run(since, args.transcripts_dir, min_sessions=args.min_sessions,
                 detector=detector)
    sys.stdout.reconfigure(errors="replace")
    print(render(report, args.since))
    return 0
