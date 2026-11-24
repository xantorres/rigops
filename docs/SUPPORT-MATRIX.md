# Support matrix

What's first-class on which platform, the version floors, and exactly what CI covers.

## Platform coverage

| Feature | macOS | Linux |
|---|---|---|
| CLI (`rigops <cmd>`) | First-class. | Runs - every subcommand is plain Python 3.11+ stdlib. |
| `rigops doctor` | `--supervisor launchd` (default) - full runtime/exit-code judgment via `launchctl`. | `--supervisor none` (default off macOS) - evidence-file-age judgment, no runtime/exit-code signal (no `launchctl` to ask). |
| Scheduled jobs | `install.sh` installs launchd jobs: `doctor` (passes `--exit-zero`; report-only unless installed with `--heal`) every 30 min and `ledger write` weekly Monday 09:05 by default; `janitor --apply` (Sunday 10:00) and `reap --apply` (daily 08:30) only through `--jobs`. | No installer-managed scheduler. `lib/rigops/templates/cron/crontab.example` ships as a starting point - **unverified**, read it before use. |
| Plugin hooks | Full. | Full - pure bash, no `jq`, nothing macOS-specific. |
| `rigops reap` | Full - process matching via `ps`/`lsof`. | Full - the same `ps`/`lsof` parsing handles both tty formats (`??` on macOS, `?` on Linux; both are stripped to detect "no controlling tty"). A process is killed only when reparented to pid 1; with a per-user service manager (systemd `--user`), orphans go to that manager instead, so reap leaves them alone and keeps the tree. |

`install.sh` itself: when `launchctl` isn't found on PATH, it automatically behaves as `--no-jobs` and prints a note pointing at the cron template - see [INSTALL.md](INSTALL.md#linux).

## CI coverage

From `.github/workflows/ci.yml`:

- **lint job** - `ubuntu-latest` only, Python 3.9 and 3.13 matrix. Runs `make check` (redaction gate over the tree and full history, shellcheck, ruff, unit tests, hook tests, manifest validation, strict plugin validation), then re-runs the redaction gate unit tests with `mawk` as `awk` under GNU grep, and fails if either tool is another flavour.
- **smoke job** - `ubuntu-latest` on Python 3.9, and `macos-latest` on Python 3.11. Runs `make smoke` (`tests/smoke.sh`) on both. `make smoke-launchd` (`tests/smoke-launchd.sh`) runs on the macOS leg only.

So: the Python 3.9 floor is exercised on Linux; launchd-specific behavior is exercised on macOS; there is no Windows leg anywhere, and cron is templated but never actually executed by CI on Linux.

## Floors

- **Python** - 3.11+, stdlib only, for the shipped package (`lib/rigops/`). Dev tooling (`ruff`, `shellcheck`) is exempt - it isn't shipped to installs.
- **Shell** - bash 3.2 / BSD userland compatible for everything shipped (`install.sh`, `plugin/hooks/*.sh`, `patterns/statusline/*.sh`). No GNU-only flags assumed; where GNU and BSD tools diverge (for example `stat`), the script tries GNU's flags first and falls back to BSD's.
- **jq** - optional, not a hard dependency. The hooks no longer need it. The statusline example in `patterns/statusline/` degrades per-segment without `jq`: both its context and EIT segments go blank, but it still prints (empty) and exits `0`.

## Explicitly unverified

- `lib/rigops/templates/cron/crontab.example` - ships as a starting point, not exercised by any test or CI leg.
- Any Linux launchd-equivalent for `doctor` - there isn't one; `--supervisor none` evidence-only judgment is Linux's only path, and having no runtime/exit-code signal there is by design, not a gap being tracked.
- Windows - not supported, not tested, not a target.

## See also

- [INSTALL.md](INSTALL.md) - the Linux install path.
- [SAFETY.md](SAFETY.md) - destructive-action caps (platform-independent).
