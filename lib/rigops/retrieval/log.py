"""Every query leaves a row, and the rows answer whether retrieval works.

Three numbers the phase is judged on live here: how long a query takes, how
often a query is followed by a raw scan of a tree the index already covers
(the honest sign that retrieval did not answer), and what a labelled probe
suite scores. The log is written outside `~/.claude`, which holds no unattended
state by rule.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

from rigops import core

from . import roots as roots_mod

LOG_NAME = "retrieval.jsonl"
SEARCH_RE = re.compile(r"\b(docs-search|rigops retrieval search|rigops-retrieval search)\b")
SCAN_RE = re.compile(r"(?:^|[|;&(]\s*)(rg|grep|ag)\b[^|;&]*", re.M)


def log_path() -> Path:
    env = os.environ.get("RIGOPS_RETRIEVAL_LOG")
    return Path(os.path.expanduser(env)) if env else roots_mod.state_path(LOG_NAME)


def record(result, mode="search", session_id=None, extra=None) -> dict:
    """Append one query to the log and return the row written."""
    row = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "session_id": session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
        "mode": mode,
        "cwd": result.cwd,
        "query": result.query,
        "realms": list(result.realms),
        "scopes": list(result.scopes),
        "repo": result.repo,
        "rows": len(result.hits),
        "tokens": result.tokens,
        "latency_ms": result.latency_ms,
        "silent_reason": result.silent_reason,
        "paths": [
            {"path": hit.short_path, "section": hit.section, "rank": round(hit.rank, 3),
             "realm": hit.realm, "scope": hit.scope}
            for hit in result.hits
        ],
    }
    if extra:
        row.update(extra)
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row


def read(since_days=7, path=None) -> list:
    path = Path(path or log_path())
    if not path.exists():
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            stamp = core.parse_ts(row["ts"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if stamp >= cutoff:
            rows.append(row)
    return rows


def _indexed_prefixes(registry) -> list:
    return [str(base) for _root, base in registry.anchors()]


def fallback_rg_rate(registry, since_days=7, window_turns=3) -> dict:
    """Share of retrieval calls followed by a raw scan of an indexed tree.

    Read from the transcripts rather than the retrieval log: the thing being
    measured is what the session did next, which only the transcript knows.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)
    prefixes = _indexed_prefixes(registry)
    home = str(Path.home())
    queries = fallbacks = 0
    for path in core.find_transcripts():
        if "/subagents/" in path:
            continue
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        pending = 0
        with fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "assistant":
                    continue
                try:
                    if core.parse_ts(obj.get("timestamp", "")) < cutoff:
                        continue
                except ValueError:
                    continue
                message = obj.get("message") or {}
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_use":
                        continue
                    name = item.get("name") or ""
                    payload = item.get("input") or {}
                    command = payload.get("command", "") if isinstance(payload, dict) else ""
                    target = ""
                    if isinstance(payload, dict):
                        target = str(payload.get("path") or payload.get("pattern") or "")
                    if SEARCH_RE.search(command):
                        queries += 1
                        pending = window_turns
                        continue
                    if pending <= 0:
                        continue
                    scanned = ""
                    if name == "Grep":
                        scanned = target or command
                    elif name == "Bash":
                        match = SCAN_RE.search(command)
                        scanned = match.group(0) if match else ""
                    if scanned:
                        expanded = scanned.replace("~", home)
                        if any(prefix in expanded for prefix in prefixes):
                            fallbacks += 1
                            pending = 0
                if pending > 0:
                    pending -= 1
    rate = round(fallbacks / queries, 3) if queries else None
    return {"queries": queries, "fallbacks": fallbacks, "fallback_rg_rate": rate,
            "window_days": since_days}


def latency(rows) -> dict:
    values = sorted(row.get("latency_ms", 0) for row in rows if row.get("rows"))
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "retrieval_latency_ms_p50": round(core.percentile(values, 0.5)),
        "retrieval_latency_ms_p95": round(core.percentile(values, 0.95)),
        "retrieved_tokens_p50": round(core.percentile(
            sorted(row.get("tokens", 0) for row in rows if row.get("rows")), 0.5)),
    }


def summary(registry, since_days=7) -> dict:
    rows = read(since_days)
    out = {"window_days": since_days, "queries_logged": len(rows)}
    out.update(latency(rows))
    prefetches = [row for row in rows if row.get("mode") == "prefetch"]
    if prefetches:
        silent = sum(1 for row in prefetches if not row.get("rows"))
        out["prefetch_silent_rate"] = round(silent / len(prefetches), 3)
        out["prefetches"] = len(prefetches)
    out.update(fallback_rg_rate(registry, since_days))
    return out


def write_summary(payload, name="retrieval-stats.jsonl") -> Path:
    path = roots_mod.state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(payload)
    row.setdefault("ts", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path


__all__ = ["record", "read", "summary", "latency", "fallback_rg_rate", "log_path",
           "write_summary", "core"]
