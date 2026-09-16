"""What a pointer looks like in prose, and which ones this rig should know.

Separated from the check itself so the grammar can be read (and corrected) on
its own: every default list, every extraction pattern and every exclusion lives
here, while ``check_pointers`` holds the resolution rules that use them.
"""

from __future__ import annotations

import re

from rigops import core

DEFAULT_SOURCES = [
    "~/.claude/CLAUDE.md",
    "~/.claude/rules/**/*.md",
    "~/.claude/references/**/*.md",
    "~/.claude/skills/**/SKILL.md",
    "~/.claude/agents/*.md",
    "~/.claude/settings.json",
    "~/docs/CLAUDE.md",
    "~/docs/AGENT-CHEATSHEET.md",
    "~/projects/personal/*/CLAUDE.md",
]

DEFAULT_IGNORE_PREFIXES = [
    "/tmp", "/private/tmp", "/var/folders", "~/Downloads",
    "~/.claude/plugins/cache", "~/.claude/plugins/marketplaces",
    "~/.claude/sessions", "~/.claude/backups", "~/.claude/shell-snapshots",
]

# Session transcripts and their sidecar directories are runtime state named after
# session ids, which come and go. Ignoring the projects directory wholesale to
# skip them took the fact store beside them out of the check too, and a memory
# note that points at something deleted is exactly what this check is for.
TRANSCRIPT_RE = re.compile(
    r"/\.claude/projects/[^/]+/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\.jsonl|/|$)"
)

# Segments that mark a documentation placeholder rather than a real location.
DEFAULT_IGNORE_SEGMENTS = [
    "skill-name", "my-project", "path/to", "example", "foo", "bar", "repo-name",
]

# Skills and agents ship in several layouts (user directory, project directory,
# cached plugin, marketplace checkout), so the roots are walked rather than
# matched shape by shape: a layout this check has not seen would otherwise turn
# every sound pointer to that skill into a finding.
DEFAULT_SKILL_ROOTS = ["~/.claude/skills", "~/.claude/plugins", "~/docs/.claude/skills"]
DEFAULT_AGENT_ROOTS = ["~/.claude/agents", "~/.claude/plugins"]

DEFAULT_MODELS = [
    "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001",
    "claude-fable-5", "claude-fable-5-1",
]

# settings.json is the source of truth for plugin and permission state, so only
# its executable pointers are checked; a disabled plugin listed there is a fact,
# not a stale recommendation. The allow, deny and ask rules stay out for the same
# reason: a rule naming a file that does not exist is the rule working.
SETTINGS_PATH_KEYS = ("hooks", "statusLine", "env", "mcpServers", "modelSettings")

BUILTIN_AGENTS = {
    "general-purpose", "explore", "plan", "claude",
    "statusline-setup", "output-style-setup",
}

# Absolute paths live outside the home directory too, and the tools this rig
# names most often (the package manager prefix, the system binaries) all do. The
# lookbehind keeps the match from starting inside a longer token, so a relative
# path such as `.config/bin/run.sh` is not read as `/bin/run.sh`.
PATH_ROOTS = "opt|usr|etc|srv|var|tmp|private|bin|sbin|Applications|Library|Volumes"


def _home_root() -> str:
    """The directory homes are made in, as a regex branch.

    Read off this rig rather than written down: the name differs by platform,
    and a home directly under the filesystem root has no such directory, in
    which case the branch is dropped instead of matching everything.
    """
    parent = str(core.HOME.parent)
    return "" if parent == "/" else f"|{re.escape(parent)}/[A-Za-z0-9._-]+"


PATH_RE = re.compile(
    rf"(?<![\w./~-])(?:~{_home_root()}|/(?:{PATH_ROOTS}))/[^\s`'\"()\[\],;:|<>]*"
)
# Only the "<name> agent" order is trusted: "agent <name>" also matches ordinary
# prose such as "the agent auto-invokes", which is not a pointer at all.
AGENT_RE = [
    re.compile(r"[`*]{1,2}([a-z][a-z0-9:_-]{2,})[`*]{1,2}\s+(?:sub)?agent\b"),
    re.compile(r"subagent_type[\"']?\s*[:=]\s*[\"'`]([a-z][a-z0-9:_-]{2,})"),
]
SKILL_RE = [
    re.compile(r"\bskills?\s+[`*]{1,2}([a-z][a-z0-9:_-]{2,})[`*]{1,2}"),
    re.compile(r"[`*]{1,2}([a-z][a-z0-9:_-]{2,})[`*]{1,2}\s+skill\b"),
    re.compile(r"Skill\(\s*[\"']([a-z][a-z0-9:_-]+)"),
]
# A marketplace name starts with a letter, which a version specifier such as
# `node@20` or `pnpm@9.1.0` does not, and the boundaries exclude a token that is
# part of a longer one: an email address, a scoped package, an ssh remote with a
# path (`git@host:org/repo.git`).
PLUGIN_ID_RE = re.compile(
    r"(?<![\w.@/+-])([a-z][a-z0-9-]*)@([a-z][a-z0-9-]*[a-z0-9])(?![\w./:@-])"
)
# A dist tag pins a dependency; no marketplace is called this.
DIST_TAGS = {
    "latest", "next", "beta", "alpha", "canary", "rc", "stable",
    "dev", "nightly", "edge", "lts", "main",
}
# An ssh remote with no path left (`ssh -T git@host`) is shaped exactly like a
# plugin id, and these are the account names it is written with.
NOT_PLUGIN_NAMES = {"git", "root", "admin", "ubuntu", "deploy", "ec2-user"}
PLUGIN_WORD_RE = re.compile(r"[`*]{1,2}([a-z0-9][a-z0-9-]{2,})[`*]{1,2}\s+plugin\b")
MCP_RE = [
    re.compile(r"[`*]{1,2}([A-Za-z0-9][A-Za-z0-9_-]{2,})[`*]{1,2}\s+MCP\b"),
    re.compile(r"\bMCP\s+server\s+[`*]{1,2}([A-Za-z0-9][A-Za-z0-9_-]{2,})[`*]{1,2}"),
]
# Two segments is a label: `local.rigops-doctor` is the shape this fleet's own
# default prefix produces, and requiring a third missed all of them. The
# lookbehind keeps a filename such as `settings.local.json` out of the match.
LAUNCHD_RE = re.compile(r"(?<![\w.-])((?:com|local)\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\b")
MODEL_RE = re.compile(r"\bclaude-(?:opus|sonnet|haiku|fable|instant|[0-9])[a-z0-9.-]*\b")
