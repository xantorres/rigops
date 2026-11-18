#!/usr/bin/env python3
"""Count dead absolute paths across every memory note, archives included.

    memory-path-sweep.py [--root DIR] [--no-exempt | --plain] [--secret-skip PREFIX]...

--no-exempt  every path match, cleaned, must exist literally (globs, templates and /tmp count).
--plain      skips paths holding $ { } ... < > or glob characters.
default      --plain plus brace expansion (any missing alternative is dead) and these classes,
             counted on the last line and never stat-ed unless noted:
  scope      note frontmatter `paths-scope: historical|remote`; any other value is itself a finding
  tmp        /tmp, /private/tmp, /var/folders, /private/var/folders
  cut        the match ends at a space and a sibling starts with "<name> " (check_pointers._path_ok)
  template   `YYYY` (unchecked), `<x>` (parent holds a sibling with the prefix), a glob that matches
  secret-skip  token, credential and env paths plus doctor.pointers.secret_skip and --secret-skip:
             matched on the path text before any stat, glob or listing
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core  # noqa: E402
from rigops.doctor import check_pointers as cp  # noqa: E402
from rigops.doctor.pointer_grammar import PATH_RE  # noqa: E402

EPHEMERAL = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
TOKENS = ("$", "{", "}", "...", "<", ">")
GLOBS = ("*", "?", "[")
SCOPES = ("historical", "remote")
SECRET_SEGMENTS = {".ssh", ".aws", ".gnupg"}
SECRET_PARTS = ("token", "secret", "credential")
TRAIL = ".,;:!?)]\"'`"
SCOPE_RE = re.compile(r"^paths-scope:\s*(\S*)\s*$", re.M)


def is_secret(path: str, prefixes) -> bool:
    expanded = str(core.expand(path))
    if any(expanded == p or expanded.startswith(p + "/") for p in prefixes):
        return True
    for seg in expanded.split("/"):
        low = seg.lower()
        if low.startswith((".env", ".netrc")) or seg in SECRET_SEGMENTS:
            return True
        if any(part in low for part in SECRET_PARTS):
            return True
    return False


def braces(path: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", path)
    if not m:
        return [path]
    return [x for alt in m.group(1).split(",")
            for x in braces(path[:m.start()] + alt + path[m.end():])]


def _exists(path: str) -> bool:
    if any(g in path for g in GLOBS):
        return bool(glob.glob(str(core.expand(path))))
    return core.expand(path).exists()


def _sibling(target, prefix) -> bool:
    parent = target.parent
    return parent.is_dir() and any(c.name.startswith(prefix) for c in parent.iterdir())


def _span(line: str, m) -> tuple[str, int]:
    raw, end = m.group(0), m.end()
    if "{" in raw and "}" not in raw:
        j = line.find("}", end)
        if j != -1:
            end = j + 1
            while end < len(line) and line[end] not in " \t`'\"),;|":
                end += 1
            raw = line[m.start():end]
    return raw, end


def scan(root, mode="default", secret_skip=()):
    prefixes = [str(core.expand(p)) for p in secret_skip]
    classes = dict.fromkeys(("scope", "tmp", "cut", "template", "secret-skip"), 0)
    found = []
    root = str(root)
    for f in sorted(glob.glob(root + "/*/memory/**/*.md", recursive=True)):
        text = Path(f).read_text(errors="replace")
        rel = f[len(root) + 1:]
        scoped = False
        if mode == "default":
            fm = re.match(r"---\n(.*?)\n---\n", text, re.S)
            value = SCOPE_RE.search(fm.group(1)) if fm else None
            if value and value.group(1) not in SCOPES:
                found.append((rel, 1, f"paths-scope: {value.group(1)}"))
            scoped = bool(value and value.group(1) in SCOPES)
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in PATH_RE.finditer(line):
                raw, end = _span(line, m) if mode == "default" else (m.group(0), m.end())
                p = raw.rstrip(TRAIL) if "{" in raw else cp._clean_path(raw)
                glob_like = any(g in raw for g in GLOBS)
                nxt = line[end:end + 1]
                dead = _classify(mode, p, raw, nxt, glob_like, scoped, prefixes, classes)
                if dead:
                    found.append((rel, lineno, p))
    return found, classes


def _classify(mode, p, raw, nxt, glob_like, scoped, prefixes, classes) -> bool:
    if mode == "plain":
        if any(t in p for t in TOKENS) or glob_like:
            return False
        return not core.expand(p).exists()
    if mode == "none":
        return not core.expand(p).exists()
    if scoped:
        classes["scope"] += 1
        return False
    if any(is_secret(t, prefixes) for t in braces(p)) or is_secret(raw.rstrip(TRAIL), prefixes):
        classes["secret-skip"] += 1
        return False
    target = core.expand(p)
    if "{" not in raw and ("YYYY" in p or nxt == "<" or glob_like):
        if "YYYY" in p:
            ok = True
        elif glob_like:
            ok = bool(glob.glob(str(core.expand(raw.rstrip(TRAIL)))))
        else:
            ok = _sibling(target, target.name)
        if ok:
            classes["template"] += 1
            return False
    if str(target).startswith(EPHEMERAL):
        classes["tmp"] += 1
        return False
    if all(_exists(t) for t in braces(p)):
        return False
    if nxt == " " and _sibling(target, target.name + " "):
        classes["cut"] += 1
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(core.expand("~/.claude/projects")))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--no-exempt", action="store_const", dest="mode", const="none")
    mode.add_argument("--plain", action="store_const", dest="mode", const="plain")
    ap.add_argument("--secret-skip", action="append", default=[])
    args = ap.parse_args(argv)
    skip = list(args.secret_skip)
    cfg_skip = core.cfg_get(core.load_config(), "doctor.pointers.secret_skip", [])
    skip += cfg_skip if isinstance(cfg_skip, list) else []
    mode_name = args.mode or "default"
    found, classes = scan(args.root, mode_name, skip)
    for rel, lineno, p in found:
        print(f"{rel}:{lineno}\t{p}")
    print("TOTAL", len(found), "exempt-by-class", classes if mode_name == "default" else "-")
    return 0


if __name__ == "__main__":
    sys.exit(main())
