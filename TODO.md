# TODO

Gaps found while migrating a live rig onto rigops as its canonical source. Each item
is a real seam hit during adoption, not speculation.

## Install / jobs

- [ ] launchd templates and `install.sh` jobs exist only for `doctor` and `ledger`;
  `janitor` and `reap` schedules must be hand-built. Ship `janitor.plist.in` +
  `reap.plist.in` and add both to `AVAILABLE_JOBS`.
- [ ] No per-host config layering: one `config.json`, or a full `RIGOPS_CONFIG`
  swap. A `config.d/` merge or host overlay would let a dotfiles repo carry shared
  config with machine deltas.

## Ledger

- [ ] `ledger.md` columns are fixed; custom levers (`sources.custom`) reach the
  jsonl only. A config key mapping a source field to an md column would let adopters
  keep bespoke lever columns visible in the weekly table.
- [ ] The ledger state path is welded to the state dir; the only way to keep an
  existing ledger in place is `RIGOPS_STATE_DIR` for everything. A `ledger.path`
  config key would decouple data location from scratch state.
- [ ] `sources/file_json.py` has tests but no config wiring or caller — either wire
  it into `sources.custom` as a `file:` variant or drop it.
- [ ] `rigops ledger --dry-run` still executes `sources.custom` subprocesses; worth
  documenting, or gating custom sources behind the real write.

## Janitor

- [ ] `older_than_days` is strict seconds; `find -mtime +N` truncates to whole days,
  so a rule ported from a find one-liner deletes up to a day earlier than the
  original. Document the `N+1` mapping in CONFIG.md (bitten during adoption: a
  `-mtime +1` rule matched 3.5x more entries until re-mapped).
- [ ] Report rules have no exclude patterns (live find one-liners often carry
  `-not -path`); an `exclude_globs` field would close that.

## Doctor

- [ ] No keepalive cadence branch: a live pid past `max_runtime_h` is judged hung
  even for `always-on`/`KeepAlive` jobs, so `--heal` would kill them on a default
  6h ceiling. Adopters must set huge per-item `max_runtime_h`; a cadence-aware
  skip would be safer.
- [ ] Self-exclusion covers the live pid only: a nonzero exit on the doctor's own item still kickstarts it.
- [x] Items with no label and no evidence are listed in one report footer line instead of padding the table.
- [ ] No chain-step evidence checks (a job that only proves itself via another
  job's output file); such items read `unknown`.
- [ ] Registry required fields beyond `id` are documentation-only, and
  `last_verified` is parsed by nothing.

## Plugin

- [ ] `plugin/commands/backlog.md` still hedges that `rigops backlog` may not
  exist; the subcommand shipped — drop the hedge.
- [ ] `ctx-probe.sh` lives inside the version-rotating plugin cache; a local
  statusline that wants to call it needs its own stable copy. Consider installing
  it under the prefix (as the statusline already is).

## Config

- [ ] `config/config.schema.json` is enforced by nothing; `config check` warns on
  unknown top-level keys only, so a nested shape mistake (e.g. `watermarks` as an
  object instead of an array) surfaces as a runtime crash on the first `--apply`.
  Validate nested shapes in `config check`.
