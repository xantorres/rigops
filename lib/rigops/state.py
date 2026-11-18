from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from rigops.config import expand


class MalformedJsonl(ValueError):
    pass


def state_dir() -> Path:
    env_dir = os.environ.get("RIGOPS_STATE_DIR")
    if env_dir:
        path = expand(env_dir)
    else:
        xdg = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
        path = expand(Path(xdg) / "rigops")
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path, content: str) -> None:
    """Replace `path` with `content` through a temp file of its own, so two
    writers of the same path never share a temp name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_jsonl(path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise MalformedJsonl(f"malformed JSON line in {path}:{lineno}: {exc}") from exc
    return rows
