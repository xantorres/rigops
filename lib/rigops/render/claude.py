"""render_claude: ~/rig -> ~/.claude (CLAUDE.md, rules/, agents/, skills/)."""

from __future__ import annotations

import stat
from pathlib import Path

from . import frontmatter
from .common import Rendered, marker_md, render_markdown_file

CLASS_ORDER = ("read", "search", "edit", "shell", "web")
CLASS_MAP = {
    "read": ["Read"],
    "search": ["Grep", "Glob"],
    "edit": ["Edit", "Write"],
    "shell": ["Bash"],
    "web": ["WebFetch", "WebSearch"],
}

IGNORE_NAMES = {"__pycache__", ".DS_Store"}


def is_ignored(path: Path) -> bool:
    if path.suffix == ".pyc":
        return True
    return bool(IGNORE_NAMES & set(path.parts))


def _tools_line(fields: dict) -> str:
    classes = fields.get("tools", [])
    if isinstance(classes, str):
        classes = [c.strip() for c in classes.split(",") if c.strip()]
    names = []
    for cls in CLASS_ORDER:
        if cls in classes:
            names.extend(CLASS_MAP[cls])
    return ", ".join(names)


def _render_agent(agent_file: Path, home: Path) -> bytes:
    name = agent_file.stem
    fields, body = frontmatter.parse(agent_file.read_text())
    lines = [
        "---",
        f"name: {fields.get('name', name)}",
        f"description: {fields.get('purpose', '')}",
        f"tools: {_tools_line(fields)}",
    ]
    tier = fields.get("tier", "inherit")
    if tier != "inherit":
        lines.append(f"model: {tier}")
    memory = fields.get("memory", "none")
    if memory != "none":
        lines.append(f"memory: {memory}")
    lines.append("---")
    frontmatter_text = "\n".join(lines) + "\n"
    marker = marker_md(agent_file, home)
    return (frontmatter_text + marker + "\n" + body).encode()


def render_claude(source: Path, home: Path) -> Rendered:
    claude_dir = home / ".claude"
    files, modes = {}, {}

    law_path = source / "law.md"
    if law_path.is_file():
        files[claude_dir / "CLAUDE.md"] = render_markdown_file(law_path, home)

    rules_src = source / "rules"
    if rules_src.is_dir():
        for rule in sorted(rules_src.rglob("*.md")):
            rel = rule.relative_to(rules_src)
            files[claude_dir / "rules" / rel] = render_markdown_file(rule, home)

    agents_src = source / "agents"
    if agents_src.is_dir():
        for agent_file in sorted(agents_src.glob("*.md")):
            target = claude_dir / "agents" / agent_file.name
            files[target] = _render_agent(agent_file, home)

    skills_src = source / "skills"
    if skills_src.is_dir():
        for path in sorted(skills_src.rglob("*")):
            if path.is_dir() or is_ignored(path):
                continue
            rel = path.relative_to(skills_src)
            target = claude_dir / "skills" / rel
            mode = stat.S_IMODE(path.stat().st_mode)
            if path.name == "SKILL.md":
                files[target] = render_markdown_file(path, home)
            else:
                files[target] = path.read_bytes()
            modes[target] = mode

    managed_dirs = [claude_dir / "rules", claude_dir / "agents", claude_dir / "skills"]
    return Rendered(files=files, managed_dirs=managed_dirs, modes=modes)
