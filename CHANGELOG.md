# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
