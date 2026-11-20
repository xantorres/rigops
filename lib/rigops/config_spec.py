from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rigops.errors import RigopsError

UNSET = object()


@dataclass(frozen=True)
class Type:
    label: str
    check: Callable[[Any, str], None]


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _fail(path: str, want: str, value: Any) -> None:
    raise RigopsError(f"config key {path}: expected {want}, got {_kind(value)}")


def _scalar(label: str, accepts: Callable[[Any], bool]) -> Type:
    def check(value: Any, path: str) -> None:
        if not accepts(value):
            _fail(path, label, value)
    return Type(label, check)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


BOOL = _scalar("boolean", lambda v: isinstance(v, bool))
INT = _scalar("integer", _is_int)
NUMBER = _scalar("number", lambda v: _is_int(v) or isinstance(v, float))
STRING = _scalar("string", lambda v: isinstance(v, str))
ANY = Type("any value", lambda value, path: None)


def list_of(item: Type) -> Type:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, list):
            _fail(path, f"array of {item.label}", value)
        for index, entry in enumerate(value):
            item.check(entry, f"{path}[{index}]")
    return Type(f"array of {item.label}", check)


def open_map(item: Type = ANY) -> Type:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            _fail(path, f"object of {item.label}", value)
        for name, entry in value.items():
            item.check(entry, f"{path}.{name}")
    return Type(f"object of {item.label}", check)


def nullable(inner: Type) -> Type:
    def check(value: Any, path: str) -> None:
        if value is not None:
            inner.check(value, path)
    return Type(f"{inner.label} or null", check)


def one_of(*options: Type) -> Type:
    label = " or ".join(option.label for option in options)

    def check(value: Any, path: str) -> None:
        for option in options:
            try:
                option.check(value, path)
            except RigopsError:
                continue
            return
        _fail(path, label, value)
    return Type(label, check)


def obj(**fields: Type) -> Type:
    """An object whose listed fields are typed; other fields are left alone."""
    def check(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            _fail(path, "object", value)
        for name, field_type in fields.items():
            if name in value:
                field_type.check(value[name], f"{path}.{name}")
    return Type("object", check)


JANITOR_ACTIONS = {
    "report": {},
    "delete": {},
    "keep_newest_n": {"keep": INT},
    "truncate": {"keep_lines": INT, "archive": BOOL},
    "command": {"command": STRING, "timeout_s": NUMBER},
}
_JANITOR_COMMON = {
    "name": STRING, "path": STRING, "glob": STRING, "older_than_days": NUMBER,
    "max_depth": INT, "dirs": BOOL, "only_empty": BOOL,
}


def _janitor_rule(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        _fail(path, "object", value)
    action = value.get("action", "report")
    if not isinstance(action, str) or action not in JANITOR_ACTIONS:
        names = ", ".join(sorted(JANITOR_ACTIONS))
        raise RigopsError(f"config key {path}.action: expected one of {names}, got {action!r}")
    for name, field_type in {**_JANITOR_COMMON, **JANITOR_ACTIONS[action]}.items():
        if name in value:
            field_type.check(value[name], f"{path}.{name}")


JANITOR_RULE = Type("janitor rule", _janitor_rule)
WATERMARK = obj(name=STRING, path=STRING, max_files=INT, max_kb=NUMBER, area=STRING)
CUSTOM_CHECK = obj(name=STRING, command=STRING, warn_exit=INT, fail_exit=INT, timeout_s=NUMBER)
DISK_FREE = obj(path=STRING, warn_gb=NUMBER, fail_gb=NUMBER)
CUSTOM_SOURCE = obj(name=STRING, argv=list_of(STRING), timeout_s=NUMBER)
DERIVED_REPO = one_of(STRING, obj(repo=STRING, paths=list_of(STRING)))
STRINGS = list_of(STRING)


@dataclass(frozen=True)
class Key:
    path: str
    type: Type
    default: Any
    doc: str


def _k(path: str, type_: Type, default: Any, doc: str) -> Key:
    return Key(path, type_, default, doc)


SPEC: tuple[Key, ...] = (
    _k("transcripts_dir", STRING, "~/.claude/projects",
        "Root of the Claude Code project transcripts."),
    _k("ledger.watch_projects", open_map(STRING), {}, "Column name to transcript-path substring."),
    _k("ledger.derive_interventions", list_of(DERIVED_REPO), [],
        "Repos whose commits count as interventions."),
    _k("ledger.extra_correction_patterns", STRINGS, [],
        "Regexes that also count a prompt as a correction."),
    _k("ledger.correction_exclude_patterns", STRINGS, [],
        "Regexes that stop a line counting as a correction."),
    _k("ledger.extra_denial_patterns", STRINGS, [],
        "Regexes that also count a tool error as a denial."),
    _k("ledger.week_reports", STRING, UNSET, "Glob of weekly reports written by other tools."),
    _k("fixed_tax.paths", STRINGS, [], "Files always counted toward the fixed tax."),
    _k("fixed_tax.globs", STRINGS, [],
        "Globs of always-loaded files counted toward the fixed tax."),
    _k("friction.headless_projects", STRINGS, [],
        "Transcript-path substrings counted as headless."),
    _k("context.nudge_tiers", list_of(INT), [250000, 350000, 500000],
        "Ascending token thresholds for the context nudge."),
    _k("context.rearm_tokens", INT, 50000, "Token climb needed to re-fire the nudge."),
    _k("context.card.doctor_max_age_h", NUMBER, 2,
        "Hours before a doctor gate record counts as stale."),
    _k("doctor.registry", STRING, "~/.config/rigops/registry.md", "Path of the job registry file."),
    _k("doctor.label_prefix", STRINGS, ["local."],
        "launchd label prefixes tried for a registry item."),
    _k("doctor.kill_grace_s", NUMBER, 5, "Seconds between SIGTERM and SIGKILL for a hung job."),
    _k("doctor.heal_cooldown_h", NUMBER, 12, "Hours a repeat heal of the same job is suppressed."),
    _k("doctor.checks.custom", list_of(CUSTOM_CHECK), [], "Custom checks judged by exit code."),
    _k("doctor.checks.disk_free", nullable(DISK_FREE), None, "Disk free check; null skips it."),
    _k("doctor.notify_command", STRING, "", "Command run with the report path when a page is due."),
    _k("doctor.cause_policy", STRING, "off", "Failure-cause policy: off, shadow or on."),
    _k("doctor.cause_probe_command", STRING, "", "Command whose exit 0 releases one held kick."),
    _k("doctor.budget.always_on_tokens", INT, 6000,
        "Ceiling for the always-loaded instruction tokens."),
    _k("doctor.levers.rules", open_map(ANY), {}, "Lever rules keyed by ledger column."),
    _k("doctor.levers.defaults", BOOL, True, "False drops the built-in lever rules."),
    _k("doctor.levers.max_age_days", INT, 10, "Days before the newest ledger row is stale."),
    _k("doctor.memory.sources", STRINGS, ["~/.claude/projects/*/memory/*.md"],
        "Globs of memory notes to check."),
    _k("doctor.memory.index_max_lines", INT, 150, "Line cap for a memory index."),
    _k("doctor.memory.index_max_bytes", INT, 20000, "Byte cap for a memory index."),
    _k("doctor.mcp.stdio_allow", STRINGS, [], "stdio MCP server names the mcp check accepts."),
    _k("doctor.pointers.sources", STRINGS, UNSET,
        "Files the pointer check scans; unset uses a built-in list."),
    _k("doctor.pointers.skill_roots", STRINGS, UNSET,
        "Roots skills resolve against; unset uses a built-in list."),
    _k("doctor.pointers.agent_roots", STRINGS, UNSET,
        "Roots agents resolve against; unset uses a built-in list."),
    _k("doctor.pointers.memory_roots", STRINGS, UNSET,
        "Roots a memory note's paths must resolve under; unset uses a built-in list."),
    _k("doctor.pointers.ignore_prefixes", STRINGS, UNSET,
        "Path prefixes the pointer check skips; unset uses a built-in list."),
    _k("doctor.pointers.ignore_segments", STRINGS, UNSET,
        "Placeholder path segments to skip; unset uses a built-in list."),
    _k("doctor.pointers.known_mcp", STRINGS, UNSET,
        "Extra MCP server names the pointer check accepts."),
    _k("doctor.pointers.known_agents", STRINGS, UNSET,
        "Extra agent names the pointer check accepts."),
    _k("doctor.pointers.known_skills", STRINGS, UNSET,
        "Extra skill names the pointer check accepts."),
    _k("doctor.pointers.known_models", STRINGS, UNSET,
        "Model ids the pointer check accepts; unset uses a built-in list."),
    _k("reaper.roots", STRINGS, [], "Trees scanned for repositories."),
    _k("reaper.sessions_dir", STRING, "~/.claude/sessions",
        "Directory of live-session JSON files."),
    _k("reaper.grace_days", INT, 14, "Age of an unmerged branch tip before it is reapable."),
    _k("reaper.max_kills_per_tree", INT, 20,
        "More matched processes than this under one worktree stops the reap."),
    _k("reaper.orphan_min_age_s", INT, 172800,
        "Minimum runtime before a build daemon counts as orphaned."),
    _k("reaper.orphan_sweep", BOOL, False, "Run the orphan-daemon sweep."),
    _k("reaper.kill_grace_s", NUMBER, 5,
        "Seconds between SIGTERM and SIGKILL when clearing a worktree."),
    _k("reaper.max_worktrees", INT, 5,
        "Linked worktrees per repo before it is flagged; 0 disables."),
    _k("reaper.ignored_untracked_dirs", STRINGS, ["node_modules", "dist", ".turbo"],
        "Directory names treated as regenerated output."),
    _k("reaper.ignored_untracked_prefixes", STRINGS, ["coverage"],
        "Directory-name prefixes treated as regenerated output."),
    _k("reaper.precious", STRINGS,
        [".env*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"],
        "File-name globs; a tree holding an ignored match is kept, not removed."),
    _k("janitor.rules", list_of(JANITOR_RULE), [], "Cleanup rules applied in order."),
    _k("janitor.watermarks", list_of(WATERMARK), [], "Size watermarks that append to the backlog."),
    _k("janitor.max_delete", INT, 200, "Delete budget for one run."),
    _k("backlog.path", STRING, "~/.config/rigops/backlog.md", "The backlog markdown file."),
    _k("backlog.areas", STRINGS,
       ["hooks", "skills", "memory", "permissions", "metrics", "repos", "docs", "jobs"],
        "Allowed area tags in backlog lines."),
    _k("sources.ccusage.argv", STRINGS, ["ccusage", "daily", "--json"],
        "Command line of the ccusage source."),
    _k("sources.ccusage.timeout_s", NUMBER, 60, "Timeout of the ccusage source."),
    _k("sources.custom", list_of(CUSTOM_SOURCE), [],
        "Custom JSON-emitting sources run on every ledger write."),
    _k("authprobe.enabled", BOOL, False, "Run the credential probe without --force."),
    _k("authprobe.command", STRINGS, ["claude", "-p", "reply with exactly: ok", "--max-turns", "1"],
        "Argv the probe runs."),
    _k("authprobe.expect", STRING, "ok", "Exact stdout expected on success."),
    _k("authprobe.timeout_s", NUMBER, 60, "Probe timeout."),
    _k("authprobe.path", STRING, "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "PATH of the scrubbed probe environment."),
    _k("authprobe.keychain_service", STRING, "", "Keychain service holding the long-lived token."),
    _k("eval.cases_dir", STRING, "", "Directory of eval case files."),
    _k("eval.system_file", STRING, "", "System prompt file sent before every case."),
    _k("eval.endpoint", STRING, "", "Base URL of the OpenAI-compatible API."),
    _k("eval.model", STRING, "", "Model id sent with every request."),
    _k("eval.api_key_env", STRING, "", "Name of the env var holding the bearer token."),
)

REMOVED: dict[str, str] = {
    "context.preload": "the card left; nothing reads it",
    "context.card": "the card left; only context.card.doctor_max_age_h is still read, by the hook",
    "context.nudges_path": "the prompt nudge left",
    "context.prompt_nudge": "the prompt nudge left",
    "doctor.imports": "the imports check left",
    "doctor.plans": "the plans check left",
    "doctor.rtk": "the rtk check left; pin a version with a doctor.checks.custom entry",
    "render": "the render command left",
    "retrieval": "the retrieval subsystem left",
    "sources.rtk": "the rtk source is not built in; add a sources.custom entry",
    "sources.caveman": "the caveman source is not built in; add a sources.custom entry",
}
REMOVED_RELEASE = "0.11"


def _build_defaults() -> dict:
    tree: dict = {}
    for key in SPEC:
        if key.default is UNSET:
            continue
        node = tree
        *parents, leaf = key.path.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = copy.deepcopy(key.default)
    return tree


DEFAULTS = _build_defaults()
_LEAVES = {key.path for key in SPEC}
_BRANCHES = {".".join(parts[:i]) for key in SPEC for parts in [key.path.split(".")]
             for i in range(1, len(parts))}


def removed_note(path: str) -> str | None:
    for prefix, note in REMOVED.items():
        if (path == prefix or path.startswith(prefix + ".")) and path not in _LEAVES:
            return note
    return None


def validate(cfg: dict) -> None:
    for key in SPEC:
        node: Any = cfg
        parts = key.path.split(".")
        for depth, part in enumerate(parts):
            if not isinstance(node, dict):
                parent = ".".join(parts[:depth])
                if parent in REMOVED:
                    break
                _fail(parent, "object", node)
            if part not in node:
                break
            node = node[part]
        else:
            key.type.check(node, key.path)


def audit(cfg: dict) -> list[tuple[str, str | None]]:
    """Every key the spec does not know, with the removal note when it is a removed one."""
    found: list[tuple[str, str | None]] = []

    def walk(node: dict, prefix: str) -> None:
        for name, value in node.items():
            path = f"{prefix}{name}"
            if path in _LEAVES:
                continue
            if path in _BRANCHES:
                if isinstance(value, dict):
                    walk(value, f"{path}.")
                continue
            found.append((path, removed_note(path)))

    walk(cfg, "")
    return sorted(found)
