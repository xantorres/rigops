"""Watermarks: warn into a backlog when a watched directory or file outgrows its caps."""

from __future__ import annotations

import glob
import math
import os
import sys
from datetime import datetime
from pathlib import Path

from rigops import backlog as rigops_backlog
from rigops import config as rigops_config


def _dir_files_at_depth1(d: Path) -> int:
    try:
        return sum(1 for e in d.iterdir() if e.is_file())
    except OSError:
        return 0


def _dir_size_kb(d: Path) -> int:
    total = 0
    for root, _dirnames, filenames in os.walk(d):
        for name in filenames:
            try:
                total += os.lstat(Path(root) / name).st_size
            except OSError:
                continue
    return total // 1024


def _file_watermark(f: Path, max_kb) -> tuple:
    """Judge a single-file match: only a size cap can apply to one file.

    Size rounds up so a reported KB figure never reads as being at the cap it
    just tripped.
    """
    size = f.stat().st_size
    return math.ceil(size / 1024), max_kb is not None and size / 1024 > max_kb


def check_watermarks(watermarks: list, backlog_path, areas: list, apply: bool) -> list:
    results = []
    backlog_p = Path(backlog_path)
    for wm in watermarks:
        name = wm.get("name", "")
        pattern = str(rigops_config.expand(wm.get("path", "")))
        max_files = wm.get("max_files")
        max_kb = wm.get("max_kb")
        area = wm.get("area", "memory")
        # The cap is a property of the watermark, not of any one match, so a
        # glob resolving to many files still says it once.
        warned_max_files = False
        for match in sorted(glob.glob(pattern)):
            d = Path(match)
            caps = []
            if d.is_dir():
                files = _dir_files_at_depth1(d)
                kb = _dir_size_kb(d)
                tripped = (max_files is not None and files > max_files) or (
                    max_kb is not None and kb > max_kb
                )
                measured = f"{files} files, {kb}KB"
                if max_files is not None:
                    caps.append(f"{max_files} files")
            elif d.is_file():
                files = 1
                if max_files is not None and not warned_max_files:
                    print(f"warning: watermark {name}: max_files ignored for file matches",
                          file=sys.stderr)
                    warned_max_files = True
                try:
                    kb, tripped = _file_watermark(d, max_kb)
                except OSError:
                    continue
                measured = f"{kb}KB"
            else:
                continue
            if max_kb is not None:
                caps.append(f"{max_kb}KB")
            target = None
            if not tripped:
                status = "ok"
            elif not apply:
                target = backlog_p
                status = "would-append"
            else:
                # One net around the whole append path: a failure here must
                # degrade to a reported status, never abort a run whose
                # deletions already happened.
                try:
                    target = backlog_p
                    text = target.read_text(encoding="utf-8") if target.exists() else ""
                    # Anchored on both sides: a bare path prefix would let an open
                    # line for a file inside a watched directory dedupe the
                    # directory's own line, and vice versa.
                    needle = f"watermark {d} tripped:"
                    if rigops_backlog.has_open_line(text, needle):
                        status = "deduped"
                    else:
                        today = datetime.now().strftime("%Y-%m-%d")
                        msg = (
                            f"watermark {d} tripped: {measured} "
                            f"(caps {'/'.join(caps)}). Fix: compaction pass."
                        )
                        rigops_backlog.add_line(target, area, msg, today, areas)
                        status = "appended"
                except (ValueError, OSError) as exc:
                    status = f"error: {exc}"
            results.append({
                "name": name, "dir": str(d), "files": files, "kb": kb, "tripped": tripped,
                "status": status, "backlog": str(target) if target else None,
            })
    return results
