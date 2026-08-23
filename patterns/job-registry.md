# Job Registry

**What this pattern buys you:** a human-owned source of truth for what automation SHOULD
exist, judged against reality, instead of trusting the supervisor's own list of what happens
to be loaded.

## Problem

Scheduled automation rots invisibly. A launchd agent or cron job that stops firing doesn't
announce itself — it quietly stops producing output, and nobody notices until something
downstream breaks. Jobs accumulate: one gets added for a one-off need and never removed,
another gets renamed and the old plist lingers, a third's output file nobody has opened in
months. The supervisor (launchd, cron) only knows what's currently loaded; it has no concept
of what should exist, so a job that silently failed to reload after a machine restart is
invisible to it — from the supervisor's point of view, that job simply doesn't exist, so
there's nothing to report on.

## Pattern

Keep a single, human-owned registry — a plain file, not a database — as the fleet's source
of truth: one entry per job with an id, trigger, cadence, entry point, output, an evidence
file whose staleness proves the job is still running, and a date the human last actually
checked on it by hand. A watchdog then judges each entry against reality — evidence age
versus expected cadence, runtime versus a cap — instead of against the supervisor's own
list, so a job the supervisor has silently forgotten is still visible as "registered but
stale," not simply absent from a report.

The registry deliberately never gets to pick a kill target by itself. If the registry could
name any process label and have it killed, a bad edit — a copy-paste label meant for one job
pointing at another, or corruption in the file — becomes a kill switch aimed at an unrelated
process by accident. That defense has to live outside the data the registry supplies.

## How rigops implements it

The registry format is documented as frontmatter in the example file itself:
[../config/registry.example.md](../config/registry.example.md), schema `rigops.v1`, one
`items:` list, required fields `id`, `domain`, `trigger`, `entry_point`, `output`,
`cadence`, `last_verified`, `health`, `group`; optional `plist`, `notes`, `max_runtime_h`
(default 6 hours, `DEFAULT_MAX_RUNTIME_H`).

[../lib/rigops/registry.py](../lib/rigops/registry.py) (`parse_front_matter`,
`load_registry`) is a dependency-free, hand-rolled parser — flat `key: value` scalars plus
one `notes: |` literal block, no YAML dependency, no nesting. It raises `SystemExit` naming
the offending line on anything malformed, rather than silently dropping a bad entry.

[../lib/rigops/judge.py](../lib/rigops/judge.py) (`judge_job`) is a pure function: no I/O,
no side effects, just a decision table over point-in-time signals (`runtime_h`,
`max_runtime_h`, `last_exit_status`, `evidence_age_h`, `expected_interval_h`,
`heal_cooldown_active`) producing a `(status, action)` pair. A live runtime over its cap
always wins and is judged `hung`/`kill` before anything else is considered, on the reasoning
in the module's own docstring: "launchd never starts a scheduled job whose previous run is
still alive, so a hung run must be judged -- and killed -- before anything else is
considered." Absent a live run, staleness only applies when both `expected_interval_h` and
`evidence_age_h` are known; no evidence yet reads as `unknown`, not `stale`, because the job
may simply not have fired since registration.

The kill-target defense lives in [../libexec/rigops-doctor](../libexec/rigops-doctor)'s
`_resolve_label`: a candidate label only counts as a job's real label when it is both
actually loaded (`launchd.is_loaded`) and starts with one of the prefixes from
`doctor.label_prefix` in config — operator-owned config, not registry data. Verified
directly in that function's own comment: "prefixes are config-owned, the registry is data,
so an operator-supplied `plist:` path can't by itself point doctor at an unrelated
already-loaded daemon." A `plist:` field an operator puts in the registry can therefore
never by itself point doctor at an arbitrary already-loaded daemon outside the configured
label namespace.

`--list` prints the registry without judging anything; a full run judges every item,
optionally heals under `--heal`, and always writes an event line to `doctor/events.jsonl`
regardless of outcome.

Sample `rigops doctor --list`:

```text
id              domain        trigger              cadence
--------------  ------------  -------------------  -----------
nightly-backup  backup        launchd.daily        daily 02:30
metrics-rollup  metrics       launchd.every-15min  every 15min
log-prune       housekeeping  launchd.weekly       weekly
```

Sample `rigops doctor --report`:

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

`metrics-rollup` is stale because its evidence age (72h) exceeds 1.5x its 15-minute expected
interval by a wide margin; `log-prune` reads `unknown` rather than `stale` because it has no
evidence file configured (`health: -`) — the registry records that choice explicitly rather
than the watchdog guessing at it.

## Adopting it without rigops

- Make the registry a file a human edits directly, not a database an install script owns — the point is that it's cheap to add, remove, and read.
- Require an evidence file (or explicit "no evidence" marker) per entry; a job with nothing to check staleness against should read as unknown, not silently pass as healthy.
- Separate "judge" from "act": compute status from signals as a pure function first, decide what to do with that status second — this makes the decision table testable without touching real processes.
- Never let data the registry supplies alone authorize killing a process; cross-check against something only the operator controls, like a label prefix or allowlist, before any destructive action.
- Record a `last_verified` field and actually update it by hand periodically — the registry's honesty depends on someone occasionally confirming an entry still matches reality, not just on the automation running.

[drift-ledger.md](./drift-ledger.md) and [hardened-launchd.md](./hardened-launchd.md) cover
the other two pieces of the same fleet: whether the rig's tuning is working, and how the
jobs this registry describes actually get scheduled.
