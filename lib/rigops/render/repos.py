"""render_repos: registry/roots.json pointer_repos -> <repo>/AGENTS.md."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from .common import Rendered, expand_home

MARKER = (
    "<!-- rendered by rigops render repos; edit the renderer or "
    "~/rig/registry/roots.json, not this file -->"
)


def _find_realm(repo_path: Path, roots: list, home: Path) -> str:
    for root in roots:
        pattern = str(expand_home(root.get("path", ""), home))
        if fnmatch.fnmatch(str(repo_path), pattern) or fnmatch.fnmatch(
            str(repo_path.parent), pattern
        ):
            return root.get("realm", "")
    return ""


def _content(entry: str, name: str, realm: str) -> str:
    # Worded without vendor/model/assistant names on purpose: this file is committed
    # in public and client-adjacent repos, so the rule has to obey itself.
    lines = [
        MARKER,
        "# AGENTS.md",
        "",
        f"Entry point for agents that do not auto-load `{entry}`.",
        "## Source of truth",
        "",
        f"`{entry}` at the repo root holds this project's stack, commands and "
        "entrypoints. Read it in full at the start of every session, then follow it.",
        "## Law",
        "",
        "Zero tolerance, every committed artifact: source, comments, commit "
        "messages, pull request text, test names, branch names, file names.",
        "",
        "1. No vendor, model or assistant name and no machine-authorship marker "
        "in any committed artifact.",
        "2. No external tracker reference in code or commit subjects; keys "
        "belong in the commit body and the pull request description.",
        "3. Code self-documents: no comment by default; when one is needed, "
        "one line saying why, never what.",
        "4. Report with precision: extremely concise, grammar yields to concision.",
        "5. Human-facing text obeys hard length caps.",
        "6. Simplest solution first: name the existing capability checked "
        "before proposing new machinery.",
        "## Search",
        "",
        "Content: `rg`. Files: `fd`. Structural questions: the language "
        "server, else `rg`. Never plain `grep -r` or `find` over a large tree.",
        "## Realm",
        "",
        f"Knowledge here is realm `{realm}`, repo `{name}`. Retrieval is scoped "
        "by working directory: read nothing from another realm, and write "
        "repo-durable facts only to this repo's own memory.",
    ]
    return "\n".join(lines) + "\n"


def render_repos(source: Path, home: Path) -> Rendered:
    roots_path = source / "registry" / "roots.json"
    if not roots_path.is_file():
        return Rendered()

    data = json.loads(roots_path.read_text())
    roots = data.get("roots", [])
    files, skipped = {}, []

    for raw in data.get("pointer_repos", []):
        repo_path = expand_home(raw, home)
        target = repo_path / "AGENTS.md"
        if not repo_path.is_dir():
            skipped.append(target)
            continue
        entry = "CLAUDE.md" if (repo_path / "CLAUDE.md").is_file() else "README.md"
        content = _content(entry, repo_path.name, _find_realm(repo_path, roots, home))
        line_count = len(content.splitlines())
        if line_count > 25:
            raise ValueError(f"{target} is {line_count} lines, over the 25 line cap")
        files[target] = content.encode()

    return Rendered(files=files, skipped=skipped)
