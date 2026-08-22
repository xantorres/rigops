from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
from pathlib import Path

from rigops import config as rigops_config

CTX_BUCKETS = [
    ("<50k", 0, 50_000),
    ("50-100k", 50_000, 100_000),
    ("100-200k", 100_000, 200_000),
    ("200-300k", 200_000, 300_000),
    ("300-500k", 300_000, 500_000),
    (">500k", 500_000, float("inf")),
]


def default_transcripts_dir() -> Path:
    cfg = rigops_config.load()
    return rigops_config.expand(rigops_config.get(cfg, "transcripts_dir"))


def _resolve_dir(transcripts_dir) -> Path:
    if transcripts_dir is not None:
        return Path(transcripts_dir)
    return default_transcripts_dir()


def find_transcripts(transcripts_dir=None) -> list[str]:
    root = _resolve_dir(transcripts_dir)
    pattern1 = os.path.join(str(root), "*", "*.jsonl")
    pattern2 = os.path.join(str(root), "*", "**", "*.jsonl")
    files = set(glob.glob(pattern1))
    files |= set(glob.glob(pattern2, recursive=True))
    return sorted(files)


def project_label(path, transcripts_dir=None) -> str:
    root = _resolve_dir(transcripts_dir)
    rel = os.path.relpath(path, str(root))
    top = rel.split(os.sep)[0]
    # Suffix from the coding harness's worktree-session dir naming; not something this rig assigns.
    return re.sub(r"--claude-worktrees-.*$", "", top)


def parse_bound(value, now):
    if value is None:
        return None
    if value == "today":
        local_midnight = dt.datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local_midnight.astimezone(dt.timezone.utc)
    m = re.match(r"^-(\d+)d$", value)
    if m:
        return now - dt.timedelta(days=int(m.group(1)))
    s = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(s)
    if parsed.tzinfo is None:
        local_tz = dt.datetime.now().astimezone().tzinfo
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(dt.timezone.utc)


def parse_ts(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def eit(turn: dict) -> float:
    """EIT = input + 1.25 * cache_creation + 0.1 * cache_read (effective input tokens)."""
    return turn["input"] + 1.25 * turn["cache_creation"] + 0.1 * turn["cache_read"]


def ctx(turn: dict) -> float:
    return turn["input"] + turn["cache_creation"] + turn["cache_read"]


def collect_turns(since, until, transcripts_dir=None) -> list[dict]:
    """Collect one row per assistant turn from every transcript under transcripts_dir.

    A streamed turn can appear as multiple JSONL lines sharing the same
    message.id within one file, each line carrying one content block
    (text, thinking, or tool_use) rather than the full accumulated content.
    tool_use blocks are unioned across all lines for a given message.id;
    usage and timestamp are taken from the last line seen per message.id.
    Lines with no message.id are each treated as their own turn.
    """
    root = _resolve_dir(transcripts_dir)
    turns = []
    for path in find_transcripts(root):
        proj = project_label(path, root)
        entries = {}
        none_key = 0
        try:
            fh = open(path)
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
                if obj.get("type") != "assistant":
                    continue
                message = obj.get("message") or {}
                usage = message.get("usage")
                if not usage:
                    continue
                mid = message.get("id")
                if mid is None:
                    key = ("none", none_key)
                    none_key += 1
                else:
                    key = mid
                tool_names = [
                    item.get("name")
                    for item in (message.get("content") or [])
                    if isinstance(item, dict) and item.get("type") == "tool_use"
                ]
                entry = entries.get(key)
                if entry is None:
                    entries[key] = {
                        "ts_raw": obj.get("timestamp"),
                        "project": proj,
                        "session": obj.get("sessionId") or "unknown",
                        "model": message.get("model") or "unknown",
                        "usage": usage,
                        "tools": tool_names,
                    }
                else:
                    entry["ts_raw"] = obj.get("timestamp") or entry["ts_raw"]
                    entry["usage"] = usage
                    entry["tools"].extend(tool_names)

        for entry in entries.values():
            ts_raw = entry["ts_raw"]
            if not ts_raw:
                continue
            try:
                ts = parse_ts(ts_raw)
            except ValueError:
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts >= until:
                continue
            usage = entry["usage"]
            turns.append(
                {
                    "ts": ts,
                    "path": path,
                    "project": entry["project"],
                    "session": entry["session"],
                    "model": entry["model"],
                    "input": usage.get("input_tokens") or 0,
                    "cache_creation": usage.get("cache_creation_input_tokens") or 0,
                    "cache_read": usage.get("cache_read_input_tokens") or 0,
                    "output": usage.get("output_tokens") or 0,
                    "tools": entry["tools"],
                }
            )
    return turns


def percentile(sorted_vals, p) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = p * (len(sorted_vals) - 1)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)
