# Registry

The plain-text list of automation jobs `rigops doctor` watches.

## What it is

- A human-owned markdown file with YAML-shaped frontmatter, schema `rigops.v1`.
- Default path: the `doctor.registry` config key, default `~/.config/rigops/registry.md` (see [CONFIG.md](CONFIG.md#doctor)).
- `config/registry.example.md` ships as a scaffold; `install.sh` copies it to that default path if nothing is already there (see [INSTALL.md](INSTALL.md)).
- The parser is hand-rolled (`lib/rigops/registry.py`), not a YAML library: flat `key: value` pairs inside each `- id: ...` item, plus one optional `notes: |` literal block. No nesting, no anchors, no multi-document support - the format never needs them.

## Field reference

Per the shipped example's own documented contract, each item is expected to carry:

**Required**: `id`, `domain`, `trigger`, `entry_point`, `output`, `cadence`, `last_verified`, `health`, `group`.

**Optional**: `plist` - explicit launchd plist path; when absent, `doctor` guesses the label from `doctor.label_prefix` + `id`, and only trusts a guess that's both currently loaded in launchd *and* starts with one of the configured prefixes. `notes` - a free-text `|` block. `max_runtime_h` - hours a scheduled run may execute before it's judged hung; default `6` (blank or absent both mean the default; a non-numeric or `<= 0` value is a hard parse error).

`health` format: a plain path, a brace-expansion like `foo.{log,err.log}` (every alternative is checked, the newest mtime wins), or `-` for "no evidence file, skip staleness for this job."

**Enforcement note**: only `id` is structurally required by the parser - it's the item delimiter itself, a line must read `- id: <value>` to start a new item. The parser does not error if `domain`/`trigger`/`entry_point`/`output`/`last_verified`/`group` are missing from an item; those are a documented convention for humans and tooling, not a load-time check. `cadence` and `health` *do* change judgment output when absent (see below). A duplicate `id`, or a frontmatter `schema:` that isn't `rigops.v1` when an `items:` list is present, is a hard error.

## Judgment model

`rigops doctor` judges every item from point-in-time signals only - no history, no state carried between runs except heal-cooldown timestamps.

Five statuses, first matching row wins (`lib/rigops/judge.py`, `judge_job`):

| Runtime | Condition | Status | Action |
|---|---|---|---|
| Running, over `max_runtime_h` | - | `hung` | kill |
| Running, within `max_runtime_h` | - | `ok` | none |
| Not running | last exit != 0 | `failing` | kickstart (or notify if cooling down) |
| Not running | evidence age > 1.5x expected cadence interval | `stale` | kickstart (or notify if cooling down) |
| Not running | no exit recorded and no evidence file at all | `unknown` | none |
| Not running | else (clean exit and/or evidence fresh/absent) | `ok` | none |

A running job is always judged on runtime alone - a hung job has to be caught before its exit code or evidence age matter at all, since launchd never starts a new run while the old one is still alive.

Staleness only applies when the cadence is interval-checkable. `cadence` strings map to an expected interval: `daily HH:MM` → 24h (or 24h / N for N distinct times), `every N min` → N/60 hours, `weekly` → 168h. `on-demand`/`manual`/`deadline`, `event`, and `always-on`/`keepalive` cadences are never staleness-checked - there's no "should have run by now" clock for them.

**Kill**: SIGTERM, wait `doctor.kill_grace_s` (default 5s), SIGKILL if still alive - sent to the process group when the target is its own group leader, never a bare `killpg` on a non-leader pid. Full guards in [SAFETY.md](SAFETY.md).

**`heal_cooldown_h`** (default 12): once a job has been killed or kickstarted, a repeat kickstart of the *same* job id is suppressed for this many hours - the action becomes `notify` instead, so a job that dies immediately after every restart doesn't get kickstart-looped. A kill stamps the same cooldown (the next scheduled launchd fire restarts the job regardless).

**`--heal`** (default off): `rigops doctor` alone only judges and reports - nothing is killed or kickstarted. `--heal` applies every `kill`/`kickstart` action from that same run. Custom checks (`doctor.checks.custom`) run their configured commands on every judging run, report-only included, but not under `--list`; `doctor.notify_command` runs only when a job failed, a check failed, or something was healed this run - only the registry-driven kill/kickstart path is gated by `--heal`.

**`--supervisor none`**: skips `launchctl` entirely. Every job is judged from evidence-file age alone (no runtime, no exit code - so `hung`/`failing` can never be reached, only `stale`/`unknown`/`ok`). Use this off macOS, or under cron; see [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md).

## Examples

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
id              status   runtime  evidence age  action
--------------  -------  -------  ------------  ---------
nightly-backup  ok       -        2.0h          none
metrics-rollup  stale    -        72.0h         kickstart
log-prune       unknown  -        -             none

== checks ==
name          status  detail
------------  ------  -----------------
backup-fresh  ok      exit 0
disk-free     ok      400.2GB free on ~
```

`metrics-rollup` is `stale` here: its evidence is 72h old against an expected 15-minute cadence. `log-prune` is `unknown`, not `ok` or `stale` - its `health` field is `-`, so it's never evidence-checked at all; `unknown` means "no data," not "broken."

## Registry hygiene

`last_verified` is a plain human attestation, not machine-checked. Update it by hand whenever you actually re-verify a job - confirm `entry_point` still runs, `output`/`health` paths are still right. Nothing in `rigops doctor` reads or enforces it.

## See also

- [SAFETY.md](SAFETY.md) - the kill-path guards in full.
- [CONFIG.md](CONFIG.md#doctor) - every `doctor.*` config key.
- The plugin's `fleet-triage` skill - interpreting a doctor report interactively (see [INSTALL.md](INSTALL.md)).
- [../patterns/job-registry.md](../patterns/job-registry.md)
