---
description: Lint or add to the rigops backlog. Use for "add this to the backlog", "lint the backlog", "what's in the backlog".
---

# rigops backlog

1. Check the CLI is reachable: `command -v rigops` or `$HOME/.local/bin/rigops`. If neither exists, tell the user to install the `rigops` package (see `docs/INSTALL.md`) and stop.

2. `rigops backlog lint` checks the backlog file for structural problems, and `lint --json` prints them as JSON. `rigops backlog add "<text>"` appends an entry. `add` takes the area from `--area`, or from an `area: ` prefix on the text that names one of `backlog.areas` (with neither, it exits 2), and the date from `--date YYYY-MM-DD` (default today). Both subcommands take `--file <path>` to work on a file other than `backlog.path`.

`lint --json` is one object with `version` added to the existing keys.
