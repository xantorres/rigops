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
    r"\bread-only\b|\bnever\s+(?:edits?|writes?)(?:\s*/\s*(?:edit|write))?\b|\bno\s+edit\s+tool\b"
    r"|\b(?:does\s+not|doesn't|must\s+not|cannot|can't|do\s+not|don't)\s+(?:edit|write)\b",
    re.IGNORECASE,
)
SCOPE_AFTER_RE = re.compile(r"\s+(?:\S+\s+){0,3}?(?:outside|except|in|under)\b", re.IGNORECASE)
NEGATED_RE = re.compile(r"(?:\bnot|\bisn't|\bnon-?|\bnever|\bno)\s*$", re.IGNORECASE)
SCOPED_RE = re.compile(
    r"\b(?:shell|bash|git|gh|sql|database|db|api|mcp|network|access)\b\W+(?:is\s+)?read-only\b",
    re.IGNORECASE,
)
ASIDE_RE = re.compile(r"\([^()]*\)")
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n")


def _sentences(text: str):
    text = FENCE_RE.sub(" ", text)
    while True:
        stripped = ASIDE_RE.sub(" ", text)
        if stripped == text:
            break
        text = stripped
    return [s for s in SENTENCE_RE.split(text) if s.strip()]


def _claims_read_only(text: str) -> bool:
    for sentence in _sentences(text):
        if SCOPED_RE.search(sentence):
            continue
        for match in CLAIM_RE.finditer(sentence):
            if NEGATED_RE.search(sentence[:match.start()]):
                continue
            if "read-only" not in match.group().lower() and SCOPE_AFTER_RE.match(sentence, match.end()):
                continue
            return True
    return False


def _grants_edit(fields: dict) -> bool:
    if "tools" not in fields:
        return True
    tools = fields["tools"]
    if isinstance(tools, str):
        tools = tools.split(",")
    return "edit" in (t.strip().lower() for t in tools)


def run(cfg):
    source = core.expand(core.cfg_get(cfg, "render.source", str(core.HOME / "rig")))
    findings = []
    for agent_file in sorted((source / "agents").glob("*.md")):
        try:
            fields, body = core.parse_frontmatter(core.read_text(agent_file))
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
