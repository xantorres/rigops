---
description: Run the rigops job-fleet health check and interpret the report. Use for "is my rig healthy", "job fleet status", "why is a job stuck".
---

# rigops doctor

1. Check the CLI is reachable: `command -v rigops` or `$HOME/.local/bin/rigops`. If neither exists, tell the user to run `install.sh` (or use the plugin-only features - skills and hooks still work without the CLI) and stop.

2. Run `rigops doctor --report`. This is report-only: no kill/kickstart side effects. Add `--json` when you need structured output to reason over instead of the text table. On Linux (or anywhere launchd isn't the supervisor), add `--supervisor=none` - this skips launchctl entirely and judges each job from evidence-file age only.

3. Read the `== jobs ==` table: columns are id, status, runtime, evidence age, action. Statuses and what they mean live in the `fleet-triage` skill - invoke it for anything beyond a quick read (stale vs hung vs failed, when `--heal` is warranted).

4. Read the `== checks ==` table (custom shell checks + optional disk-free threshold, configured under `doctor.checks` in the rigops config).

5. If jobs need healing (kill a hung process, kickstart a stalled one) and the user confirms, re-run with `--heal` added: `rigops doctor --report --heal`. This is the only flag that allows kill/kickstart side effects - the default is always report-only. Custom checks and the notify command run either way.

6. For the registry itself (job cadence, evidence paths, `id`/`plist` fields), point the user at `config/registry.example.md` in the rigops repo, or their configured `doctor.registry` path (`rigops config get doctor.registry`).
