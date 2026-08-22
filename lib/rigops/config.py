from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    "transcripts_dir": "~/.claude/projects",
    "ledger": {"watch_projects": {}},
    "fixed_tax": {"paths": [], "globs": []},
}


def config_path() -> Path:
    env_path = os.environ.get("RIGOPS_CONFIG")
    if env_path:
        return expand(env_path)
    xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return expand(Path(xdg) / "rigops" / "config.json")


def _deep_merge(base, overlay):
    merged = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load(path=None) -> dict:
    cfg_path = Path(path) if path is not None else config_path()
    if not cfg_path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"error: cannot read config {cfg_path}: {exc}") from exc
    try:
        overlay = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in config {cfg_path}: {exc}") from exc
    if not isinstance(overlay, dict):
        raise SystemExit(f"error: config {cfg_path} must be a JSON object")
    return _deep_merge(DEFAULTS, overlay)


def get(cfg, dotted, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def expand(p) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(str(p))))
