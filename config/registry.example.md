---
schema: rigops.v1
items:
  - id: nightly-backup
    domain: backup
    trigger: launchd.daily
    entry_point: ~/bin/nightly-backup.sh
    output: ~/backups/latest.tar.gz
    cadence: daily 02:30
    last_verified: 2026-08-01
    health: ~/backups/nightly-backup.log
    group: AUTOMATED-LAUNCHD
    plist: ~/Library/LaunchAgents/local.nightly-backup.plist
    max_runtime_h: 2
  - id: metrics-rollup
    domain: metrics
    trigger: launchd.every-15min
    entry_point: ~/bin/metrics-rollup.py
    output: ~/state/metrics/rollup.json
    cadence: every 15min
    last_verified: 2026-08-10
    health: ~/state/metrics/rollup.{log,err.log}
    group: AUTOMATED-LAUNCHD
    plist: ~/Library/LaunchAgents/local.metrics-rollup.plist
    notes: |
      Rolls up per-minute samples into 15-minute buckets.
      Safe to kickstart at any time; idempotent.
  - id: log-prune
    domain: housekeeping
    trigger: launchd.weekly
    entry_point: ~/bin/log-prune.sh
    output: -
    cadence: weekly
    last_verified: 2026-07-20
    health: -
    group: AUTOMATED-LAUNCHD
---

# rigops registry example

One entry per automated job, under the `items:` list above.

Required fields: `id`, `domain`, `trigger`, `entry_point`, `output`,
`cadence`, `last_verified`, `health`, `group`.

Optional fields: `plist` (explicit launchd plist path; when absent, `rigops
doctor` guesses the label from `doctor.label_prefix` + `id`), `notes`
(free-text `|` block), `max_runtime_h` (hours a scheduled job may run
before it's judged hung; default 6).

`health` points at the evidence file(s) a job's staleness is judged
against: a plain path, a brace-expansion like `foo.{log,err.log}`, or `-`
for "no evidence file, don't check staleness".
