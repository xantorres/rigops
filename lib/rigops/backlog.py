from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

LINE_RE = re.compile(r"^- \[( |x)\] (\d{4}-\d{2}-\d{2}) ([a-z]+): (.+)$")

OPEN_HEADING = "## Open"


def parse(text: str) -> dict:
    """Walk a backlog file's lines, splitting them into items and malformed candidates.

    A candidate is any line starting with "- [" (checkbox syntax). Non-candidate
    lines (prose, indented text) are ignored entirely -- they're not part of the
    machine grammar.
    """
    items = []
    malformed = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("- ["):
            continue
        m = LINE_RE.match(line)
        if not m:
            reason = "does not match backlog grammar"
            malformed.append({"line_no": line_no, "line": line, "reason": reason})
            continue
        checkbox, date_str, area, rest = m.groups()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            malformed.append({"line_no": line_no, "line": line, "reason": "invalid date"})
            continue
        items.append({
            "line_no": line_no, "done": checkbox == "x", "date": date_str,
            "area": area, "rest": rest,
        })
    return {"items": items, "malformed": malformed}


def lint(text: str, areas: list) -> dict:
    parsed = parse(text)
    items = parsed["items"]
    unknown_area = [
        {"line_no": it["line_no"], "area": it["area"]} for it in items if it["area"] not in areas
    ]
    return {
        "open": sum(1 for it in items if not it["done"]),
        "done": sum(1 for it in items if it["done"]),
        "malformed": parsed["malformed"],
        "unknown_area": unknown_area,
        "ok": not parsed["malformed"] and not unknown_area,
    }


def format_line(date: str, area: str, text: str) -> str:
    """Build one open-item line. area format/membership is the caller's job (add_line)."""
    if not text:
        raise ValueError("text must not be empty")
    if any(ord(c) < 0x20 for c in text):
        raise ValueError("text must not contain control characters")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"invalid date: {date!r}") from exc
    return f"- [ ] {date} {area}: {text}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = path.stat().st_mode if path.exists() else None
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, encoding="utf-8", suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, path)
    # NamedTemporaryFile defaults to 0600; an existing file's mode must survive
    # the swap, and a fresh scaffold gets a normal file mode.
    os.chmod(path, original_mode if original_mode is not None else 0o644)


def add_line(path, area: str, text: str, date: str, areas: list) -> str:
    """Append (newest-first, under the '## Open' heading) a new open item. Returns the line."""
    if area not in areas:
        raise ValueError(f"unknown area {area!r}; must be one of {areas}")
    line = format_line(date, area, text)
    p = Path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        content = f"# Backlog\n\n{OPEN_HEADING}\n\n{line}\n\n## Done\n"
    else:
        lines = p.read_text(encoding="utf-8").splitlines()
        try:
            heading_idx = lines.index(OPEN_HEADING)
        except ValueError as exc:
            raise ValueError("no '## Open' heading") from exc
        insert_at = heading_idx + 1
        if insert_at < len(lines) and lines[insert_at] == "":
            insert_at += 1
        lines.insert(insert_at, line)
        content = "\n".join(lines) + "\n"
    _atomic_write(p, content)
    return line


def has_open_line(text: str, needle: str) -> bool:
    """True when an OPEN item's raw line contains needle (dedup hook for janitor watermarks)."""
    for line in text.splitlines():
        if not line.startswith("- ["):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        checkbox = m.group(1)
        if checkbox == " " and needle in line:
            return True
    return False
