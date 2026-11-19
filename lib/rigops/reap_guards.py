"""Config and run guards for reap: refuse malformed settings before anything is signalled."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from rigops.reap_procs import orphan_daemons

PRECIOUS_PATTERNS = (".env*", "*.pem", "*.key", "*.p12", "*.pfx",
                     "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
DEPENDENCY_DIRS = frozenset({"node_modules", "site-packages"})


def is_ignored_untracked(path: str, ignored_dirs: set[str],
                         ignored_prefixes: tuple[str, ...]) -> bool:
    """True when any path segment is generated noise (build output, type
    codegen): a file under an ignored directory is noise wherever it sits."""
    for part in path.split("/"):
        if part in ignored_dirs or part.startswith(ignored_prefixes):
            return True
    return False


def precious_path(paths: list[str], ignored_dirs: set[str],
                  ignored_prefixes: tuple[str, ...]) -> str | None:
    """First ignored file that looks like a secret or key, outside dependency
    and generated directories."""
    for path in paths:
        if not path or DEPENDENCY_DIRS & set(path.split("/")):
            continue
        if is_ignored_untracked(path, ignored_dirs, ignored_prefixes):
            continue
        if any(fnmatchcase(path.rsplit("/", 1)[-1], pat) for pat in PRECIOUS_PATTERNS):
            return path
    return None


def human(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def list_setting_error(key: str, value: object) -> str | None:
    if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
        return None
    return f"{key} in config must be a list of non-empty strings"


def bool_setting_error(key: str, value: object) -> str | None:
    if isinstance(value, bool):
        return None
    return f"{key} in config must be true or false"


def orphan_sweep(enabled: bool, roots: list[Path], snap: dict[int, dict],
                 cwds: dict[int, str], min_age_s: int) -> set[int]:
    """Orphan candidates under every root; empty unless the sweep is switched on."""
    found: set[int] = set()
    if not enabled:
        return found
    for root in roots:
        found |= orphan_daemons(snap, cwds, str(root.resolve()), min_age_s)
    return found
