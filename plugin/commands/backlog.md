---
description: Lint or add to the rigops backlog. Use for "add this to the backlog", "lint the backlog", "what's in the backlog".
---

# rigops backlog

`rigops backlog` requires a rigops release with backlog support. It does not
exist in every install — check before using it.

1. Check the CLI is reachable: `command -v rigops` or `$HOME/.local/bin/rigops`. If neither exists, tell the user to run `install.sh` and stop.

2. Check the subcommand exists: `rigops help` and look for `backlog` in the command list, or just try `rigops backlog lint` and check the exit code. If it errors with "unknown command", this install predates backlog support — tell the user their rigops version doesn't have it yet, and skip the rest of this command. Do not try to fake backlog behavior yourself.

3. If it exists: `rigops backlog lint` checks the backlog file for structural problems. `rigops backlog add "<text>"` appends an entry.

4. Verify actual flags against `rigops backlog --help` before using anything beyond `lint` and `add` — this doc predates the implementation and may not match it exactly.
