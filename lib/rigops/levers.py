from __future__ import annotations

import datetime as dt
import glob
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from rigops import config as rigops_config
from rigops import fmt, transcripts

WINDOW_DAYS = 7
SMOOTH_DAYS = 30
CHEAP_MODELS = ("sonnet", "haiku")

BASE_MD_COLUMNS = [
    ("date", "date"), ("label", "label"), ("turns", "turns"), ("sessions", "sessions"),
    ("eit_per_turn", "EIT/turn 7d"), ("eit_per_turn_30d", "EIT/turn 30d"),
    ("out_per_turn", "out/turn"), ("ctx_p50", "ctx p50"), ("over300k_pct", ">300k EIT %"),
    ("cache_hit_pct", "cache %"), ("turn1_p10", "turn-1 p10"), ("turn1_p50", "turn-1 p50"),
]
TAIL_MD_COLUMNS = [
    ("agent_per_100", "Agent/100"), ("cheap_model_eit_pct", "cheap-model EIT %"),
    ("fixed_tax_b", "fixed tax"),
]

MD_HEADER = """# Token ledger

One row per ledger run (`rigops ledger`). Window = the 7 full days before the row date.
Columns map to levers: turn-1 and ctx p50 track context diet and discipline (p10 is the
floor a bare harness plus a small first prompt costs, p50 is a typical session start);
Agent/100 tracks delegation rate; cheap-model EIT % tracks cheap-model routing; out/turn
tracks output verbosity; cache % tracks prompt-cache reuse; fixed tax tracks the
always-loaded config bytes named in `fixed_tax.paths`/`fixed_tax.globs`, the regrowth
gauge for the per-session fixed context cost.
Machine form of the same rows: `ledger.jsonl`. Re-run with `--show` to print this table.

"""


def md_columns(watch_projects: dict) -> list[tuple[str, str]]:
    per_project = [(f"turn1_{slug}", f"{slug} turn-1 p10") for slug in sorted(watch_projects)]
    return BASE_MD_COLUMNS + per_project + TAIL_MD_COLUMNS


def model_family(model: str) -> str:
    m = re.match(r"^claude-([a-z]+)", model or "")
    return m.group(1) if m else "other"


def local_midnight(day: dt.date) -> dt.datetime:
    local_tz = dt.datetime.now().astimezone().tzinfo
    return dt.datetime.combine(day, dt.time.min, tzinfo=local_tz).astimezone(dt.timezone.utc)


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def fixed_tax_entries(cfg=None) -> list[tuple[Path, int]]:
    """Per-file byte sizes counted toward the fixed context tax (always-loaded config).

    fixed_tax.paths are flat files, always counted. fixed_tax.globs are glob
    patterns; a matched file is excluded when its frontmatter declares
    paths: or globs: (it's path-scoped, not always-loaded).
    """
    cfg = cfg if cfg is not None else rigops_config.load()
    entries: list[tuple[Path, int]] = []
    seen: set[Path] = set()
    for raw in rigops_config.get(cfg, "fixed_tax.paths", []) or []:
        p = rigops_config.expand(raw)
        if not p.is_file():
            print(f"warning: fixed_tax.paths entry not found: {raw}", file=sys.stderr)
            continue
        resolved = p.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        entries.append((p, p.stat().st_size))
    for pattern in rigops_config.get(cfg, "fixed_tax.globs", []) or []:
        expanded = str(rigops_config.expand(pattern))
        for match in sorted(glob.glob(expanded, recursive=True)):
            p = Path(match)
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            parts = text.split("---", 2)
            fm = parts[1] if len(parts) >= 3 and not parts[0].strip() else ""
            if re.search(r"^\s*(paths|globs)\s*:", fm, re.M):
                continue
            resolved = p.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            entries.append((p, p.stat().st_size))
    return entries


def fixed_tax_bytes(cfg=None) -> int | None:
    entries = fixed_tax_entries(cfg)
    return sum(size for _, size in entries) if entries else None


def build_row(at: dt.date, label: str, cfg=None, transcripts_dir=None) -> dict:
    cfg = cfg if cfg is not None else rigops_config.load()
    watch_projects = rigops_config.get(cfg, "ledger.watch_projects", {}) or {}
    if transcripts_dir is None:
        transcripts_dir = rigops_config.expand(
            rigops_config.get(cfg, "transcripts_dir", rigops_config.DEFAULTS["transcripts_dir"])
        )
    until = local_midnight(at)
    since = until - dt.timedelta(days=WINDOW_DAYS)
    since_30 = until - dt.timedelta(days=SMOOTH_DAYS)

    # One pass over the corpus; first turns need history before the window, so no lower bound.
    turns = transcripts.collect_turns(None, until, transcripts_dir=transcripts_dir)
    main = [t for t in turns if "/subagents/" not in t.get("path", "")]

    first_by_session: dict[str, dict] = {}
    for t in main:
        cur = first_by_session.get(t["session"])
        if cur is None or t["ts"] < cur["ts"]:
            first_by_session[t["session"]] = t
    started = [t for t in first_by_session.values() if since <= t["ts"] < until]
    turn1_all = [transcripts.ctx(t) for t in started]
    turn1_by_project: dict[str, list[float]] = defaultdict(list)
    for t in started:
        rel_path = os.path.relpath(t.get("path", ""), transcripts_dir)
        for col, substr in watch_projects.items():
            if substr and substr in rel_path:
                turn1_by_project[col].append(transcripts.ctx(t))

    win = [t for t in turns if since <= t["ts"] < until]
    win_main = [t for t in win if "/subagents/" not in t.get("path", "")]
    win30 = [t for t in turns if since_30 <= t["ts"] < until]
    n = len(win)
    eits = [transcripts.eit(t) for t in win]
    ctxs = sorted(transcripts.ctx(t) for t in win)
    total_eit = sum(eits)
    total_ctx = sum(ctxs)
    cache_read = sum(t["cache_read"] for t in win)
    over300k = sum(e for e, c in zip(eits, (transcripts.ctx(t) for t in win)) if c > 300_000)
    agent_calls = sum(1 for t in win_main for name in t["tools"] if name == "Agent")
    eit_by_family: dict[str, float] = defaultdict(float)
    for t, e in zip(win, eits):
        eit_by_family[model_family(t["model"])] += e
    cheap_eit = sum(v for k, v in eit_by_family.items() if k in CHEAP_MODELS)
    eit30 = sum(transcripts.eit(t) for t in win30)

    row = {
        "date": at.isoformat(),
        "label": label,
        "window": {"since": since.isoformat(), "until": until.isoformat(), "days": WINDOW_DAYS},
        "turns": n,
        "sessions": len(started),
        "eit_total": round(total_eit),
        "eit_per_turn": round(total_eit / n) if n else None,
        "eit_per_turn_30d": round(eit30 / len(win30)) if win30 else None,
        "out_per_turn": round(sum(t["output"] for t in win) / n) if n else None,
        "ctx_p50": round(transcripts.percentile(ctxs, 0.5)) if ctxs else None,
        "ctx_mean": round(total_ctx / n) if n else None,
        "over300k_pct": round(over300k / total_eit * 100, 1) if total_eit else None,
        "cache_hit_pct": round(cache_read / total_ctx * 100, 1) if total_ctx else None,
        "turn1_p10": round(transcripts.percentile(sorted(turn1_all), 0.1)) if turn1_all else None,
        "turn1_p50": round(median(turn1_all)) if turn1_all else None,
        "agent_per_100": round(agent_calls / len(win_main) * 100, 2) if win_main else None,
        "cheap_model_eit_pct": round(cheap_eit / total_eit * 100, 1) if total_eit else None,
        "eit_pct_by_model": (
            {k: round(v / total_eit * 100, 1) for k, v in sorted(eit_by_family.items())}
            if total_eit else {}
        ),
    }
    for col in watch_projects:
        vals = turn1_by_project.get(col, [])
        row[f"turn1_{col}"] = round(transcripts.percentile(sorted(vals), 0.1)) if vals else None
        row[f"turn1_{col}_p50"] = round(median(vals)) if vals else None
        row[f"sessions_{col}"] = len(vals)
    row["fixed_tax_b"] = fixed_tax_bytes(cfg)
    return row


def format_cell(key: str, value) -> str:
    if value is None:
        return "-"
    if key in ("eit_per_turn", "eit_per_turn_30d", "ctx_p50") or key.startswith("turn1_"):
        return f"{value / 1000:.1f}k"
    if key == "fixed_tax_b":
        return fmt.human_bytes(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def pct_delta(new, old) -> str:
    if new is None or old in (None, 0):
        return "n/a"
    d = (new - old) / old * 100
    return f"{d:+.0f}%"


DIFF_SKIP_COLUMNS = {"date", "label", "notes"}


def diff_columns(old: dict, new: dict) -> list[str]:
    """Numeric columns present in either row; non-numeric metadata excluded."""
    cols = []
    for key in sorted(set(old) | set(new)):
        if key in DIFF_SKIP_COLUMNS:
            continue
        if any(
            isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
            for row in (old, new)
        ):
            cols.append(key)
    return cols


def compute_deltas(old: dict, new: dict) -> dict:
    deltas = {}
    for key in diff_columns(old, new):
        ov, nv = old.get(key), new.get(key)
        if ov is not None and nv is not None:
            delta = nv - ov
            if isinstance(ov, float) or isinstance(nv, float):
                delta = round(delta, 1)
        else:
            delta = None
        pct = round((nv - ov) / ov * 100, 1) if nv is not None and ov not in (None, 0) else None
        deltas[key] = {"old": ov, "new": nv, "delta": delta, "pct": pct}
    return deltas


def format_delta(key: str, delta) -> str:
    if delta is None:
        return "-"
    return format_cell(key, delta)


def format_pct(value) -> str:
    return f"{value:+.1f}%" if value is not None else "-"


def render_markdown(rows: list[dict], watch_projects: dict) -> str:
    columns = md_columns(watch_projects)
    lines = [MD_HEADER,
             "| " + " | ".join(h for _, h in columns) + " |\n",
             "|" + "|".join("---" for _ in columns) + "|\n"]
    lines += [
        "| " + " | ".join(format_cell(k, r.get(k)) for k, _ in columns) + " |\n" for r in rows
    ]
    return "".join(lines)


def summary_line(row: dict, prev: dict | None, base: dict | None, watch_projects: dict) -> str:
    parts = [f"ledger {row['date']}" + (f" [{row['label']}]" if row.get("label") else "") + ":"]
    e = row.get("eit_per_turn")
    deltas = []
    if prev and prev.get("date") != row["date"]:
        deltas.append(f"vs {prev['date']} {pct_delta(e, prev.get('eit_per_turn'))}")
    if base and base is not prev:
        deltas.append(f"vs {base['date']} {pct_delta(e, base.get('eit_per_turn'))}")
    delta_suffix = f" ({', '.join(deltas)})" if deltas else ""
    parts.append(f"EIT/turn {format_cell('eit_per_turn', e)}" + delta_suffix)
    parts.append(f"| ctx p50 {format_cell('ctx_p50', row.get('ctx_p50'))}")
    parts.append(f"| out/turn {format_cell('out_per_turn', row.get('out_per_turn'))}")
    turn1_line = (
        f"| turn-1 p10/p50 {format_cell('turn1_p10', row.get('turn1_p10'))}"
        f"/{format_cell('turn1_p50', row.get('turn1_p50'))}"
    )
    if watch_projects:
        per_project = ", ".join(
            f"{col} {format_cell(f'turn1_{col}', row.get(f'turn1_{col}'))}"
            for col in sorted(watch_projects)
        )
        turn1_line += f" (p10 {per_project})"
    parts.append(turn1_line)
    parts.append(f"| Agent/100 {format_cell('agent_per_100', row.get('agent_per_100'))}")
    cheap_pct = format_cell("cheap_model_eit_pct", row.get("cheap_model_eit_pct"))
    parts.append(f"| cheap-model EIT {cheap_pct}%")
    parts.append(f"| fixed tax {format_cell('fixed_tax_b', row.get('fixed_tax_b'))}")
    parts.append(f"| turns {row.get('turns')} sessions {row.get('sessions')}")
    return " ".join(parts)
