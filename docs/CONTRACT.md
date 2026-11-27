# Contract

This page lists what rigops 1.x promises to scripts, CI jobs and the plugin: commands, exit codes, `--json` output, state files, config, environment variables, hooks, extension points and the eval case schema. Text output is for people and can change in any release, so a script reads exit codes and `--json`. [Compatibility and deprecation](#compatibility-and-deprecation) says how those may change.

## Tiers

| Tier | What | Promise |
|---|---|---|
| Stable | `ledger` (`write`, `note`, `diff`), `tax`, `doctor` with its `jobs`, `budget` and `levers` checks, `reap` for worktrees, `janitor`, `eval` (`run`, `diff`, `list`), `config`, `jobs`, `version` | Everything on this page, through 1.x. |
| Plugin interface | `rigops hook <event>` | Stable, but called only by the plugin, so it is left out of `rigops --help`. |
| Experimental | `authprobe`, `backlog`, `eit`, `mine`; the `pointers`, `mcp`, `memory` and `agents` doctor checks; the reap orphan sweep; the context-size nudge tiers; janitor watermarks | Can change or go in any minor release. [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md#experimental-commands) lists the experimental commands, and `rigops --help` names them on its last line. |
| Internal | Python modules and imports, how doctor finds its `check_*.py` modules, text output, plist and crontab contents | No promise. |

## Commands

`rigops --help` lists the commands. Every command and subcommand takes `-h` and `--help` and exits `0`. Bare `rigops`, `rigops help` and `rigops --help` print the usage and exit `0`; `rigops --version` is the same as `rigops version`. An unknown command exits `2`.

| Command | Subcommands | Kind | `--json` |
|---|---|---|---|
| `rigops doctor` | | gate | yes |
| `rigops eval` | `run`, `diff`, `list` | `diff` is a gate | yes |
| `rigops ledger` | `write` (the default), `note`, `diff` | `diff` is a report | `write` and `diff` |
| `rigops tax` | | report | yes |
| `rigops reap` | | action | yes |
| `rigops janitor` | | action | yes |
| `rigops jobs` | `install`, `uninstall`, `status` | action | no |
| `rigops config` | `get`, `path`, `check` | | no; `get` prints JSON |
| `rigops version` | | | no |
| `rigops authprobe` | | gate, experimental | yes |
| `rigops backlog` | `lint`, `add` | `lint` is a gate, experimental | `lint` |
| `rigops eit` | | report, experimental | yes |
| `rigops mine` | | report, experimental | no |
| `rigops hook` | `session-start`, `user-prompt-submit` | plugin interface | no |

The flags of each command are in its own page: [LEDGER.md](LEDGER.md), [EVAL.md](EVAL.md), [REGISTRY.md](REGISTRY.md) for `doctor`, [SAFETY.md](SAFETY.md) for `reap` and `janitor`, [INSTALL.md](INSTALL.md) for `jobs`, [CONFIG.md](CONFIG.md) for `config`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Ok. |
| `1` | A negative verdict: a regression, a failing check, a failing or hung job, a key that is not there. |
| `2` | An error, or a run that cannot judge: bad usage, bad config, malformed state, an unverified eval, nothing to compare, a refused guard. |

When both `1` and `2` apply, `2` wins. A `2` never carries a verdict, so a caller that treats any non-zero code as "not passed" is always right.

| Command | `0` | `1` | `2` |
|---|---|---|---|
| `rigops doctor` | Nothing failing | A failing or hung job, a failing custom check, or a finding with `fail` severity. `--exit-zero` turns this into `0` | A finding with `error` severity (a check that crashed or failed to import, an input it could not read, a plist it could not read or parse), unreadable or newer guard state, a missing explicit registry or a malformed one. Guard state gives `2` even with `--exit-zero` |
| `rigops eval run` | The run is recorded, whatever the cases scored | | A missing or invalid setting, an unreadable system prompt, an empty or missing cases directory, an invalid case file, or a bearer token that would go over plain http to a host that is not loopback |
| `rigops eval diff` | No regression | A regression and nothing unverified or dropped | A case unverified or dropped, no runs, no run matching `--base` or `--head`, base and head the same run, no shared cases, or a bad `--latency-tolerance` |
| `rigops eval list` | Listed | | Unreadable file or row |
| `rigops ledger write` | Row written, or printed with `--dry-run` or `--show` | | `transcripts_dir` does not exist, or the ledger is unreadable or malformed |
| `rigops ledger note` | Recorded | | Bad `--at` |
| `rigops ledger diff` | Compared, or fewer than two rows to compare | | A bad `--since`, or an unreadable file |
| `rigops tax` | Printed | `--starts --session <id>` matched no session | A bad `--dir`, `--since` or `--until`, or an unreadable ledger under `--history`. A malformed row is skipped and counted, not an error |
| `rigops reap` | Done or planned | | A removal failed under `--apply`, or a live-session file is unreadable |
| `rigops janitor` | Done or planned | | A rule or watermark error |
| `rigops jobs install`, `rigops jobs uninstall` | Done or planned; on Linux, crontab lines printed | | An unknown `--jobs` name, no persistent `rigops` on `PATH` to schedule, or a plist it cannot render or read |
| `rigops jobs status` | Every installed job is loaded and its program passes, or nothing is installed | An installed job is not loaded or its program check fails | launchd cannot be asked or a plist cannot be read or parsed; any run on Linux |
| `rigops config get` | Printed | The key is not set | Config unreadable |
| `rigops config check` | Valid; unknown keys are warnings | | Unreadable, not JSON, not an object, or a key of the wrong type |
| `rigops authprobe` | Probe passed, or disabled | Probe failed | Config error |
| `rigops backlog lint` | Clean, or no backlog file | Malformed lines or unknown areas | The backlog file cannot be read |
| `rigops mine` | Printed with `--dry-run` | | Run without `--dry-run` |
| `rigops hook` | Always | | |

Every command also exits `2` on a usage error, on a config that cannot be loaded, and on a config or registry that group or others can write where [SAFETY.md](SAFETY.md) says it is checked. An interrupted `eval run` exits `130`. A Python traceback with exit `1` is a bug, not a verdict; please report it with the bug template.

## Gates and reports

A gate's exit code is the contract: `doctor`, `eval diff`, `jobs status`, `backlog lint`, `authprobe`. A report exits `0` whenever it can run, whatever it found: `ledger diff`, `tax`, `eit`, `mine`, doctor's lever findings (always `warn`), and the session-start line. A report never means a pass.

## `--json`

Every `--json` payload is one JSON object with `"version": 1`, printed on stdout. A list is wrapped under a name. Within 1.x, fields are only added: no key is removed, renamed or given a new meaning. Read the keys you need and ignore the rest.

When a command exits `2` because it could not run, stdout is empty and the message is on stderr. Four commands finish the run, print the full payload and then exit `2`:

- `doctor`, on a finding with `error` severity or on unreadable or newer guard state.
- `janitor`, on a rule or watermark error.
- `reap`, when a removal failed under `--apply`.
- `eval diff`, when cases are unverified or dropped.

`jobs status` has no `--json`, but it too can print to stdout and exit `2`: on Linux its one line, on macOS the rows for the jobs before the one whose plist or launchd query failed.

Read the payload for what went wrong and the exit code for whether it passed.

| Form | Top-level keys |
|---|---|
| `rigops doctor --json` | `version`, `ts`, `supervisor`, `jobs`, `checks`, `healed`, `fail_count`, `stale_count`, `gates`, `unknown_config_keys`, `findings` |
| `rigops doctor --list --json` | `version`, `items` |
| `rigops doctor --config-only --json` | `version`, `findings` |
| `rigops eval run --json` | `version`, `run_id`, `ts`, `label`, `endpoint`, `model`, `temperature`, `suite`, `system`, `git_sha`, `totals`, `groups`, `cases` |
| `rigops eval diff --json` | `version`, `base`, `head`, `shared`, `added`, `removed`, `changed`, `dropped`, `groups`, `newly_failing`, `newly_passing`, `unverified`, `regressions` |
| `rigops eval list --json` | `version`, `rows` |
| `rigops ledger write --json` | `version`, `row` |
| `rigops ledger write --show --json` | `version`, `rows`, `skipped` |
| `rigops ledger diff --json` | `version`, `skipped`, `incomplete`, `insufficient_data`, `baseline`, `latest`, `deltas`, `interventions`, `derived`, `fingerprints` |
| `rigops tax --json` | `version`, `files` (each `path`, `bytes`), `total_bytes` |
| `rigops tax --history --json` | `version`, `rows` (each `date`, `fixed_tax_b`), `skipped` |
| `rigops tax --starts --json` | `version`, `starts`, `surfaces`, `groups` |
| `rigops tax --starts --session <id> --json` | `version`, `rows` |
| `rigops tax --dead --json` | `version`, `starts`, `max_per_100`, `tools`, `groups` |
| `rigops reap --json` | `version`, `generated`, `repos`, `orphan_daemons`, `process_observation_ok` |
| `rigops janitor --json` | `version`, `rules`, `total_deleted`, `watermarks`, `apply` |
| `rigops authprobe --json` | `version`, `ok`, `detail`, `enabled` |
| `rigops backlog lint --json` | `version`, `open`, `done`, `malformed`, `unknown_area`, `ok` |
| `rigops eit --json` | `version`, `turns`, `eit_total`, `eit_per_turn`, `output_tokens_total`, `cache_hit_pct`, `ctx_mean`, `ctx_p50`, `ctx_p90`, `ctx_max`, `eit_share_over_300k_pct`, `top_tools`, `cost`, `cost_pct`, `by`, `top_groups`, `histogram`, `window` |
| `rigops eit --rebuilds --json` | `version`, `count`, `main_eit`, `eit_pct`, `classes`, `largest` |

In `ledger diff --json`, each entry of `deltas` carries `comparable`. Read one with `comparable` false as "no trend": the metric's rule changed between the two rows.

`rigops config get <key>` prints the value as JSON, not an envelope: a string comes back quoted.

### Doctor findings

A finding is an object with `id`, `check`, `area`, `severity`, `symptom`, `evidence` and `fix`. `severity` is `warn`, `fail` or `error`. The `id` is the first 8 hex digits of the SHA-1 of the check name and the finding's key, so it stays the same across runs and releases for the same defect. A backlog line deduplicated on an id keeps working through 1.x.

## Gating on `eval diff` in CI

The verdict is the exit code, and any non-zero code means "not passed". Read `--json` for the reason. This GitHub Actions step builds on the recipe in [EVAL.md](EVAL.md#gating-a-change): both runs use the base commit's cases, so a change can't edit the test that judges it. It needs `fetch-depth: 0` on checkout, and `eval.endpoint` and `eval.model` in config or `--endpoint` and `--model` on each run.

```yaml
- name: Eval gate
  shell: bash
  run: |
    git checkout origin/main
    rigops eval run --label main --cases eval/cases
    cp -R eval/cases "$RUNNER_TEMP/base-cases"
    git checkout "$GITHUB_SHA"
    rigops eval run --label proposal --cases "$RUNNER_TEMP/base-cases"
    set +e
    rigops eval diff --base main --head proposal --json > "$RUNNER_TEMP/eval-diff.json"
    status=$?
    set -e
    if [ -s "$RUNNER_TEMP/eval-diff.json" ]; then
      jq -r '.regressions[] | "regression: \(.group) \(.metric)"' "$RUNNER_TEMP/eval-diff.json"
      jq -r '.unverified[] | "unverified: \(.)"' "$RUNNER_TEMP/eval-diff.json"
      jq -r '.dropped[] | "dropped: \(.)"' "$RUNNER_TEMP/eval-diff.json"
    fi
    if [ "$status" -ne 0 ]; then
      echo "eval gate not passed (exit $status)" >&2
      exit 1
    fi
```

Don't branch on `1` versus `2` to decide whether to pass. A `2` with an empty file is an error (no runs, no matching label, a bad flag); the message is in the step log.

## State files

The state directory is `RIGOPS_STATE_DIR`, or `${XDG_STATE_HOME:-~/.local/state}/rigops` when that is unset. It is created `0700`, and a wider one is tightened on first use. Keep it on a local disk: locking uses `flock`. [SAFETY.md](SAFETY.md#state-files) has the write rules.

| File | Written by | Shape | Read by |
|---|---|---|---|
| `ledger.jsonl` | `ledger write` | JSONL, one row per week | `ledger write` (strict); `ledger diff`, `ledger write --show --json`, `tax --history`, the lever checks (tolerant) |
| `ledger.md` | `ledger write` | Markdown table, unversioned | people |
| `interventions.jsonl` | `ledger note` | JSONL | `ledger diff` (tolerant) |
| `eval.jsonl` | `eval run` | JSONL, one row per run | `eval diff`, `eval list` (strict) |
| `reap.log` | `reap --apply` | text, unversioned | people |
| `doctor/events.jsonl` | `doctor` | JSONL, append only | people |
| `doctor/heal-stamps.json` | `doctor` | `{"version": 1, "entries": {...}}` | `doctor` (guard, fails closed) |
| `doctor/holds.json` | `doctor` with `doctor.cause_policy` set to `shadow` or `on` under launchd | `{"version": 1, "entries": {...}}` | `doctor` (guard, fails closed) |
| `doctor/notified.json`, `doctor/kicked.json` | `doctor` with a notify command | `{"version": 1, "entries": {...}}` | `doctor` (tolerant) |
| `doctor/report-latest.txt` | `doctor` before it notifies | text, unversioned | the notify command |

Two files always live under `${XDG_STATE_HOME:-~/.local/state}/rigops`, even when `RIGOPS_STATE_DIR` points elsewhere: `doctor/gates.json` (`{"version": 1, "ts", "gates"}`, read by the session-start hook) and `ctx-nudge/<session>` (the nudge's per-session tier, unversioned). Locks (`*.lock`), quarantined files (`*.corrupt-<stamp>`) and temp files (`*.tmp`) sit beside the files they belong to.

Every JSONL row and JSON state file carries an integer `version`, currently `1`. It covers both the shape and the metric definitions. A missing version reads as `0`, the 0.10 shape.

Readers split by what they decide:

- Gates and writers are strict. A malformed row or a row with a newer version exits `2`.
- Reports are tolerant. They skip a row they can't read, print `skipped N ...` on stderr, count it in `skipped` under `--json`, and exit `0`. `ledger diff` sets `incomplete` when any row in `ledger.jsonl` or `interventions.jsonl` was skipped.
- An unreadable file exits `2` everywhere.
- State that guards an action fails closed. If `heal-stamps.json` or a guarded `holds.json` is unreadable, corrupt or from a newer rigops, `doctor` exits `2`. Under `doctor --heal` with launchd it also moves the file aside, restarts no job and releases no hold that run; a hung job is still stopped. `reap --apply` removes nothing when a live-session file is unreadable.

Every 1.x release reads 0.10 state and state from any earlier 1.x. There is no migration tool, and rows already written are never rewritten. An older rigops may not read state a newer one wrote, so don't share a state directory between versions.

## Config

The config file is `RIGOPS_CONFIG`, or `${XDG_CONFIG_HOME:-~/.config}/rigops/config.json`. The job registry is `--registry`, then `doctor.registry`, then `${XDG_CONFIG_HOME:-~/.config}/rigops/registry.md`. That default ignores `RIGOPS_CONFIG`, so a registry next to a config file in another directory is not read. Every key, its type and its default are in [CONFIG.md](CONFIG.md#key-reference), which a test holds equal to the spec table in the code. Path values expand `~` and environment variables.

- A known key with the wrong type exits `2` with `config key <path>: expected <type>, got <kind>`.
- An unknown key is never an error at run time. `rigops config check` warns about it and exits `0`, and `rigops doctor` prints one info line and lists it in `unknown_config_keys`. A key that 0.11 removed is reported as `removed in 0.11: <key>`.
- Config is code. Anything that runs a command or deletes refuses a config or registry that group or others can write, with exit `2`. [SAFETY.md](SAFETY.md) lists which commands check.
- The hooks never fail on config. `session-start` prints `rigops: config unreadable: ...` in place of its gate line when a gate record exists and reads; `user-prompt-submit` uses the defaults.

## Environment

| Variable | Effect |
|---|---|
| `RIGOPS_CONFIG` | Config file path. |
| `RIGOPS_STATE_DIR` | State directory. |
| `XDG_CONFIG_HOME` | Base for the config file when `RIGOPS_CONFIG` is unset, and for the registry when `doctor.registry` is unset. |
| `XDG_STATE_HOME` | Base for the state directory when `RIGOPS_STATE_DIR` is unset, and always for the gate record, the nudge state and cron logs. |
| `RIGOPS_PLUGIN_VERSION` | Set by the plugin's hook script. `session-start` warns when its major.minor differs from the CLI's. |
| `RIGOPS_RUN_DEADLINE_S` | Seconds `reap` may run before it stops, default `3600`. A value that is not an integer means `3600`. |
| The name given to `--api-key-env` or `eval.api_key_env` | Bearer token for `eval run`. |

`rigops jobs install` copies `RIGOPS_CONFIG`, `RIGOPS_STATE_DIR`, `XDG_CONFIG_HOME` and `XDG_STATE_HOME` into each plist when they are set, so a scheduled run sees the same files you do.

Internal, free to change: `RIGOPS_REDACTION_PATTERNS` and `RIGOPS_REDACTION_PATTERNS_B64` (the redaction patterns `mine` applies), and `HOME` and `USER` as `authprobe` passes them to its probe.

## Hooks

`rigops hook session-start` and `rigops hook user-prompt-submit` are the only events. The plugin runs them through `rigops-hook.sh`, wired in `plugin/hooks/hooks.json` ([PLUGIN.md](PLUGIN.md#hooks)).

- Exit `0` always, including on an unknown event or a crash.
- One process, no child processes.
- At most 1000 bytes of output per event, trailing newline included, cut on a UTF-8 boundary with `…`.
- A time budget of 1 second, checked in CI: the median of 3 runs under 1 second, and the 95th percentile of 20 runs at most 0.25 seconds in at least one of up to 3 samples, so one sample spoiled by a loaded runner does not fail the check. rigops does not enforce it at run time.
- `session-start` prints at most two lines. The first names the stale doctor gates, or why there is no readable gate record, and is left out when the gates are fresh. The config is loaded only once a gate record has been read, so a config error replaces that first line only then; any other failure replaces it with `rigops: session-start hook failed (<error type>)`. A second line follows when the plugin's major.minor differs from the CLI's. `user-prompt-submit` prints the context-size nudge when the transcript crosses a tier, and nothing otherwise.

## Extension points

| Key | What it does |
|---|---|
| `doctor.checks.custom` | `{name, command, warn_exit, fail_exit, timeout_s}`. Runs `command` through the shell in its own process group, default timeout 10 seconds. Exit `fail_exit` is `fail`, `warn_exit` is `warn`, `0` is `ok`, any other exit or a timeout is `warn`, and a command that cannot start is `fail`. The first line of stdout is the detail. |
| `sources.custom` | `{name, argv, timeout_s}`. Runs `argv` without a shell, found on `PATH`, default timeout 30 seconds. Its stdout, parsed as JSON, goes into the ledger row under `sources.<name>`; a failure stores `null`. The name `ccusage` is reserved. |
| `ledger.extra_correction_patterns` | Regexes matched, ignoring case, against the first non-blank line of each human prompt. A match counts a correction. |
| `ledger.correction_exclude_patterns` | Regexes that cancel a correction match. They are checked only on lines that already matched. |
| `ledger.extra_denial_patterns` | Regexes searched in the text of tool results marked as errors. A match counts a denial. |

[CONFIG.md](CONFIG.md) has examples of each.

## Eval cases

A case file holds `group`, `prompt`, `expect`, and optionally `note`, `max_tokens` (default `512`) and `timeout_s` (default `120`, at most `3600`). [EVAL.md](EVAL.md#case-files) has the format.

The `expect` kinds are frozen: `contains`, `not_contains`, `regex`, `not_regex`, `json`, `json_keys`. `contains` and `not_contains` ignore case. `regex` and `not_regex` search anywhere in the reply. `json` compares the first JSON object in the reply with strict, typed equality. `json_keys` wants exactly the listed keys.

A case's `status` is `pass`, `fail` or `error`. Each case runs once per run. No status is added in 1.x. [EVAL.md](EVAL.md#regression-rules) has the regression rules `eval diff` applies.

## Versions

The package, the plugin and the release tag carry one version string. `rigops version` prints it. `plugin/.claude-plugin/plugin.json` and the marketplace entry carry the same string, and the release check fails when they differ. A final release differs from its last release candidate only in that string and the changelog heading; [RELEASING.md](RELEASING.md) has the steps.

## Compatibility and deprecation

rigops follows semantic versioning from 1.0.

- Within 1.x, changes to anything on this page are additive: a new command, flag, `--json` key, config key, environment variable, state field or finding. An exit code keeps its meaning.
- A removal, a rename, or a change of meaning waits for 2.0.
- Before something is removed in 2.0, a 1.x minor release marks it deprecated: a line under "Deprecated" in [CHANGELOG.md](../CHANGELOG.md) and a warning on stderr when you use it. It keeps working through the rest of 1.x. A deprecation warning never goes to stdout or into `--json`.
- A change that makes rigops do what this page says is a fix, not a break, and ships in a patch release.
- Experimental parts can change or go in any minor release, with a changelog line. When one becomes stable, its rows move into the tables above.
- Every 1.x release reads state written by 0.10 and by any earlier 1.x.

[UPGRADING.md](UPGRADING.md) covers the move from 0.10 to 1.0.

## See also

- [SAFETY.md](SAFETY.md): defaults, caps, the kill path and state-file rules
- [CONFIG.md](CONFIG.md): every config key
- [EVAL.md](EVAL.md): case files and the regression rules
- [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md): platforms, CI coverage and the experimental table
- [../SECURITY.md](../SECURITY.md): threat model and how to report a vulnerability
