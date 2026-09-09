"""Renders ~/rig (law, rules, agents, skills, registry) onto every consuming
surface: ~/.claude, ~/.codex, ~/.config/ai-agent, ~/.claude/references/agents.md,
and the pointer repos in registry/roots.json.

Each render_<x>(source, home) -> Rendered is pure: it reads the source tree and
returns the bytes it would write, never touching disk itself. apply() and
check() are the only two functions that do.
"""

from __future__ import annotations

from .agents_doc import render_agents
from .claude import render_claude
from .codex import render_codex
from .combine import apply, check, render_all, render_selected
from .common import Rendered
from .local import render_local
from .repos import render_repos

__all__ = [
    "Rendered",
    "render_claude",
    "render_codex",
    "render_local",
    "render_agents",
    "render_repos",
    "render_all",
    "render_selected",
    "apply",
    "check",
]
