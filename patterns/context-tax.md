# Context Tax

**What this pattern buys you:** makes an invisible, silently-regrowing cost —
always-loaded config bytes — visible as a number and a trend line, instead of
a vague feeling that the rig got slower.

## Problem

Every always-loaded byte of config — a top-level instructions file, always-on
rule files, always-on skill frontmatter — gets paid on every single turn,
whether that turn needs it or not. It is a fixed tax charged regardless of
task. The tax regrows silently: someone adds a paragraph to a rule file during
an unrelated fix, a new always-on skill gets installed, a debugging note meant
to be temporary becomes permanent. None of these single edits feel expensive.
The sum does. Nobody notices until the rig feels slow or expensive again, and
by then the cause is buried in months of small, individually-reasonable edits.

## Pattern

Name the corpus explicitly (a list of exact paths plus glob patterns), measure
bytes per file, and keep the total as a time series next to the rig's other
effectiveness levers rather than as a one-off audit. Regrowth then shows up as
a rising line on a chart instead of staying invisible until the rig "feels"
heavier — the same failure mode [drift-ledger.md](./drift-ledger.md) addresses
for effectiveness levers generally, applied here to one specific always-on
cost.

The sharp edge: not everything a glob matches is actually always-loaded. A
file whose own frontmatter declares it's path-scoped (a `paths:` or `globs:`
key) only loads when the current file or directory matches — it's conditional,
not fixed, and counting it would overstate the tax. The pattern has to exclude
path-scoped matches, not just sum every glob hit.

## How rigops implements it

Config declares two lists: `fixed_tax.paths` (exact files, always counted) and
`fixed_tax.globs` (patterns, filtered). `levers.fixed_tax_entries` in
[../lib/rigops/levers.py](../lib/rigops/levers.py) is the filter: for each
glob match, it reads the file, looks for a leading `---` frontmatter block,
and regex-searches it for a `paths:` or `globs:` key
(`re.search(r"^\s*(paths|globs)\s*:", fm, re.M)`); a hit means the file is
path-scoped and gets excluded from the tax. The fixtures at
[../tests/fixtures/fixed_tax/globbed/scoped.md](../tests/fixtures/fixed_tax/globbed/scoped.md)
(frontmatter declares `paths:` — excluded) and
[../tests/fixtures/fixed_tax/globbed/plain.md](../tests/fixtures/fixed_tax/globbed/plain.md)
(no frontmatter — always counted) are the pattern's own test cases for this
rule.

`rigops tax` ([../libexec/rigops-tax](../libexec/rigops-tax)) has two modes.
The default prints the current per-file breakdown sorted by size with a total
(`cmd_current`). `--history` prints a sparkline plus first/last/delta across
every ledger row that recorded `fixed_tax_b` (`cmd_history`, backed by
`fmt.sparkline`). `fixed_tax_b` is itself one column on every ledger row
(`levers.build_row` calls `fixed_tax_bytes`), so the tax rides along with
every other lever rigops tracks and shows up in `rigops ledger diff` deltas
for free, with no separate tracking mechanism.

Sample `rigops tax` output (files renamed as generic examples, not any
specific rig's real paths):

```text
file                             size
-------------------------------  -------
~/.claude/CLAUDE.md              40.8 KB
~/.claude/rules/git-workflow.md  17.8 KB
~/.claude/rules/code-style.md    11.8 KB
~/.claude/rules/testing.md       9.2 KB
~/.claude/rules/security.md      7.4 KB
total: 87.0 KB
```

Sample `rigops tax --history`:

```text
▂▃▄▅▆▇█▁
first 2026-06-29: 97.0 KB
last  2026-08-17: 87.0 KB
delta: -10215 B (-10.3%)
```

The sparkline's final low bar corresponds to a pruning pass; a rising trend
before it is exactly the silent regrowth this pattern exists to catch.

## Adopting it without rigops

- List the files and globs that are actually always-loaded for your setup; check what your tool actually injects into every prompt rather than guessing.
- Exclude anything conditionally loaded (path-scoped rules, on-demand references) — counting them overstates the number and trains you to ignore it.
- Measure bytes, not "does it feel long" — a byte count is objective and diffable.
- Record it on a cadence, piggybacked on whatever other metrics you already track weekly, rather than auditing once and forgetting.
- Alert yourself by looking at the trend, not the absolute number — a stable 90 KB is fine; 60 KB quietly becoming 140 KB over two months is the actual problem.

[drift-ledger.md](./drift-ledger.md) is where this tax rides along as one
lever among several.
