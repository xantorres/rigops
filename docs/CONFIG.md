# Config

Every key rigops reads, its type, its default, and one focused example per block.

## Resolution

Config file path: `${RIGOPS_CONFIG:-${XDG_CONFIG_HOME:-~/.config}/rigops/config.json}`.

For any given setting, the mental model is CLI flag > config file value > built-in default. In practice this means: where a command exposes a matching flag (`--dir` for `transcripts_dir`, `--registry` for `doctor.registry`, `--max-delete` for `janitor.max_delete`, `--grace-days` for `reaper.grace_days`), that flag wins for that one run; otherwise the loaded config file's value wins; a key absent from the config file falls back to the built-in default. Config values are deep-merged onto the defaults, so a config file only needs to specify what it's overriding.

There is no generic "environment variable overrides any config key" layer. A small, fixed set of env vars exist, each with one specific job - see the table below.

A missing config file is not an error: every command runs on built-in defaults alone.

Commands:

- `rigops config get <dotted.key>` - prints the value at that dotted path in the merged config. Dicts/lists print as compact JSON; scalars print bare. Exits `3` if the key isn't present anywhere in the merged config (including defaults).
- `rigops config path` - prints the resolved config file path (the file need not exist).
- `rigops config check` - loads the config (prints the load error to stderr and exits `1` on failure), warns on stderr about any top-level key not recognized by the schema, then prints `config: OK (<path>)`.

All paths anywhere in config accept `~` and `$VAR` expansion.

State directory (where `ledger.jsonl`, `ledger.md`, `interventions.jsonl`, and the `doctor`/`reap` logs live): `${RIGOPS_STATE_DIR:-${XDG_STATE_HOME:-~/.local/state}/rigops}`.

### Env vars

| Var | Read by | Effect |
|---|---|---|
| `RIGOPS_CONFIG` | The CLI's config loader (every command) and `install.sh` | Overrides the config file path entirely, skipping XDG resolution. `install.sh` also treats a set value as marking the config directory "externally managed" - skipped by `--purge`. |
| `RIGOPS_STATE_DIR` | The CLI's state helper (every command that touches the ledger or logs) and `install.sh` | Overrides the state directory entirely. Same "externally managed" treatment for `--purge`. |
| `RIGOPS_INSTALL_LABEL_PREFIX` | `install.sh` only | launchd label prefix for the `doctor`/`ledger` jobs it installs (default `com.rigops`). |
| `RIGOPS_RUN_DEADLINE_S` | `rigops reap` only | Wall-clock budget in seconds for one reap run across every configured repo (default 3600). Scanning stops at the deadline; whatever's left is picked up next run. |
| `RIGOPS_SKILL_GATES` | Plugin hook `skill-gate.sh` | Path to a skill-gate config JSON file (default `${XDG_CONFIG_HOME:-~/.config}/rigops/skill-gates.json`). No file at that path means the hook is a silent no-op. |

`XDG_CONFIG_HOME` and `XDG_STATE_HOME` feed the two resolution formulas above but aren't `RIGOPS_`-prefixed themselves.

## `transcripts_dir`

Root directory of Claude Code project transcripts. Type `string`, default `~/.claude/projects`. Everything that scans transcripts (`rigops ledger`, `rigops eit`, `rigops tax`) walks `<transcripts_dir>/*/*.jsonl` and `<transcripts_dir>/*/**/*.jsonl`.

```json
{"transcripts_dir": "~/.claude/projects"}
```

## `ledger.watch_projects`

Per-project turn-1 tracking on top of the fleet-wide ledger row. Type: object mapping a short column name to a substring.

- `watch_projects.<name>` (`string`) - a substring matched against each transcript's path *relative to `transcripts_dir`*. Any session whose transcript path contains the substring counts toward that project's `turn1_<name>` column (see [LEDGER.md](LEDGER.md)). Default: `{}` (no per-project columns).

```json
{"ledger": {"watch_projects": {"myapp": "projects/myapp"}}}
```

## `fixed_tax`

Always-loaded config counted toward the "fixed tax" - bytes that reload on every turn no matter what the turn is about. Feeds the `fixed tax` ledger column and `rigops tax`.

- `paths` (`array<string>`, default `[]`) - explicit files, always counted. A path that doesn't resolve to a file prints a warning to stderr and is skipped, not fatal.
- `globs` (`array<string>`, default `[]`) - glob patterns (recursive `**` supported). A matched file is **excluded** when its own frontmatter (the leading `---`-delimited block) declares a `paths:` or `globs:` key anywhere inside it - that marks the file as itself path-scoped or on-demand (for example a skill or hook config loaded only for certain paths), not something always loaded on every turn. Verified in `fixed_tax_entries` (`lib/rigops/levers.py`).

```json
{"fixed_tax": {"paths": ["~/.claude/CLAUDE.md"], "globs": []}}
```

## `friction`

Scopes the ledger's interaction-friction columns (`denials`, `denials_headless`, `corrections`, `tier3_breaches`, `tool_err_per_100` - see [LEDGER.md](LEDGER.md)).

- `headless_projects` (`array<string>`, default `[]`) - substrings matched against each transcript's path *relative to `transcripts_dir`* (same semantics as `ledger.watch_projects`). A denial on a transcript path containing any of these substrings also counts toward `denials_headless`.

```json
{"friction": {"headless_projects": ["-Users-example-agent-runner"]}}
```

## `context`

Config for the plugin's `ctx-nudge` hook and the statusline.

- `nudge_tiers` (`array<int>`, minimum 3 items, default `[250000, 350000, 500000]`) - ascending token thresholds: low / heavy / critical.
- `rearm_tokens` (`int`, default `50000`) - token climb past the lowest tier required to re-fire the nudge after it's already fired once in a session.

When the `rigops` CLI isn't reachable, `plugin/hooks/ctx-nudge.sh` falls back to these exact numbers (250000 / 350000 / 500000, rearm 50000) rather than reading config; it also needs `jq`, and without `jq` the hook is a no-op. The statusline hardcodes only the first two thresholds (250000 / 350000) for coloring, has no re-arm, and can only read config values when `jq` is present - see [INSTALL.md](INSTALL.md).

```json
{"context": {"nudge_tiers": [250000, 350000, 500000], "rearm_tokens": 50000}}
```

## `doctor`

Config for `rigops doctor` (see [REGISTRY.md](REGISTRY.md) for the judgment model this drives).

- `registry` (`string`, default `~/.config/rigops/registry.md`) - path to the `rigops.v1` registry file.
- `label_prefix` (`array<string>`, default `["local."]`) - launchd label prefixes tried, in order, to guess a registry item's label when it has no explicit `plist:`. A guess only counts when it's both currently loaded in launchd *and* starts with one of these prefixes.
- `kill_grace_s` (`number`, default `5`) - seconds between SIGTERM and SIGKILL for a hung job.
- `heal_cooldown_h` (`number`, default `12`) - hours a repeat auto-heal of the same job id is suppressed after healing it once; the action becomes `notify` instead of `kickstart` while cooling down.
- `checks.custom` (`array`, default `[]`) - each `{name, command, warn_exit, fail_exit, timeout_s}` runs `command` via `/bin/sh -c`, judged by exit code: a match on `fail_exit` → `fail`; else a match on `warn_exit` → `warn`; else exit `0` → `ok`; anything else → `warn` ("unexpected exit").
- `checks.disk_free` (`object` or `null`, default `null`) - `{path, warn_gb, fail_gb}`; `null` skips the check entirely. `warn_gb` defaults to `25`, `fail_gb` to `10` when the block is present but a key is omitted. `path` accepts `~` and `$VAR` expansion; the report's detail line keeps the configured string as written.
- `notify_command` (`string`, default `""`) - shell command run with the report path appended, whenever a job is failing/hung, a check fails, or anything was healed this run. Empty disables notification. Runs regardless of `--heal` (see [SAFETY.md](SAFETY.md)).

```json
{
  "doctor": {
    "registry": "~/.config/rigops/registry.md",
    "label_prefix": ["local."],
    "kill_grace_s": 5,
    "heal_cooldown_h": 12,
    "checks": {
      "custom": [{"name": "backup-fresh", "command": "test -f ~/backups/latest", "fail_exit": 1, "timeout_s": 10}],
      "disk_free": null
    },
    "notify_command": ""
  }
}
```

## `reaper`

Config for `rigops reap` (see [SAFETY.md](SAFETY.md) for the kill/removal guards).

- `roots` (`array<string>`, default `["~/projects"]`) - trees scanned for repositories. A repository is any directory with a `.git` directory two levels under a root (a `group/repo` layout).
- `sessions_dir` (`string`, default `~/.claude/sessions`) - directory of live-session JSON files (`{"pid": N, "cwd": "..."}`). A worktree whose cwd is claimed by a live session (pid still alive) is never touched. A missing directory just disables this extra guard; it isn't an error.
- `grace_days` (`int`, default `14`) - an unmerged branch with no unpushed commits becomes reap-eligible once its tip commit is older than this.
- `max_kills_per_tree` (`int`, default `20`) - more matched processes under one worktree than this means the match isn't trusted: no signals sent, no removal attempted, for that tree.
- `orphan_min_age_s` (`int`, default `172800`, 48h) - minimum runtime before a launchd-reparented, `node_modules`-rooted build daemon counts as orphaned rather than still doing real work.
- `kill_grace_s` (`number`, default `5`) - SIGTERM-to-SIGKILL grace when clearing processes out of a reapable worktree.
- `ignored_untracked_dirs` (`array<string>`, default `["node_modules", "dist", ".turbo", "@mf-types"]`) / `ignored_untracked_prefixes` (`array<string>`, default `["coverage"]`) - an untracked path is treated as regenerated build output, never work in progress, when **any one** of its path segments is listed in `ignored_untracked_dirs` or starts with a listed prefix (`lib/rigops/reap.py` `is_ignored_untracked`).

```json
{
  "reaper": {
    "roots": ["~/projects"],
    "sessions_dir": "~/.claude/sessions",
    "grace_days": 14,
    "max_kills_per_tree": 20,
    "orphan_min_age_s": 172800,
    "kill_grace_s": 5,
    "ignored_untracked_dirs": ["node_modules", "dist", ".turbo", "@mf-types"],
    "ignored_untracked_prefixes": ["coverage"]
  }
}
```

## `janitor`

Config for `rigops janitor` (see [SAFETY.md](SAFETY.md) for the delete cap).

- `rules` (`array`, default `[]`) - applied in order. Each rule:
  - `name`, `path` (base file or directory), `glob` (default `*`; matched against entry **names** only - a pattern containing `/` matches nothing), `older_than_days`, `max_depth`.
  - `action`: `delete` (remove every match) · `truncate` (keep the last `keep_lines` lines of a file, default `500`) · `keep_newest_n` (delete all but the newest `keep` matches by mtime, default `3`) · `report` (list matches, never act) · `command` (run `command` via `/bin/sh -c` with `timeout_s`, default `120`, under `--apply` only).
  - `dirs` (`bool`) - match directories instead of files. `only_empty` (`bool`) - with `dirs`, match only empty directories.
- `watermarks` (`array`, default `[]`) - each `{name, path, max_files, max_kb, area}`; `path` is a directory or a glob over directories. A tripped watermark appends one deduplicated line to the backlog under `area` (default `"memory"`) - only under `--apply`.
- `max_delete` (`int`, default `200`) - total delete/`keep_newest_n` budget for the whole run. A directory candidate counts as one plus every file and directory under it. A rule whose match total would push the running count over the cap is skipped **whole**, never partially applied.

```json
{
  "janitor": {
    "rules": [
      {"name": "stale-spool", "path": "~/.local/state/myapp/spool", "glob": "*.json", "older_than_days": 7, "action": "delete"}
    ],
    "watermarks": [
      {"name": "auto-memory", "path": "~/.claude/projects/*/memory", "max_files": 60, "max_kb": 400, "area": "memory"}
    ],
    "max_delete": 200
  }
}
```

## `backlog`

Config for `rigops backlog`.

- `path` (`string`, default `~/.config/rigops/backlog.md`) - the backlog markdown file.
- `areas` (`array<string>`, default `["hooks", "skills", "memory", "permissions", "metrics", "repos", "docs", "jobs"]`) - allowed area tags in backlog lines.

```json
{
  "backlog": {
    "path": "~/.config/rigops/backlog.md",
    "areas": ["hooks", "skills", "memory", "permissions", "metrics", "repos", "docs", "jobs"]
  }
}
```

## `sources`

External CLIs mined for ledger row annotations, stored under `row.sources` in `ledger.jsonl` only - never as columns in `ledger.md` (see [LEDGER.md](LEDGER.md)).

- `rtk` (default `{"argv": ["rtk", "gain", "--format", "json"], "timeout_s": 20}`) - probe-gated: `rtk.probe()` is just `shutil.which("rtk")`; if the binary isn't on PATH, the annotation is `null` and no subprocess is ever spawned.
- `ccusage` (default `{"argv": ["ccusage", "daily", "--json"], "timeout_s": 60}`) - same probe-gating. Context annotation only; spend accounting stays `ccusage`'s job, rigops never re-counts it.
- `custom` (`array`, default `[]`) - each `{name, argv, timeout_s}` runs an arbitrary JSON-emitting CLI, stored under its own `name`. `timeout_s` defaults to `30` if omitted. The names `rtk` and `ccusage` are reserved for the built-in adapters above; a custom entry reusing either name is dropped before it runs. **Every entry here runs on every `rigops ledger write`, including `--dry-run`** (though not on `--show`, which never computes a row) - give them read-only commands.

```json
{
  "sources": {
    "rtk": {"argv": ["rtk", "gain", "--format", "json"], "timeout_s": 20},
    "ccusage": {"argv": ["ccusage", "daily", "--json"], "timeout_s": 60},
    "custom": [{"name": "myprobe", "argv": ["mytool", "stats", "--json"], "timeout_s": 10}]
  }
}
```

## `authprobe`

Headless-credential probe: does the unattended `claude -p` credential still work, independent of an interactive login session. Normally wired into `doctor` via a `checks.custom` entry that runs `rigops authprobe`.

- `enabled` (`bool`, default `false`) - ships off. Run with `--force` on the command line to bypass without touching config.
- `command` (`array<string>`, default `["claude", "-p", "reply with exactly: ok", "--max-turns", "1"]`) - argv run under a scrubbed environment.
- `expect` (`string`, default `"ok"`) - exact stdout expected on success.
- `timeout_s` (`number`, default `60`).
- `path` (`string`, default `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`) - `PATH` for the scrubbed probe environment (the probe otherwise inherits only `HOME`, `USER`, `SHELL=/bin/sh`, `TERM=dumb`, plus an optional injected token).
- `keychain_service` (`string`, default `""`) - macOS keychain service name holding a long-lived token, injected as `CLAUDE_CODE_OAUTH_TOKEN`. Empty (default) skips the keychain read entirely.

```json
{
  "authprobe": {
    "enabled": false,
    "command": ["claude", "-p", "reply with exactly: ok", "--max-turns", "1"],
    "expect": "ok",
    "timeout_s": 60,
    "path": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    "keychain_service": ""
  }
}
```

## See also

- [INSTALL.md](INSTALL.md) - what scaffolds the config file, and when.
- [LEDGER.md](LEDGER.md), [REGISTRY.md](REGISTRY.md), [SAFETY.md](SAFETY.md) - the commands these keys drive.
