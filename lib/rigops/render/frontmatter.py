"""A tiny YAML-shaped frontmatter reader for the ~/rig agent source files.

Only what agents/<name>.md actually uses: scalar keys and one list field
(``tools``, written either bracketed or as a bare comma list). No yaml
dependency, so no need for the rest of the grammar.
"""

from __future__ import annotations

import re

BLOCK_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def parse(text: str) -> tuple:
    """Return (fields, body). fields is {} and body is the whole text when
    there is no frontmatter block."""
    match = BLOCK_RE.match(text)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fields[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif key == "tools" and "," in value:
            fields[key] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            fields[key] = value
    return fields, text[match.end():]
