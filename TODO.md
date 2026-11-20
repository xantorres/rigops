# TODO

Deferred gaps found while migrating a live rig onto rigops as its canonical source.
Each one is a real seam hit during adoption. None is scheduled, and nothing found so
far argues for or against building it.

## Install / jobs

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

## Janitor

- [ ] Report rules have no exclude patterns (live find one-liners often carry
  `-not -path`); an `exclude_globs` field would close that.

## Doctor

- [ ] No chain-step evidence checks (a job that only proves itself via another
  job's output file); such items read `unknown`.
