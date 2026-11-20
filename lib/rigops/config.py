from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from rigops import config_spec
from rigops.errors import RigopsError

DEFAULTS = config_spec.DEFAULTS


def config_path() -> Path:
    env_path = os.environ.get("RIGOPS_CONFIG")
    if env_path:
        return expand(env_path)
    xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return expand(Path(xdg) / "rigops" / "config.json")


def require_private(*paths) -> None:
    """Refuse a file that exists and, symlinks followed, group or others can write.

    Config and registry name the commands rigops runs and the paths it deletes,
    so whoever can write them can run code as this user.
    """
    for path in paths:
        try:
            mode = os.stat(path).st_mode
        except OSError:
            continue
        if mode & 0o022:
            raise RigopsError(
                f"{path} is writable by group or others; refusing to run commands "
                f"from it (chmod go-w {path})"
            )


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
    except (OSError, UnicodeDecodeError) as exc:
        raise RigopsError(f"cannot read config {cfg_path}: {exc}") from exc
    try:
        overlay = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RigopsError(f"invalid JSON in config {cfg_path}: {exc}") from exc
    if not isinstance(overlay, dict):
        raise RigopsError(f"config {cfg_path} must be a JSON object")
    merged = _deep_merge(DEFAULTS, overlay)
    config_spec.validate(merged)
    return merged


def unknown_keys(cfg) -> list:
    return config_spec.audit(cfg)


def get(cfg, dotted, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def expand(p) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(str(p))))
