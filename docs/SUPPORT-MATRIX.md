# Support matrix

What's first-class on which platform, the version floors, and exactly what CI covers.

## Platform coverage

| Feature | macOS | Linux |
|---|---|---|
| CLI (`rigops <cmd>`) | First-class. | Runs - every subcommand is plain Python 3.11+ stdlib. |
| `rigops doctor` | `--supervisor launchd` (default) - full runtime/exit-code judgment via `launchctl`. | `--supervisor none` (default off macOS) - evidence-file-age judgment, no runtime/exit-code signal (no `launchctl` to ask). |
| Scheduled jobs | `rigops jobs install --apply` installs launchd jobs: `doctor` (passes `--exit-zero`; report-only unless installed with `--heal`) every 30 min and `ledger write` weekly Monday 09:05 by default; `janitor --apply` (Sunday 10:00), `reap --apply` (daily 08:30) and the experimental `reap-procs` (`reap --procs-only --apply`, every 15 minutes, launchd `StartInterval` 900) only through `--jobs`. | `rigops jobs install` prints crontab lines to add by hand; `lib/rigops/templates/cron/crontab.example` ships the same lines. **Unverified**, read them before use. |
| Plugin hooks | Full. | Full - pure bash, no `jq`, nothing macOS-specific. |
| `rigops reap` | Full - process matching via `ps`/`lsof`. | Full - the same `ps`/`lsof` parsing handles both tty formats (`??` on macOS, `?` on Linux; both are stripped to detect "no controlling tty"). A process is killed only when reparented to pid 1; with a per-user service manager (systemd `--user`), orphans go to that manager instead, so reap leaves them alone and keeps the tree. |

`rigops jobs install` on a host without launchd (any non-macOS platform) prints crontab lines instead of writing plists - see [INSTALL.md](INSTALL.md#linux).

## CI coverage

From `.github/workflows/ci.yml`, which also runs as the first stage of the release workflow:

- `lint` - `ubuntu-latest`, Python latest. Runs `make check` (redaction gate over the tree and full history, shellcheck, ruff, unit tests, hook tests, manifest validation, strict plugin validation), then re-runs the redaction gate unit tests with `mawk` as `awk` under GNU grep, and fails if either tool is another flavour.
- `test` - `ubuntu-latest` and `macos-latest`, each on Python 3.11 (the floor) and latest. Runs the unit tests, the hook tests and `make smoke`.
- `package` - builds the sdist and wheel on Python 3.11 and checks the wheel against the repo: every shipped file, the `rigops` entry point, the Python floor and no runtime dependencies.
- `runners` - the built wheel under `uvx`, `pipx run` and a plain venv on `ubuntu-latest` and `macos-latest`. Each reports the wheel's version; `jobs install --apply` exits 2 and writes no plist under `uvx`, `pipx run` and with an empty `PATH`; the venv holds the templates.
- `quickstart` - `ubuntu-latest` and `macos-latest`. Runs the bash fences under the README's "Quickstart" heading as written, in a temp `HOME` with an empty `~/.claude/projects`, installing the built wheel where the README installs `rigops` from the index. Every command must exit 0. On macOS `rigops jobs status` must then show `doctor` and `ledger` loaded, and the jobs are removed at the end; on Linux `jobs install --apply` must print two crontab lines.
- `jobs-roundtrip` - `macos-latest` only. Installs the previous release's wheel, schedules the jobs with `rigops jobs install --apply`, checks both are loaded, upgrades with `pipx install --force` and checks the plists are byte-identical and still loaded, then `rigops jobs uninstall --apply` leaves no plist. Until a package release exists the previous wheel is a copy of the current tree at version 0.0.0.

So: launchd is exercised on macOS only, and only against the runner's own throwaway launchd; cron is templated but never executed by CI on Linux; there is no Windows leg anywhere. Python versions between 3.11 and latest are not run.

## Floors

- **Python** - 3.11+, stdlib only, for the shipped package (`lib/rigops/`). Dev tooling (`ruff`, `shellcheck`) is exempt - it isn't shipped to installs.
- **Shell** - bash 3.2 / BSD userland compatible for everything shipped (`plugin/hooks/*.sh`, `patterns/statusline/*.sh`). No GNU-only flags assumed; where GNU and BSD tools diverge (for example `stat`), the script tries GNU's flags first and falls back to BSD's.
- **jq** - optional, not a hard dependency. The hooks no longer need it. The statusline example in `patterns/statusline/` degrades per-segment without `jq`: both its context and EIT segments go blank, but it still prints (empty) and exits `0`.

## Experimental commands

These four commands carry no compatibility promise; the CLI help line says the same.

| Command | Status |
|---|---|
| `authprobe` | No compatibility promise; may change or leave in any release. |
| `backlog` | No compatibility promise; may change or leave in any release. |
| `eit` | No compatibility promise; may change or leave in any release. |
| `mine` | No compatibility promise; may change or leave in any release. |

## Explicitly unverified

- `lib/rigops/templates/cron/crontab.example` - ships as a starting point, not exercised by any test or CI leg.
- Any Linux launchd-equivalent for `doctor` - there isn't one; `--supervisor none` evidence-only judgment is Linux's only path, and having no runtime/exit-code signal there is by design, not a gap being tracked.
- Windows - not supported, not tested, not a target.

## See also

- [INSTALL.md](INSTALL.md) - the Linux install path.
- [SAFETY.md](SAFETY.md) - destructive-action caps (platform-independent).
