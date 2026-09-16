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

State directory (where `ledger.jsonl`, `ledger.md`, `interventions.jsonl`, `eval.jsonl`, and the `doctor`/`reap` logs live): `${RIGOPS_STATE_DIR:-${XDG_STATE_HOME:-~/.local/state}/rigops}`.

### Env vars

| Var | Read by | Effect |
|---|---|---|
| `RIGOPS_CONFIG` | The CLI's config loader (every command) and `install.sh` | Overrides the config file path entirely, skipping XDG resolution. `install.sh` also treats a set value as marking the config directory "externally managed" - skipped by `--purge`. |
| `RIGOPS_STATE_DIR` | The CLI's state helper (every command that touches the ledger or logs) and `install.sh` | Overrides the state directory entirely. Same "externally managed" treatment for `--purge`. |
| `RIGOPS_INSTALL_LABEL_PREFIX` | `install.sh` only | launchd label prefix for the `doctor`/`ledger` jobs it installs (default `com.rigops`). |
| `RIGOPS_RUN_DEADLINE_S` | `rigops reap` only | Wall-clock budget in seconds for one reap run across every configured repo (default 3600). Scanning stops at the deadline; whatever's left is picked up next run. |

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

Config for the plugin's `ctx-nudge` hook, the statusline, `rigops card` (the `doctor` section below documents the gates record it reads) and `rigops nudge`.

- `nudge_tiers` (`array<int>`, minimum 3 items, default `[250000, 350000, 500000]`) - ascending token thresholds: low / heavy / critical.
- `rearm_tokens` (`int`, default `50000`) - token climb past the lowest tier required to re-fire the nudge after it's already fired once in a session.

When the `rigops` CLI isn't reachable, `plugin/hooks/ctx-nudge.sh` falls back to these exact numbers (250000 / 350000 / 500000, rearm 50000) rather than reading config; it also needs `jq`, and without `jq` the hook is a no-op. The statusline hardcodes only the first two thresholds (250000 / 350000) for coloring, has no re-arm, and can only read config values when `jq` is present - see [INSTALL.md](INSTALL.md).

```json
{"context": {"nudge_tiers": [250000, 350000, 500000], "rearm_tokens": 50000}}
```

- `prompt_nudge` (`bool`, default `true`) - when `false`, `plugin/hooks/ctx-nudge.sh` skips `rigops nudge --hook` entirely; the context-size check above still runs.
- `nudges_path` (`string`, default: `nudges.json` beside the roots registry file) - the nudge declarations `rigops nudge` matches a prompt against: `{"version": 1, "budget": 400, "repeat_after": 20, "nudges": [{"name": str, "pattern": <ERE>, "flags": "i"|"", "say": str, "realms": [str]?}]}`. A pattern that fails to compile, or an entry missing `name`/`pattern`/`say`, is skipped with a warning on stderr; a missing file means no nudges (the prefetch below still runs). `budget` (token ceiling for the matched `say` lines) and `repeat_after` (prompts before a nudge that already fired in this session can fire again) both fall back to the numbers shown if the file omits them; `--budget` on the command line wins over both.
- `card.max_tokens` (`int`, default `250`) - token ceiling for the whole `rigops card` SessionStart summary; scope-note lines are dropped from the end (never the header, the stale line or the budget line) until it fits.
- `card.doctor_max_age_h` (`int`, default `2`) - a doctor gate record (see the `doctor` section below) older than this adds a "last recorded `<N>`h ago" item to the card's `stale:` line.
- `preload.law` (`string`, default `~/.claude/CLAUDE.md`) - the always-loaded instruction file `rigops card` counts toward its preload estimate.
- `preload.fixed_tokens` (`int`, default `0`) - a flat addition to that estimate for surfaces the card cannot read itself (skill listing, agent descriptions, plugin session-start text).
- `preload.budget_tokens` (`int` or `{realm: int}`, default unset) - ceiling for the preload estimate; unset means `rigops card` never prints a `budget:` line. A map is looked up by the cwd's realm; a realm missing from the map is treated the same as unset, for that realm.

```json
{
  "context": {
    "prompt_nudge": true,
    "nudges_path": "~/rig/registry/nudges.json",
    "card": {"max_tokens": 250, "doctor_max_age_h": 2},
    "preload": {"law": "~/.claude/CLAUDE.md", "fixed_tokens": 0,
                "budget_tokens": {"work": 6000, "personal": 4000}}
  }
}
```

## `doctor`

Config for `rigops doctor` (see [REGISTRY.md](REGISTRY.md) for the judgment model this drives).

- `registry` (`string`, default `~/.config/rigops/registry.md`) - path to the `rigops.v1` registry file.
- `label_prefix` (`array<string>`, default `["local."]`) - launchd label prefixes tried, in order, to guess a registry item's label when it has no explicit `plist:`. A guess only counts when it's both currently loaded in launchd *and* starts with one of these prefixes.
- `kill_grace_s` (`number`, default `5`) - seconds between SIGTERM and SIGKILL for a hung job.
- `heal_cooldown_h` (`number`, default `12`) - hours a repeat auto-heal of the same job id is suppressed after healing it once; the action becomes `notify` instead of `kickstart` while cooling down.
- `checks.custom` (`array`, default `[]`) - each `{name, command, warn_exit, fail_exit, timeout_s, realm}` runs `command` via `/bin/sh -c`, judged by exit code: a match on `fail_exit` → `fail`; else a match on `warn_exit` → `warn`; else exit `0` → `ok`; anything else → `warn` ("unexpected exit"). On a non-`ok` result, `detail` is the first non-empty line of the command's stdout (truncated to 200 chars), falling back to the exit-code text above when stdout has none. `realm` (`string`, optional) tags the check as belonging to one realm: `rigops card` shows a realm-tagged check's `detail` only from a cwd in that same realm, and shows an untagged check's name and status everywhere but never its free-text detail (it might name another realm's things).
- `checks.disk_free` (`object` or `null`, default `null`) - `{path, warn_gb, fail_gb}`; `null` skips the check entirely. `warn_gb` defaults to `25`, `fail_gb` to `10` when the block is present but a key is omitted. `path` accepts `~` and `$VAR` expansion; the report's detail line keeps the configured string as written.
- `notify_command` (`string`, default `""`) - shell command run with the report path appended, whenever a job is failing/hung, a check fails, or anything was healed this run. Empty disables notification. Runs regardless of `--heal` (see [SAFETY.md](SAFETY.md)).
- Gates record (not configurable) - every fleet run (not `--list`, not `--config-only`) writes the non-`ok` jobs, checks and config-finding groups to `${XDG_STATE_HOME:-~/.local/state}/rigops/doctor/gates.json` - this one path ignores `RIGOPS_STATE_DIR`, so it stays off a shared or synced state directory even when one is configured: `{"version": 1, "ts": "<UTC Z>", "gates": [{"kind": "job"|"check"|"config", "name": str, "status": str, "realm": str|null, "detail": str}]}`. Written even when every gate is `ok` (`gates: []`), so its `ts` alone proves the doctor ran; `rigops card` reads it for the `stale:` line on its SessionStart summary.
- `rtk.version` (`string`, default unset) - pins the rtk token-reduction proxy: `doctor --config-only` flags a missing `rtk` or any other version. Unset skips the check.
- `budget.always_on_tokens` (`int`, default `6000`) - ceiling for the instruction surface every session pays: the global instruction file, the rules with no `paths:` frontmatter, the largest per-project memory index, and the instruction file of the largest repository in `<render.source>/registry/roots.json`. A session loads one memory index and one project file, so the largest of each is the worst case. Tokens are bytes over four.
- `plans.dir` (`string`, default `~/.claude/plans`) - the plans directory to lint. Set it and the check reports the directory itself when it is missing; left at the default, a rig that keeps no plans stays quiet.
- `pointers.sources` / `pointers.skill_roots` / `pointers.agent_roots` (`array<string>`) - files to scan and roots to resolve skills and agents against. Same rule throughout: an entry you configured that no longer exists is a finding, a default that does not exist on this rig is not. `pointers.ignore_prefixes`, `pointers.ignore_segments`, `pointers.known_mcp`, `pointers.known_agents`, `pointers.known_skills` and `pointers.known_models` replace their defaults (an empty array is a deliberate override). Session transcripts under the transcripts root are skipped by shape, so the memory store beside them is still checked.
- `pointers.memory_roots` (`array<string>`, default `~/.claude`, `~/rig`, `~/bin`, `~/.local/bin`) - memory notes (`~/.claude/projects/*/memory/*.md`, a default source) are scanned narrowly. A session recalls a note and acts on it, so a dead path in one misleads like a dead rule; a note is also a dated record, so it is held to the path rule alone, and only for paths under these roots. Another host's filesystem is a fact about that host, and a launchd label, model id, plugin, agent, skill or MCP server named in a note is history. A note recording that a path was retired names it without its root, so a root-anchored path in a note always claims the path exists.
- `levers.rules` (`object`, default unset) - lever regressions read from `ledger.jsonl`, keyed by ledger column. Each rule takes one or more of `max` (the newest row must not exceed it, for a lever whose right value is zero), `min` (the newest row must not fall below it, a floor), `rise_pct` and `drop_pct` (the newest row must not move further than this percentage, in the worse direction, from the median of up to four earlier weekly rows). Rows closer together than the ledger's 7-day window count as one week, a band needs at least two earlier weekly rows before it judges anything, and a limit judges from the first row. Every finding is a warning: printed under `warnings`, never a nonzero exit. A deliberate change is recorded with `rigops ledger note "<why>" --accept <lever>`: rows on or before that note's date are not judged for that lever, and a band's baseline restarts at the accepted row. A missing or malformed ledger, a misspelt column and a malformed rule are warnings too. Unset skips the check.
- `levers.max_age_days` (`int`, default `10`) - warn when the newest ledger row is older than this.
- `imports.max_lines` (`int`, default `400`) - line cap for every module in the rigops package, whether or not it sits in a subpackage. The two flat modules that predate the cap carry a recorded allowance in the check itself: they may shrink, never grow, and that list is deliberately not configurable.

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
- `watermarks` (`array`, default `[]`) - each `{name, path, max_files, max_kb, area}`; `path` is a glob: a directory match is judged by `max_files` and `max_kb`, a single-file match by `max_kb` only (`max_files` against a file is a configuration error and says so on stderr). A tripped watermark appends one deduplicated line to the backlog under `area` (default `"memory"`) - only under `--apply`.
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

## `eval`

Defaults for `rigops eval run` (see [EVAL.md](EVAL.md)). Each key has a matching flag, which wins for that one run.

- `cases_dir` (`string`, default `""`, flag `--cases`) - directory of case files, one JSON case per file.
- `system_file` (`string`, default `""`, flag `--system`) - system prompt sent before every case; empty sends none. Usually this is the file under test.
- `endpoint` (`string`, default `""`, flag `--endpoint`) - base URL of an OpenAI-compatible API, e.g. `http://127.0.0.1:1234/v1`; `/chat/completions` is appended unless the URL already ends with it. Credentials, a query or a fragment in the URL are refused; use `api_key_env`.
- `model` (`string`, default `""`, flag `--model`) - model id sent with every request.
- `api_key_env` (`string`, default `""`, flag `--api-key-env`) - name of the env var holding a bearer token. Empty sends no `Authorization` header. The token itself never lands in config or in `eval.jsonl`.

`cases_dir`, `endpoint` and `model` have no usable default: `rigops eval run` exits `2` naming whichever one neither the flag nor config supplied.

```json
{
  "eval": {
    "cases_dir": "~/projects/myapp/eval/cases",
    "system_file": "~/projects/myapp/prompts/system.md",
    "endpoint": "http://127.0.0.1:1234/v1",
    "model": "local-model",
    "api_key_env": ""
  }
}
```

## `prefetch` (roots registry, not `config.json`)

Not a `config.json` key: it is a top-level `prefetch` object inside the retrieval roots registry (default `~/rig/registry/roots.json`, see `rigops retrieval roots`), read by `lib/rigops/retrieval/search.py` for prompt-time prefetch calls only - an explicit `rigops retrieval search` never reads it.

- `min_score` (`number`, default `-14.0`) - bm25 ceiling (more negative is a better match; `rank > min_score` is dropped) a row must clear before the term gate below is even checked.
- `min_terms` (`int`, default `2`) - a row also needs at least this many distinct prompt content words matching it somewhere, not just the one word that got it selected.
- `min_head_terms` (`int`, default `1`) - and at least this many of those matches must land in its `title`, `section`, `answers` or `pathwords` columns, not only in the body prose.
- `max_rows` (`int`, default `3`) - caps `top` for a prefetch call regardless of what the caller asked for.
- `budget` (`int`, default `400`) - token ceiling for a prefetch call, used when the caller passes none.

The two gate keys exist because a bare score cutoff let through rows that shared one weak word with the prompt and nothing else; a prefetch row now has to clear a real term-coverage bar before it is ever silently added to the prompt.

```json
{"prefetch": {"min_score": -14.0, "min_terms": 2, "min_head_terms": 1, "max_rows": 3, "budget": 400}}
```

## See also

- [INSTALL.md](INSTALL.md) - what scaffolds the config file, and when.
- [LEDGER.md](LEDGER.md), [EVAL.md](EVAL.md), [REGISTRY.md](REGISTRY.md), [SAFETY.md](SAFETY.md) - the commands these keys drive.
