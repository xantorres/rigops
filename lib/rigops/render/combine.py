"""render_all: the union of every target, plus apply() and check()."""

from __future__ import annotations

import os
from pathlib import Path

from .agents_doc import render_agents
from .claude import is_ignored, render_claude
from .codex import render_codex
from .common import Rendered, merge
from .local import render_local
from .repos import render_repos

RENDERERS = {
    "claude": render_claude,
    "codex": render_codex,
    "local": render_local,
    "agents": render_agents,
    "repos": render_repos,
}


def render_all(source: Path, home: Path) -> Rendered:
    return merge(fn(source, home) for fn in RENDERERS.values())


def render_selected(names, source: Path, home: Path) -> Rendered:
    if "all" in names:
        return render_all(source, home)
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return merge(RENDERERS[name](source, home) for name in seen)


def apply(rendered: Rendered, home: Path, delete_stale: bool = True) -> list:
    log = []
    written = {path.resolve() for path in rendered.files}

    for path, content in rendered.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".{path.name}.tmp"
        tmp.write_bytes(content)
        os.replace(tmp, path)
        mode = rendered.modes.get(path)
        if mode is not None:
            os.chmod(path, mode)
        log.append(f"wrote {path}")

    if delete_stale:
        for managed_dir in dict.fromkeys(rendered.managed_dirs):
            if not managed_dir.is_dir():
                continue
            for existing in sorted(managed_dir.rglob("*"), reverse=True):
                if existing.is_dir() or existing.resolve() in written or is_ignored(existing):
                    continue
                existing.unlink()
                log.append(f"removed {existing}")
            for candidate in sorted(managed_dir.rglob("*"), reverse=True):
                if candidate.is_dir():
                    try:
                        candidate.rmdir()
                    except OSError:
                        pass

    return log


def check(rendered: Rendered) -> list:
    results = []
    seen = set()
    for path, content in sorted(rendered.files.items()):
        seen.add(path.resolve())
        if not path.exists():
            results.append({"path": str(path), "state": "missing"})
        elif path.read_bytes() != content:
            results.append({"path": str(path), "state": "differs"})

    for managed_dir in dict.fromkeys(rendered.managed_dirs):
        if not managed_dir.is_dir():
            continue
        for existing in sorted(managed_dir.rglob("*")):
            if existing.is_dir() or existing.resolve() in seen or is_ignored(existing):
                continue
            results.append({"path": str(existing), "state": "stale"})

    for path in rendered.skipped:
        results.append({"path": str(path), "state": "skipped"})

    return results
