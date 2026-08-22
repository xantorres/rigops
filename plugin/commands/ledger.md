---
description: Write, show, note, or diff the rigops weekly effectiveness ledger. Use for "log this week", "how's the ledger looking", "note that intervention".
---

# rigops ledger

1. Check the CLI is reachable: `command -v rigops` or `$HOME/.local/bin/rigops`. If neither exists, tell the user to run `install.sh` and stop — the ledger has no plugin-only fallback, it needs the CLI's transcript scan.

2. To write this week's row: `rigops ledger write`. Useful flags: `--at YYYY-MM-DD` (default today), `--label "<text>"` (free-text tag for the row), `--dry-run` (compute and print, write nothing), `--force` (replace an existing row for that date), `--json`.

3. To read without writing: `rigops ledger --show` or `rigops ledger write --show` (both print the existing ledger; add `--json` for the raw rows instead of the markdown table).

4. To record an intervention (a config change, a habit change, anything meant to move the numbers): `rigops ledger note "<text>"`. Optional `--at YYYY-MM-DD` to backdate it. Interventions are what `ledger diff` surfaces between two rows — note them as they happen, not in bulk after the fact.

5. To see week-over-week deltas: `rigops ledger diff`. Defaults to the last two rows; `--since YYYY-MM-DD` picks an older baseline instead. Add `--json` for structured output. The diff table includes every intervention noted between the baseline and latest row, so read it before writing new notes — it tells you whether the last round of changes actually moved anything.
