"""Every pointer in the instruction surface must resolve to something real.

Prose cannot be type-checked, so it rots: a retired MCP, a deleted agent, a
disabled plugin recommended as live, a model id two generations old. This check
extracts the pointers and asserts each one against the filesystem, settings.json,
installed_plugins.json and launchctl.
"""

from __future__ import annotations

import json
import re

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
    "/tmp", "/private/tmp", "/var", "/dev/null", "~/Downloads",
    "~/.claude/projects", "~/.claude/plugins/cache", "~/.claude/plugins/marketplaces",
    "~/.claude/sessions", "~/.claude/backups", "~/.claude/shell-snapshots",
]

# Segments that mark a documentation placeholder rather than a real location.
DEFAULT_IGNORE_SEGMENTS = [
    "skill-name", "my-project", "path/to", "example", "foo", "bar", "repo-name",
]

DEFAULT_MODELS = [
    "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001",
    "claude-fable-5", "claude-fable-5-1",
]

# settings.json is the source of truth for plugin and permission state, so only
# its executable pointers are checked; a disabled plugin listed there is a fact,
# not a stale recommendation.
SETTINGS_PATH_KEYS = ("hooks", "statusLine", "env", "mcpServers")

BUILTIN_AGENTS = {
    "general-purpose", "explore", "plan", "claude",
    "statusline-setup", "output-style-setup",
}


def _home_root() -> str:
    """The directory homes are made in, as a regex branch.

    Read off this rig rather than written down: the name differs by platform,
    and a home directly under the filesystem root has no such directory, in
    which case the branch is dropped instead of matching everything.
    """
    parent = str(core.HOME.parent)
    return "" if parent == "/" else f"|{re.escape(parent)}/[A-Za-z0-9._-]+"


PATH_RE = re.compile(rf"(?:~{_home_root()})/[^\s`'\"()\[\],;:|<>]*")
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
LAUNCHD_RE = re.compile(r"\b((?:com|local)\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+)\b")
MODEL_RE = re.compile(r"\bclaude-(?:opus|sonnet|haiku|fable|instant|[0-9])[a-z0-9.-]*\b")


def _cfg(cfg, key, default):
    return core.cfg_get(cfg, f"doctor.pointers.{key}", default) or default


def _sources(cfg):
    out = []
    for pattern in _cfg(cfg, "sources", DEFAULT_SOURCES):
        expanded = str(core.expand(pattern))
        if any(ch in expanded for ch in "*?["):
            root, _, rest = expanded.partition("*")
            base = core.expand(root).parent if not root.endswith("/") else core.expand(root)
            try:
                out.extend(sorted(base.glob(("*" + rest).lstrip("/"))))
            except (OSError, ValueError):
                continue
        else:
            out.append(core.expand(expanded))
    return [p for p in out if p.is_file()]


def _json(path, default):
    try:
        return json.loads(core.expand(path).read_text())
    except (OSError, ValueError):
        return default


def _known(cfg):
    settings = _json("~/.claude/settings.json", {})
    installed = _json("~/.claude/plugins/installed_plugins.json", {}).get("plugins", {})
    plugin_root = core.CLAUDE_DIR / "plugins" / "cache"
    agents = {p.stem.lower() for p in (core.CLAUDE_DIR / "agents").glob("*.md")}
    agents |= {p.stem.lower() for p in plugin_root.glob("*/*/*/agents/*.md")}
    agents |= {p.stem.lower() for p in plugin_root.glob("*/*/agents/*.md")}
    agents |= BUILTIN_AGENTS
    agents |= {a.lower() for a in _cfg(cfg, "known_agents", [])}
    skills = {p.parent.name.lower() for p in (core.CLAUDE_DIR / "skills").glob("*/SKILL.md")}
    skills |= {p.parent.name.lower() for p in plugin_root.glob("*/*/*/skills/*/SKILL.md")}
    skills |= {p.parent.name.lower() for p in plugin_root.glob("*/*/skills/*/SKILL.md")}
    skills |= {s.lower() for s in _cfg(cfg, "known_skills", [])}
    mcp = set(settings.get("mcpServers", {}) or {})
    mcp |= set(_json("~/.claude.json", {}).get("mcpServers", {}) or {})
    for extra in core.CLAUDE_DIR.glob(".mcp.json"):
        mcp |= set(_json(extra, {}).get("mcpServers", {}) or {})
    mcp |= {m.lower() for m in _cfg(cfg, "known_mcp", [])}
    labels = set()
    for line in core.run(["launchctl", "list"]).splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            labels.add(parts[-1].strip())
    return {
        "plugins": settings.get("enabledPlugins", {}) or {},
        "installed": installed,
        "agents": agents,
        "skills": skills,
        "mcp": {m.lower() for m in mcp},
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


def _path_ok(raw, known):
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
        pattern = resolved[len(str(anchor)) + 1:]
        try:
            return any(True for _ in anchor.glob(pattern))
        except (OSError, ValueError, IndexError):
            return True
    target = core.expand(path)
    if target.exists():
        return True
    # A path containing a space is cut short by the tokenizer, so treat a prefix
    # of a real entry ("~/Library/Application" for "Application Support") as sound.
    parent = target.parent
    return parent.is_dir() and any(
        child.name.startswith(target.name + " ") for child in parent.iterdir()
    )


def _finding(kind, value, where, fix, area=AREA):
    return core.Finding(
        check=CHECK, area=area, key=f"{kind}:{value}:{where}",
        symptom=f"{kind} pointer `{value}` does not resolve",
        evidence=where, fix=fix,
    )


def _scan_text(path, text, known):
    out = []
    label = str(path).replace(str(core.HOME), "~")
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f"{label}:{lineno}"
        for raw in PATH_RE.findall(line):
            if not _path_ok(raw, known):
                out.append(_finding("path", _clean_path(raw), where,
                                    "repoint at the live path or delete the claim"))
        for regex in AGENT_RE:
            for name in regex.findall(line):
                target = name.split(":")[-1].lower()
                if target not in known["agents"]:
                    out.append(_finding("agent", name, where,
                                        "name an agent that exists or drop the dispatch"))
        for regex in SKILL_RE:
            for name in regex.findall(line):
                target = name.split(":")[-1].lower()
                if target not in known["skills"]:
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
        for label_name in LAUNCHD_RE.findall(line):
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
    """Return only the executable slice of settings.json as scannable text."""
    data = _json(path, {})
    return "\n".join(
        json.dumps(data.get(key, {}), indent=1) for key in SETTINGS_PATH_KEYS
    ) + "\n" + json.dumps(data.get("modelSettings", {}), indent=1)


def run(cfg):
    known = _known(cfg)
    findings = []
    for path in _sources(cfg):
        try:
            text = (
                _settings_text(path) if path.name == "settings.json"
                else path.read_text(errors="replace")
            )
        except OSError:
            continue
        findings.extend(_scan_text(path, text, known))
    seen, unique = set(), []
    for finding in findings:
        if finding.id in seen:
            continue
        seen.add(finding.id)
        unique.append(finding)
    return unique
