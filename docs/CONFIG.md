# Config

Every key rigops reads, its type, its default, and one focused example per block.

## Resolution

Config file path: `${RIGOPS_CONFIG:-${XDG_CONFIG_HOME:-~/.config}/rigops/config.json}`.

For any given setting, the mental model is CLI flag > config file value > built-in default. In practice this means: where a command exposes a matching flag (`rigops tax --dir` for `transcripts_dir`, `rigops doctor --registry` for `doctor.registry`, `rigops janitor --max-delete` for `janitor.max_delete`, `rigops reap --grace-days` for `reaper.grace_days`), that flag wins for that one run; otherwise the loaded config file's value wins; a key absent from the config file falls back to the built-in default. Config values are deep-merged onto the defaults, so a config file only needs to specify what it's overriding.

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
| `RIGOPS_INSTALL_LABEL_PREFIX` | `install.sh` only | launchd label prefix for the jobs it installs (default `com.rigops`). |
| `RIGOPS_RUN_DEADLINE_S` | `rigops reap` only | Wall-clock budget in seconds for one reap run across every configured repo (default 3600). Scanning stops at the deadline; whatever's left is picked up next run. |

`XDG_CONFIG_HOME` and `XDG_STATE_HOME` feed the two resolution formulas above but aren't `RIGOPS_`-prefixed themselves.

## `transcripts_dir`

Root directory of Claude Code project transcripts. Type `string`, default `~/.claude/projects`. Everything that scans transcripts (`rigops ledger`, `rigops eit`, `rigops tax`) walks `<transcripts_dir>/*/*.jsonl` and `<transcripts_dir>/*/**/*.jsonl`. A `--dir` that names one project directory (it holds session files directly) walks that directory's `*.jsonl` and `**/*.jsonl` instead and keeps its project label.

```json
{"transcripts_dir": "~/.claude/projects"}
```

## `ledger.watch_projects`

Per-project turn-1 tracking on top of the fleet-wide ledger row. Type: object mapping a short column name to a substring.

- `watch_projects.<name>` (`string`) - a substring matched against each transcript's path *relative to `transcripts_dir`*. Any session whose transcript path contains the substring counts toward that project's `turn1_<name>` column (see [LEDGER.md](LEDGER.md)). Default: `{}` (no per-project columns).

```json
{"ledger": {"watch_projects": {"myapp": "projects/myapp"}}}
```

## `ledger.derive_interventions`

Git repositories whose commits `rigops ledger diff` lists as derived interventions, beside the manual notes. Type: array, default `[]` (none). Each entry is a repo path string, or `{repo, paths}`:

- `repo` (`string`) - the repository, `~` expanded.
- `paths` (`array<string>`) - git glob pathspecs, relative to the repo root, that count as always-loaded config. Default: `**/CLAUDE.md`, `**/AGENTS.md`, `**/settings.json`, `**/settings.local.json`, `**/.mcp.json`, `**/SKILL.md` and `rules/**`.

A commit counts when it is not a merge, touches at least one of those paths and its committer date (local time) falls inside the diff window. Derived entries are computed on every diff and never written to `interventions.jsonl`. A repo that is missing or not a git repository prints a warning on stderr and is skipped.

```json
{"ledger": {"derive_interventions": ["~/dotfiles", {"repo": "~/dotfiles-work", "paths": ["rules/**", "law/**"]}]}}
```

## `ledger` correction and denial patterns

Three keys tune what the `corrections` and `denials` metrics and `rigops mine` count. Each is an array of regular expressions (Python `re`), default `[]`. Every entry is compiled with `IGNORECASE`; a non-array, a non-string entry or an entry that does not compile stops the command with `error: ledger.<key>[<index>]: <reason>`.

- `extra_correction_patterns` - a human prompt counts as a correction when its *first non-blank line* is matched (`search`, anywhere in the line) by one of these, in addition to the built-in English rule (which anchors at the start of the line).
- `correction_exclude_patterns` - a line matched (`search`) by one of these never counts, whether the built-in rule or an extra pattern found it. Use it for lines the built-in leading-`no` rule misreads, such as Romance-language statements.
- `extra_denial_patterns` - an `is_error` tool result counts as a denial when its flattened text is matched (`search`) by one of these, in addition to the built-in set.

Changing any of them changes `config_sha`, so `rigops ledger diff` shows `measurement config: changed`. `tests/fixtures/corrections/es.json` is a worked example for a Spanish-speaking rig.

```json
{"ledger": {
  "extra_correction_patterns": ["^\\s*eso\\s+no\\b"],
  "correction_exclude_patterns": ["^\\s*no\\s+se\\b"],
  "extra_denial_patterns": ["checkout behind origin"]
}}
```

## `ledger.week_reports`

A glob of weekly reports written by tools other than rigops. Type: string, default unset. No rigops command reads it. The `/rigops:week` ritual reads the matching file with the newest modification time and lists what it proposes beside its own findings. `rigops config get ledger.week_reports` exits `3` while the key is unset, and the ritual skips the read.

```json
{"ledger": {"week_reports": "~/reports/weekly-*.md"}}
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

Config for the plugin's `rigops hook` command and the statusline example (the `doctor` section below documents the gates record the hook reads).

- `nudge_tiers` (`array<int>`, minimum 3 items, default `[250000, 350000, 500000]`) - ascending token thresholds: low / heavy / critical.
- `rearm_tokens` (`int`, default `50000`) - token climb past the lowest tier required to re-fire the nudge after it's already fired once in a session.

`rigops hook <event>` is what the plugin's two hooks run: `session-start` prints one line of stale doctor gates (or one line saying the gate record is missing or unreadable), and `user-prompt-submit` prints the context-size nudge. It always exits 0, prints at most 1000 bytes and reads the payload from stdin. Without these keys, or with invalid values, it uses the defaults shown here (250000 / 350000 / 500000, rearm 50000). The statusline example colours by the first two thresholds only, has no re-arm, and can only read config values when `jq` is present - see [../patterns/statusline.md](../patterns/statusline.md).

```json
{"context": {"nudge_tiers": [250000, 350000, 500000], "rearm_tokens": 50000}}
```

- `card.doctor_max_age_h` (`int`, default `2`) - a doctor gate record (see the `doctor` section below) older than this is a stale gate record to the session-start hook (`rigops hook`), which says so.

```json
{"context": {"card": {"doctor_max_age_h": 2}}}
```

## `doctor`

Config for `rigops doctor` (see [REGISTRY.md](REGISTRY.md) for the judgment model this drives).

- `registry` (`string`, default `~/.config/rigops/registry.md`) - path to the `rigops.v1` registry file. When the default path does not exist, doctor prints one `note:` line on stderr, treats the fleet as empty and runs the rest (config checks, gates record); `--list` prints an empty list. A missing `--registry PATH` is still an error and exits 1.
- `label_prefix` (`array<string>`, default `["local."]`) - launchd label prefixes tried, in order, to guess a registry item's label when it has no explicit `plist:`. A guess only counts when it's both currently loaded in launchd *and* starts with one of these prefixes. The pointer check also reads them: a prefix ending in a dot followed by a hyphenated name (`user.sync-job`) in a scanned file must be a loaded label, on top of the `com.` and `local.` labels it always checks.
- `kill_grace_s` (`number`, default `5`) - seconds between SIGTERM and SIGKILL for a hung job.
- `heal_cooldown_h` (`number`, default `12`) - hours a repeat auto-heal of the same job id is suppressed after healing it once; the action becomes `notify` instead of `kickstart` while cooling down.
- `cause_policy` (`"off"|"shadow"|"on"`, default `"off"`) - classify each failing job from the tail of its plist log (auth, quota, network, deterministic, unknown) and act on the cause. `shadow` changes nothing and writes what `on` would do as `would` in `events.jsonl`; `on` holds auth, quota and network failures instead of kicking them (retry every 6h, 4h and 1h, network at most 3 per day), never kicks a deterministic failure, and keeps today's behaviour for unknown. Quota and network holds page only after 24h. Hold state lives in `holds.json` beside `events.jsonl` (written by `--heal` runs, in `shadow` too; the probe runs only in `on` with `--heal`) and clears when the job stops failing. A released hold kicks through `heal_cooldown_h` but not through the cooldown of a kill (a kill also stamps `kill:<id>` in `heal-stamps.json`); its spacing (1h for network) and cap (3 per 24h) count every kick of the launchd label, so a sibling's kick or released hold holds it too, and a kick stamps every held id of its label. Each `would` entry carries `id`, `cause`, `action`, `would`, `exit`, `tail_sha` (first 12 hex of the sha256 of the log tail, never the tail, which can carry secrets; `e3b0c44298fc` is an empty tail) and `log` (the log read, home as `~`). Review the classification from `cause`, never from `would`: `would` is the action the hold clock allows at that moment, so the same `cause: network` reads `kickstart` or `hold` depending on it, and in `shadow` the probe never runs (DNS down at +2h: shadow says `kickstart`, `on` holds; DNS ok on the second pass: shadow says `hold`, `on` kicks). A review needs n>0 non-empty entries (an empty `would` proves nothing) and compares `cause` with the log only for jobs whose log carries timestamps; `ops.nightly` and `ops.weekly` logs are empty or absent and always classify `unknown`. `API Error: 529`, `ETIMEDOUT` and `ECONNREFUSED` classify as network while DNS still resolves, so the kick comes on the next pass; `529 overloaded_error` without the `API Error:` prefix classifies `unknown`, and no plist job log holds that bare form (checked 2026-11-04), so the pattern is not added.
- `cause_probe_command` (`string`, default `""`) - optional command, split like a shell line and run without one; exit 0 releases one kick, anything else keeps the hold. Auth and quota holds ask it at each retry interval; a network hold asks it on every run, so it lifts as soon as the probe passes (kicks at least 1h apart, 3 a day). Empty or whitespace-only means unset: the hold releases on the interval alone. A value that is not a string is ignored with a warning event. A probe that times out (120s), is missing or cannot be parsed keeps the hold and records a warning event; an unexpected exception also keeps it. The probe runs at most once per doctor run, whatever the number of held jobs. A DNS probe: `/usr/bin/python3 -c "import socket; socket.getaddrinfo('example.com', 443)"` exits 0 only when the name resolves.
- `checks.custom` (`array`, default `[]`) - each `{name, command, warn_exit, fail_exit, timeout_s}` runs `command` via `/bin/sh -c`, judged by exit code: a match on `fail_exit` → `fail`; else a match on `warn_exit` → `warn`; else exit `0` → `ok`; anything else → `warn` ("unexpected exit"). On a non-`ok` result, `detail` is the first non-empty line of the command's stdout (truncated to 200 chars), falling back to the exit-code text above when stdout has none. Unknown keys on a check are ignored.
- `checks.disk_free` (`object` or `null`, default `null`) - `{path, warn_gb, fail_gb}`; `null` skips the check entirely. `warn_gb` defaults to `25`, `fail_gb` to `10` when the block is present but a key is omitted. `path` accepts `~` and `$VAR` expansion; the report's detail line keeps the configured string as written.
- `notify_command` (`string`, default `""`) - shell command run with the report path appended, when a job is failing/hung or a check fails under a key not yet sent, or one last sent 24 hours ago or more (sent keys live in `doctor/notified.json`; a failure that stays clear for 6 hours and returns is new again, one that flaps sooner is the same finding). A job the doctor kicked does not page, and neither does any other id on the launchd label it kicked, whatever its action: a key gets one silent kick per 7 days (`kicked.json`, kept across a recovery and across a failed send), and a further kick of it inside 7 days pages by the normal rules. If the kick did not work the next run sees it failing with its cooldown active and pages. A kill (a hung job) pages even past the daily ceiling, does not count against it and is never `held`; a kill that returns within 6h of clearing is the same finding, so it stays silent until its `sent` stamp is 24h old. For everything else at most 2 pages go out per local day, and checks share that ceiling with jobs, so checks alone can use it up and hold a job page: keys due beyond it stay unstamped, are recorded as `held` in the event and page the next day if still failing, so a page held by the ceiling is lost when the job is cured by a kick before then. The 7-day rule has a cost under the ceiling: a key cured by a kick every night pages about 6 nights in 7, and each such page spends a ceiling slot (replay: 12 more pages, and held passes rise from 1 to 9). A kicked.json written as a list by an older version migrates with the mark time set to now. The ceiling is soft against a notifier that delivers and then exits nonzero: the count is not saved on a failed send, so each retry can deliver again. A failure that returns within 6h of clearing, even with a different cause, keeps its key and stays silent until its `sent` stamp is 24h old. `RECOVERY_H=6` was swept with `python3 tools/replay-notify.py <events.jsonl> --until 2026-11-03T15:52 --recovery-h N` (a model, not a measurement: that window holds no check pages). Under the ceiling, 5h and 6h leave one day over 2 (2026-08-27, a kill, which the ceiling does not cap) and 3h and 4h leave two; 6h does not stand out there, only in delaying fewer passes (9, against 45, 88 and 119 at 5, 4 and 3h) and in the uncapped maximum (3 a day against 4). Empty disables notification. A custom check that times out is a warning and never notifies. Runs regardless of `--heal` (see [SAFETY.md](SAFETY.md)).
- Pinning a tool version is a custom check, for example rtk: `{"name": "rtk-pin", "command": "test \"$(rtk --version)\" = \"rtk 0.42.4\"", "fail_exit": 1, "timeout_s": 10}`. The command exits 0 on the pinned output and 1 otherwise (a missing binary also exits nonzero, so it fails the same way). Under launchd, call the binary by absolute path.
- Gates record (not configurable) - every fleet run (not `--list`, not `--config-only`) writes the non-`ok` jobs, checks and config-finding groups to `${XDG_STATE_HOME:-~/.local/state}/rigops/doctor/gates.json` - this one path ignores `RIGOPS_STATE_DIR`, so it stays off a shared or synced state directory even when one is configured: `{"version": 1, "ts": "<UTC Z>", "gates": [{"kind": "job"|"check"|"config", "name": str, "status": str, "detail": str, "since": "<UTC Z>"}]}`. `since` is the `ts` of the run a gate first appeared in and carries over while the gate stays in the record; a gate that clears, or that a `--check` run leaves out, starts over. The default output's first line counts failing or hung jobs, failing checks and failing config findings (`OK` when there are none), and a `red for:` line gives each failing gate's age from `since`; `--report` adds a `red for` column to the jobs and checks tables. Written even when every gate is `ok` (`gates: []`), so its `ts` alone proves the doctor ran; the session-start hook (`rigops hook`) reads it for its stale line. A state directory that can't be created for this write logs a warning on stderr and never fails the run.
- `budget.always_on_tokens` (`int`, default `6000`) - ceiling for the instruction surface every session pays: the global instruction file, the rules with no `paths:` frontmatter, and the largest per-project memory index. A session loads one memory index, so the largest is the worst case. Tokens are bytes over four.
- `pointers.sources` / `pointers.skill_roots` / `pointers.agent_roots` (`array<string>`) - files to scan and roots to resolve skills and agents against. Same rule throughout: an entry you configured that no longer exists is a finding, a default that does not exist on this rig is not. `pointers.ignore_prefixes`, `pointers.ignore_segments`, `pointers.known_mcp`, `pointers.known_agents`, `pointers.known_skills` and `pointers.known_models` replace their defaults (an empty array is a deliberate override). Session transcripts under the transcripts root are skipped by shape, so the memory store beside them is still checked.
- `pointers.memory_roots` (`array<string>`, default `~/.claude`, `~/bin`, `~/.local/bin`) - memory notes (`~/.claude/projects/*/memory/*.md`, a default source) are scanned narrowly. A session recalls a note and acts on it, so a dead path in one misleads like a dead rule; a note is also a dated record, so it is held to the path rule alone, and only for paths under these roots. Another host's filesystem is a fact about that host, and a launchd label, model id, plugin, agent, skill or MCP server named in a note is history. A note recording that a path was retired names it without its root, so a root-anchored path in a note always claims the path exists.
- `levers.rules` (`object`, default `{}`) - lever regressions read from `ledger.jsonl`, keyed by ledger column. Each rule takes one or more of `max` (the newest row must not exceed it, for a lever whose right value is zero), `min` (the newest row must not fall below it, a floor), `rise_pct` and `drop_pct` (the newest row must not move further than this percentage, in the worse direction, from the median of up to four earlier weekly rows). Rows closer together than the ledger's 7-day window count as one week, a band needs at least two earlier weekly rows before it judges anything, and a limit judges from the first row. Every finding is a warning: printed under `warnings`, never a nonzero exit. A deliberate change is recorded with `rigops ledger note "<why>" --accept <lever>`: rows on or before that note's date are not judged for that lever, and a band's baseline restarts at the accepted row. A missing or malformed ledger, a misspelt column and a malformed rule are warnings too. Built-in rules apply on top: `turn1_p50`, `turn1_{group}` and `turn1_{group}_p50` rise 30%, `eit_per_turn` rise 20%, `cache_hit_pct` drop 3%, `fixed_tax_b` rise 10%, `start_tool_chars` rise 10%, `rebuild_eit_pct` rise 30%, `main_default_model_pct` min 50, `corrections` rise 150%, `tool_err_per_100` rise 50%. `{group}` stands for every `ledger.watch_projects` key, in built-in and configured rule keys alike. A configured rule replaces the built-in one for its lever and `null` turns a lever off. A built-in rule whose column the ledger lacks is skipped without a warning, and built-ins alone never report a missing, empty or stale ledger. The tolerances sit just above the widest swing in the author's flat weeks: replaying a 21-row ledger with `python3 tools/replay-levers.py --defaults-only -v` fired on the weeks a session-start change landed and on none of the ten weeks before them. `start_tool_chars` is newer than that ledger; four weekly backfills moved 1% between flat weeks and 15% on the week the overall turn-1 p50 jumped. `rebuild_eit_pct` is newer too; nine weekly backfills sat between 7.6% and 11.5% until compaction rebuilds pushed one week to 16.1%, the only week a 30% rise fires on. `main_default_model_pct` is a floor, not a band: weekly backfills ran 97-98% while settings named one family and 8-30% every week after settings switched to another while sessions kept picking the old one. Set it to `null` when sessions choose their model on purpose. Replay your own ledger the same way before tightening one.
- `levers.defaults` (`bool`, default `true`) - `false` drops the built-in rules and judges only `levers.rules`.
- `levers.max_age_days` (`int`, default `10`) - warn when the newest ledger row is older than this.

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

Config for `rigops reap` (see [SAFETY.md](SAFETY.md) for the kill/removal guards). For one run, `--root <dir>` scans that tree in place of `roots`, `--repo <path>` (repeatable) limits the worktree scan to the named repositories, while the orphan-daemon sweep still walks `--root` or `roots`. `--no-fetch` skips `git fetch`, so the merged check reads local refs only, and `--json` prints the report as JSON.

- `roots` (`array<string>`, default `[]`) - trees scanned for repositories. A repository is any directory with a `.git` directory two levels under a root (a `group/repo` layout). With no roots configured and no `--root`, `rigops reap` prints one line to stderr naming the config path and exits 0 without scanning; `--repo` still works.
- `sessions_dir` (`string`, default `~/.claude/sessions`) - directory of live-session JSON files (`{"pid": N, "cwd": "..."}`). A worktree whose cwd is claimed by a live session (pid still alive) is never touched. A missing directory just disables this extra guard; it isn't an error.
- `grace_days` (`int`, default `14`) - an unmerged branch with no unpushed commits becomes reap-eligible once its tip commit is older than this.
- `max_kills_per_tree` (`int`, default `20`) - more matched processes under one worktree than this means the match isn't trusted: no signals sent, no removal attempted, for that tree.
- `orphan_min_age_s` (`int`, default `172800`, 48h) - minimum runtime before a launchd-reparented, `node_modules`-rooted build daemon counts as orphaned rather than still doing real work.
- `orphan_sweep` (`bool`, default `false`) - the orphan-daemon sweep (see `orphan_min_age_s`) runs only when this is `true`; otherwise reap never looks for or signals orphans. Anything but a JSON boolean exits 2 with an error naming the key.
- `kill_grace_s` (`number`, default `5`) - SIGTERM-to-SIGKILL grace when clearing processes out of a reapable worktree.
- `max_worktrees` (`int`, default `5`) - a repository left with more linked worktrees than this, once the run's removals and prunes land, is flagged: a `!` line in the report, `over_cap` and `worktrees_left` in its `--json` entry, and an `over_cap=` count in the log line. Nothing extra is removed. `0` turns the check off. Anything but a non-negative integer exits 2 with an error naming the key.
- `ignored_untracked_dirs` (`array<string>`, default `["node_modules", "dist", ".turbo"]`) / `ignored_untracked_prefixes` (`array<string>`, default `["coverage"]`) - an untracked path is treated as regenerated build output, never work in progress, when **any one** of its path segments is listed in `ignored_untracked_dirs` or starts with a listed prefix (`lib/rigops/reap.py` `is_ignored_untracked`).

```json
{
  "reaper": {
    "roots": ["~/projects"],
    "sessions_dir": "~/.claude/sessions",
    "grace_days": 14,
    "max_kills_per_tree": 20,
    "orphan_min_age_s": 172800,
    "orphan_sweep": false,
    "kill_grace_s": 5,
    "max_worktrees": 5,
    "ignored_untracked_dirs": ["node_modules", "dist", ".turbo"],
    "ignored_untracked_prefixes": ["coverage"]
  }
}
```

## `janitor`

Config for `rigops janitor` (see [SAFETY.md](SAFETY.md) for the delete cap). `--json` prints the run report as JSON.

- `rules` (`array`, default `[]`) - applied in order. Each rule:
  - `name`, `path` (base file or directory), `glob` (default `*`; matched against entry **names** only - a pattern containing `/` matches nothing), `older_than_days`, `max_depth`. `older_than_days` ages a file by its mtime and a directory by the newest mtime of anything under it, so a directory holding one recent file is not old; an entry is never old when it, or anything under it, cannot be stat'd or listed. Age counts in seconds: `older_than_days: 1` matches anything over 24 hours old. `find -mtime +N` rounds down to whole days and matches only entries at least `N+1` days old, so port it as `older_than_days: N+1`.
  - `action`: `delete` (remove every match) · `truncate` (keep the last `keep_lines` lines of a file, default `500`; with `archive: true` the dropped head is first appended to `<file>.<YYYY-MM-DD>`, left for a `delete` rule to age out) · `keep_newest_n` (delete all but the newest `keep` matches by mtime, default `3`) · `report` (list matches, never act) · `command` (run `command` via `/bin/sh -c` with `timeout_s`, default `120`, under `--apply` only).
  - `dirs` (`bool`) - match directories instead of files. `only_empty` (`bool`) - with `dirs`, match only empty directories; one that gains an entry before removal is left in place and reported as an error.
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

- `ccusage` (default `{"argv": ["ccusage", "daily", "--json"], "timeout_s": 60}`) - probe-gated: `ccusage.probe()` is just `shutil.which("ccusage")`; if the binary isn't on PATH, the annotation is `null` and no subprocess is ever spawned. Context annotation only; spend accounting stays `ccusage`'s job, rigops never re-counts it.
- `custom` (`array`, default `[]`) - each `{name, argv, timeout_s}` runs an arbitrary JSON-emitting CLI, stored under its own `name`. `timeout_s` defaults to `30` if omitted. The name `ccusage` is reserved for the built-in adapter above; a custom entry reusing it is dropped before it runs. **Every entry here runs on every `rigops ledger write`, including `--dry-run`** (though not on `--show`, which never computes a row) - give them read-only commands.

A custom source stores the command's whole JSON output for the run, unparsed and not windowed to the row's week, and the summary line does not print its figures. Two examples, a token-reduction proxy's totals and a compression proxy's trailing 7 days:

```json
{
  "sources": {
    "custom": [
      {"name": "rtk", "argv": ["rtk", "gain", "--format", "json"], "timeout_s": 20},
      {"name": "caveman", "argv": ["caveman", "stats", "--json", "--days", "7"], "timeout_s": 20}
    ]
  }
}
```

Both tools report totals for their own span (all time, and the 7 days ending now), so a backdated `--at` row reads the current figures. An absent binary makes the entry `null`.

```json
{
  "sources": {
    "ccusage": {"argv": ["ccusage", "daily", "--json"], "timeout_s": 60},
    "custom": [{"name": "myprobe", "argv": ["mytool", "stats", "--json"], "timeout_s": 10}]
  }
}
```

## `authprobe`

Headless-credential probe: does the unattended `claude -p` credential still work, independent of an interactive login session. Normally wired into `doctor` via a `checks.custom` entry that runs `rigops authprobe`. `--json` prints the result as one JSON object.

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

## See also

- [INSTALL.md](INSTALL.md) - what scaffolds the config file, and when.
- [LEDGER.md](LEDGER.md), [EVAL.md](EVAL.md), [REGISTRY.md](REGISTRY.md), [SAFETY.md](SAFETY.md) - the commands these keys drive.
