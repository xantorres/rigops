"""Watermarks: warn into a backlog when a watched directory or file outgrows its caps."""

from __future__ import annotations

import glob
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

from rigops import backlog as rigops_backlog
from rigops import config as rigops_config
from rigops import realm as rigops_realm
from rigops.retrieval import roots as rigops_roots


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


CWD_SCAN_LINES = 50


def _session_cwd(project_dir: Path):
    """The cwd recorded in a project's newest transcript, or None."""
    try:
        newest = max(project_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, default=None)
        if newest is None:
            return None
        with newest.open(encoding="utf-8") as fh:
            for _ in range(CWD_SCAN_LINES):
                line = fh.readline(1 << 20)
                if not line:
                    break
                try:
                    cwd = json.loads(line).get("cwd")
                except (ValueError, AttributeError):
                    continue
                if isinstance(cwd, str) and cwd:
                    return cwd
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _project(match: Path, transcripts_dir):
    """Name of the transcripts project dir holding a match, or None."""
    if transcripts_dir is None:
        return None
    try:
        parts = match.relative_to(transcripts_dir).parts
    except ValueError:
        return None
    return parts[0] if parts else None


def _owner(match: Path, transcripts_dir):
    """What decides a match's realm: the session cwd under the transcripts dir, else the match."""
    project = _project(match, transcripts_dir)
    if project:
        cwd = _session_cwd(Path(transcripts_dir) / project)
        if cwd:
            return cwd
    return match


def _client_mount(match: Path, transcripts_dir, reg):
    """The most specific non-personal root whose slug prefixes the match's project dir name."""
    project = _project(match, transcripts_dir)
    if project is None or reg is None:
        return None
    best = None
    for mount in rigops_realm.mounts(reg):
        slug = rigops_roots.encode_project(mount)
        if (project == slug or project.startswith(slug + "-")) and (best is None or len(slug) > len(best[0])):
            best = (slug, mount)
    return best[1] if best else None


def _backlog_for(match: Path, transcripts_dir, default: Path, reg):
    """The backlog for a match; None when it is client-owned and no realm backlog resolves.

    Also None when the registry is unreadable: ownership cannot be judged.
    """
    if reg is None:
        return None
    realm = rigops_realm.find(_owner(match, transcripts_dir), reg)
    client = realm is not None
    if not client:
        mount = _client_mount(match, transcripts_dir, reg)
        if mount is not None:
            client = True
            realm = rigops_realm.find(mount, reg)
    if not client:
        return default
    target = realm / "plans" / "backlog.md" if realm is not None else None
    return target if target is not None and target.exists() else None


def check_watermarks(watermarks: list, backlog_path, areas: list, apply: bool,
                     transcripts_dir=None) -> list:
    results = []
    backlog_p = Path(backlog_path)
    if transcripts_dir is not None:
        transcripts_dir = Path(transcripts_dir)
    reg = rigops_realm.registry()
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
                try:
                    target = _backlog_for(d, transcripts_dir, backlog_p, reg)
                    status = "would-append" if target is not None else "unrouted"
                except OSError as exc:
                    status = f"error: {exc}"
            else:
                # One net around the whole append path: a failure here must
                # degrade to a reported status, never abort a run whose
                # deletions already happened.
                try:
                    target = _backlog_for(d, transcripts_dir, backlog_p, reg)
                    text = target.read_text(encoding="utf-8") if target and target.exists() else ""
                    # Anchored on both sides: a bare path prefix would let an open
                    # line for a file inside a watched directory dedupe the
                    # directory's own line, and vice versa.
                    needle = f"watermark {d} tripped:"
                    if target is None:
                        status = "unrouted"
                    elif rigops_backlog.has_open_line(text, needle):
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
