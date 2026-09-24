"""A stdio MCP server spawns one process per live session, and the desktop app
never reaps an idle one -- eighteen sentry-mcp processes measured at 1.5 GB on
this host. The sentry server is being retired separately; this check guards the
whole class across every registry a session can resolve one from.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigops import core

AREA = "rigops"
CHECK = "mcp"

FIX = (
    "use an http MCP or a skill script; a stdio server spawns one process per "
    "live session (or add it to doctor.mcp.stdio_allow)"
)


def _json(path, default):
    try:
        return json.loads(core.read_text(path))
    except (OSError, ValueError):
        return default


def _label(path):
    return str(path).replace(str(core.HOME), "~")


def _is_stdio(server):
    if not isinstance(server, dict):
        return False
    server_type = server.get("type")
    if server_type == "stdio":
        return True
    return server_type is None and bool(server.get("command"))


def _claude_json_servers():
    path = core.HOME / ".claude.json"
    data = _json(path, {})
    label = _label(path)
    out = [
        (name, server, f"{label} (user)")
        for name, server in (data.get("mcpServers", {}) or {}).items()
    ]
    for project_path, project in (data.get("projects", {}) or {}).items():
        for name, server in ((project or {}).get("mcpServers", {}) or {}).items():
            out.append((name, server, f"{label} (project {project_path})"))
    return out


def _settings_servers():
    path = core.CLAUDE_DIR / "settings.json"
    data = _json(path, {})
    label = _label(path)
    return [(name, server, label) for name, server in (data.get("mcpServers", {}) or {}).items()]


def _dir_mcp_servers():
    path = core.CLAUDE_DIR / ".mcp.json"
    data = _json(path, {})
    label = _label(path)
    return [(name, server, label) for name, server in (data.get("mcpServers", {}) or {}).items()]


def _plugin_servers():
    settings = _json(core.CLAUDE_DIR / "settings.json", {})
    enabled = [key for key, on in (settings.get("enabledPlugins", {}) or {}).items() if on is True]
    if not enabled:
        return []
    installed = _json(core.CLAUDE_DIR / "plugins" / "installed_plugins.json", {}).get("plugins", {})
    out = []
    for key in enabled:
        install_paths = {
            entry.get("installPath") for entry in installed.get(key, []) or []
            if entry.get("installPath")
        }
        for install_path in install_paths:
            base = Path(install_path)
            manifest_path = base / ".mcp.json"
            manifest = _json(manifest_path, {})
            for name, server in (manifest.get("mcpServers", {}) or {}).items():
                out.append((name, server, f"plugin {key} ({_label(manifest_path)})"))
            plugin_json_path = base / ".claude-plugin" / "plugin.json"
            if plugin_json_path.is_file():
                plugin_json = _json(plugin_json_path, {})
                inline = plugin_json.get("mcpServers")
                if isinstance(inline, dict):
                    for name, server in inline.items():
                        out.append((name, server, f"plugin {key} ({_label(plugin_json_path)})"))
    return out


def run(cfg):
    allow = {name.lower() for name in core.cfg_get(cfg, "doctor.mcp.stdio_allow", [])}
    findings = []
    sources = _claude_json_servers() + _settings_servers() + _dir_mcp_servers() + _plugin_servers()
    for name, server, where in sources:
        if not _is_stdio(server) or name.lower() in allow:
            continue
        command = server.get("command", "")
        findings.append(core.Finding(
            check=CHECK, area=AREA, key=f"stdio:{name}",
            symptom=f"stdio MCP server `{name}` is defined in {where}",
            evidence=Path(str(command)).name,
            fix=FIX,
        ))
    return findings
