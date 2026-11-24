# Registry

The plain-text list of automation jobs `rigops doctor` watches.

## What it is

- A human-owned markdown file with YAML-shaped frontmatter, schema `rigops.v1`.
- Default path: the `doctor.registry` config key, default `~/.config/rigops/registry.md` (see [CONFIG.md](CONFIG.md#doctor)). While the key is left at that default and no `--registry` is given, the path follows `$XDG_CONFIG_HOME` (`$XDG_CONFIG_HOME/rigops/registry.md`), the same rule the config file uses.
- Nothing creates the file: installing the package writes no registry. While the default path does not exist, `doctor` prints one `note:` line on stderr and treats the fleet as empty. Copy `config/registry.example.md` from the repository there and edit it to start; it shows the entry format.
- A registry that group or others can write is refused by `doctor` (exit 2) because it drives kill and kickstart: `chmod go-w` the file (the check follows symlinks, and `doctor --list` does not apply it).
- The parser is hand-rolled (`lib/rigops/registry.py`), not a YAML library: flat `key: value` pairs inside each `- id: ...` item, plus one optional `notes: |` literal block. No nesting, no anchors, no multi-document support - the format never needs them.

## Field reference

Every item must carry the required fields:

**Required**: `id`, `domain`, `trigger`, `entry_point`, `output`, `cadence`, `last_verified`, `health`, `group`.

**Optional**: `plist` - explicit launchd plist path; when absent, `doctor` guesses the label from `doctor.label_prefix` + `id`, and only trusts a guess that's both currently loaded in launchd *and* starts with one of the configured prefixes. `notes` - a free-text `|` block. `max_runtime_h` - hours a scheduled run may execute before it's judged hung; default `6` (blank or absent both mean the default; a non-numeric or `<= 0` value is a hard parse error).

`health` format: a plain path, a brace-expansion like `foo.{log,err.log}` (every alternative is checked, the newest mtime wins), or `-` for "no evidence file, skip staleness for this job."

**Enforcement note**: `id` starts an item, so a line must read `- id: <value>` to begin one. Every other required field has to be present and non-blank, or the loader exits with an error naming the item and the missing fields, and the whole `doctor` run stops. Write `-` for a field with nothing to point at, as in `output: -` or `health: -`. A duplicate `id`, or a frontmatter `schema:` that isn't `rigops.v1` when an `items:` list is present, is also a hard error.

## Judgment model

`rigops doctor` judges every item from point-in-time signals only - no history, no state carried between runs except heal-cooldown timestamps.

Five statuses, first matching row wins (`lib/rigops/judge.py`, `judge_job`):

| Runtime | Condition | Status | Action |
|---|---|---|---|
| Running | `always-on`/`keepalive` cadence | `ok` | none |
| Running, over `max_runtime_h` | - | `hung` | kill |
| Running, within `max_runtime_h` | - | `ok` | none |
| Not running | last exit != 0 | `failing` | kickstart (or notify if cooling down) |
| Not running | evidence age > 1.5x expected cadence interval | `stale` | kickstart (or notify if cooling down) |
| Not running | no exit recorded and no evidence file at all | `unknown` | none |
| Not running | else (clean exit and/or evidence fresh/absent) | `ok` | none |

A running job is always judged on runtime alone - a hung job has to be caught before its exit code or evidence age matter at all, since launchd never starts a new run while the old one is still alive. A keepalive daemon is meant to run forever, so its runtime never makes it `hung`.

Staleness only applies when the cadence is interval-checkable. `cadence` strings map to an expected interval: `daily HH:MM` → 24h (or 24h / N for N distinct times), `every N min` → N/60 hours, `weekly` → 168h. `on-demand`/`manual`/`deadline`, `event`, and `always-on`/`keepalive` cadences are never staleness-checked - there's no "should have run by now" clock for them.

**Kill**: SIGTERM, wait `doctor.kill_grace_s` (default 5s), SIGKILL if still alive - sent to the process group when the target is its own group leader, never a bare `killpg` on a non-leader pid. Full guards in [SAFETY.md](SAFETY.md).

**`heal_cooldown_h`** (default 12): once a job has been killed or kickstarted, a repeat kickstart of the *same* job id is suppressed for this many hours - the action becomes `notify` instead, so a job that dies immediately after every restart doesn't get kickstart-looped. A kill stamps the same cooldown (the next scheduled launchd fire restarts the job regardless).

**`--heal`** (default off): `rigops doctor` alone only judges and reports - nothing is killed or kickstarted. `--heal` applies every `kill`/`kickstart` action from that same run. Custom checks (`doctor.checks.custom`) run their configured commands on every judging run, report-only included, but not under `--list`; `doctor.notify_command` runs only when a job failed, a check failed, or something was healed this run - only the registry-driven kill/kickstart path is gated by `--heal`.

**Exit code**: `0` when no job is `failing` or `hung` and no check or finding fails; `1` when one is (`stale` and `unknown` never count); `2` when the config or registry cannot be loaded, or when a config check could not measure (it failed to import, crashed, or could not read a file it judges); `2` wins over `1`. `--heal` does not change it. `--exit-zero` turns `1` into `0` and never hides a `2`; scheduled runs pass it. The doctor never judges, kills or kickstarts its own registry row, which is any item whose `entry_point` runs `rigops-doctor` or `rigops doctor`.

**`--supervisor`** defaults to `launchd` on macOS and `none` elsewhere. **`--supervisor none`** skips `launchctl` entirely. Every job is judged from evidence-file age alone (no runtime, no exit code - so `hung`/`failing` can never be reached, only `stale`/`unknown`/`ok`). Use this off macOS, or under cron; see [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md).

## Examples

`rigops doctor --list --json` prints `{"version": 1, "items": [...]}`, and `--config-only --json` prints `{"version": 1, "findings": [...]}`. Heal state (`heal-stamps.json`, `holds.json`, `kicked.json`, `notified.json`) is versioned and written atomically, and a run that writes it holds a lock; see [SAFETY.md](SAFETY.md#state-files).

`rigops doctor --list` against the shipped example registry:

```text
id              domain        trigger              cadence
--------------  ------------  -------------------  -----------
nightly-backup  backup        launchd.daily        daily 02:30
metrics-rollup  metrics       launchd.every-15min  every 15min
log-prune       housekeeping  launchd.weekly       weekly
```

`rigops doctor --report --supervisor none` (evidence-only judgment, sandbox data):

```text
rigops doctor report 2026-08-23T10:53:12Z
supervisor: none

== jobs ==
id              status  runtime  evidence age  action
--------------  ------  -------  ------------  ------
nightly-backup  ok      -        2.0h          none
metrics-rollup  stale   -        72.0h         none
not judged (no launchd label, no evidence): log-prune

== checks ==
name          status  detail
------------  ------  -----------------
backup-fresh  ok      exit 0
disk-free     ok      400.2GB free on ~
```

`metrics-rollup` is `stale` here: its evidence is 72h old against an expected 15-minute cadence. Its action is `none` because `--supervisor none` has nothing to kickstart. `log-prune` has neither a launchd label nor a `health` path, so it is never judged at all and collapses into the footer line instead of a table row; that is "no data," not "broken."

## Registry hygiene

`last_verified` is a plain human attestation. The loader requires it but never parses it as a date or checks its age. Update it by hand whenever you actually re-verify a job - confirm `entry_point` still runs, `output`/`health` paths are still right.

## See also

- [SAFETY.md](SAFETY.md) - the kill-path guards in full.
- [CONFIG.md](CONFIG.md#doctor) - every `doctor.*` config key.
- The plugin's `fleet-triage` skill - interpreting a doctor report interactively (see [INSTALL.md](INSTALL.md)).
- [../patterns/job-registry.md](../patterns/job-registry.md)
