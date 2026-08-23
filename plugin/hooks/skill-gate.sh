#!/usr/bin/env bash
# UserPromptSubmit hook: scope-gated skill-forcing engine. Each gate in the
# config declares a skill, a scope (cwd substrings and/or a prompt regex),
# and a verb/noun (or pull-request URL) proximity test. A prompt that is in
# scope and matches the test gets one mandate line injected telling the
# model to invoke that Skill before doing anything else. Never blocks,
# every path exits 0.
#
# Config: $RIGOPS_SKILL_GATES env var (path to a gates JSON file), else
# ${XDG_CONFIG_HOME:-$HOME/.config}/rigops/skill-gates.json. No config file
# at either location -> silent exit 0. skill-gate.example.json is never
# auto-active: copy it to the default path, or point RIGOPS_SKILL_GATES at
# a copy, to actually use it. See that file for the config shape.
set -u
# Drain stdin before any early exit -- the hook runner writes the prompt
# payload expecting a reader on the other end; exiting first can hand it a
# broken pipe.
IN="$(cat 2>/dev/null || true)"
[ -n "$IN" ] || exit 0

command -v jq >/dev/null 2>&1 || exit 0

CONFIG="${RIGOPS_SKILL_GATES:-${XDG_CONFIG_HOME:-$HOME/.config}/rigops/skill-gates.json}"
[ -f "$CONFIG" ] || exit 0

PROMPT="$(printf '%s' "$IN" | jq -r '.prompt // empty' 2>/dev/null)"
CWD="$(printf '%s' "$IN" | jq -r '.cwd // empty' 2>/dev/null)"
[ -n "$PROMPT" ] || exit 0

# System-injected turns (background-task notifications) carry agent output
# that can mention a gate's verbs/nouns freely; only genuine user prompts
# are requests.
case "$PROMPT" in
  '[SYSTEM NOTIFICATION'*|*'<task-notification>'*|'<system-reminder>'*) exit 0 ;;
esac

# One lowercase line. Every gate's verb/noun regex is expected lowercase and
# boundary-anchored, so no -i flag is needed and a verb can never pair with a
# noun across a newline.
FLAT="$(printf '%s' "$PROMPT" | tr '\n\t' '  ' | tr '[:upper:]' '[:lower:]' | tr -s ' ')"
CWD_LC="$(printf '%s' "$CWD" | tr '[:upper:]' '[:lower:]')"

# Built-in provenance default: a thing cited as having been added/fixed/etc
# "in"/"by"/"via" a PR or MR is history, not a request target. Blanked out
# before the verb/noun proximity test runs. A gate may override this.
DEFAULT_PROVENANCE='( |^)(added|introduced|landed|shipped|merged|reverted|broke|broken|caused|fixed|implemented|written|removed|renamed|refactored|regressed|came)( [a-z]+){0,2} (by|in|from|via) (the )?(prs?|mrs?|pull[- ]?requests?|merge[- ]?requests?) ?#? ?[0-9]*'

# sed delimiter for the provenance scrub: a control byte, not "@", so a
# gate-supplied provenance_regex containing a literal "@" (e.g. an email
# address) can never collide with it. A delimiter byte appearing in the
# data being scrubbed (FLAT) would never be a problem either way -- sed
# only uses the delimiter to parse the s-command's own pattern/replacement
# text, not the input stream it runs against. What still matters is the
# delimiter not appearing inside that pattern/replacement text itself; a
# config-supplied provenance_regex containing this exact control byte is
# guarded separately below (skip that gate rather than hand sed a broken
# script).
SED_DELIM=$'\001'

GATES_JSON="$(cat "$CONFIG" 2>/dev/null || true)"
[ -n "$GATES_JSON" ] || exit 0
N="$(printf '%s' "$GATES_JSON" | jq '.gates | length' 2>/dev/null || true)"
[[ "$N" =~ ^[0-9]+$ ]] || exit 0

i=0
while [ "$i" -lt "$N" ]; do
  gate="$(printf '%s' "$GATES_JSON" | jq -c ".gates[$i]" 2>/dev/null || true)"
  i=$((i + 1))
  if [ -z "$gate" ] || [ "$gate" = "null" ]; then continue; fi

  name="$(printf '%s' "$gate" | jq -r '.name // empty')"
  skill="$(printf '%s' "$gate" | jq -r '.skill // empty')"
  verb="$(printf '%s' "$gate" | jq -r '.verb_regex // empty')"
  noun="$(printf '%s' "$gate" | jq -r '.noun_regex // empty')"
  if [ -z "$name" ] || [ -z "$skill" ] || [ -z "$verb" ] || [ -z "$noun" ]; then continue; fi

  pull_url="$(printf '%s' "$gate" | jq -r '.pull_url_regex // empty')"
  provenance="$(printf '%s' "$gate" | jq -r '.provenance_regex // empty')"
  if [ -n "$provenance" ]; then
    # A provenance_regex containing this exact control byte would collide
    # with the sed delimiter below and hand sed a broken script (silent
    # gate death) -- skip the gate instead of guessing at a repair.
    has_ctrl="$(printf '%s' "$gate" | jq -r '(.provenance_regex // "") | explode | any(. < 32)' 2>/dev/null)"
    [ "$has_ctrl" = "true" ] && continue
  else
    provenance="$DEFAULT_PROVENANCE"
  fi
  message="$(printf '%s' "$gate" | jq -r '.message // empty')"
  win_verb_first="$(printf '%s' "$gate" | jq -r '.windows.verb_first // empty')"
  win_noun_first="$(printf '%s' "$gate" | jq -r '.windows.noun_first // empty')"
  [[ "$win_verb_first" =~ ^[0-9]+$ ]] || win_verb_first=40
  [[ "$win_noun_first" =~ ^[0-9]+$ ]] || win_noun_first=15

  # Scope: cwd substring match, or prompt_regex match; gate skipped when
  # neither matches.
  in_scope=0
  while IFS= read -r sub; do
    [ -n "$sub" ] || continue
    case "$CWD_LC" in
      *"$sub"*) in_scope=1 ;;
    esac
  done < <(printf '%s' "$gate" | jq -r '.scope.cwd_substrings[]?' 2>/dev/null)
  if [ "$in_scope" -eq 0 ]; then
    prompt_regex="$(printf '%s' "$gate" | jq -r '.scope.prompt_regex // empty')"
    if [ -n "$prompt_regex" ] && printf '%s' "$FLAT" | grep -Eq "$prompt_regex"; then
      in_scope=1
    fi
  fi
  [ "$in_scope" -eq 1 ] || continue

  SCRUBBED="$(printf '%s' "$FLAT" | sed -E "s${SED_DELIM}${provenance}${SED_DELIM} (earlier change) ${SED_DELIM}g")"

  FIRE=0

  # A pull URL names the target unambiguously, so the verb may sit anywhere
  # in the prompt; a URL pasted on its own is already the whole request.
  # Empty pull_url_regex disables this rule for the gate.
  if [ -n "$pull_url" ] && printf '%s' "$FLAT" | grep -Eq "$pull_url"; then
    bare_url_re="^ *<?https?://${pull_url}[^ ]*>? *\$"
    if printf '%s' "$FLAT" | grep -Eq "$verb" || printf '%s' "$FLAT" | grep -Eq "$bare_url_re"; then
      FIRE=1
    fi
  fi

  # Asymmetric windows: verb-first allows real distance ("review PR 123",
  # "thoughts on the pull request"); noun-first only reads as a request when
  # the verb follows immediately ("PR 123, thoughts?") -- a far trailing verb
  # belongs to a different ask about a PR that was merely mentioned.
  if [ "$FIRE" -eq 0 ] &&
     printf '%s' "$SCRUBBED" | grep -Eq "($verb).{0,$win_verb_first}$noun|$noun.{0,$win_noun_first}($verb)"; then
    FIRE=1
  fi

  [ "$FIRE" -eq 1 ] || continue

  if [ -n "$message" ]; then
    printf '%s\n' "$message"
  else
    printf "MANDATORY (skill-gate:%s): this prompt matches gate '%s'. Invoke the Skill tool with skill \"%s\" BEFORE any other action and follow it end to end. If this is a false trigger, ignore this line.\n" \
      "$name" "$name" "$skill"
  fi
done

exit 0
