# Drift Ledger

**What this pattern buys you:** turns "did last week's change actually help"
from a guess into a reviewable, joined answer instead of a rewritten spend
total.

## Problem

Tuning a long-running agent rig is usually guess-driven. You prune a rule, add
a hook, or move a class of edits onto a cheaper model, and then nothing
measures whether it worked. A week later the rig feels faster or slower, but
there's no record of what changed and when, so the improvement (or regression)
gets attributed to whichever change you happen to remember most vividly.

Spend dashboards make this worse, not better. A tool that reports cost answers
"how much did I spend this week" - a single number, no history of what you
touched, no link between the number and the change that produced it. If three
interventions land in the same week, the dashboard can't tell you which one
moved the needle, or whether any of them did.

The failure compounds over time: without a running record, every retuning
session starts from zero. You re-litigate decisions you already made, because
there's no ledger of "tried X, here's what happened to the numbers around it."

## Pattern

Keep one append-only row per period (a week works well) recording whatever
effectiveness levers matter for the rig - cost proxies, latency proxies,
structural levers like delegation rate. Alongside it, keep a separate,
timestamped intervention journal: free-text notes of what actually changed,
written at the moment you change it, not reconstructed later from memory.

The join is the point. A diff between two ledger rows produces one delta per
lever; a query against the intervention journal for the window between those
two rows produces the candidate causes. Put them side by side. Causation is
never proven - several things can move a lever in one week - but attribution
becomes a reviewable question instead of a guess, and the record survives past
the point where you'd otherwise have forgotten what you changed.

This is tool-agnostic: a spreadsheet with two tabs and a manual lookup does
the same job. The value is in the discipline - write a row every period, write
a note at every change - more than in the tooling.

## How rigops implements it

`rigops ledger` (dispatched from
[../libexec/rigops-ledger](../libexec/rigops-ledger)) has three subcommands.
`write` (`cmd_write`, the default when none is given) appends one row via
`levers.build_row` in [../lib/rigops/levers.py](../lib/rigops/levers.py) -
EIT/turn over a 7-day window, a smoothed 30-day EIT/turn, context p50, turn-1
percentiles, cache-hit %, delegation rate (`agent_per_100`), cheap-model EIT
share, and the fixed context tax byte count. `note TEXT` (`cmd_note`) appends
`{ts, date, text}` to `interventions.jsonl`, independent of the ledger row
shape. `diff` (`cmd_diff`) computes deltas between two rows via
`levers.compute_deltas` and pulls the interventions that fall inside the
window with `_interventions_between`, whose join is a strict half-open range:
`baseline_date < date <= latest_date`. An intervention noted on the baseline
date belongs to the period before it, not the one being measured; one noted on
the latest date is still in scope.

`ledger.jsonl` is the source of truth; `ledger.md` is fully regenerated from
it on every write (`levers.render_markdown`), so adding a lever column never
desyncs the table header from rows written before that column existed.

Sample `rigops ledger diff` output:

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

Three interventions land inside this window, and the ledger doesn't try to
isolate a single cause - it hands the reviewer three candidate explanations
for an eit_per_turn drop of -28.8% and lets them reason about it, rather than
forcing a single attribution neither the tool nor the reviewer can actually
back up.

rigops draws a deliberate boundary at the ledger's edge:
`levers.source_annotations` calls out to `rtk` and `ccusage` when they're
installed, gated behind each adapter's own `probe()` so an absent tool never
breaks a write. Their output is attached under `row["sources"]` as annotation
only - a `saved_pct` or `cost_usd` alongside the row - and never folded back
into the lever math itself. The ledger computes its own levers independently
from transcript history; external spend tools corroborate, they don't feed.

## Adopting it without rigops

- Pick a fixed period (weekly is a reasonable default) and limit yourself to the handful of levers you actually act on. More than six or seven columns and nobody reads the table.
- Write one row per period, unconditionally, even when nothing changed - a flat line is data too.
- Log every intervention the moment you make it, in one line, with a date. Retroactive reconstruction loses the ones that felt too small to remember.
- Make the diff step strict about the join window: an intervention on the boundary date belongs to exactly one side, never both.
- Never let the ledger claim causation. Its job is to narrow the list of suspects, not to name one.
- Keep any external spend/usage tool as an annotation source, not as the thing computing your levers - if the tool disappears or changes shape, your history shouldn't break with it.

[context-tax.md](./context-tax.md) is one of the levers this ledger tracks
over time; [job-registry.md](./job-registry.md) covers the automation side of
the same rig.
