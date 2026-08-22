from __future__ import annotations

import json
import os
from pathlib import Path

from rigops.config import expand


def state_dir() -> Path:
    env_dir = os.environ.get("RIGOPS_STATE_DIR")
    if env_dir:
        path = expand(env_dir)
    else:
        xdg = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
        path = expand(Path(xdg) / "rigops")
    path.mkdir(parents=True, exist_ok=True)
    return path


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
                raise SystemExit(f"error: malformed JSON line in {path}:{lineno}: {exc}") from exc
    return rows
