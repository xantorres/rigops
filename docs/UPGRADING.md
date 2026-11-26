# Upgrading from 0.10 to 1.0

0.10 ran from a checkout installed into a prefix by `install.sh`. 1.0 is a Python package with a `rigops` console script. In between, 0.11 removed the commands that only worked on the author's rig, 0.12 gave every command the same exit codes and versioned the state files, and 0.13 replaced `install.sh` with `rigops jobs`. This page is the path across all three. Each `### Upgrading from` section in [CHANGELOG.md](../CHANGELOG.md) has the full detail.

## Steps

1. Remove the 0.10 install with the uninstaller from tag `v0.12.0`, as [INSTALL.md](INSTALL.md#moving-from-an-installsh-install) shows. Do this first: it boots out every job its manifest records, and it would disable jobs the package installed before it.
2. Install the package: `pipx install rigops` (or `uv tool install rigops`). Python 3.11 or newer.
3. Make the config and the job registry writable by you alone: `chmod go-w ~/.config/rigops/config.json ~/.config/rigops/registry.md`. A file that group or others can write stops `doctor`, `janitor --apply`, `reap --apply` and the other commands [SAFETY.md](SAFETY.md) lists, with exit `2`.
4. Run `rigops config check`. Fix every key it names with the wrong type, since each of those stops every command with exit `2`. Delete the keys it reports as `removed in 0.11`; they do nothing.
5. If you want `rigops reap` to keep scanning, set `reaper.roots`. It defaults to `[]` now, and an unset value scans nothing.
6. Schedule the jobs: `rigops jobs install`, read the plan, then `rigops jobs install --apply` and `rigops jobs status`.
7. Update the plugin from its marketplace, so the plugin and the CLI report the same version.
8. Update any script or CI step that reads an exit code or `--json` output, using the sections below.

## Exit codes

Every command now follows one rule: `0` ok, `1` a negative verdict, `2` an error or a run that cannot judge. When both apply, `2` wins. [CONTRACT.md](CONTRACT.md) has the table for each command.

| Case | 0.10 | 1.0 |
|---|---|---|
| `config get` on a missing key | `3` | `1` |
| Config unreadable, not JSON, not an object, or a key of the wrong type | `1`, or ignored | `2` |
| Malformed state in `ledger write`, or unreadable state in `ledger` or `tax --history` | `1` | `2` |
| A malformed row in `tax --history` | `1` | `0`; the row is skipped and counted in `skipped` |
| `janitor` rule or watermark error | `1` | `2` |
| `doctor` with a missing explicit registry or a malformed one | `1` | `2` |
| Plain `doctor` with a failing or hung job | `0` | `1` |
| `doctor --heal` with a failing job | `0` | `1`; add `--exit-zero` for `0` |
| `doctor` check that crashes or fails to import | `1` | `2`, even with `--exit-zero` |
| `doctor` with a scheduled job's plist it cannot read or parse | no such check; `1` in 0.13, which added it | `2` |
| `doctor` with a directory, FIFO or other non-file at a plist path | no such check; `0` in 0.13, which skipped it | `2` |
| `jobs status` with a plist that does not parse | no such command; `1` in 0.13, which added it | `2` |
| `jobs status` or `jobs install` with a FIFO at a plist path | no such command; blocked in 0.13 | `2` |
| `eval diff` with a regression and an unverified or dropped case | `1` | `2` |
| `ledger write` when `transcripts_dir` does not exist | `0`, empty row | `2`, no row |
| A `--since` or `--until` that `tax` or `eit` cannot read | `1`, traceback | `2` |
| `reap --apply` when a removal fails | `0` | `2` |

A gate that fails only on exit `1` misses the `2` cases. Treat any non-zero code as "not passed".

## Removed

- `rigops card`, `rigops nudge`, `rigops render` and `rigops retrieval`, with the `plans`, `realms`, `render`, `imports` and `rtk` doctor checks. Nothing in rigops replaces them; the sources stay at tag `v0.10.1`.
- `install.sh`, its flags and the `bin/rigops` dispatcher. Anything that ran a `libexec/rigops-<command>` file from the old prefix must run `rigops <command>`.
- The plugin's `card.sh` and `ctx-nudge.sh`. The plugin runs `rigops hook <event>` through `rigops-hook.sh`, and a hand-wired copy of either script must point there. The prompt-scoped nudge is gone; the context-size nudge stays.
- `install.sh --statusline`. The statusline is an example under `patterns/statusline/` that you copy and wire yourself.
- `rigops mine --registry`, its memory dedupe and its realm labels.
- The built-in `rtk` and `caveman` ledger sources. A `sources.custom` entry records either one; [CONFIG.md](CONFIG.md) has the example.
- The environment variables `RIGOPS_ROOTS`, `RIGOPS_RETRIEVAL_LOG`, `RIGOPS_PROBES` and `RIGOPS_INSTALL_LABEL_PREFIX`. They are no longer read.

## Config

- `rigops config get` prints JSON, so a string value comes back quoted.
- Integer keys no longer accept `true` or `false`.
- With `XDG_CONFIG_HOME` set and `doctor.registry` unset, the registry is read from `$XDG_CONFIG_HOME/rigops/registry.md`.
- `pointers.memory_roots` and `reaper.ignored_untracked_dirs` lost their author-specific default entries. Add yours back if you relied on them.
- The correction and denial counts no longer handle Spanish or two author-specific denial phrases. `ledger.extra_correction_patterns`, `ledger.correction_exclude_patterns` and `ledger.extra_denial_patterns` add your own rules.
- New keys keep the old behaviour until you set them: `reaper.precious` defaults to the list reap already used.

## State

- The state directory is created `0700`, and a wider one is tightened on first use. Keep it on a local disk: locking needs `flock`.
- Every JSONL row and JSON state file carries an integer `version`. The guard files (`heal-stamps.json`, `holds.json`, `kicked.json`, `notified.json`) moved to `{"version": 1, "entries": {...}}`. The old shapes still read, but 0.11 and older can't read the new ones, so don't point an older rigops at a state directory a newer one has written. Guard state that is unreadable, corrupt or written by a newer rigops stops `doctor` with exit `2`, even with `--exit-zero`.
- Ledger rows keep every 0.10 field and gain `version`, `fixed_tax_sha`, `config_sha`, `lines_seen`, `lines_parsed` and `window_empty`. `ledger diff` marks `corrections`, `denials` and `denials_headless` `not comparable` across a 0.10 row and a newer one, because their rules changed.
- Weeks are built from local calendar days. A row's edges sit on the true local midnight, so a week around a clock change holds 167 or 169 hours. Rows already written are not rewritten.

## `--json`

Every payload is one object with `"version": 1`, and a list is wrapped under a name. Existing keys are kept. These changed shape:

| Form | 0.10 | 1.0 |
|---|---|---|
| `ledger write --json` | the row | `{"version", "row"}` |
| `ledger write --show --json` | a list of rows | `{"version", "rows", "skipped"}` |
| `tax --history --json` | a list | `{"version", "rows", "skipped"}` |
| `tax --starts --session <id> --json` | a list | `{"version", "rows"}` |
| `eval list --json` | a list | `{"version", "rows"}` |
| `doctor --list --json` | a list | `{"version", "items"}` |
| `doctor --config-only --json` | a list | `{"version", "findings"}` |

Doctor findings can carry `"severity": "error"`. `ledger diff --json` gains `skipped`, `incomplete`, `insufficient_data` and `fingerprints`, and each delta gains `comparable`; read a delta with `comparable` false as no trend.

## Scheduled jobs

- `rigops jobs install` writes the same `com.rigops.*` labels 0.10 used, so it replaces those plists. A hand-written LaunchAgent under another label is no longer skipped; remove it or you get a duplicate.
- The scheduled doctor passes `--exit-zero`, so a failing job does not fail the scheduled run, and it reports only unless you install with `--heal`.
- The doctor no longer judges its own registry row. Delete the row if it was there only to watch the doctor.
- A scheduled `ledger write` pointed at a wrong `transcripts_dir` now fails instead of recording an empty week.
- On Linux, `jobs install` prints crontab lines for you to add.
