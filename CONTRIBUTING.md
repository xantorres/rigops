# Contributing

## Setup

After cloning, run `make hooks` once to enable the commit-message and pre-push redaction gates.

## Commits

Format: `<type>: <description>`

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

No AI-authorship attribution: commits and PRs never carry authorship trailers crediting AI tools, and never carry "generated with" footers. The content stands on its own.

No tracker-style keys anywhere - code, comments, commit messages, PR text. The redaction gate enforces this history-wide, not just in the tree. Write the actual reason in plain language instead - trackers move, code shouldn't depend on them.

## Before pushing

`make check` must pass: redaction gate (tree + history), shellcheck, ruff, unit tests, hook tests, the install smoke test, manifest validation, plugin validation.

CI on a fork runs the public redaction patterns only; the private layer is required on pushes to the upstream repository.

`make smoke-launchd` stays out of `make check` and has to be run by hand. It loads and unloads real launchd jobs, so it is macOS-only and mutates the machine it runs on; `make smoke` covers the installer end to end without it, on a throwaway `HOME` with `--no-jobs`.

## Tests

`make test` runs the whole suite. A single module runs either way: `PYTHONPATH="$PWD/lib" python3 -m unittest tests.test_reap` or `PYTHONPATH="$PWD/lib" python3 -m unittest discover -s tests -t . -p test_reap.py`. The path must be absolute: some tests run children from other directories.

The suite points home, state and config at a throwaway directory itself (`tests/__init__.py`), so a run never writes into the state a real job owns.

Every pure decision function gets a table test (stdlib `unittest`, zero runtime dependencies) (enforced from the first ported module; the suite and `make test` land with it). OS glue - `launchctl`, `ps`, `git` - is smoke-tested only.

## Example outputs

Every example output block in README, docs/, or patterns/ is generated on a throwaway sandbox HOME (or hand-authored from scratch) - never captured from a real rig. A real-rig capture in a diff is a blocker, not a style nit.

## Runtime

Python 3.11 floor, stdlib only for the shipped package (`lib/rigops/`). Dev tooling (`ruff`, `shellcheck`) is exempt.
