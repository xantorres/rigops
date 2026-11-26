# Contributing

## Setup

After cloning, run `make hooks` once to enable the commit-message and pre-push redaction gates.

From a checkout, `make check` runs everything (the Makefile exports `PYTHONPATH`). To get the `rigops` command, create a virtualenv and run `python3 -m pip install -e .` in it. There is no install script.

## Commits

Format: `<type>(<scope>): <description>`, for example `fix(doctor): report an unreadable plist as an error`.

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`. The scope is the command or area the change touches: `doctor`, `ledger`, `hook`, `release`, `jobs`.

The subject is at most 72 characters, lower case after the colon, in the imperative, with no full stop. The body is prose: what was wrong or missing and why this change fixes it. One change per commit; a fix and the refactor it needed are two commits.

A user-visible change adds a line under `## [Unreleased]` in `CHANGELOG.md` in the same commit.

No authorship attribution for any tool or model: commits and PRs never carry trailers crediting one, and never carry "generated with" footers. The content stands on its own.

No tracker-style keys anywhere - code, comments, commit messages, PR text. The redaction gate enforces this history-wide, not just in the tree. Write the actual reason in plain language instead - trackers move, code shouldn't depend on them.

## Before pushing

`make check` must pass: redaction gate (tree + history), shellcheck, ruff, unit tests, hook tests, the smoke test, manifest validation, plugin validation.

CI on a fork runs the public redaction patterns only; the private layer is required on pushes to the upstream repository.

`make smoke` runs the CLI end to end in a throwaway `HOME` and never touches launchd; the jobs migration test (`tests/test_migration.py`) drives a stateful fake `launchctl` placed first on `PATH`.

## Tests

`make test` runs the whole suite. A single module runs either way: `PYTHONPATH="$PWD/lib" python3 -m unittest tests.test_reap` or `PYTHONPATH="$PWD/lib" python3 -m unittest discover -s tests -t . -p test_reap.py`. The path must be absolute: some tests run children from other directories.

The suite points home, state and config at a throwaway directory itself (`tests/__init__.py`), so a run never writes into the state a real job owns.

A fix starts with a test that fails without it. Never weaken an assertion to make a test pass.

Every pure decision function gets a table test (stdlib `unittest`, zero runtime dependencies) (enforced from the first ported module; the suite and `make test` land with it). OS glue - `launchctl`, `ps`, `git` - is smoke-tested only.

## Example outputs

Every example output block in README, docs/, or patterns/ is generated on a throwaway sandbox HOME (or hand-authored from scratch) - never captured from a real rig. A real-rig capture in a diff is a blocker, not a style nit.

## Runtime

Python 3.11 floor, stdlib only for the shipped package (`lib/rigops/`). Dev tooling (`ruff`, `shellcheck`) is exempt.
