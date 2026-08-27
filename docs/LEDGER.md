# Ledger

Tracks whether a change to a Claude Code rig actually moved the numbers, one append-only row at a time.

## Concepts

- One row per `rigops ledger` run, one per calendar date; by convention run weekly.
- **Window** = the 7 full days before the row's date (local midnight to local midnight, converted to UTC internally).
- `ledger.jsonl` (in the [state directory](CONFIG.md#resolution)) is the source of truth. `ledger.md` is fully regenerated from `ledger.jsonl` on every write - never hand-edit it, column additions would desync a hand-edited table from the jsonl anyway.
- `interventions.jsonl` is a side file: free-text notes, each with a date and a UTC timestamp, written by `rigops ledger note`.

## EIT

EIT ("effective input tokens") is the cost-weighted token count everything else in the ledger is built from:

```
EIT = input + 1.25 * cache_creation + 0.1 * cache_read
```

(`lib/rigops/transcripts.py`, `eit()`). A cache-creation token costs more than a plain input token (writing the cache), a cache-read token costs much less (reading it back) - EIT weights each turn by what it actually cost rather than by raw token count.

## Columns

| Column | Meaning |
|---|---|
| `date` | Row's date (`YYYY-MM-DD`). |
| `label` | Optional free-text tag (`--label`). |
| `turns` | Assistant turns in the 7-day window. |
| `sessions` | Sessions whose *first* turn fell in the window. |
| `EIT/turn 7d` | Total window EIT divided by window turns. |
| `EIT/turn 30d` | Same ratio over a trailing 30-day window - a smoother trend line than the noisier 7-day figure. |
| `out/turn` | Output tokens divided by turns. |
| `ctx p50` | Median context size (`input + cache_creation + cache_read`) across window turns. |
| `>300k EIT %` | Share of window EIT that came from turns whose context exceeded 300k tokens. |
| `cache %` | `cache_read` divided by total context, window-wide - prompt-cache reuse rate. |
| `turn-1 p10` | 10th-percentile context size of each session's *first* turn, this window. The floor a bare harness plus a small first prompt costs. |
| `turn-1 p50` | Median of the same - a typical session start. |
| `Agent/100` | `Agent` tool calls per 100 main-thread turns (subagent transcripts excluded from both sides of the ratio). Delegation rate. |
| `cheap-model EIT %` | Share of window EIT attributed to `sonnet`/`haiku`-family models. |
| `fixed tax` | `fixed_tax.paths`/`fixed_tax.globs` byte total at write time - see [CONFIG.md](CONFIG.md#fixed_tax). |
| `denials` | Permission-denial tool-result rows across the window's user turns. |
| `denials hl` | Same, restricted to transcript paths matching a `friction.headless_projects` substring - see [CONFIG.md](CONFIG.md#friction). |
| `corrections` | User text turns whose first line reads as a correction (at most one counted per turn). |
| `tier3 sess` | Main-thread sessions with at least one turn at or above the third `context.nudge_tiers` threshold. |
| `tool err/100` | `is_error` tool-result rows per 100 main-thread turns (subagent transcripts excluded from the denominator). |
| `<name> turn-1 p10` | One column per `ledger.watch_projects` entry: turn-1 p10 restricted to sessions whose transcript path (relative to `transcripts_dir`) contains that entry's configured substring. |

Any column can be `-` (no data yet - e.g. `sessions` and everything downstream of `watch_projects` on early rows, before per-session/per-project tracking had data to compute from).

## Commands

### `rigops ledger` (same as `rigops ledger write`)

Appends today's row (or `--at`'s date). Flags:

- `--at YYYY-MM-DD` - row date, default today.
- `--label TEXT` - free-text tag stored on the row.
- `--dry-run` - compute and print, write nothing.
- `--force` - replace an existing row for that date.
- `--show` - print the current ledger and exit; no write, no computation.
- `--json` - machine-readable row instead of the summary line.

A row already existing for that date is a no-op ("row exists, nothing appended") unless `--force`.

### `rigops ledger note "<text>"`

- `--at YYYY-MM-DD` - note date, default today.

Appends `{ts, date, text}` to `interventions.jsonl` (`ts` is a UTC, second-precision timestamp independent of `--at`'s date).

### `rigops ledger diff`

- `--since YYYY-MM-DD` - pick an explicit baseline.
- `--json`.

Needs at least 2 ledger rows total; with fewer it prints "not enough ledger rows to diff yet (need at least 2)" and exits `0`. Baseline selection: without `--since`, the baseline is the second-to-last row (the previous run). With `--since`, the baseline is the latest row on or before that date - an error if no row qualifies, and an error if the selected baseline turns out to be the same row as the latest one (a baseline can't be its own comparison point). The diff prints every numeric column's old/new/delta/percent, plus every intervention noted strictly after the baseline date and on or before the latest date.

### `rigops eit`

Ad hoc usage report, independent of the ledger (no row is written).

- `--since` - ISO date/time, `today`, or `-Nd` (default `-7d`).
- `--until` - ISO date/time.
- `--json`.
- `--by {project,model,session}` - grouping for the top-N breakdown (default `project`).
- `--top N` - how many groups to show (default `10`).
- `--dir PATH` - override `transcripts_dir` for this run.

### `rigops tax` / `rigops tax --history`

- `rigops tax` - current `fixed_tax` file-by-file breakdown and total (or a "not configured" / "no matching files" message if empty).
- `--history` - sparkline plus first/last/delta across every ledger row's `fixed_tax_b`, the regrowth gauge for always-loaded config.
- `--json` on either.

## `sources` annotations

A row's `sources` object (`rtk`, `ccusage`, and any `sources.custom` entries) is written to `ledger.jsonl` only - it never becomes a column in `ledger.md`. See [CONFIG.md](CONFIG.md#sources) for how each adapter is gated and what it contains. `ccusage`'s contribution is a context annotation; spend accounting stays `ccusage`'s own job.

## Examples

`rigops ledger --show` (`ledger.md`, middle rows elided):

```text
# Token ledger

One row per ledger run (`rigops ledger`). Window = the 7 full days before the row date.
Columns map to levers: turn-1 and ctx p50 track context diet and discipline (p10 is the
floor a bare harness plus a small first prompt costs, p50 is a typical session start);
Agent/100 tracks delegation rate; cheap-model EIT % tracks cheap-model routing; out/turn
tracks output verbosity; cache % tracks prompt-cache reuse; fixed tax tracks the
always-loaded config bytes named in `fixed_tax.paths`/`fixed_tax.globs`, the regrowth
gauge for the per-session fixed context cost.
Machine form of the same rows: `ledger.jsonl`. Re-run with `--show` to print this table.

| date | label | turns | sessions | EIT/turn 7d | EIT/turn 30d | out/turn | ctx p50 | >300k EIT % | cache % | turn-1 p10 | turn-1 p50 | Agent/100 | cheap-model EIT % | fixed tax |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06-29 |  | 384 | - | 36.4k | - | - | - | - | - | - | - | - | - | 97.0 KB |
...
| 2026-08-10 |  | 412 | 38 | 41.0k | 38.2k | 1450 | 212.0k | 34.2 | 71.4 | 78.0k | 92.0k | 6.3 | 11.2 | 136.0 KB |
| 2026-08-17 |  | 397 | 41 | 29.2k | 35.6k | 1290 | 168.0k | 18.7 | 76.9 | 41.0k | 44.5k | 11.8 | 23.6 | 87.0 KB |
```

`rigops ledger diff`:

```text
ledger diff: 2026-08-10 -> 2026-08-17
column               old       new       delta     pct
-------------------  --------  --------  --------  -------
agent_per_100        6.3       11.8      5.5       +87.6%
cache_hit_pct        71.4      76.9      5.5       +7.7%
cheap_model_eit_pct  11.2      23.6      12.4      +110.7%
ctx_mean             189000    151000    -38000    -20.1%
ctx_p50              212.0k    168.0k    -44.0k    -20.8%
eit_per_turn         41.0k     29.2k     -11.8k    -28.8%
eit_per_turn_30d     38.2k     35.6k     -2.6k     -6.8%
eit_total            16892000  11592400  -5299600  -31.4%
fixed_tax_b          136.0 KB  87.0 KB   -49.0 KB  -36.0%
out_per_turn         1450      1290      -160      -11.0%
over300k_pct         34.2      18.7      -15.5     -45.3%
sessions             38        41        3         +7.9%
turn1_p10            78.0k     41.0k     -37.0k    -47.4%
turn1_p50            92.0k     44.5k     -47.5k    -51.6%
turns                412       397       -15       -3.6%

Interventions in this window:
2026-08-12  pruned always-loaded rules: moved the code-search runbook to an on-demand reference
2026-08-13  turned on ctx-nudge tiers; long sessions now get restarted at the 350k nudge
2026-08-14  mechanical multi-file edits now go to a cheap-model subagent by default
```

`rigops tax --history`:

```text
▂▃▄▅▆▇█▁
first 2026-06-29: 97.0 KB
last  2026-08-17: 87.0 KB
delta: -10215 B (-10.3%)
```

`rigops ledger note "..."`:

```text
noted 2026-08-14: mechanical multi-file edits now go to a cheap-model subagent by default
```

`rigops eit` - run against this repo's own tiny test fixtures (`tests/fixtures/transcripts`), not a real rig:

```text
Window: 2016-08-25T10:53:13.488827+00:00 -> now
Turns: 6
EIT total: 314,625
EIT/turn: 52,438
Output tokens total: 426
Cache hit %: 0.2
Context mean/p50/p90/max: 52,500 / 1,150 / 156,000 / 310,000
EIT share from turns >300k ctx: 98.5%

Top tool calls:
  Read: 1
  Write: 1
  Agent: 1
  Bash: 1

Top 2 by project (EIT):
  proj-alpha: EIT=314,000 turns=5
  proj-beta: EIT=625 turns=1
```

## The ops loop

Reading a diff every week, one deliberate change at a time, is the point - a single row or a single diff proves nothing on its own. The plugin's `ops-loop` skill and the `/rigops:week` command (`plugin/commands/week.md`) walk the full measure → intervene → note → diff cycle; see [INSTALL.md](INSTALL.md) for what the plugin installs.

## See also

- [CONFIG.md](CONFIG.md) - `ledger.watch_projects`, `fixed_tax`, `friction`, `sources`.
- [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) - the Python floor these scripts run under.
- [../patterns/drift-ledger.md](../patterns/drift-ledger.md)
- [../patterns/context-tax.md](../patterns/context-tax.md)
