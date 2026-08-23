# Install

How to get rigops running: as a Claude Code plugin, as installed scripts and launchd jobs, or both.

## Two halves

rigops ships in two independent pieces that read the same JSON config:

- **Plugin half** — a Claude Code marketplace plugin: five slash commands, two skills, and three hook entries. Zero daemons; nothing runs on a schedule, everything fires from inside a Claude Code session.
- **Script half** — `install.sh` copies the `rigops` CLI and its libraries onto disk, symlinks it onto your PATH, and (optionally) installs two launchd jobs on macOS. This is what gives you `rigops ledger`, `rigops doctor`, `rigops reap`, and the rest outside of a Claude Code session, plus scheduled runs.

Install either half alone, or both. The plugin degrades gracefully when the CLI isn't present (below); the CLI works with the plugin absent.

See [CONFIG.md](CONFIG.md) for the config file both halves read.

## Plugin install

Inside Claude Code:

```
/plugin marketplace add xantorres/rigops
```

Then install the `rigops` plugin from that marketplace.

What lands:

- **5 commands** — `/rigops:backlog`, `/rigops:doctor`, `/rigops:ledger`, `/rigops:tax`, `/rigops:week`.
- **2 skills** — `ops-loop` (the measure/intervene/note/diff weekly loop, see [LEDGER.md](LEDGER.md)) and `fleet-triage` (reading a `doctor` report, see [REGISTRY.md](REGISTRY.md)).
- **3 hook entries**, from `plugin/hooks/hooks.json`: two `UserPromptSubmit` hooks (`skill-gate.sh`, `ctx-nudge.sh`) and one `PreToolUse` hook on `Bash` (`rg-flag-guard.sh`, which blocks `rg -r`/`-rn`/`-rl` — in ripgrep `-r` means `--replace`, not recursive, and the mistake fails silently otherwise).
- The statusline script (`plugin/statusline/rigops-statusline.sh`) — shipped but not wired into `~/.claude/settings.json` automatically; see `--statusline` below.

The plugin works standalone. `skill-gate.sh` needs `jq`; `rg-flag-guard.sh` needs `python3` instead. `ctx-nudge.sh` needs `jq` too — without it the hook exits immediately, a no-op. `ctx-nudge.sh` and the statusline script look for a `rigops` binary on PATH or at `~/.local/bin/rigops`; if the binary isn't found (but `jq` is), `ctx-nudge.sh` falls back to hardcoded context tiers (250000 / 350000 / 500000, rearm 50000 — the same numbers as `context.nudge_tiers`/`rearm_tokens` in [CONFIG.md](CONFIG.md)) instead of reading config. The statusline hardcodes only the first two thresholds (250000 / 350000) for coloring, has no re-arm, and only reads config at all when `jq` is present. Without `jq`, the statusline's context and EIT segments both go blank, but it still prints and exits cleanly.

## Script install

```
git clone https://github.com/xantorres/rigops
cd rigops
bash install.sh
```

Plan-by-default: with no `--apply`, `install.sh` prints exactly what it would do and changes nothing, exit 0. On a clean `$HOME`, that plan looks like this:

```text
rigops install: plan for ~ (dry run; re-run with --apply to act)
1. plan: preflight
     payload dirs OK under ~/rigops
     python /opt/homebrew/opt/python@3.14/bin/python3.14 (>=3.9 OK)
     sha256 tool: shasum -a 256
     launchd jobs enabled: doctor ledger
2. plan: copy tree -> ~/.local/share/rigops
     installed ~/.local/share/rigops/bin/rigops
     installed ~/.local/share/rigops/libexec/rigops-authprobe
     installed ~/.local/share/rigops/libexec/rigops-backlog
     installed ~/.local/share/rigops/libexec/rigops-config
     installed ~/.local/share/rigops/libexec/rigops-doctor
     installed ~/.local/share/rigops/libexec/rigops-eit
     installed ~/.local/share/rigops/libexec/rigops-janitor
     installed ~/.local/share/rigops/libexec/rigops-ledger
     installed ~/.local/share/rigops/libexec/rigops-reap
     installed ~/.local/share/rigops/libexec/rigops-tax
     installed ~/.local/share/rigops/lib/rigops/__init__.py
     installed ~/.local/share/rigops/lib/rigops/authprobe.py
     installed ~/.local/share/rigops/lib/rigops/backlog.py
     installed ~/.local/share/rigops/lib/rigops/config.py
     installed ~/.local/share/rigops/lib/rigops/fmt.py
     installed ~/.local/share/rigops/lib/rigops/janitor.py
     installed ~/.local/share/rigops/lib/rigops/judge.py
     installed ~/.local/share/rigops/lib/rigops/launchd.py
     installed ~/.local/share/rigops/lib/rigops/levers.py
     installed ~/.local/share/rigops/lib/rigops/reap.py
     installed ~/.local/share/rigops/lib/rigops/registry.py
     installed ~/.local/share/rigops/lib/rigops/sources/__init__.py
     installed ~/.local/share/rigops/lib/rigops/sources/ccusage.py
     installed ~/.local/share/rigops/lib/rigops/sources/command.py
     installed ~/.local/share/rigops/lib/rigops/sources/file_json.py
     installed ~/.local/share/rigops/lib/rigops/sources/rtk.py
     installed ~/.local/share/rigops/lib/rigops/state.py
     installed ~/.local/share/rigops/lib/rigops/transcripts.py
     installed ~/.local/share/rigops/templates/cron/crontab.example
     installed ~/.local/share/rigops/templates/launchd/doctor.plist.in
     installed ~/.local/share/rigops/templates/launchd/ledger.plist.in
     installed ~/.local/share/rigops/plugin/statusline/rigops-statusline.sh
     installed ~/.local/share/rigops/LICENSE
3. plan: symlink ~/.local/bin/rigops -> ~/.local/share/rigops/bin/rigops
     installed ~/.local/bin/rigops
note: ~/.local/bin is not on PATH
4. plan: scaffold config + registry under ~/.config/rigops
     scaffolded ~/.config/rigops/config.json
     scaffolded ~/.config/rigops/registry.md
5. plan: jobs under ~/Library/LaunchAgents
     installed loaded    label=com.rigops.doctor plist=~/Library/LaunchAgents/com.rigops.doctor.plist
     installed loaded    label=com.rigops.ledger plist=~/Library/LaunchAgents/com.rigops.ledger.plist
6. plan: write manifest -> ~/.local/share/rigops/install.manifest.json
     would write ~/.local/share/rigops/install.manifest.json
7. plan: summary
     installed=35 updated=0 unchanged=0 jobs_loaded=2
     next: add ~/.local/bin to PATH if needed, then run "rigops help"
```

Re-run with `--apply` to actually do it:

```
bash install.sh --apply
```

The two installed jobs: `doctor` runs `rigops-doctor --heal` every 30 minutes; `ledger` runs `rigops-ledger write` weekly, Monday 09:05 local time.

### Flags

From `bash install.sh --help`:

| Flag | Meaning |
|---|---|
| `--apply` | Perform the actions. Without it, every run is plan-only, regardless of any other flag. |
| `--plugin-only` | Install the CLI tree and symlink only; skip jobs, templates, and the statusline copy. Config and registry are still scaffolded. Conflicts with `--statusline`. |
| `--no-jobs` | Skip launchd job installation. Conflicts with `--jobs`. |
| `--jobs a,b` | Comma-separated job list to install. Available jobs: `doctor`, `ledger`. Naming anything else is a usage error. |
| `--prefix DIR` | Install prefix (default `$HOME/.local/share/rigops`). |
| `--python PATH` | `python3` interpreter to use (default: first `python3` on PATH). Must be >= 3.9. |
| `--statusline` | Wire the plugin statusline into `~/.claude/settings.json`. Conflicts with `--plugin-only`. |
| `--uninstall` | Remove a previous install, driven entirely by its manifest. |
| `--purge` | With `--uninstall`, also delete the config/state/log directories. Requires `--uninstall`. |
| `--yes` | Skip the confirmation prompt before a `--purge` deletion. |
| `--home DIR` | Override home for every derived path (used by the test suite; also usable to install into another account's home). |
| `-h`, `--help` | Show help. |

Env: `RIGOPS_INSTALL_LABEL_PREFIX` — launchd label prefix for the jobs it installs, default `com.rigops`. Read only by `install.sh`, mainly so smoke tests never collide with a real install's jobs.

### Idempotency

Every installed file is hashed (SHA-256, `shasum -a 256` if present, else `sha256sum`) and recorded in `install.manifest.json` under the install prefix. A re-run hashes the source tree again and compares: a file whose hash already matches is left alone (`unchanged`). A genuine no-op re-run reports `installed=0 updated=0` in the final summary line — the unchanged count carries the rest.

launchd jobs follow the same idea: if the rendered plist's hash matches what's on disk *and* the job is currently loaded, it's left alone. Otherwise the plist is written and reloaded through `launchctl bootout` then `launchctl bootstrap`, in that order (trying the `gui/<uid>` domain first, falling back to `user/<uid>`) — this covers both a brand-new job and a changed one.

Config and registry are scaffolded from `config/config.example.json` and `config/registry.example.md` **only if the destination doesn't already exist** — `install.sh` never overwrites a config file you've edited.

### Statusline wiring

`--statusline` (without `--plugin-only`) edits `~/.claude/settings.json`, setting its `statusLine` key to run the installed statusline script. It prints a unified diff of the change before writing, and on `--apply` writes a timestamped backup first: `~/.claude/settings.json.rigops-backup.<YYYYMMDDHHMMSS>`. If `settings.json` doesn't exist yet, it's created fresh with no backup needed.

### Uninstall and purge

`--uninstall` reads `install.manifest.json` and undoes exactly what it recorded — nothing else:

- Each job is booted out of launchd. If its on-disk plist still matches the manifest's recorded hash, the plist is removed; if it's been edited since install, it's renamed to `<plist>.rigops-disabled` instead (with a printed restore command) — never silently deleted, never silently left live.
- Each file is removed if its hash still matches what was installed; a file modified since install is left in place with a warning, never overwritten or deleted out from under you.
- `__pycache__` directories under the prefix are swept (they're not in the manifest — Python writes them lazily) and the now-empty prefix tree is removed.

`--purge` (requires `--uninstall`) additionally deletes the config, state, and log directories. It prompts for confirmation unless `--yes` is passed. Before deleting anything, each target passes a `purge_safe` check: it must be an absolute, non-symlink, existing directory named exactly `rigops`, distinct from `/` and from `$HOME` — checked both as given and after resolving any symlinked ancestor, so a bad manifest or an env override can only refuse the purge, never widen it. A refused target is reported with the exact `rm -rf` command to run by hand if you're sure.

If `RIGOPS_CONFIG` or `RIGOPS_STATE_DIR` pointed outside anything this installer created, that path is recorded as "managed externally," and `--purge` skips it, printing a note instead of touching it.

### PATH

The symlink lands at `~/.local/bin/rigops`. If `~/.local/bin` isn't already on your PATH, `install.sh` prints a note; add it yourself (`export PATH="$HOME/.local/bin:$PATH"`).

## Linux

`install.sh` runs anywhere Python 3.9+ and a POSIX shell exist, but launchd jobs are macOS-only. When `launchctl` isn't found, the installer automatically behaves as `--no-jobs` and prints `note: launchd not found; jobs skipped (Linux: see templates/cron/crontab.example)`.

A cron fallback template ships at `templates/cron/crontab.example` — install with `install.sh --apply --no-jobs`, then add lines from that template yourself via `crontab -e`. It is **unverified**: read it before use. See [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) for exactly what is and isn't exercised by CI on Linux.

## See also

- [CONFIG.md](CONFIG.md) — every key both halves read.
- [SAFETY.md](SAFETY.md) — what's destructive, what's opt-in, and the caps on each.
- [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) — platform coverage and what's unverified.
