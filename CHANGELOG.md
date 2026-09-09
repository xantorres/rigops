# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-09-09

### Added

- render: a `rigops.render` package and `rigops render` command that renders a
  single source tree onto every surface that consumes it: `claude` (CLAUDE.md,
  rules, agents, skills into `~/.claude`), `codex` (a trimmed AGENTS.md digest
  and per-agent TOML into `~/.codex`), `local` (the law and voice sections into
  `~/.config/ai-agent/system-prompt.md`), `agents` (a generated agent roster at
  `~/.claude/references/agents.md`), and `repos` (a fixed AGENTS.md pointer
  into every registered repo). `--check` reports drift without writing; `all`
  renders every target in one pass.
- doctor: `check_render` runs `rigops render all --check` and turns every
  drifted or stale path into a finding; `check_rtk` pins the token-reduction
  proxy to a configured version and flags a missing install or a mismatch.

### Removed

- plugin: the `rg -r`/`-rn`/`-rl` guard hook is no longer shipped with the
  plugin. It now lives in the user's own `PreToolUse` dispatcher.

## [0.4.0] - 2026-09-08

### Added

- doctor: a `rigops/doctor/` package of config checks, one module per check,
  discovered by naming convention (`check_*.py`). Each module imports
  `rigops.core` and nothing else from the package, and `check_imports` fails the
  run when a module reaches for a sibling or grows past its line cap.
- doctor: `check_pointers` extracts every path, agent, skill, plugin id, MCP
  server, launchd label and model id from the instruction surface (CLAUDE.md,
  rules, references, skills, agents, settings.json, the vault entry points and
  personal-repo CLAUDE.md files) and asserts each one resolves against the
  filesystem, `enabledPlugins`, `installed_plugins.json` and `launchctl list`.
  Prose cannot be type-checked, so a pointer to something deleted is the one
  defect class that rots silently; this makes it a build failure.
- doctor: `check_budget` caps the always-on instruction surface, meaning
  CLAUDE.md plus the rules that carry no `paths:` frontmatter plus the memory
  index, measured as bytes over four. Ceiling is `doctor.budget.always_on_tokens`.
- doctor: `check_plans` lints plan filenames, frontmatter and archive placement
  against the grammar the plans directory documents.
- doctor: `--config-only`, `--no-config` and `--check NAME` flags. Findings print
  in the backlog line grammar with a stable id derived from the defect, so a run
  can be deduplicated against an existing backlog. A finding exits nonzero
  everywhere except under `--heal`, the unattended supervisor path whose own
  registry row is judged by that exit code.
- core: a `rigops/core/` package holding the shared config, state and `Finding`
  primitives, the single import hop the modular packages are allowed.

## [0.3.2] - 2026-09-07

### Fixed

- reap: `discover_repos` no longer aborts a whole `--root` scan when one
  directory under it cannot be read. The `.git` probe raises EACCES on
  Linux where macOS answers False, so a single unreadable directory ended
  a nightly sweep of the tree.

## [0.3.1] - 2026-09-07

### Added

- CLI: `rigops version` (and `rigops --version`) prints the installed
  version, so a report can name the copy it came from.
- reap: the run summary carries `listing_skipped`, the repositories whose
  worktree listing the deadline cut short. `deadline_skipped` counts
  worktrees, so it read 0 when none were ever enumerated.

### Changed

- The install smoke test runs as part of `make check`. It is hermetic --
  a throwaway `HOME`, `--no-jobs`, no network -- and adds about 14 s.
  `make smoke-launchd` stays manual: it loads real launchd jobs.

### Fixed

- reap: the worktree size probe is capped at what is left of the run
  deadline instead of a fixed 120 s, the last call in a repository scan
  that could carry a run past its deadline. A budget too thin to spend
  leaves the size at 0 and marks the repository timed out.
- janitor: the `max_files ignored` warning is printed once per watermark
  instead of once per matched file.
- ledger: `diff` no longer lets the per-model share lifted out of
  `eit_pct_by_model` overwrite a top-level `<model>_pct`, matching the
  precedence the cave score lift already had.
- tests: `tests/__init__.py` makes `python3 -m unittest tests.<module>`
  work alongside `unittest discover`.

## [0.3.0] - 2026-09-06

### Added

- reap: `--repo` is repeatable and resolved paths are deduped, so one run
  can scan several checkouts and a checkout named twice is scanned once.
  Discovery now accepts a `.git` directory one or two levels below the
  root, not only `root/<group>/<repo>`.
- reap: the run summary carries `deadline_skipped`, the worktrees a spent
  budget left unjudged.
- ledger: `diff` lifts the per-model shares and the cave score out of the
  nested source payloads, so the Opus share is visible again. rtk counters
  are all-time snapshots and stay out of the diff.
- janitor: a watermark whose glob matches a single file is judged by
  `max_kb` instead of being skipped. `max_files` against a file is a
  configuration error and says so on stderr.
- doctor: the events log and `--json` carry `stale_count` beside
  `fail_count`, so report-only stale rows stay countable without
  opening the report. Staleness still never triggers the notify
  command.

### Changed

- doctor: a completed run exits 0. Fleet health flows through the report,
  the notify command, the events log and `--json`; nonzero is left to an
  unreadable registry or a bad argument. A red job elsewhere in the fleet
  no longer made the doctor kickstart itself.
- doctor: an item with no launchd label and no evidence collapses into one
  footer line under the table. The JSON payload still lists every job.

### Fixed

- reap: the run deadline is enforced inside each repository scan. The
  worktree listing, every per-worktree git call, and merge-target and
  default-branch resolution take what is left of the budget; worktrees
  with nothing left are reported as deadline skips rather than judged.
  Nightly runs no longer finish hours past a 3600 s deadline.
- doctor: an item whose launchd label does not resolve is judged by
  evidence age. Unknown is reserved for an item with neither a label nor
  evidence.
- doctor: interval cadences are tested before the on-demand pattern, and
  monthly and quarterly are understood, so "manual, expected at least
  monthly" is staleness-checked.
- doctor: a stale item with no label reports no action, since the heal
  loop skips those and nothing would act on a kickstart.
- janitor: a tripped single-file watermark reports one file, not zero.
- reap tests: every case redirects the state directory and the config path
  into a temporary directory, so a run can no longer append fake kill and
  overflow lines to the operator's reaper log.

## [0.2.0] - 2026-08-27

### Added

- Ledger friction columns: `denials`, `denials_headless`, `corrections`,
  `tier3_breaches`, `tool_err_per_100`, computed from user-role transcript
  rows each `ledger write`. Historical rows render `-`. New config key
  `friction.headless_projects` scopes the headless split.
- skill-gate: each gate now fires at most once per session (marker files
  under the state dir, 7-day GC). `RIGOPS_SKILL_GATE_NO_DEDUPE=1` restores
  the old every-prompt behavior.

### Fixed

- reap: git subprocesses run in their own process group and are SIGKILLed
  group-wide on timeout, so a hung remote can no longer stall a run for
  hours past the deadline. The fetch and worktree-list timeouts are also
  clamped to the remaining run budget. Deeper local git calls are not yet
  deadline-clamped; worst-case overrun is one repo scan.

## [0.1.0] - 2026-08-24

Initial release.

### Added

- Measure, intervene, diff loop: `rigops eit` and `rigops ledger` weekly
  rollups, with `ledger note` to record an intervention and `ledger diff` to
  compare the weeks around it.
- Context tax loop: `rigops tax` tracks the config-regrowth series so
  always-loaded context earns its keep.
- Fleet doctor loop: `rigops doctor` reports on registered automation jobs,
  report-only by default with `--heal` as an explicit opt-in.
- Hygiene loop: `rigops reap` for stale worktrees and processes, a
  rules-driven janitor for state directories, and a linted backlog for
  deferred findings.
- Ledger source adapters for `rtk`, `ccusage`, and custom commands or JSON
  files, annotating ledger rows without ever breaking a write.
- Headless auth probe for unattended rigs; ships disabled and off the
  default schedules.
- Claude Code plugin: five commands (doctor, ledger, tax, backlog, week),
  two skills (ops-loop, fleet-triage), three hooks (skill gate, context
  nudge, rg flag guard), and an EIT statusline.
- `install.sh` installer that prints its plan by default and only writes
  with `--apply`, plus launchd job templates for macOS and a cron example
  for Linux.
- Redaction gate over the working tree and full history, shellcheck, ruff,
  unit tests, hook table tests, plugin manifest validation, and fresh-HOME
  smoke tests wired into `make check` and CI.
