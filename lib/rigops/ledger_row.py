from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROW_VERSION = 1
REDEFINED_METRICS = {1: frozenset({"corrections", "denials"})}
CONFIG_SECTIONS = ("ledger", "fixed_tax", "sources", "transcripts_dir")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _home_relative(path: Path) -> str:
    try:
        return str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def fixed_tax_sha(entries: list) -> str | None:
    if not entries:
        return None
    lines = sorted(
        f"{_home_relative(path)}\0{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path, _ in entries
    )
    return _digest("\n".join(lines))


def config_sha(cfg: dict) -> str:
    sections = {name: cfg.get(name) for name in CONFIG_SECTIONS}
    return _digest(json.dumps(sections, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def row_version(row: dict) -> int:
    version = row.get("version", 0)
    return version if isinstance(version, int) and not isinstance(version, bool) else 0


def comparable(metric: str, old: dict, new: dict) -> bool:
    low, high = sorted((row_version(old), row_version(new)))
    return not any(
        metric in metrics for version, metrics in REDEFINED_METRICS.items() if low < version <= high
    )


def fingerprint_state(old: dict, new: dict, key: str) -> str:
    before, after = old.get(key), new.get(key)
    if not before or not after:
        return "unknown"
    return "unchanged" if before == after else "changed"


def fingerprints(old: dict, new: dict) -> dict:
    return {
        "always_loaded_files": fingerprint_state(old, new, "fixed_tax_sha"),
        "measurement_config": fingerprint_state(old, new, "config_sha"),
    }
