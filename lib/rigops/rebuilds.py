"""Cache rebuilds: main-thread turns that re-create a prefix the previous turn had cached.

A turn rebuilds when most of the context the previous turn sent (input, cache
creation and cache read together) fails to come back as cache read: at least
MIN_LOST tokens lost and under KEEP_RATIO of it read. Measuring against the
previous turn, not this one's cache creation, keeps a large tool result from
reading as a rebuild. Each rebuild is classed by what lay between the two
turns: a compaction boundary, a quick restart inside the short cache lifetime,
an idle gap past the long one, or other.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import Counter

from rigops import transcripts

MIN_LOST = 10_000
KEEP_RATIO = 0.5
QUICK_S = 300
IDLE_S = 3600
CLASSES = ("compaction", "quick", "idle", "other")
COMPACT = "system:compact_boundary"
# Present between almost every pair of turns, so they say nothing about the cause.
ROUTINE = frozenset({"user", "attachment", "api-request", "api-request-blob"})


def classify(gap_s: float, between: set) -> str:
    if COMPACT in between:
        return "compaction"
    if gap_s <= QUICK_S:
        return "quick"
    if gap_s > IDLE_S:
        return "idle"
    return "other"


def _rebuild(prev: dict, cur: dict, turn: dict, between: set) -> dict | None:
    lost = prev["ctx"] - turn["cache_read"]
    if lost < MIN_LOST or turn["cache_read"] >= KEEP_RATIO * prev["ctx"]:
        return None
    gap = (cur["ts"] - prev["ts"]).total_seconds()
    return {
        "ts": cur["ts"], "class": classify(gap, between), "gap_s": round(gap), "lost": lost,
        "eit": transcripts.eit(turn),
        "model_change": prev["model"] != cur["model"],
        "version_change": prev["version"] != cur["version"],
        "shape_change": bool(prev["shape"] and cur["shape"] and prev["shape"] != cur["shape"]),
        "before": sorted(between - ROUTINE),
    }


def scan(path: str, since=None, until=None) -> tuple[list, float]:
    """Rebuild turns in one transcript, plus the main-thread EIT it spent inside the window."""
    found, total = [], 0.0
    prev = None
    seen = set()
    between: set = set()
    version = shape = None
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return found, total
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            version = obj.get("version") or version
            if kind == "api-request":
                shape = obj.get("shapeHash") or shape
            if kind != "assistant":
                sub = obj.get("subtype")
                between.add(f"{kind}:{sub}" if sub else str(kind))
                continue
            message = obj.get("message") or {}
            usage, mid = message.get("usage"), message.get("id")
            if not usage or (mid is not None and mid in seen):
                continue
            seen.add(mid)
            try:
                ts = transcripts.parse_ts(obj.get("timestamp") or "")
            except ValueError:
                continue
            turn = {
                "input": usage.get("input_tokens") or 0,
                "cache_creation": usage.get("cache_creation_input_tokens") or 0,
                "cache_read": usage.get("cache_read_input_tokens") or 0,
            }
            cur = {"ts": ts, "ctx": transcripts.ctx(turn), "model": message.get("model"),
                   "version": version, "shape": shape}
            if (since is None or ts >= since) and (until is None or ts < until):
                total += transcripts.eit(turn)
                hit = _rebuild(prev, cur, turn, between) if prev is not None else None
                if hit:
                    found.append({**hit, "session": obj.get("sessionId") or "unknown"})
            prev, between = cur, set()
    return found, total


def collect(since, until, transcripts_dir=None) -> tuple[list, float]:
    """Rebuilds across main-session transcripts and the main-thread EIT they sit in."""
    root = transcripts._resolve_dir(transcripts_dir)
    floor = since.timestamp() if since is not None else None
    found, total = [], 0.0
    for path in transcripts.find_transcripts(root):
        if "/subagents/" in path:
            continue
        try:
            if floor is not None and os.stat(path).st_mtime < floor:
                continue
        except OSError:
            continue
        hits, spent = scan(path, since, until)
        project = transcripts.project_label(path, root)
        found.extend({**h, "project": project} for h in hits)
        total += spent
    return sorted(found, key=lambda h: h["ts"]), total


def _pct(part: float, whole: float):
    return round(part / whole * 100, 1) if whole else None


def summarize(found: list, total_eit: float) -> dict:
    classes = {}
    for cls in CLASSES:
        hits = [h for h in found if h["class"] == cls]
        if not hits:
            continue
        before = Counter(b for h in hits for b in h["before"])
        classes[cls] = {
            "count": len(hits),
            "eit_pct": _pct(sum(h["eit"] for h in hits), total_eit),
            "gap_p50_s": round(statistics.median(h["gap_s"] for h in hits)),
            "model_change": sum(h["model_change"] for h in hits),
            "version_change": sum(h["version_change"] for h in hits),
            "shape_change": sum(h["shape_change"] for h in hits),
            "before": before.most_common(3),
        }
    return {
        "count": len(found), "main_eit": round(total_eit),
        "eit_pct": _pct(sum(h["eit"] for h in found), total_eit), "classes": classes,
    }


def ledger_fields(found: list, total_eit: float) -> dict:
    fields = {"rebuild_eit_pct": _pct(sum(h["eit"] for h in found), total_eit)}
    for cls in CLASSES:
        spent = sum(h["eit"] for h in found if h["class"] == cls)
        fields[f"rebuild_{cls}_eit_pct"] = _pct(spent, total_eit)
    return fields
