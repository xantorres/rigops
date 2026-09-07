from __future__ import annotations

import fnmatch
import glob
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from rigops import backlog as rigops_backlog
from rigops import config as rigops_config

DELETE_ACTIONS = ("delete", "keep_newest_n")
_UNSAFE_BASES = {Path("/"), Path.home(), Path.home().parent}


def _safe_base(base: Path):
    """Gate every rule's base path before it's ever walked or matched.

    Deliberately simple and strict rather than clever: absolute, not a symlink,
    exists, and its realpath isn't the filesystem root, $HOME, or $HOME's parent.
    A rule failing this check never reaches matching, let alone deletion.
    """
    if not base.is_absolute():
        return False, "base is not an absolute path"
    if base.is_symlink():
        return False, "base is a symlink"
    if not base.exists():
        return False, "base does not exist"
    real = Path(os.path.realpath(base))
    if real in _UNSAFE_BASES:
        return False, f"refusing to operate on {real}"
    return True, None


def _safe_target(target: Path, base_real: str) -> bool:
    """target's parent (realpathed) must be base_real or under it.

    target itself is never resolved: a symlink target must be removed as the
    link, its destination left untouched.
    """
    parent_real = os.path.realpath(target.parent)
    return parent_real == base_real or parent_real.startswith(base_real + os.sep)


def _remove(target: Path) -> None:
    if target.is_symlink():
        os.remove(target)
    elif target.is_dir():
        shutil.rmtree(target)
    else:
        os.remove(target)


def _iter_matches(base: Path, rule: dict, now: float) -> list:
    """Entries under base matching rule's glob/dirs/only_empty/older_than_days/max_depth.

    Symlinks always count as files, matched by name, never followed: os.walk
    buckets a symlink-to-directory into dirnames, so those are moved into the
    file candidates instead of the directory candidates here.
    """
    if base.is_file():
        return [base]
    if not base.is_dir():
        return []

    glob_pat = rule.get("glob", "*")
    max_depth = rule.get("max_depth")
    want_dirs = bool(rule.get("dirs", False))
    only_empty = bool(rule.get("only_empty", False))
    older_than_days = rule.get("older_than_days")
    cutoff = now - older_than_days * 86400 if older_than_days is not None else None

    matches = []
    for root, dirnames, filenames in os.walk(base, followlinks=False):
        root_path = Path(root)
        depth = len(root_path.relative_to(base).parts)
        entry_depth = depth + 1

        if max_depth is not None and entry_depth > max_depth:
            dirnames[:] = []
            continue

        symlinked_dirs = [d for d in dirnames if (root_path / d).is_symlink()]
        if want_dirs:
            candidate_names = [d for d in dirnames if d not in symlinked_dirs]
        else:
            candidate_names = list(filenames) + symlinked_dirs

        if max_depth is not None and entry_depth == max_depth:
            dirnames[:] = []

        for name in candidate_names:
            if not fnmatch.fnmatch(name, glob_pat):
                continue
            entry = root_path / name
            if want_dirs and only_empty:
                try:
                    if any(entry.iterdir()):
                        continue
                except OSError:
                    continue
            if cutoff is not None:
                try:
                    mtime = os.lstat(entry).st_mtime
                except OSError:
                    continue
                if mtime >= cutoff:
                    continue
            matches.append(entry)
    return sorted(matches, key=str)


def _skipped_result(rule: dict, base, reason: str) -> dict:
    return {
        "name": rule.get("name", ""), "action": rule.get("action", ""), "path": str(base),
        "matched": [], "acted": 0, "overflow": False, "skipped": reason, "errors": [],
        "output": None,
    }


def _candidate_weight(p: Path) -> int:
    """Cap accounting counts files and dirs, not top-level entries: a directory
    candidate removed whole can carry many entries behind one queue slot."""
    if p.is_symlink() or not p.is_dir():
        return 1
    weight = 1
    for _root, dirnames, filenames in os.walk(p, followlinks=False):
        weight += len(filenames) + len(dirnames)
    return weight


def _delete_like_result(
    rule: dict, action: str, base: Path, candidates, apply, running_delete_total, max_delete
):
    base_real = os.path.realpath(base)
    weights = [_candidate_weight(p) for p in candidates]
    total_weight = sum(weights)
    overflow = total_weight > max_delete or running_delete_total + total_weight > max_delete
    matched = [str(p) for p in candidates]
    if overflow:
        return {
            "name": rule.get("name", ""), "action": action, "path": str(base), "matched": matched,
            "acted": 0, "overflow": True, "skipped": None, "errors": [], "output": None,
            "deleted_weight": 0,
        }
    acted = 0
    deleted_weight = 0
    errors = []
    if apply:
        for target, weight in zip(candidates, weights):
            if not os.path.lexists(target):
                # Already gone with an ancestor this same rule deleted.
                continue
            if not _safe_target(target, base_real):
                errors.append(f"{target}: safety re-check failed")
                continue
            try:
                _remove(target)
                acted += 1
                deleted_weight += weight
            except OSError as exc:
                errors.append(f"{target}: {exc}")
    return {
        "name": rule.get("name", ""), "action": action, "path": str(base), "matched": matched,
        "acted": acted, "overflow": False, "skipped": None, "errors": errors, "output": None,
        "deleted_weight": deleted_weight,
    }


def _run_delete_rule(rule, base, matches, apply, running_delete_total, max_delete) -> dict:
    base_real = os.path.realpath(base)
    candidates = [m for m in matches if m != base and _safe_target(m, base_real)]
    return _delete_like_result(
        rule, "delete", base, candidates, apply, running_delete_total, max_delete
    )


def _run_keep_newest_n_rule(rule, base, matches, apply, running_delete_total, max_delete) -> dict:
    keep = rule.get("keep", 3)

    def _mtime(p):
        try:
            return os.lstat(p).st_mtime
        except OSError:
            return 0.0

    ordered = sorted(matches, key=_mtime, reverse=True)
    base_real = os.path.realpath(base)
    to_delete = ordered[keep:]
    candidates = [m for m in to_delete if m != base and _safe_target(m, base_real)]
    return _delete_like_result(
        rule, "keep_newest_n", base, candidates, apply, running_delete_total, max_delete
    )


def _run_truncate_rule(rule, base, apply) -> dict:
    keep_lines = rule.get("keep_lines", 500)
    name = rule.get("name", "")
    if keep_lines <= 0:
        return {
            "name": name, "action": "truncate", "path": str(base), "matched": [], "acted": 0,
            "overflow": False, "skipped": "keep_lines must be positive", "errors": [],
            "output": None,
        }
    try:
        text = base.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        return {
            "name": name, "action": "truncate", "path": str(base), "matched": [], "acted": 0,
            "overflow": False, "skipped": None, "errors": [str(exc)], "output": None,
        }
    lines = text.splitlines(keepends=True)
    if len(lines) <= keep_lines:
        return {
            "name": name, "action": "truncate", "path": str(base), "matched": [], "acted": 0,
            "overflow": False, "skipped": None, "errors": [], "output": None,
        }
    matched = [str(base)]
    if not apply:
        return {
            "name": name, "action": "truncate", "path": str(base), "matched": matched, "acted": 0,
            "overflow": False, "skipped": None, "errors": [], "output": None,
        }
    kept = lines[-keep_lines:]
    acted = 0
    errors = []
    try:
        mode = os.stat(base).st_mode
        fd, tmp_path = tempfile.mkstemp(dir=str(base.parent))
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as fh:
            fh.writelines(kept)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, base)
        acted = 1
    except OSError as exc:
        errors.append(str(exc))
    return {
        "name": name, "action": "truncate", "path": str(base), "matched": matched, "acted": acted,
        "overflow": False, "skipped": None, "errors": errors, "output": None,
    }


def _run_report_rule(rule, base, matches) -> dict:
    return {
        "name": rule.get("name", ""), "action": "report", "path": str(base),
        "matched": [str(p) for p in matches], "acted": 0, "overflow": False, "skipped": None,
        "errors": [], "output": None,
    }


def _run_command_rule(rule: dict, apply: bool) -> dict:
    name = rule.get("name", "")
    path = rule.get("path", "")
    if not apply:
        return {
            "name": name, "action": "command", "path": path, "matched": [], "acted": 0,
            "overflow": False, "skipped": None, "errors": [], "output": None,
        }
    command = rule.get("command", "")
    timeout_s = rule.get("timeout_s", 120)
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", command], capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "name": name, "action": "command", "path": path, "matched": [], "acted": 0,
            "overflow": False, "skipped": None, "errors": [f"timed out after {timeout_s}s"],
            "output": None,
        }
    combined = (proc.stdout + proc.stderr).splitlines()
    errors = [] if proc.returncode == 0 else [f"exit {proc.returncode}"]
    return {
        "name": name, "action": "command", "path": path, "matched": [], "acted": 1,
        "overflow": False, "skipped": None, "errors": errors, "output": "\n".join(combined[-5:]),
    }


def _run_rule(
    rule: dict, apply: bool, max_delete: int, now: float, running_delete_total: int
) -> dict:
    action = rule.get("action", "report")
    if action == "command":
        return _run_command_rule(rule, apply)

    base = rigops_config.expand(rule.get("path", ""))
    safe, reason = _safe_base(base)
    if not safe:
        return _skipped_result(rule, base, reason)

    if action == "truncate":
        return _run_truncate_rule(rule, base, apply)

    if action in ("delete", "keep_newest_n") and base.is_file():
        return _skipped_result(rule, base, "base is a file (needs a directory)")

    matches = _iter_matches(base, rule, now)
    if action == "delete":
        return _run_delete_rule(rule, base, matches, apply, running_delete_total, max_delete)
    if action == "keep_newest_n":
        return _run_keep_newest_n_rule(rule, base, matches, apply, running_delete_total, max_delete)
    return _run_report_rule(rule, base, matches)


def run_rules(rules: list, apply: bool, max_delete: int, now: float | None = None) -> dict:
    if now is None:
        now = time.time()
    max_delete = 200 if max_delete is None else int(max_delete)
    results = []
    running_delete_total = 0
    for rule in rules:
        result = _run_rule(rule, apply, max_delete, now, running_delete_total)
        results.append(result)
        if result["action"] in DELETE_ACTIONS:
            running_delete_total += result.get("deleted_weight", 0)
    return {"rules": results, "total_deleted": running_delete_total}


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
            if not tripped:
                status = "ok"
            elif not apply:
                status = "would-append"
            else:
                # One net around the whole append path: a failure here must
                # degrade to a reported status, never abort a run whose
                # deletions already happened.
                try:
                    text = backlog_p.read_text(encoding="utf-8") if backlog_p.exists() else ""
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
                        rigops_backlog.add_line(backlog_p, area, msg, today, areas)
                        status = "appended"
                except (ValueError, OSError) as exc:
                    status = f"error: {exc}"
            results.append({
                "name": name, "dir": str(d), "files": files, "kb": kb, "tripped": tripped,
                "status": status,
            })
    return results
