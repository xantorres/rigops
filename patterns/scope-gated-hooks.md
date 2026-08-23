# Scope-Gated Hooks

**What this pattern buys you:** a hook that forces a workflow exactly when the
prompt is actually asking for it, in exactly the projects you want, and stays
silent everywhere and on everything else.

## Problem

A hook meant to force a specific workflow — "any PR review request must go
through the review checklist skill" — is tempting to implement as a keyword
match on the prompt. That breaks in three predictable ways. It fires on
prompts about the hook itself, or about pull requests in the abstract, not a
request to act on one. It fires on quoted or pasted text that merely mentions
the trigger word without asking for anything — a bug report that happens to
say "the editor added by PR 123 drops pasted images" is not a request to
review that PR. And a hook wired globally can't distinguish one project's
workflow requirement from another's; every repo either gets the same forced
workflow or none does.

## Pattern

A generic gating engine, driven entirely by a config file rather than
hardcoded logic, where each gate declares: a scope (which project or directory
this rule applies to), a trigger vocabulary expressed as two proximity-tested
regexes (a verb and a noun that must appear near each other, not just anywhere
in the same prompt), and a forced action. Before matching, the input text gets
provenance-scrubbed — content that reads as reporting history ("added by PR
#123", "fixed in the merge request") gets blanked out, so a bug report that
mentions a PR in passing doesn't look like a request about that PR. No config
file present means the engine does nothing at all; it's opt-in per machine and
per project, never a default that fires unpredictably.

## How rigops implements it

[../plugin/hooks/skill-gate.sh](../plugin/hooks/skill-gate.sh) is a
`UserPromptSubmit` hook. Config resolution: the `RIGOPS_SKILL_GATES`
environment variable if set, else
`${XDG_CONFIG_HOME:-$HOME/.config}/rigops/skill-gates.json`, else the hook
exits 0 silently.
[../plugin/hooks/skill-gate.example.json](../plugin/hooks/skill-gate.example.json)
ships as documentation only and is never auto-active at either default
location, so installing the plugin alone changes nothing until an operator
opts a specific gate in.

Provenance scrubbing runs before the verb/noun test, via a `sed` substitution
that uses a control byte as its delimiter rather than a printable character
like `@` — a gate-supplied `provenance_regex` containing a literal `@` (an
email address, say) must not collide with the delimiter. A built-in default
provenance pattern catches phrasing like "added by PR 123" or "fixed in the
merge request" across a handful of past-tense verbs; a gate can override it
with its own `provenance_regex`. If that override itself contains the same
control byte the delimiter uses, the script detects it (`jq -r
'(.provenance_regex // "") | explode | any(. < 32)'`) and skips the gate
entirely, rather than handing `sed` a broken script that could fail
unpredictably.

The verb/noun proximity windows are asymmetric by default —
`windows.verb_first` defaults to 40 characters, `windows.noun_first` to 15 —
for a reason the script's own comment states directly: "verb-first allows real
distance (\"review PR 123\", \"thoughts on the pull request\"); noun-first
only reads as a request when the verb follows immediately (\"PR 123,
thoughts?\") -- a far trailing verb belongs to a different ask about a PR that
was merely mentioned." A verb appearing well before the noun is almost always
still talking about the same request; a noun that shows up first with the verb
only appearing much later is more likely two separate thoughts in the same
prompt. A pasted pull-request URL is treated as unambiguous on its own — the
verb may then appear anywhere in the prompt, or a bare URL with nothing else
can be the whole request.

[../plugin/hooks/skill-gate.example.json](../plugin/hooks/skill-gate.example.json)
is the shipped worked example: one gate scoped to a `cwd_substrings` match
plus a `prompt_regex` fallback, verb and noun regexes covering both English
and Spanish phrasings of "review this PR."

[../tests/hooks/](../tests/hooks/) is the pattern's proof artifact — a
table-test harness, not prose claims about behavior. `run.sh` runs every
`*.test.sh` in the directory and aggregates a passed/failed count; running it
against this checkout, `skill-gate.test.sh` alone passes 24 checks and fails
0, covering real review requests (plain, re-review, noun-first, conversational
phrasing, Spanish, pasted URLs), reported false positives that used to fire
(provenance-scrubbed PR mentions, "checkout" not matching the verb "check",
"prereview" not matching "review"), config-resolution edge cases (no config
file, malformed JSON), and the two delimiter-collision cases above — an `@` in
a provenance regex still fires cleanly, a literal control byte in one causes
the gate to be skipped rather than erroring. The full hook suite (skill-gate,
ctx-nudge, ctx-probe, rg-flag-guard, statusline) totals 56 passed, 0 failed in
this checkout.

## Adopting it without rigops

- Match on proximity between a verb and a noun, not on either alone — a bare keyword match can't distinguish "review the PR" from "the PR that was reviewed last week."
- Scrub provenance and history phrasing before matching, so past-tense mentions of an action don't read as a present-tense request for that action.
- Make windows asymmetric where the underlying language reads asymmetrically — don't assume "verb near noun" is a symmetric relationship in both directions.
- Scope every rule explicitly (directory, project, repo) rather than shipping one global rule that fires everywhere.
- No config present should mean silent no-op, not an error and not a default-on rule — the engine should be inert until someone opts a gate in.
- Back every regex change with a table test that encodes both "should fire" and "should stay quiet" cases pulled from real false positives you've hit — that's what actually proves a proximity heuristic still discriminates correctly after an edit, not a description of intended behavior.

[drift-ledger.md](./drift-ledger.md) covers a different config-driven engine
in this rig — the same instinct, data-driven behavior over hardcoded logic,
applied to measuring effectiveness instead of gating a workflow.
