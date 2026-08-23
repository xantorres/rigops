---
name: ops-loop
description: Run the measure-intervene-note-diff weekly loop for tuning a Claude Code rig's cost/effectiveness. Use when the user asks "is my rig getting better", "did that config change help", "weekly review", or wants to know whether some change actually moved the numbers.
---

# Ops loop

The rigops ledger only tells you something useful if it's read in a fixed
loop, not glanced at ad hoc. Four steps, one week apart, in this order:

## 1. Measure

`rigops ledger write` (or `/rigops:week`, which runs the whole loop) appends
this week's row: EIT/turn, context percentiles, cache-hit %, output/turn,
Agent-delegation rate, cheap-model EIT share, fixed context tax. This is the
baseline the next diff compares against - write it even on a week with no
planned changes, or the loop has gaps.

## 2. Intervene

Make **one** deliberate change: a CLAUDE.md trim, a new hook, a subagent
delegation habit, a model-routing rule. One, not three. Multiple simultaneous
interventions in the same week make the next diff unreadable - if EIT/turn
drops 15%, there's no way to say which of the three changes did it, and no
way to know if one of them actually made things worse while another
compensated.

## 3. Note

The moment an intervention lands (not at week's end from memory), run
`rigops ledger note "<what changed>"`. Interventions are timestamped and
dated; `ledger diff` pulls in everything noted between two ledger rows. A
note written after the fact, backdated by guess, is worse than no note.

## 4. Diff

Next week, after step 1's fresh `ledger write`, run `rigops ledger diff` (or
`rigops ledger diff --since <date>` for a wider window than the last two
rows). It prints the delta on every numeric column plus the interventions
noted in that window side by side. Read this **before** noting anything new - it's the only step that closes the loop back to step 2.

`rigops tax --history` is a secondary check worth running in the same pass:
it's the regrowth gauge for always-loaded config (CLAUDE.md and friends), and
it can drift upward silently between interventions that target it directly.

## Honest scope

This loop shows correlation, not causation. A week-over-week EIT/turn drop
that lines up with a noted intervention is evidence, not proof - session mix,
task type, and model choice all move the same numbers for reasons that have
nothing to do with the change under test. Two things make the signal
readable instead of noise:

- **One intervention per week.** This is the whole point of step 2's
  constraint - it's the only way an isolated delta means anything.
- **Look for direction across multiple weeks, not one week's delta.** A
  single good week after a change could be normal variance; the same
  direction holding for two or three weeks running is a much stronger
  signal than any single diff.

If the user wants to change three things at once, that's their call, but say
plainly that the following diff won't be able to attribute the result to any
one of them.
