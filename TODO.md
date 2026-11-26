# TODO

Gaps found while migrating a live rig onto rigops as its canonical source. Each one is a real seam hit during adoption. All of them were reviewed for 1.0 and none blocks it.

Each item below is additive: a new config key or a new field, with the current behaviour as the default. Under [the compatibility policy](docs/CONTRACT.md#compatibility-and-deprecation) that fits any 1.x minor, so none of them has to land before the 1.0 contract freezes.

## Install / jobs

- **Not planned.** No per-host config layering: one `config.json`, or a full `RIGOPS_CONFIG` swap. A `config.d/` merge or host overlay would let a dotfiles repo carry shared config with machine deltas. It changes how every key resolves, so it waits until a second adopter asks for it.

## Ledger

- **Deferred past 1.0.** `ledger.md` columns are fixed; custom levers (`sources.custom`) reach the jsonl only. A config key mapping a source field to an md column would let adopters keep bespoke lever columns visible in the weekly table.
- **Deferred past 1.0.** The ledger state path is welded to the state dir; the only way to keep an existing ledger in place is `RIGOPS_STATE_DIR` for everything. A `ledger.path` config key would decouple data location from scratch state.

## Janitor

- **Deferred past 1.0.** Report rules have no exclude patterns (live find one-liners often carry `-not -path`); an `exclude_globs` field would close that.

## Doctor

- **Deferred past 1.0.** No chain-step evidence checks (a job that only proves itself via another job's output file); such items read `unknown`.
