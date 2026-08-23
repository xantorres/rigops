# Support matrix

What's first-class on which platform, the version floors, and exactly what CI covers.

## Platform coverage

| Feature | macOS | Linux |
|---|---|---|
| CLI (`rigops <cmd>`) | First-class. | Runs - every subcommand is plain Python 3.9+ stdlib. |
| `rigops doctor` | `--supervisor launchd` (default) - full runtime/exit-code judgment via `launchctl`. | `--supervisor none` only - evidence-file-age judgment, no runtime/exit-code signal (no `launchctl` to ask). Not auto-detected by the CLI itself: pass `--supervisor none` explicitly when running by hand. |
| Scheduled jobs | `install.sh` installs two launchd jobs (`doctor --heal` every 30 min, `ledger write` weekly Monday 09:05). | No installer-managed scheduler. `templates/cron/crontab.example` ships as a starting point - **unverified**, read it before use. |
| Statusline / plugin hooks | Full. | Full - pure bash plus optional `jq`, nothing macOS-specific. |
| `rigops reap` | Full - process matching via `ps`/`lsof`. | Full - the same `ps`/`lsof` parsing handles both tty formats (`??` on macOS, `?` on Linux; both are stripped to detect "no controlling tty"). |

`install.sh` itself: when `launchctl` isn't found on PATH, it automatically behaves as `--no-jobs` and prints a note pointing at the cron template - see [INSTALL.md](INSTALL.md#linux).

## CI coverage

From `.github/workflows/ci.yml`:

- **lint job** - `ubuntu-latest` only, Python 3.9 and 3.13 matrix. Runs `make check` (redaction gate over the tree and full history, shellcheck, ruff, unit tests, hook tests, manifest validation, strict plugin validation).
- **smoke job** - `ubuntu-latest` on Python 3.9, and `macos-latest` on Python 3.11. Runs `make smoke` (`tests/smoke.sh`) on both. `make smoke-launchd` (`tests/smoke-launchd.sh`) runs on the macOS leg only.

So: the Python 3.9 floor is exercised on Linux; launchd-specific behavior is exercised on macOS; there is no Windows leg anywhere, and cron is templated but never actually executed by CI on Linux.

## Floors

- **Python** - 3.9+, stdlib only, for every shipped script (`bin/`, `libexec/`, `lib/rigops/`). Dev tooling (`ruff`, `shellcheck`) is exempt - it isn't shipped to installs.
- **Shell** - bash 3.2 / BSD userland compatible for everything shipped (`install.sh`, `plugin/hooks/*.sh`, `plugin/statusline/*.sh`). No GNU-only flags assumed; where GNU and BSD tools diverge (for example `stat`), the script tries GNU's flags first and falls back to BSD's.
- **jq** - optional, not a hard dependency. Hooks that parse hook-stdin JSON need it: `skill-gate.sh` and `ctx-nudge.sh` (plus the `ctx-probe.sh` helper both of those call). Each exits `0` silently when `jq` is missing. `rg-flag-guard.sh` doesn't need `jq` - it parses its JSON stdin with `python3` instead. The statusline degrades per-segment without `jq`: both its context and EIT segments go blank, but it still prints (empty) and exits `0`.

## Explicitly unverified

- `templates/cron/crontab.example` - ships as a starting point, not exercised by any test or CI leg.
- Any Linux launchd-equivalent for `doctor` - there isn't one; `--supervisor none` evidence-only judgment is Linux's only path, and having no runtime/exit-code signal there is by design, not a gap being tracked.
- Windows - not supported, not tested, not a target.

## See also

- [INSTALL.md](INSTALL.md) - the Linux install path.
- [SAFETY.md](SAFETY.md) - destructive-action caps (platform-independent).
