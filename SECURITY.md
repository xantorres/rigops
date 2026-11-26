# Security

## Reporting a vulnerability

Report it privately to `[security contact, set by the maintainer before 1.0]`. Don't open a public issue for it. Say which version (`rigops version`), which command, and what an attacker controls; leave out paths, transcripts and config values from your own machine.

## Threat model

rigops runs as you, with your rights, on your machine. It has no network listener and no daemon. Its inputs are your config, its own state, Claude Code's files under `~/.claude`, and, for `eval`, the replies of the endpoint you name.

### Config is code

A custom check, a notify command, a probe, a janitor `command` rule, a `sources.custom` entry and `sources.ccusage.argv` run as you. The reaper kills and removes worktrees on the word of the config and the registry. Anyone who can edit those files can run code as you, so a config or registry file that group or others can write is refused with exit `2` before anything runs. [docs/SAFETY.md](docs/SAFETY.md) lists the commands that check.

Treat a config you copied from someone else the way you treat a shell script from them: read it before the first run.

### Hook output enters the model's context

The plugin runs `rigops hook session-start` and `rigops hook user-prompt-submit`, and Claude Code adds what they print to the conversation. The text comes from rigops' own state: doctor gate names and statuses from the gate record, a config error message, a version mismatch, a context-size nudge. Check names can come from your config, so whoever can write your config or state directory can put words in front of the model.

The guards are size and source: at most 1000 bytes per event, one process, exit `0` always, and nothing read from the transcript except token counts. The state directories are `0700`.

### Transcripts are untrusted input

`ledger`, `tax`, `eit`, `mine` and the context nudge read Claude Code's session transcripts. Those files hold whatever the model, a tool or a web page wrote. rigops parses them as data: JSON per line, fixed patterns for corrections and denials, counts and token totals. It never runs anything a transcript names. A line that doesn't parse counts against parse health and is skipped. A ledger row holds counts, percentages and model family names, never message text; `mine` prints repeated corrections to your terminal and writes nothing.

### Destructive commands

Every command that deletes, kills or loads something is a dry run until you pass `--apply` (`reap`, `janitor`, `jobs install`, `jobs uninstall`) or `--heal` (`doctor`). The scheduled `reap` and `janitor` jobs are opt-in and run with `--apply`. The caps, the process-group guards and the fail-closed rules are in [docs/SAFETY.md](docs/SAFETY.md). Read a plan or report before you add the flag.

### Eval keys

`eval run` reads a bearer token from the environment variable you name with `--api-key-env` or `eval.api_key_env`; the token is never read from or written to config or state. An endpoint URL that carries credentials, a query or a fragment is refused. The token is sent only over https or to a loopback host, redirects are refused so the header is never replayed to another host, and an error message that quotes the token is withheld. See [docs/EVAL.md](docs/EVAL.md).

## Out of scope

- Someone who already runs code as you, or can write your home directory.
- What Claude Code itself does with hook output or transcripts.
- A config you wrote that deletes what you meant to keep. The dry run is the guard for that.
