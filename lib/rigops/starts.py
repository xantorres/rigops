"""What each session start sends: tool schemas and system prompt, read from transcripts.

Transcripts record the request shape the harness sent (``api-request-shape``
entries: the system blocks and the tool definitions). The first one in a file
is that session's start. A tool's size is ``len(json.dumps(tool))``, the same
measure for built-in and server tools, so sizes compare across surfaces.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import Counter, defaultdict

from rigops import transcripts

SHAPE_TYPE = "api-request-shape"
BUILTIN = "builtin"


surface = transcripts.surface


def tool_group(name: str) -> str:
    parts = name.split("__")
    if len(parts) >= 3 and parts[0] == "mcp":
        return f"mcp__{parts[1]}"
    return BUILTIN


def compose(shape: dict) -> dict:
    tools = [t for t in shape.get("tools") or [] if isinstance(t, dict)]
    sizes = {}
    for tool in tools:
        name = str(tool.get("name") or "?")
        sizes[name] = sizes.get(name, 0) + len(json.dumps(tool))
    groups: dict[str, dict] = defaultdict(lambda: {"tools": 0, "chars": 0})
    for name, size in sizes.items():
        group = groups[tool_group(name)]
        group["tools"] += 1
        group["chars"] += size
    system = shape.get("system") or []
    if isinstance(system, str):
        system_chars = len(system)
    else:
        system_chars = sum(
            len(b.get("text") or "") for b in system if isinstance(b, dict)
        )
    return {
        "tools": len(tools),
        "tool_chars": sum(sizes.values()),
        "system_chars": system_chars,
        "groups": dict(groups),
        "tool_sizes": sizes,
    }


def read_start(path: str) -> dict | None:
    """The first request shape in one transcript, with the surface it ran on."""
    shape_obj = None
    entrypoint = None
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            if shape_obj is None and SHAPE_TYPE in line:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict) and obj.get("type") == SHAPE_TYPE and isinstance(
                    obj.get("shape"), dict
                ):
                    shape_obj = obj
            if entrypoint is None and '"entrypoint"' in line:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict) and isinstance(obj.get("entrypoint"), str):
                    entrypoint = obj["entrypoint"]
            if shape_obj is not None and entrypoint is not None:
                break
    if shape_obj is None:
        return None
    try:
        ts = transcripts.parse_ts(shape_obj.get("timestamp") or "")
    except ValueError:
        return None
    return {
        "ts": ts,
        "session": shape_obj.get("sessionId") or "unknown",
        "version": shape_obj.get("version"),
        "surface": surface(entrypoint),
        **compose(shape_obj["shape"]),
    }


def collect_starts(since, until, transcripts_dir=None, subagents=False, session=None) -> list:
    """One start per transcript whose first request shape falls inside [since, until).

    ``session`` keeps only transcripts whose path names that session id prefix.
    """
    root = transcripts._resolve_dir(transcripts_dir)
    floor = since.timestamp() if since is not None else None
    found = []
    for path in transcripts.find_transcripts(root):
        is_sub = "/subagents/" in path
        if is_sub and not subagents:
            continue
        if session and f"{os.sep}{session}" not in path:
            continue
        try:
            if floor is not None and os.stat(path).st_mtime < floor:
                continue
        except OSError:
            continue
        start = read_start(path)
        if start is None:
            continue
        if since is not None and start["ts"] < since:
            continue
        if until is not None and start["ts"] >= until:
            continue
        found.append({
            **start, "path": path, "project": transcripts.project_label(path, root),
            "subagent": is_sub,
        })
    return sorted(found, key=lambda s: s["ts"])


def summarize(starts: list[dict]) -> dict:
    """Per-surface medians plus the tool groups that cost the most per start."""
    by_surface: dict[str, list[dict]] = defaultdict(list)
    for s in starts:
        by_surface[s["surface"]].append(s)
    surfaces = {}
    for name, group in sorted(by_surface.items()):
        surfaces[name] = {
            "starts": len(group),
            "tools_p50": statistics.median(s["tools"] for s in group),
            "tool_chars_p50": statistics.median(s["tool_chars"] for s in group),
            "system_chars_p50": statistics.median(s["system_chars"] for s in group),
        }
    group_chars: dict[str, list[int]] = defaultdict(list)
    for s in starts:
        for g, v in s["groups"].items():
            group_chars[g].append(v["chars"])
    groups = sorted(
        (
            {"group": g, "starts": len(v), "chars_p50": statistics.median(v)}
            for g, v in group_chars.items()
        ),
        key=lambda g: (-g["chars_p50"] * g["starts"], g["group"]),
    )
    return {"starts": len(starts), "surfaces": surfaces, "groups": groups}


def count_calls(since, until, transcripts_dir=None) -> Counter:
    """tool_use blocks per tool name inside the window, subagent transcripts included."""
    root = transcripts._resolve_dir(transcripts_dir)
    floor = since.timestamp() if since is not None else None
    calls: Counter = Counter()
    for path in transcripts.find_transcripts(root):
        try:
            if floor is not None and os.stat(path).st_mtime < floor:
                continue
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                    ts = transcripts.parse_ts(obj.get("timestamp") or "")
                except (json.JSONDecodeError, ValueError, AttributeError):
                    continue
                if obj.get("type") != "assistant":
                    continue
                if (since is not None and ts < since) or (until is not None and ts >= until):
                    continue
                for item in (obj.get("message") or {}).get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        calls[str(item.get("name") or "?")] += 1
    return calls


def _usage(name: str, sizes: list, sessions: int, calls: int, **extra) -> dict:
    return {
        "name": name, **extra, "sessions": sessions, "calls": calls,
        "per_100": round(calls / sessions * 100, 1) if sessions else 0.0,
        "chars_p50": round(statistics.median(sizes)), "carried": sum(sizes),
    }


def dead_weight(starts: list[dict], calls: Counter, max_per_100: float = 1.0) -> dict:
    """Tools and tool groups loaded at start yet called at most max_per_100 times per 100 loads.

    Ranked by carried chars: schema size summed over every start that loaded it.
    """
    tool_sizes: dict[str, list[int]] = defaultdict(list)
    group_sizes: dict[str, list[int]] = defaultdict(list)
    group_tools: dict[str, set] = defaultdict(set)
    for s in starts:
        for name, size in s["tool_sizes"].items():
            tool_sizes[name].append(size)
            group_tools[tool_group(name)].add(name)
        for g, v in s["groups"].items():
            group_sizes[g].append(v["chars"])
    tools = [
        _usage(n, sizes, len(sizes), calls.get(n, 0), group=tool_group(n))
        for n, sizes in tool_sizes.items()
    ]
    groups = [
        _usage(g, sizes, len(sizes), sum(calls.get(n, 0) for n in group_tools[g]),
               tools=len(group_tools[g]))
        for g, sizes in group_sizes.items()
    ]

    def ranked(rows):
        keep = [r for r in rows if r["per_100"] <= max_per_100]
        return sorted(keep, key=lambda r: (-r["carried"], r["name"]))

    return {"starts": len(starts), "max_per_100": max_per_100,
            "tools": ranked(tools), "groups": ranked(groups)}


def ledger_fields(starts: list[dict]) -> dict:
    """Medians over main-session starts that sent tools; tool-less headless runs swamp them."""
    main = [s for s in starts if not s.get("subagent") and s["tools"]]
    if not main:
        return {"start_tools": None, "start_tool_chars": None}
    return {
        "start_tools": round(statistics.median(s["tools"] for s in main)),
        "start_tool_chars": round(statistics.median(s["tool_chars"] for s in main)),
    }
