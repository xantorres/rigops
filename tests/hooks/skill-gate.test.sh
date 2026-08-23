#!/usr/bin/env bash
# Table test for plugin/hooks/skill-gate.sh, using skill-gate.example.json
# (acme scope) as the gate config. "fire" = the mandate line is injected,
# "quiet" = it is not.
set -u
HOOK_DIR="$(cd "$(dirname "$0")/../../plugin/hooks" && pwd)"
HOOK="$HOOK_DIR/skill-gate.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

GATES="$WORK/skill-gates.json"
cp "$HOOK_DIR/skill-gate.example.json" "$GATES"

ACME="$WORK/acme-web"
OTHER="$WORK/other-project"
mkdir -p "$ACME" "$OTHER"

PASS=0; FAIL=0

check() { # check <fire|quiet> <cwd> <prompt> <name>
  local expect="$1" cwd="$2" prompt="$3" name="$4" out got
  out="$(jq -n --arg p "$prompt" --arg c "$cwd" '{prompt:$p,cwd:$c}' | RIGOPS_SKILL_GATES="$GATES" bash "$HOOK")"
  case "$out" in
    *"MANDATORY (skill-gate:pr-review)"*) got=fire ;;
    *) got=quiet ;;
  esac
  if [ "$got" = "$expect" ]; then
    PASS=$((PASS + 1)); printf 'ok    %s\n' "$name"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected %s, got %s)\n' "$name" "$expect" "$got"
  fi
}

# --- real review requests -----------------------------------------------------
check fire "$ACME" \
  'review PR 2294' \
  'plain review request'
check fire "$ACME" \
  'check PR 123 please' \
  '"check" still counts as a review verb'
check fire "$ACME" \
  're-review PR 456 - did they fix my comments?' \
  're-review'
check fire "$ACME" \
  'PR 2294, thoughts?' \
  'noun first, verb immediately after'
check fire "$ACME" \
  'can you take a look at the pull request I just opened on acme-web?' \
  'conversational phrasing'
check fire "$OTHER" \
  'https://github.com/acme/acme-web/pull/123 thoughts?' \
  'pasted pull URL plus verb, cwd out of scope'
check fire "$OTHER" \
  'https://github.com/acme/acme-web/pull/123' \
  'bare pull URL is the request'
check fire "$ACME" \
  'revisa este PR antes de que lo mergeen' \
  'spanish review request'
check fire "$ACME" \
  'échale un vistazo al PR 2294' \
  'spanish, verb phrase form'

# --- false positives that used to fire ---------------------------------------
check quiet "$ACME" \
  "the rich-text editor added by PR #2294 drops pasted images. run it from the main checkout's .claude/tools/live-verify directory and fix the clipboard handler" \
  'reported false positive: checkout + provenance PR'
check quiet "$ACME" \
  "run it from the main checkout's .claude/tools/live-verify directory" \
  '"checkout" is not the verb "check"'
check quiet "$ACME" \
  'the rich-text editor added by PR #2294 loses focus on blur' \
  'provenance PR in a bug report'
check quiet "$ACME" \
  'this regression landed in PR 41, patch src/editor/paste.ts' \
  'provenance, other wording'
check quiet "$ACME" \
  'PR #2294 is still open. meanwhile update the release notes in docs/CHANGELOG.md and bump the version' \
  'PR mentioned, the ask is something else'
check quiet "$ACME" \
  'my prereview notes are in notes.md, apply them to src/app.tsx' \
  '"prereview" is not "review"'
check quiet "$ACME" \
  'add a sprint selector to the orders page' \
  '"sprint" is not "pr"'
check quiet "$OTHER" \
  'review PR 2294' \
  'out of scope'
check quiet "$ACME" \
  '' \
  'empty prompt'

# --- harness-only cases: config resolution, not the regex engine -------------
IN_JSON="$(jq -n --arg p 'review PR 2294' --arg c "$ACME" '{prompt:$p,cwd:$c}')"

out="$(
  unset RIGOPS_SKILL_GATES
  XDG_CONFIG_HOME="$WORK/empty-xdg" bash "$HOOK" <<<"$IN_JSON"
)"
if [ -z "$out" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'no config file: RIGOPS_SKILL_GATES unset, default path absent -> quiet'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected empty output, got %s)\n' 'no config file' "$out"
fi

MALFORMED="$WORK/malformed-gates.json"
printf '{not valid json' >"$MALFORMED"
out="$(RIGOPS_SKILL_GATES="$MALFORMED" bash "$HOOK" <<<"$IN_JSON")"
if [ -z "$out" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'malformed gates JSON -> quiet exit 0'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected empty output, got %s)\n' 'malformed gates JSON' "$out"
fi

# "@" in a gate's provenance_regex must not collide with the sed delimiter
# used to scrub provenance mentions -- a gate with such a pattern must still
# fire cleanly (no sed error on stderr) for an otherwise-firing prompt.
AT_GATES="$WORK/at-gates.json"
cat >"$AT_GATES" <<'EOF'
{
  "gates": [
    {
      "name": "at-test",
      "skill": "acme:code-review",
      "scope": {"cwd_substrings": ["acme"]},
      "verb_regex": "\\breview\\b",
      "noun_regex": "\\bpr\\b",
      "provenance_regex": "contact user@example\\.com about"
    }
  ]
}
EOF
AT_IN_JSON="$(jq -n --arg p 'please contact user@example.com about that, then review PR 42' --arg c "$ACME" '{prompt:$p,cwd:$c}')"
out="$(RIGOPS_SKILL_GATES="$AT_GATES" bash "$HOOK" <<<"$AT_IN_JSON" 2>"$WORK/at-err")"
at_err="$(cat "$WORK/at-err" 2>/dev/null || true)"

case "$out" in
  *"MANDATORY (skill-gate:at-test)"*)
    PASS=$((PASS + 1)); printf 'ok    %s\n' '@ in provenance_regex: still fires'
    ;;
  *)
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected fire, got %s)\n' '@ in provenance_regex: still fires' "$out"
    ;;
esac
if [ -z "$at_err" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' '@ in provenance_regex: stderr empty'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (stderr=%s)\n' '@ in provenance_regex: stderr empty' "$at_err"
fi

# A provenance_regex containing the sed delimiter's own control byte must
# not reach sed at all -- the gate is skipped silently instead of firing
# garbage or erroring. Generated via python3's chr(1) rather than written
# as a literal control byte in this source file.
CTRL_GATES="$WORK/ctrl-gates.json"
python3 - "$CTRL_GATES" <<'PYEOF2'
import json
import sys

path = sys.argv[1]
gates = {
    "gates": [
        {
            "name": "ctrl-test",
            "skill": "acme:code-review",
            "scope": {"cwd_substrings": ["acme"]},
            "verb_regex": "\\breview\\b",
            "noun_regex": "\\bpr\\b",
            "provenance_regex": "bad" + chr(1) + "pattern",
        }
    ]
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(gates, fh)
PYEOF2
CTRL_IN_JSON="$(jq -n --arg p 'review PR 42' --arg c "$ACME" '{prompt:$p,cwd:$c}')"
out="$(RIGOPS_SKILL_GATES="$CTRL_GATES" bash "$HOOK" <<<"$CTRL_IN_JSON" 2>"$WORK/ctrl-err")"
rc=$?
ctrl_err="$(cat "$WORK/ctrl-err" 2>/dev/null || true)"

if [ -z "$out" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'control byte in provenance_regex: quiet (gate skipped)'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected empty output, got %s)\n' 'control byte in provenance_regex: quiet (gate skipped)' "$out"
fi
if [ -z "$ctrl_err" ] && [ "$rc" -eq 0 ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'control byte in provenance_regex: stderr empty, rc 0'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (rc=%s stderr=%s)\n' 'control byte in provenance_regex: stderr empty, rc 0' "$rc" "$ctrl_err"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
