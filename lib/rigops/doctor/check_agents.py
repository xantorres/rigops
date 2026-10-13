"""An agent whose prose says it never edits must not be granted an edit tool.

The prose is what the dispatcher reads when picking an agent, the ``tools`` line
is what the runtime enforces; when they disagree, the prose is a promise nothing
keeps. An absent ``tools`` line grants every tool, so it counts as granting edit.
Claims are matched on the agent's own sentences only: parenthetical asides and
fenced code are dropped, and a read-only qualifier on shell, git or similar is a
statement about that tool rather than about the agent.
"""

from __future__ import annotations

import re

from rigops import core

AREA = "docs"
CHECK = "agents"

CLAIM_RE = re.compile(
    r"\bread-only\b|\bnever\s+edits?\b|\bno\s+edit\s+tool\b"
    r"|\b(?:does\s+not|doesn't|must\s+not|cannot|can't)\s+edit\b",
    re.IGNORECASE,
)
NEGATED_RE = re.compile(r"(?:\bnot|\bisn't|\bnon-?|\bnever|\bno)\s*$", re.IGNORECASE)
SCOPED_RE = re.compile(
    r"\b(?:shell|bash|git|gh|sql|database|db|api|mcp|network|access)\b[^.;:]*\bread-only\b",
    re.IGNORECASE,
)
BLOCK_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
ASIDE_RE = re.compile(r"\([^()]*\)")
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n")


def _parse(text: str):
    """(fields, body) of an agent source; ``tools`` is a list, absent when unset."""
    match = BLOCK_RE.match(text)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "tools":
            fields[key] = [v.strip() for v in value.strip("[]").split(",") if v.strip()]
        else:
            fields[key] = value
    return fields, text[match.end():]


def _sentences(text: str):
    text = ASIDE_RE.sub(" ", FENCE_RE.sub(" ", text))
    return [s for s in SENTENCE_RE.split(text) if s.strip()]


def _claims_read_only(text: str) -> bool:
    for sentence in _sentences(text):
        if SCOPED_RE.search(sentence):
            continue
        for match in CLAIM_RE.finditer(sentence):
            if not NEGATED_RE.search(sentence[:match.start()]):
                return True
    return False


def _grants_edit(fields: dict) -> bool:
    if "tools" not in fields:
        return True
    return "edit" in fields["tools"]


def run(cfg):
    source = core.expand(core.cfg_get(cfg, "render.source", str(core.HOME / "rig")))
    findings = []
    for agent_file in sorted((source / "agents").glob("*.md")):
        try:
            fields, body = _parse(core.read_text(agent_file))
        except OSError:
            continue
        prose = f"{fields.get('purpose', '')}\n{body}"
        if _grants_edit(fields) and _claims_read_only(prose):
            name = fields.get("name", agent_file.stem)
            findings.append(core.Finding(
                check=CHECK, area=AREA, key=f"read_only_edit:{name}",
                symptom=f"{name}: claims read-only but grants edit",
                evidence=f"{agent_file} tools={fields.get('tools', '(all)')}",
                fix="drop `edit` from tools, or remove the read-only claim from the prose",
            ))
    return findings
