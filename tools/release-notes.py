#!/usr/bin/env python3
"""Print the changelog body of one release.

usage: release-notes.py VERSION [--changelog PATH]
Exit 0 with the body on stdout; 2 when the heading is missing, the body is empty or unreadable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def body(text: str, version: str) -> list[str] | None:
    prefix = f"## [{version}]"
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## ["):
            if inside:
                break
            inside = line.startswith(prefix)
            continue
        if inside:
            lines.append(line)
    if not inside:
        return None
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-notes.py")
    parser.add_argument("version")
    root = Path(__file__).resolve().parent.parent
    parser.add_argument("--changelog", default=str(root / "CHANGELOG.md"))
    args = parser.parse_args(argv)
    try:
        text = Path(args.changelog).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    lines = body(text, args.version)
    if lines is None:
        print(f"error: no '## [{args.version}]' heading in {args.changelog}", file=sys.stderr)
        return 2
    if not lines:
        print(f"error: '## [{args.version}]' has an empty body", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
