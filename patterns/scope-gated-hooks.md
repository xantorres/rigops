# Scope-Gated Hooks

**What this pattern buys you:** a hook that forces a workflow exactly when the
prompt is actually asking for it, in exactly the projects you want, and stays
silent everywhere and on everything else.

## Problem

A hook meant to force a specific workflow - "any PR review request must go
through the review checklist skill" - is tempting to implement as a keyword
match on the prompt. That breaks in three predictable ways. It fires on
prompts about the hook itself, or about pull requests in the abstract, not a
request to act on one. It fires on quoted or pasted text that merely mentions
the trigger word without asking for anything - a bug report that happens to
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
provenance-scrubbed - content that reads as reporting history ("added by PR
#123", "fixed in the merge request") gets blanked out, so a bug report that
mentions a PR in passing doesn't look like a request about that PR. No config
file present means the engine does nothing at all; it's opt-in per machine and
per project, never a default that fires unpredictably.

## How this rig implements it

A `UserPromptSubmit` hook reads a gates file - one gate per workflow, each
declaring `skill`, `scope` (`cwd_substrings` and/or a `prompt_regex`
fallback), `verb_regex`, `noun_regex`, an optional `pull_url_regex`, optional
`windows`, and the `message` to inject. No gates file means a silent exit 0,
so the engine is inert until an operator opts a gate in.

Provenance scrubbing runs before the verb/noun test, via a `sed` substitution
that uses a control byte as its delimiter rather than a printable character
like `@` - a gate-supplied `provenance_regex` containing a literal `@` (an
email address, say) must not collide with the delimiter. A built-in default
provenance pattern catches phrasing like "added by PR 123" or "fixed in the
merge request" across a handful of past-tense verbs; a gate can override it
with its own `provenance_regex`. If that override itself contains the same
control byte the delimiter uses, the script detects it (`jq -r
'(.provenance_regex // "") | explode | any(. < 32)'`) and skips the gate
entirely, rather than handing `sed` a broken script that could fail
unpredictably.

The verb/noun proximity windows are asymmetric by default -
`windows.verb_first` 40 characters, `windows.noun_first` 15 - for a reason
worth stating directly: verb-first allows real distance ("review PR 123",
"thoughts on the pull request"); noun-first only reads as a request when the
verb follows immediately ("PR 123, thoughts?"), because a far trailing verb
belongs to a different ask about a PR that was merely mentioned. A pasted
pull-request URL is treated as unambiguous on its own - the verb may then
appear anywhere in the prompt, or a bare URL with nothing else can be the
whole request. A marker file keyed by session and gate name keeps one gate
from injecting its mandate twice in a session, and from swallowing another
gate's.

The engine does not ship with this plugin. It lives in the operator's own
`UserPromptSubmit` dispatcher, next to the `rg -r`/`-rn`/`-rl` guard that
left for the same reason: which workflow a prompt must go through is rig
policy, and a gate with two implementations is a gate whose false triggers
get fixed in one of them.

## Adopting it without rigops

- Match on proximity between a verb and a noun, not on either alone - a bare keyword match can't distinguish "review the PR" from "the PR that was reviewed last week."
- Scrub provenance and history phrasing before matching, so past-tense mentions of an action don't read as a present-tense request for that action.
- Make windows asymmetric where the underlying language reads asymmetrically - don't assume "verb near noun" is a symmetric relationship in both directions.
- Scope every rule explicitly (directory, project, repo) rather than shipping one global rule that fires everywhere.
- No config present should mean silent no-op, not an error and not a default-on rule - the engine should be inert until someone opts a gate in.
- Back every regex change with a table test that encodes both "should fire" and "should stay quiet" cases pulled from real false positives you've hit - that's what actually proves a proximity heuristic still discriminates correctly after an edit, not a description of intended behavior.

[drift-ledger.md](./drift-ledger.md) covers a different config-driven engine
in this rig - the same instinct, data-driven behavior over hardcoded logic,
applied to measuring effectiveness instead of gating a workflow.
