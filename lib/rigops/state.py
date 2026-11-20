from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path

from rigops.config import expand
from rigops.errors import RigopsError

VERSION = 1
MAX_ROW_BYTES = 1 << 20
PRIVATE_MODE = 0o700


class MalformedJsonl(ValueError):
    pass


def _xdg_root() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return expand(Path(xdg) / "rigops")


def state_root() -> Path:
    env_dir = os.environ.get("RIGOPS_STATE_DIR")
    return expand(env_dir) if env_dir else _xdg_root()


def state_dir() -> Path:
    path = state_root()
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_MODE)
    return path


def _ensure_parent(path: Path) -> None:
    """Create the parent of `path`; a directory of a state tree is made and kept 0700."""
    parent = path.parent
    current = os.path.realpath(parent)
    # The gate record and nudge state stay under XDG even when the override points elsewhere.
    roots = {os.path.realpath(state_root()), os.path.realpath(_xdg_root())}
    inside = [r for r in roots if current == r or current.startswith(r + os.sep)]
    root = min(inside, key=len, default=None)
    parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_MODE if root else 0o777)
    while root:
        with contextlib.suppress(OSError):
            if os.stat(current).st_mode & 0o077:
                os.chmod(current, PRIVATE_MODE)
        if current == root:
            break
        current = os.path.dirname(current)


@contextlib.contextmanager
def locked(path):
    """Exclusive lock on `<path>.lock`; blocks until the holder lets go."""
    path = Path(path)
    lock_path = path.with_name(path.name + ".lock")
    _ensure_parent(lock_path)
    with lock_path.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def atomic_write(path, content: str) -> None:
    """Replace `path` with `content` through a temp file of its own, so two
    writers of the same path never share a temp name."""
    path = Path(path)
    _ensure_parent(path)
    # mkstemp makes 0600: keep an existing file's mode, else the one open() would give.
    mode = path.stat().st_mode if path.exists() else 0o666 & ~_umask()
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def append_jsonl(path, obj) -> None:
    """Append one row as a single write on an O_APPEND descriptor, so concurrent
    appenders never interleave. A row over MAX_ROW_BYTES is refused, not cut."""
    path = Path(path)
    _ensure_parent(path)
    data = (json.dumps({"version": VERSION, **obj}, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_ROW_BYTES:
        raise RigopsError(f"row for {path} is {len(data)} bytes, over the {MAX_ROW_BYTES} cap")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        written = os.write(fd, data)
    finally:
        os.close(fd)
    if written != len(data):
        raise RigopsError(f"short write to {path}: {written} of {len(data)} bytes")


def row_version(row) -> int:
    version = row.get("version", 0) if isinstance(row, dict) else 0
    return version if isinstance(version, int) and not isinstance(version, bool) else 0


def _lines(path: Path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RigopsError(f"cannot read {path}: {exc.strerror or type(exc).__name__}") from exc
    for lineno, chunk in enumerate(raw.split(b"\n"), start=1):
        if chunk.strip():
            yield lineno, chunk


def read_jsonl(path) -> list:
    path = Path(path)
    rows = []
    for lineno, chunk in _lines(path):
        try:
            row = json.loads(chunk.decode("utf-8"))
        except ValueError as exc:
            raise MalformedJsonl(f"malformed JSON line in {path}:{lineno}: {exc}") from exc
        if row_version(row) > VERSION:
            raise MalformedJsonl(
                f"row in {path}:{lineno} has version {row['version']}, newer than this "
                f"release reads ({VERSION})")
        rows.append(row)
    return rows


def read_rows(path, valid=None) -> tuple:
    """(rows, skipped) for reports: torn rows, rows of a newer version and rows `valid`
    rejects are counted instead of raised. Rows come back sorted by date then ts."""
    rows, skipped = [], 0
    for _, chunk in _lines(Path(path)):
        try:
            row = json.loads(chunk.decode("utf-8"))
        except ValueError:
            skipped += 1
            continue
        if not isinstance(row, dict) or row_version(row) > VERSION or (valid and not valid(row)):
            skipped += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: (str(r.get("date", "")), str(r.get("ts", ""))))
    return rows, skipped
