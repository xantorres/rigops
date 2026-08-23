---
name: fleet-triage
description: Interpret `rigops doctor --report` output - status taxonomy, stale vs hung vs failed, when to run --heal, registry hygiene, and the safety model behind kill/kickstart. Use when the user says "my launchd jobs", "automation health", "job stuck", "is my rig healthy", or shares a doctor report to read.
---

# Fleet triage

`rigops doctor` judges each registry job from point-in-time signals (runtime,
last exit code, evidence-file age, expected cadence) with no side effects by
default. This skill is about reading that judgment correctly, not about the
CLI invocation itself - see the `doctor` command doc for flags.

## Status taxonomy

Five statuses, one action each. First matching condition wins; a job that is
currently running always gets judged on runtime alone, ignoring exit code and
evidence age.

| status  | meaning                                                          | action           |
|---------|-------------------------------------------------------------------|------------------|
| `ok`    | running within its expected runtime ceiling, or exited cleanly and evidence is fresh (or the cadence isn't evidence-checkable) | none |
| `hung`  | still running past its configured max runtime                     | kill             |
| `failing` | not running, last exit code was nonzero                         | kickstart or notify |
| `stale` | not running, exit was clean (or unknown), but its evidence file is older than 1.5x the expected cadence interval | kickstart or notify |
| `unknown` | not running, no exit status recorded and no evidence file exists at all | none |

Stale vs hung: hung is a live process past its ceiling (kill it). Stale is a
dead-or-never-run job whose *output* is too old for its cadence (restart it).
A job with an on-demand, event-driven, or keepalive cadence is never
staleness-checked - those cadences have no "should have run by now" clock, so
the registry's `cadence` field determines whether stale can even apply.

`failing` vs `stale` can look the same from `--report` output (both say
"kickstart") but mean different things when explaining to the user: failing
means the job ran and errored; stale means it plain didn't run recently
enough. Check the `evidence age` and the registry's `health` field to tell
them apart when it matters.

## kickstart vs notify

Both `failing` and `stale` want a kickstart, but if that job was already
healed within `doctor.heal_cooldown_h` hours, the action becomes `notify`
instead - this is what stops `--heal` from kickstart-looping a job that keeps
dying immediately after restart. `notify` still surfaces the problem (via
`doctor.notify_command` if configured); it just doesn't retry blindly.

## When to run --heal

Default `rigops doctor --report` is read-only: it never kills or kickstarts
anything, only judges and reports (custom checks and the notify command still
run their configured commands either way). Add `--heal` only once you've read
the report and agree with its verdict - it applies every `kill`/`kickstart`
action from that same run. Don't reach for `--heal` reflexively on every
triage; a `stale` job whose cadence just hasn't come around yet is not
something to force.

Kill is process-group guarded and grace-period aware (`doctor.kill_grace_s`,
default 5s: SIGTERM, wait, SIGKILL only if still alive) so it cannot pick off
a lone child process of a job that's actually behaving as designed.

## Registry hygiene

Each job's health comes from `newest_evidence_mtime` of its registry `health`
field, and cadence classification from `cadence` (`daily HH:MM`, `every N
min`, `weekly`, `always-on`/`keepalive`, or `on-demand`/`manual`/`deadline` -
the last three are never staleness-checked). A job judged `unknown` for a long
time usually means the registry's `health` path is wrong or the job has never
produced evidence yet, not that the job is broken - check the path before
assuming failure.

## Scope

This skill reads and interprets. It does not edit the registry or run
`--heal` on its own initiative - always surface the judgment and let the user
decide, especially before anything that kills a live process.
