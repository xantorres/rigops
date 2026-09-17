"""Every pointer in the instruction surface must resolve to something real.

Prose cannot be type-checked, so it rots: a retired MCP, a deleted agent, a
disabled plugin recommended as live, a model id two generations old. This check
extracts the pointers and asserts each one against the filesystem, settings.json,
installed_plugins.json and launchctl.

A memory note is part of that surface: it is recalled into a session and acted
on, and notes pointing at deleted scripts and plans have had to be found by hand.
It is also a dated record, so it is scanned narrowly. Only the path rule applies,
and only to paths under ``doctor.pointers.memory_roots``: another host's
filesystem is a fact about that host, and a label, model id, plugin, agent,
skill or server named in a note is mostly history (a skill it names is often a
project's own, which the rig-wide skill roots do not see). A note that
records a path as retired names it without its root, so a root-anchored path in
a note is always a claim that it exists.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from rigops import core

from .pointer_grammar import (
    AGENT_RE,
    BUILTIN_AGENTS,
    DEFAULT_AGENT_ROOTS,
    DEFAULT_IGNORE_PREFIXES,
    DEFAULT_IGNORE_SEGMENTS,
    DEFAULT_MEMORY_ROOTS,
    DEFAULT_MODELS,
    DEFAULT_SKILL_ROOTS,
    DEFAULT_SOURCES,
    DIST_TAGS,
    LAUNCHD_RE,
    MCP_RE,
    MEMORY_NOTE_RE,
    MODEL_RE,
    NOT_PLUGIN_NAMES,
    PATH_RE,
    PLUGIN_ID_RE,
    PLUGIN_WORD_RE,
    SETTINGS_PATH_KEYS,
    SKILL_RE,
    TRANSCRIPT_RE,
)

AREA = "docs"
CHECK = "pointers"


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
        return json.loads(core.read_text(path))
    except (OSError, ValueError):
        return default


def _roots(cfg, key, default):
    """Return the roots that exist, plus a finding per configured root that is gone.

    Same rule as the source list: a root an operator named is a claim about this
    rig, while a default root is simply absent on a rig with no such layout.
    """
    dirs, missing = [], []
    for pattern in _cfg(cfg, key, default):
        base = core.expand(pattern)
        if base.is_dir():
            dirs.append(base)
        elif pattern not in default:
            missing.append(_finding(
                "root", str(base), f"doctor.pointers.{key}",
                "point the root at a directory that exists or drop the entry",
            ))
    return dirs, missing


def _root_findings(cfg):
    out = []
    for key, default in (
        ("skill_roots", DEFAULT_SKILL_ROOTS), ("agent_roots", DEFAULT_AGENT_ROOTS),
    ):
        out.extend(_roots(cfg, key, default)[1])
    return out


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
    for base in _roots(cfg, "agent_roots", DEFAULT_AGENT_ROOTS)[0]:
        agents |= {p.stem.lower() for p in base.rglob("*.md") if p.parent.name == "agents"}
    agents |= {a.lower() for a in _cfg(cfg, "known_agents", [])}
    skills = set()
    for base in _roots(cfg, "skill_roots", DEFAULT_SKILL_ROOTS)[0]:
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
        "memory_roots": [
            str(core.expand(p)) for p in _cfg(cfg, "memory_roots", DEFAULT_MEMORY_ROOTS)
        ],
    }


def _clean_path(raw):
    return raw.rstrip(".,;:!?)]}\"'`*")


def _within(resolved, prefixes):
    return any(resolved == pre or resolved.startswith(pre + "/") for pre in prefixes)


def _path_ok(raw, known, truncated=False):
    path = _clean_path(raw)
    if any(token in path for token in ("$", "{", "}", "...", "<", ">")):
        return True
    if any(seg in path for seg in known["placeholders"]):
        return True
    resolved = str(core.expand(path))
    if _within(resolved, known["ignore"]):
        return True
    if TRANSCRIPT_RE.search(resolved):
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


def _scan_text(path, text, known, numbered=True, memory=False):
    out = []
    label = str(path).replace(str(core.HOME), "~")
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f"{label}:{lineno}" if numbered else label
        for match in PATH_RE.finditer(line):
            raw = match.group(0)
            if memory and not _within(str(core.expand(_clean_path(raw))), known["memory_roots"]):
                continue
            if not _path_ok(raw, known, truncated=line[match.end():match.end() + 1] == " "):
                out.append(_finding("path", _clean_path(raw), where,
                                    "repoint at the live path or delete the claim"))
        if memory:
            continue
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
            if market in DIST_TAGS or name in NOT_PLUGIN_NAMES:
                continue
            if full not in known["plugins"] and full not in known["installed"]:
                out.append(_finding("plugin", full, where,
                                    "install it, or stop naming a plugin this rig lacks"))
            elif not known["plugins"].get(full, False):
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
    findings.extend(_root_findings(cfg))
    for path in paths:
        is_settings = path.name == "settings.json"
        try:
            text = _settings_text(path) if is_settings else core.read_text(path)
        except OSError:
            continue
        findings.extend(_scan_text(
            path, text, known, numbered=not is_settings,
            memory=bool(MEMORY_NOTE_RE.search(str(path))),
        ))
    seen, unique = set(), []
    for finding in findings:
        if finding.id in seen:
            continue
        seen.add(finding.id)
        unique.append(finding)
    return unique
