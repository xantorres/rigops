---
description: Run the full weekly rigops ritual - write the ledger row, diff it, check the tax trend, run doctor, and note what actually landed. Use for "weekly rig review", "do the rigops week", "how's the rig doing this week".
---

# rigops week

Check the CLI is reachable first: `command -v rigops` or `$HOME/.local/bin/rigops`. If neither exists, tell the user to run `install.sh` and stop - this ritual needs the CLI end to end.

Run these in order:

1. `rigops ledger write` - writes this week's row.
2. `rigops ledger diff` - week-over-week deltas plus every intervention noted since the baseline row. **Read this before step 6** - it's what tells you whether last week's interventions moved anything.
3. `rigops tax --history` - fixed-context-tax trend; flags regrowth in always-loaded config.
4. `rigops doctor --report` (add `--supervisor=none` off macOS) - job fleet health.
5. Optional: `rigops backlog lint`, only if that subcommand exists on this install (see the `backlog` command doc - skip silently if it's missing).
6. Summarize the deltas from steps 2-4 for the user in plain terms: what moved, what didn't, anything doctor flagged. Ask which interventions actually landed this week (a config change, a habit change, anything deliberate). For each one the user confirms, run `rigops ledger note "<text>"` - one call per intervention, dated to when it happened if that's not today (`--at YYYY-MM-DD`).

Do not skip step 2 or reorder it after step 6: noting an intervention before reading the diff means the next week's diff has no baseline-to-latest window to attribute it against.
