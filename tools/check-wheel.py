#!/usr/bin/env python3
"""Check a built wheel against the repo: files, entry point, metadata.

usage: check-wheel.py WHEEL [REPO_ROOT]
Exit 0 when the wheel is sound, 1 with one line per problem, 2 on unreadable input.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ENTRY_POINT = "rigops = rigops.cli:main"
PYTHON_FLOOR = ">=3.11"


def _dist_info(names: list[str], leaf: str) -> str | None:
    for name in names:
        parts = name.split("/")
        if len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] == leaf:
            return name
    return None


def _console_scripts(text: str) -> list[str]:
    lines: list[str] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "console_scripts" and line:
            lines.append(line)
    return lines


def problems(wheel: zipfile.ZipFile, repo_root: Path) -> list[str]:
    names = wheel.namelist()
    found: list[str] = []

    tracked = subprocess.run(
        ["git", "ls-files", "lib/rigops"], cwd=repo_root, capture_output=True, text=True,
        check=True,
    ).stdout.splitlines()
    shipped = set(names)
    for rel in tracked:
        target = rel.removeprefix("lib/")
        if target not in shipped:
            found.append(f"missing from wheel: {target}")

    entry = _dist_info(names, "entry_points.txt")
    declared = _console_scripts(wheel.read(entry).decode("utf-8")) if entry else []
    if ENTRY_POINT not in declared:
        found.append(f"entry point not declared under [console_scripts]: {ENTRY_POINT}")

    meta = _dist_info(names, "METADATA")
    if meta is None:
        found.append("METADATA missing")
        return found
    headers = wheel.read(meta).decode("utf-8").split("\n\n", 1)[0].splitlines()
    if f"Requires-Python: {PYTHON_FLOOR}" not in headers:
        found.append(f"METADATA lacks Requires-Python: {PYTHON_FLOOR}")
    if any(h.startswith("Requires-Dist:") for h in headers):
        found.append("METADATA declares Requires-Dist; the package has no runtime dependencies")
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or len(args) > 2:
        print("usage: check-wheel.py WHEEL [REPO_ROOT]", file=sys.stderr)
        return 2
    repo_root = Path(args[1]) if len(args) == 2 else Path(__file__).resolve().parent.parent
    try:
        with zipfile.ZipFile(args[0]) as wheel:
            found = problems(wheel, repo_root)
    except (OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
