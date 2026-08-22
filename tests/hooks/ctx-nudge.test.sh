#!/usr/bin/env bash
# Table test for plugin/hooks/ctx-nudge.sh: fallback tiers (250k/350k/500k,
# 50k rearm -- no rigops CLI in this harness's fake $HOME), fire-once-per-
# rearm-step, and re-arm-on-drop. Exercised as one continuous session across
# repeated invocations against the same controlled state dir.
set -u
HOOK="$(cd "$(dirname "$0")/../../plugin/hooks" && pwd)/ctx-nudge.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAKE_HOME="$WORK/home"
STATE_HOME="$WORK/state"
mkdir -p "$FAKE_HOME" "$STATE_HOME"

TRANSCRIPT="$WORK/session.jsonl"
SID="sess-nudge-test"
STATE_FILE="$STATE_HOME/rigops/ctx-nudge/$SID"

# jq's own directory: needed inside the scrubbed PATH below, but not
# reliably at any one fixed path across hosts (e.g. homebrew's
# /opt/homebrew/bin instead of /usr/bin).
JQ_DIR="$(dirname "$(command -v jq)")"

PASS=0; FAIL=0

mk_transcript() { # mk_transcript <total-context-tokens>
  printf '{"type": "assistant", "message": {"usage": {"input_tokens": %d, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}\n' "$1" >"$TRANSCRIPT"
}

run_hook() {
  # env -i: a real rigops on the host PATH, with the host's own
  # context.nudge_tiers, would otherwise override the fallback tiers this
  # harness's expectations are built on.
  jq -n --arg tp "$TRANSCRIPT" --arg sid "$SID" '{transcript_path:$tp, session_id:$sid}' \
    | env -i PATH="$JQ_DIR:/usr/bin:/bin" HOME="$FAKE_HOME" XDG_STATE_HOME="$STATE_HOME" bash "$HOOK"
}

check() { # check <fire|quiet> <ctx> <name> [required-substring-if-fire]
  local expect="$1" ctx="$2" name="$3" want="${4:-}" out got
  mk_transcript "$ctx"
  out="$(run_hook)"
  if [ -n "$out" ]; then got=fire; else got=quiet; fi
  if [ "$got" != "$expect" ]; then
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected %s, got %s)\n' "$name" "$expect" "$got"
    return
  fi
  if [ "$got" = fire ] && [ -n "$want" ]; then
    case "$out" in
      *"$want"*) : ;;
      *)
        FAIL=$((FAIL + 1)); printf 'FAIL  %s (fired but missing "%s": %s)\n' "$name" "$want" "$out"
        return
        ;;
    esac
  fi
  PASS=$((PASS + 1)); printf 'ok    %s\n' "$name"
}

check quiet 200000 'below 250k: quiet'
check fire  260000 'cross 250k: fires tier-1 once'    'Wrap the current task'
check quiet 260000 'same size again: rate-limited, quiet'
check fire  310000 '+50k climb (rearm step): refires' 'Wrap the current task'
check fire  360000 'cross 350k: fires tier-2'         'heavy'
check fire  510000 'cross 500k: fires tier-3'         'Take no new work'
check quiet 100000 'drop below 250k: quiet'

if [ ! -f "$STATE_FILE" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'drop below 250k re-arms: state file gone'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (state file still present: %s)\n' 'drop below 250k re-arms' "$STATE_FILE"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
