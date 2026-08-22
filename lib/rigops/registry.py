from __future__ import annotations

import math
import re

from rigops import config

DEFAULT_MAX_RUNTIME_H = 6.0

_ITEM_START_RE = re.compile(r"^-\s+id:\s*(\S+)\s*$")
_FIELD_RE = re.compile(r"^([a-z_]+):\s*(.*)$")


def parse_front_matter(text: str) -> dict:
    """Parse a rigops.v1 registry front-matter block.

    Hand-rolled, no YAML dependency: top-level `key: value` scalars (e.g.
    `schema:`) plus one `items:` list, each item a flat `key: value` block
    with an optional `notes: |` literal block. No nesting, anchors, or
    multi-document support -- the registry format never needs them.

    Returns `{}` when no `items:` line is found. Raises SystemExit naming
    the offending line for anything under `items:` that isn't a comment,
    a new `- id: ...` entry, or a `key: value` field of the current entry.
    """
    lines = text.splitlines()
    result: dict = {}
    try:
        items_line = next(i for i, line in enumerate(lines) if line.strip() == "items:")
    except StopIteration:
        return result
    try:
        end = next(
            i for i, line in enumerate(lines) if line.strip() == "---" and i > items_line
        )
    except StopIteration:
        end = len(lines)

    for raw in lines[:items_line]:
        stripped = raw.strip()
        if not stripped or stripped == "---" or stripped.startswith("#"):
            continue
        m = _FIELD_RE.match(stripped)
        if m:
            result[m.group(1)] = m.group(2).strip()

    items: list[dict] = []
    current: dict | None = None
    in_notes = False
    notes_indent = None
    notes_key = None
    notes_lines: list[str] = []
    for line_no, raw in enumerate(lines[items_line + 1:end], start=items_line + 2):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()

        if in_notes:
            if notes_indent is not None and indent > notes_indent:
                notes_lines.append(stripped)
                continue
            current[notes_key] = "\n".join(notes_lines)
            in_notes = False

        if stripped.startswith("# "):
            continue

        m = _ITEM_START_RE.match(stripped)
        if m:
            if current is not None:
                items.append(current)
            current = {"id": m.group(1)}
            continue

        m = _FIELD_RE.match(stripped)
        if m and current is not None:
            key, val = m.group(1), m.group(2).strip()
            if val == "|":
                in_notes = True
                notes_indent = indent
                notes_key = key
                notes_lines = []
                continue
            current[key] = val
            continue

        raise SystemExit(f"error: malformed registry line {line_no}: {raw!r}")

    if in_notes and current is not None:
        current[notes_key] = "\n".join(notes_lines)
    if current is not None:
        items.append(current)
    result["items"] = items
    return result


def _coerce_max_runtime_h(raw, item_id: str) -> float:
    if raw in (None, ""):
        return DEFAULT_MAX_RUNTIME_H
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"error: item {item_id!r}: invalid max_runtime_h {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(f"error: item {item_id!r}: invalid max_runtime_h {raw!r}")
    return value


def load_registry(path) -> list[dict]:
    """Load and parse a registry file, applying defaults for optional fields."""
    p = config.expand(path)
    if not p.exists():
        raise SystemExit(f"error: registry file not found: {p}")
    text = p.read_text(encoding="utf-8")
    front_matter = parse_front_matter(text)
    if "items" in front_matter and front_matter.get("schema") != "rigops.v1":
        got = front_matter.get("schema")
        raise SystemExit(f"error: registry schema must be 'rigops.v1', got: {got!r}")
    items = front_matter.get("items", [])
    seen_ids = set()
    for item in items:
        item_id = item.get("id", "")
        if item_id in seen_ids:
            raise SystemExit(f"error: duplicate registry item id: {item_id!r}")
        seen_ids.add(item_id)
        item.setdefault("plist", "")
        item.setdefault("notes", "")
        item["max_runtime_h"] = _coerce_max_runtime_h(item.get("max_runtime_h"), item_id)
    return items
