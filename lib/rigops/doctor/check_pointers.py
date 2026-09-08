"""Every pointer in the instruction surface must resolve to something real.

Prose cannot be type-checked, so it rots: a retired MCP, a deleted agent, a
disabled plugin recommended as live, a model id two generations old. This check
extracts the pointers and asserts each one against the filesystem, settings.json,
installed_plugins.json and launchctl.
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from rigops import core

AREA = "docs"
CHECK = "pointers"

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
    "~/.claude/projects", "~/.claude/plugins/cache", "~/.claude/plugins/marketplaces",
    "~/.claude/sessions", "~/.claude/backups", "~/.claude/shell-snapshots",
]

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
# path such as `.claude/bin/run.sh` is not read as `/bin/run.sh`.
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
PLUGIN_ID_RE = re.compile(r"\b([a-z0-9][a-z0-9-]*)@([a-z0-9][a-z0-9-]*)\b")
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

_UNSET = object()


def _cfg(cfg, key, default):
    """An explicitly empty list in config is a deliberate override, not a miss."""
    value = core.cfg_get(cfg, f"doctor.pointers.{key}", _UNSET)
    return default if value is _UNSET or value is None else value


def _sources(cfg):
    """Return the files to scan, plus a finding per configured source that is gone.

    A missing entry is only reported when the operator wrote it. The defaults
    name files that exist on this rig and not on every rig, so a fresh install
    would otherwise open with findings about a vault it does not have.
    """
    configured = _cfg(cfg, "sources", DEFAULT_SOURCES)
    out, missing = [], []
    for pattern in configured:
        expanded = str(core.expand(pattern))
        if any(ch in expanded for ch in "*?["):
            out.extend(Path(hit) for hit in sorted(glob.glob(expanded, recursive=True)))
        elif Path(expanded).is_file():
            out.append(Path(expanded))
        elif pattern not in DEFAULT_SOURCES:
            missing.append(_finding(
                "source", pattern, "doctor.pointers.sources",
                "point the source at a file that exists or drop the entry",
            ))
    return [p for p in out if p.is_file()], missing


def _json(path, default):
    try:
        return json.loads(core.expand(path).read_text())
    except (OSError, ValueError):
        return default


def _roots(cfg, key, default):
    for pattern in _cfg(cfg, key, default):
        base = core.expand(pattern)
        if base.is_dir():
            yield base


def _mcp_names(cfg):
    """Every server name a session could resolve, from all four registries."""
    names = set(_json("~/.claude/settings.json", {}).get("mcpServers", {}) or {})
    user = _json("~/.claude.json", {})
    names |= set(user.get("mcpServers", {}) or {})
    # A server wired for one project is still a real server the docs may name.
    for project in (user.get("projects", {}) or {}).values():
        names |= set((project or {}).get("mcpServers", {}) or {})
    for manifest in (core.CLAUDE_DIR / "plugins").rglob(".mcp.json"):
        names |= set(_json(manifest, {}).get("mcpServers", {}) or {})
    names |= set(_json(core.CLAUDE_DIR / ".mcp.json", {}).get("mcpServers", {}) or {})
    names |= set(_cfg(cfg, "known_mcp", []))
    return {name.lower() for name in names}


def _known(cfg):
    settings = _json("~/.claude/settings.json", {})
    installed = _json("~/.claude/plugins/installed_plugins.json", {}).get("plugins", {})
    agents = set(BUILTIN_AGENTS)
    for base in _roots(cfg, "agent_roots", DEFAULT_AGENT_ROOTS):
        agents |= {p.stem.lower() for p in base.rglob("*.md") if p.parent.name == "agents"}
    agents |= {a.lower() for a in _cfg(cfg, "known_agents", [])}
    skills = set()
    for base in _roots(cfg, "skill_roots", DEFAULT_SKILL_ROOTS):
        skills |= {p.parent.name.lower() for p in base.rglob("SKILL.md")}
    skills |= {s.lower() for s in _cfg(cfg, "known_skills", [])}
    labels = set()
    for line in core.run(["launchctl", "list"]).splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            labels.add(parts[-1].strip())
    return {
        "plugins": settings.get("enabledPlugins", {}) or {},
        "installed": installed,
        "namespaces": {key.split("@")[0].lower() for key in installed},
        "agents": agents,
        "skills": skills,
        "mcp": _mcp_names(cfg),
        "labels": labels,
        "models": set(_cfg(cfg, "known_models", DEFAULT_MODELS)),
        "ignore": [
            str(core.expand(p))
            for p in _cfg(cfg, "ignore_prefixes", DEFAULT_IGNORE_PREFIXES)
        ],
        "placeholders": _cfg(cfg, "ignore_segments", DEFAULT_IGNORE_SEGMENTS),
    }


def _clean_path(raw):
    return raw.rstrip(".,;:!?)]}\"'`*")


def _path_ok(raw, known, truncated=False):
    path = _clean_path(raw)
    if any(token in path for token in ("$", "{", "}", "...", "<", ">")):
        return True
    if any(seg in path for seg in known["placeholders"]):
        return True
    resolved = str(core.expand(path))
    if any(resolved == pre or resolved.startswith(pre + "/") for pre in known["ignore"]):
        return True
    if any(ch in path for ch in "*?["):
        head = resolved.split("*")[0].split("[")[0].split("?")[0]
        anchor = core.expand(head if head.endswith("/") else head.rsplit("/", 1)[0])
        if not anchor.is_dir():
            return False
        if "**" in resolved:
            # A recursive glob in prose describes a shape, not a file, and one
            # that matches nothing walks the entire subtree: `~/projects/**/x.md`
            # costs forty seconds. This runs in a pre-commit hook, so the check
            # stops at the anchor.
            return True
        pattern = resolved[len(str(anchor)) + 1:]
        try:
            return any(True for _ in anchor.glob(pattern))
        except (OSError, ValueError, IndexError):
            return True
    target = core.expand(path)
    if target.exists():
        return True
    if not truncated:
        return False
    # The match ended at a space, so the real path may continue past it: treat a
    # prefix of a real entry ("~/Library/Application" for "Application Support")
    # as sound. Gated on the truncation, or every missing path would escape
    # whenever some sibling happened to be named after it.
    parent = target.parent
    try:
        return parent.is_dir() and any(
            child.name.startswith(target.name + " ") for child in parent.iterdir()
        )
    except OSError:
        return True


def _finding(kind, value, where, fix, area=AREA):
    # The key names the file but not the line: the same broken pointer is one
    # defect however far an unrelated edit above it pushes the line down, and an
    # id that moved on every reflow would put a fresh row in the backlog each run.
    return core.Finding(
        check=CHECK, area=area, key=f"{kind}:{value}:{where.rsplit(':', 1)[0]}",
        symptom=f"{kind} pointer `{value}` does not resolve",
        evidence=where, fix=fix,
    )


def _unregistered(name, known, kind):
    """True when the name is one this rig should have and does not.

    A `namespace:name` pointer whose namespace is not an installed plugin is left
    alone: some namespaces are supplied by the runtime and have no directory to
    walk, and calling those broken would be the check inventing a defect.
    """
    namespace, _, bare = name.rpartition(":")
    if namespace and namespace.lower() not in known["namespaces"]:
        return False
    return bare.lower() not in known[kind]


def _scan_text(path, text, known, numbered=True):
    out = []
    label = str(path).replace(str(core.HOME), "~")
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f"{label}:{lineno}" if numbered else label
        for match in PATH_RE.finditer(line):
            raw = match.group(0)
            if not _path_ok(raw, known, truncated=line[match.end():match.end() + 1] == " "):
                out.append(_finding("path", _clean_path(raw), where,
                                    "repoint at the live path or delete the claim"))
        for regex in AGENT_RE:
            for name in regex.findall(line):
                if _unregistered(name, known, "agents"):
                    out.append(_finding("agent", name, where,
                                        "name an agent that exists or drop the dispatch"))
        for regex in SKILL_RE:
            for name in regex.findall(line):
                if _unregistered(name, known, "skills"):
                    out.append(_finding("skill", name, where,
                                        "name a skill that exists or drop the pointer",
                                        area="skills"))
        for name, market in PLUGIN_ID_RE.findall(line):
            full = f"{name}@{market}"
            if full not in known["plugins"] and full not in known["installed"]:
                continue
            if not known["plugins"].get(full, False):
                out.append(_finding("plugin", full, where,
                                    "enable it in settings.json or stop recommending it"))
        for name in PLUGIN_WORD_RE.findall(line):
            matches = [k for k in known["plugins"] if k.split("@")[0] == name]
            if matches and not any(known["plugins"][k] for k in matches):
                out.append(_finding("plugin", name, where,
                                    "enable it in settings.json or stop recommending it"))
        for regex in MCP_RE:
            for name in regex.findall(line):
                if name.lower() not in known["mcp"]:
                    out.append(_finding("mcp", name, where,
                                        "wire the server or delete the reference",
                                        area="repos"))
        # No labels at all means no launchctl, not a rig with nothing loaded, so
        # the rule stands down rather than reporting every label in the docs.
        for label_name in LAUNCHD_RE.findall(line) if known["labels"] else []:
            stripped = label_name[:-6] if label_name.endswith(".plist") else label_name
            if stripped.startswith("com.apple.") or ".example." in stripped:
                continue
            if stripped not in known["labels"]:
                out.append(_finding("launchd", stripped, where,
                                    "load the job or drop the row", area="launchd"))
        for model in MODEL_RE.findall(line):
            if model not in known["models"]:
                out.append(_finding("model", model, where,
                                    "update to a current model id", area="metrics"))
    return out


def _settings_text(path):
    """Return only the executable slice of settings.json as scannable text.

    The result is assembled rather than read, so findings against it carry the
    file but no line number: a line of synthesised text is not evidence.
    """
    data = _json(path, {})
    parts = [json.dumps(data.get(key, {}), indent=1) for key in SETTINGS_PATH_KEYS]
    # A read grant to a directory that no longer exists is dead configuration,
    # and it is the pointer class this check was written for.
    grants = (data.get("permissions", {}) or {}).get("additionalDirectories", [])
    parts.append(json.dumps(grants, indent=1))
    # Skill overrides name skills, so they are rendered into the prose grammar
    # the one scanner already reads rather than given a second code path.
    parts.extend(f"`{name}` skill" for name in data.get("skillOverrides", {}) or {})
    return "\n".join(parts)


def run(cfg):
    known = _known(cfg)
    paths, findings = _sources(cfg)
    for path in paths:
        is_settings = path.name == "settings.json"
        try:
            text = _settings_text(path) if is_settings else path.read_text(errors="replace")
        except OSError:
            continue
        findings.extend(_scan_text(path, text, known, numbered=not is_settings))
    seen, unique = set(), []
    for finding in findings:
        if finding.id in seen:
            continue
        seen.add(finding.id)
        unique.append(finding)
    return unique
