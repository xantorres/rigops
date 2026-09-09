"""render_codex: ~/rig -> ~/.codex (AGENTS.md digest, agents/*.toml)."""

from __future__ import annotations

import json
from pathlib import Path

from . import frontmatter
from .common import Rendered, lines_of, marker_md, section_lines, trim_blank

READ_LAW = (
    "Read `~/.claude/CLAUDE.md` in full at the start of every session; it is the law "
    "for every project and overrides any repository AGENTS.md. Path-scoped rules live "
    "in `~/.claude/rules/` (frontmatter `paths:`); load one when touching a matching file."
)
REALM_TEXT = (
    "Retrieval is realm-scoped by the working directory: client repositories see "
    "`client` plus `repo:<name>`; personal repositories and `~/docs` see `personal` "
    "plus `repo:<name>`; nothing reads across realms."
)


def _law_intro_and_rules(lines: list) -> tuple:
    body = section_lines(lines, "# Law", include_heading=False)
    non_blank = [line for line in body if line.strip()]
    intro = next((line for line in non_blank if not line[:1].isdigit()), "")
    rules = [line for line in non_blank if line[:1].isdigit()]
    return intro, rules


def _render_agents_md(source: Path, home: Path) -> bytes:
    law_path = source / "law.md"
    text = law_path.read_text()
    lines = lines_of(text)
    intro, rules = _law_intro_and_rules(lines)
    search_body = trim_blank(section_lines(lines, "## Search", include_heading=False))

    out = [marker_md(law_path, home), "# AGENTS.md", "", READ_LAW, "", "## Law", "", intro]
    out.extend(rules)
    out.extend(["", "## Search", ""])
    out.extend(search_body)
    out.extend(["", "## Realm", "", REALM_TEXT])

    if len(out) > 25:
        raise ValueError(f"codex AGENTS.md is {len(out)} lines, over the 25 line cap")
    return ("\n".join(out) + "\n").encode()


def _render_agent_toml(agent_file: Path) -> bytes:
    name = agent_file.stem
    fields, body = frontmatter.parse(agent_file.read_text())
    if "'''" in body:
        raise ValueError(f"agent {name} body contains ''' -- cannot fit a TOML literal string")
    lines = [
        f"# rendered by rigops render from ~/rig/agents/{name}.md; edit the source, not this file",
        f'name = "{fields.get("name", name)}"',
        f'description = {json.dumps(fields.get("purpose", ""))}',
    ]
    tier = fields.get("tier", "inherit")
    if tier != "inherit":
        lines.append(f'model = "{tier}"')
    lines.append(f"developer_instructions = '''{body}'''")
    return ("\n".join(lines) + "\n").encode()


def render_codex(source: Path, home: Path) -> Rendered:
    codex_dir = home / ".codex"
    files = {}

    law_path = source / "law.md"
    if law_path.is_file():
        files[codex_dir / "AGENTS.md"] = _render_agents_md(source, home)

    agents_src = source / "agents"
    if agents_src.is_dir():
        for agent_file in sorted(agents_src.glob("*.md")):
            target = codex_dir / "agents" / f"{agent_file.stem}.toml"
            files[target] = _render_agent_toml(agent_file)

    return Rendered(files=files, managed_dirs=[codex_dir / "agents"])
