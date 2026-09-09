"""render_agents: ~/rig/agents/*.md -> ~/.claude/references/agents.md."""

from __future__ import annotations

from pathlib import Path

from . import frontmatter
from .common import Rendered, marker_md

HEADER = [
    "# Agents (generated)",
    "",
    "Generated from `~/rig/agents/*.md` by `rigops render agents`. Do not "
    "hand-edit: edit the agent source and re-render.",
    "",
    "Native `Explore` (read-only, skips CLAUDE.md and git status), `Plan` and `general-purpose`",
    "cover most delegation. Repo-scoped reviewers live in each repo's own `.claude/agents/`.",
    "",
    "| Agent | Tier | Memory | Voice | Purpose |",
    "|---|---|---|---|---|",
]
FOOTER = [
    "",
    "Dispatch evidence: every subagent start appends `{ts, session_id, agent, model}` to",
    "`~/.local/state/rigops/dispatch.jsonl` (SubagentStart hook). Prune agents against "
    "that log,",
    "never against recollection.",
    "",
    "Parallel dispatch for independent work; sequential only when outputs feed each other.",
]


def _row(name: str, tier: str, memory: str, voice: str, purpose: str) -> str:
    if len(purpose) > 140:
        purpose = purpose[:140] + "..."
    cells = [name, tier, memory, voice, purpose]
    cells = [c.replace("|", "/") for c in cells]
    return "| " + " | ".join(cells) + " |"


def render_agents(source: Path, home: Path) -> Rendered:
    agents_src = source / "agents"
    entries = []
    if agents_src.is_dir():
        for agent_file in sorted(agents_src.glob("*.md")):
            fields, _ = frontmatter.parse(agent_file.read_text())
            entries.append((
                fields.get("name", agent_file.stem),
                fields.get("tier", "inherit"),
                fields.get("memory", "none"),
                fields.get("voice", ""),
                fields.get("purpose", ""),
            ))
    entries.sort(key=lambda e: e[0])

    lines = list(HEADER)
    lines.extend(_row(*entry) for entry in entries)
    lines.extend(FOOTER)

    marker = marker_md(agents_src, home)
    content = marker + "\n" + "\n".join(lines) + "\n"
    target = home / ".claude" / "references" / "agents.md"
    return Rendered(files={target: content.encode()})
