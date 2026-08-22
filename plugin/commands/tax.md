---
description: Show the rigops fixed context tax (always-loaded config bytes) and its trend. Use for "how big is my fixed tax", "did CLAUDE.md regrow", "context tax history".
---

# rigops tax

1. Check the CLI is reachable: `command -v rigops` or `$HOME/.local/bin/rigops`. If neither exists, tell the user to run `install.sh` and stop.

2. `rigops tax` prints the current fixed context tax: every file counted toward it (`fixed_tax.paths`, always counted, plus `fixed_tax.globs` matches that aren't themselves path-scoped) and its size, plus a total. Add `--json` for structured output. If it prints "fixed_tax not configured", the user hasn't set `fixed_tax.paths`/`fixed_tax.globs` in their rigops config yet — point them at `config/config.example.json` for the shape.

3. `rigops tax --history` prints the `fixed_tax_b` trend across every ledger row (sparkline plus first/last/delta). This is the regrowth gauge: fixed tax is context every session pays before the first prompt, whether or not that turn needs it, so an upward trend here is pure overhead creeping back in (a CLAUDE.md that grew, a new always-loaded doc). Add `--json` for the raw series.

4. `--history` reads only from the ledger's state file (`ledger.jsonl`, in the rigops state dir), not a fresh file scan — if the user wants the current number, not the trend, use plain `rigops tax` instead.
