#!/usr/bin/env python3
"""Check a release tag: versions agree; a final differs from its last candidate only by version.

usage: release-check.py TAG [--repo DIR]
Exit 0 pass, 1 with one `error:` line per problem, 2 when the check cannot run.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

INIT = "lib/rigops/__init__.py"
PLUGIN = "plugin/.claude-plugin/plugin.json"
MARKETPLACE = ".claude-plugin/marketplace.json"
CHANGELOG = "CHANGELOG.md"
VERSION_FILES = (INIT, PLUGIN, MARKETPLACE)
TAG_SHAPE = re.compile(r"^v\d+(\.\d+)*((a|b|rc)\d+)?$")
PRERELEASE = re.compile(r"(a|b|rc)\d+$")
VERSION_LINE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']\s*$""", re.MULTILINE)
HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class CannotRun(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CannotRun(str(exc)) from exc
    if proc.returncode != 0:
        raise CannotRun(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout


def show(repo: Path, tag: str, path: str) -> str:
    return git(repo, "show", f"{tag}:{path}")


def tree_versions(repo: Path, tag: str) -> tuple[str, str, str]:
    match = VERSION_LINE.search(show(repo, tag, INIT))
    if not match:
        raise CannotRun(f"{INIT} at {tag} has no __version__")
    try:
        plugin = json.loads(show(repo, tag, PLUGIN))["version"]
        market = next(p["version"] for p in json.loads(show(repo, tag, MARKETPLACE))["plugins"]
                      if p.get("name") == "rigops")
    except (ValueError, KeyError, StopIteration) as exc:
        raise CannotRun(f"cannot read plugin or marketplace version at {tag}: {exc}") from exc
    return match.group(1), plugin, market


def last_candidate(repo: Path, version: str) -> str | None:
    shape = re.compile(rf"^v{re.escape(version)}rc(\d+)$")
    names = git(repo, "tag", "--list", f"v{version}rc*").split()
    found = [(int(m.group(1)), name) for name in names if (m := shape.match(name))]
    return max(found)[1] if found else None


def diff_problems(repo: Path, candidate: str, tag: str, version: str) -> list[str]:
    candidate_version = candidate.removeprefix("v")
    removed: dict[str, list[tuple[int, str]]] = {}
    added: dict[str, list[tuple[int, str]]] = {}
    path = ""
    old_no = new_no = 0
    diff = git(repo, "diff", "--unified=0", "--no-color", "--no-ext-diff", candidate, tag)
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/") if line != "+++ /dev/null" else path
        elif line.startswith("--- "):
            path = line[4:].removeprefix("a/") if line != "--- /dev/null" else path
        elif m := HUNK.match(line):
            old_no, new_no = int(m.group(1)), int(m.group(2))
        elif line.startswith("-"):
            removed.setdefault(path, []).append((old_no, line[1:]))
            old_no += 1
        elif line.startswith("+"):
            added.setdefault(path, []).append((new_no, line[1:]))
            new_no += 1
    found: list[str] = []
    for file in sorted(set(removed) | set(added)):
        old, new = removed.get(file, []), added.get(file, [])
        if file == CHANGELOG:
            for number, text in old + new:
                if text.strip() and not text.startswith("## ["):
                    found.append(f"{file}:{number}: only a heading may change in a final")
        elif file in VERSION_FILES:
            for index in range(max(len(old), len(new))):
                if index >= len(old) or index >= len(new):
                    number = (new if index < len(new) else old)[index][0]
                    found.append(f"{file}:{number}: line without a counterpart")
                elif old[index][1].replace(candidate_version, version) != new[index][1]:
                    found.append(f"{file}:{new[index][0]}: changes more than the version string")
        else:
            number = (new or old)[0][0]
            found.append(f"{file}:{number}: not a version file or the changelog")
    return found


def check(repo: Path, tag: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    version, plugin, market = tree_versions(repo, tag)
    if tag != f"v{version}":
        errors.append(f"tag {tag} does not match __version__ {version}")
    if plugin != version:
        errors.append(f"plugin.json version {plugin} does not match __version__ {version}")
    if market != version:
        errors.append(f"marketplace.json version {market} does not match __version__ {version}")
    if errors or PRERELEASE.search(version):
        return errors, notes
    candidate = last_candidate(repo, version)
    if candidate is None:
        notes.append(f"note: no release candidate for {version}; diff check skipped")
        return errors, notes
    errors += diff_problems(repo, candidate, tag, version)
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-check.py")
    parser.add_argument("tag")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    if not TAG_SHAPE.match(args.tag):
        print(f"error: {args.tag} is not a version tag (v1.2.3, v1.2.3rc1)", file=sys.stderr)
        return 2
    try:
        errors, notes = check(Path(args.repo), args.tag)
    except CannotRun as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for note in notes:
        print(note)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
