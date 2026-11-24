# Install

How to get rigops running: the `rigops` package (a command plus optional scheduled jobs), the Claude Code plugin, or both.

rigops ships in two independent pieces that read the same JSON config ([CONFIG.md](CONFIG.md)):

- **Package** - the `rigops` command (`ledger`, `doctor`, `reap`, and the rest) and `rigops jobs`, which schedules `doctor` and `ledger` (and on request `janitor` and `reap`) on a timer.
- **Plugin** - slash commands, skills and hooks inside Claude Code. It degrades gracefully when the command is missing, and the command works without the plugin.

## Requirements

macOS or Linux, Python 3.11 or newer. No runtime dependencies.

## Install

```
pipx install rigops
```

or `uv tool install rigops`, or `python3 -m pip install --user rigops`. Then check it:

```
rigops version
rigops config check
```

## Scheduled jobs

`rigops jobs install` prints what it would do and changes nothing. Add `--apply` to write the launchd plists and load them:

```
rigops jobs install
rigops jobs install --apply
```

- `doctor` (every 30 minutes) and `ledger write` (Monday 09:05) are the default. `--jobs doctor,ledger,janitor,reap` picks others; `janitor` and `reap` run with `--apply` on schedule (Sunday 10:00, daily 08:30).
- `--heal` lets the scheduled `doctor` kill hung jobs and kickstart stale ones. Without it the job only reports.
- The plist holds the absolute path of the `rigops` on `PATH`, because launchd has no shell `PATH` to search. `jobs install` refuses (exit 2) when that `rigops` is not the one running, so `python -m rigops jobs install` is always refused: run the installed command.
- `uvx rigops ...` and `pipx run rigops ...` are for trying the tool, not for scheduling it. `jobs install` refuses under them because it recognises their default cache locations; with a custom cache directory it cannot, and `rigops jobs status` and `doctor` flag the job once that cache is cleaned.
- Re-running is safe: an unchanged plist that is loaded is left alone, a changed one is rewritten and reloaded.

```
rigops jobs status
```

`jobs status` lists each `com.rigops.*` job as installed or not, loaded or not, and whether its program still runs; it exits 1 on a problem and 2 when it cannot ask launchd or read a plist.

## Linux

launchd is macOS-only. On Linux `rigops jobs install` prints crontab lines with the absolute path of your `rigops`; create the log directory it names and add the lines with `crontab -e`. The lines never heal: there is no launchd to inspect for hung processes, so `doctor` runs with `--supervisor none`. They are **unverified**; read them before use, and see [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) for what CI does and does not exercise on Linux. A template with the same lines ships at `lib/rigops/templates/cron/crontab.example`.

## Upgrade

Upgrade the package with the tool that installed it (`pipx upgrade rigops`, `uv tool upgrade rigops`, or `python3 -m pip install --user --upgrade rigops`). The plists keep working because they name the stable console-script path, not the package files. After an interpreter upgrade the console script can break; run `rigops jobs status`, and re-run `rigops jobs install --apply` if it reports a problem.

## Uninstall

Jobs first, then the package:

```
rigops jobs uninstall
rigops jobs uninstall --apply
pipx uninstall rigops
```

`jobs uninstall --apply` boots out and removes the four `com.rigops.*` plists and nothing else. Remove the package with the tool that installed it (`uv tool uninstall rigops`, or `python3 -m pip uninstall rigops`). Config (`~/.config/rigops/`), state (`~/.local/state/rigops/`) and logs (`~/Library/Logs/rigops/`) are left in place; delete them by hand if you want them gone.

## Moving from an install.sh install

Versions 0.10 to 0.12 installed from a checkout with `install.sh` into a prefix (`~/.local/share/rigops` by default). That script is gone. Use the one from tag `v0.12.0`, which handles every prefix install from 0.10 to 0.12:

```
git clone https://github.com/xantorres/rigops
git -C rigops checkout v0.12.0
cd rigops
./install.sh --uninstall
./install.sh --uninstall --apply
```

The first run shows the plan. The second removes the prefix and its jobs and restores the `statusLine` in `~/.claude/settings.json` that `--statusline` replaced. Then install the package as above and run `rigops jobs install --apply`.

Run the old uninstaller first. It boots out every job its manifest records and renames any plist at those paths that it did not write to `.rigops-disabled`, so jobs the package installed before it would end up unloaded and disabled.

A hand-written LaunchAgent that already schedules a rigops command under another label is no longer skipped: remove it or expect a duplicate.

## Plugin install

Inside Claude Code:

```
/plugin marketplace add xantorres/rigops
```

Then install the `rigops` plugin from that marketplace.

What lands:

- **5 commands** - `/rigops:backlog`, `/rigops:doctor`, `/rigops:ledger`, `/rigops:tax`, `/rigops:week`.
- **2 skills** - `ops-loop` (the measure/intervene/note/diff weekly loop, see [LEDGER.md](LEDGER.md)) and `fleet-triage` (reading a `doctor` report, see [REGISTRY.md](REGISTRY.md)).
- **2 hook entries**, from `plugin/hooks/hooks.json`: one `SessionStart` hook (on startup, resume and compact) and one `UserPromptSubmit` hook, both running `rigops hook <event>` through `rigops-hook.sh`. Two hooks left the plugin for the user's own dispatcher: the `rg -r`/`-rn`/`-rl` guard (`-r` means `--replace` in ripgrep, not recursive, and the mistake fails silently) and the skill-forcing gate.

The plugin works standalone. Hooks need no `jq`. `rigops-hook.sh` looks for a `rigops` command on PATH or at `~/.local/bin/rigops`; without it, or with a CLI too old to know `rigops hook`, the hooks print nothing.

## See also

- [CONFIG.md](CONFIG.md) - every key both pieces read.
- [SAFETY.md](SAFETY.md) - what's destructive, what's opt-in, and the caps on each.
- [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) - platform coverage and what's unverified.
