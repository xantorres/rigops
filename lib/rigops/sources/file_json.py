from __future__ import annotations

import json

from rigops import config as rigops_config


def read_json(path) -> object | None:
    """Read and parse path as JSON. Missing, unreadable, or invalid -> None."""
    p = rigops_config.expand(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
