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

# --- prompt nudge: a fake `rigops` on PATH ---------------------------------
# A separate harness, since this one needs `rigops` resolvable -- the
# opposite of the tier harness above, which scrubs it out on purpose. The
# transcript stays well under tier 1 so the size logic prints nothing here;
# whatever the hook prints is provably the prompt-nudge stage's output.
NUDGE_WORK="$(mktemp -d)"
FAKE_BIN="$NUDGE_WORK/bin"
mkdir -p "$FAKE_BIN" "$NUDGE_WORK/home" "$NUDGE_WORK/state"
cat >"$FAKE_BIN/rigops" <<'RIGOPS_EOF'
#!/usr/bin/env bash
case "$1" in
  config)
    [ "$2" = "get" ] && [ "$3" = "context" ] || exit 1
    printf '{"nudge_tiers":[250000,350000,500000],"rearm_tokens":50000%s}' "${FAKE_CONTEXT_EXTRA:-}"
    exit 0
    ;;
  nudge)
    cat >/dev/null
    [ -n "${FAKE_NUDGE_SAYS:-}" ] && printf '%s\n' "$FAKE_NUDGE_SAYS"
    exit 0
    ;;
esac
exit 1
RIGOPS_EOF
chmod +x "$FAKE_BIN/rigops"

NUDGE_TRANSCRIPT="$NUDGE_WORK/session.jsonl"
printf '{"type": "assistant", "message": {"usage": {"input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}\n' >"$NUDGE_TRANSCRIPT"

run_nudge_hook() { # reads FAKE_NUDGE_SAYS and FAKE_CONTEXT_EXTRA from the caller's environment
  jq -n --arg tp "$NUDGE_TRANSCRIPT" --arg sid "sess-nudge-integration" \
        --arg prompt "open a pr" --arg cwd "$NUDGE_WORK" \
        '{transcript_path:$tp, session_id:$sid, prompt:$prompt, cwd:$cwd}' \
    | env -i PATH="$FAKE_BIN:$JQ_DIR:/usr/bin:/bin" HOME="$NUDGE_WORK/home" \
             XDG_STATE_HOME="$NUDGE_WORK/state" FAKE_NUDGE_SAYS="${FAKE_NUDGE_SAYS:-}" \
             FAKE_CONTEXT_EXTRA="${FAKE_CONTEXT_EXTRA:-}" bash "$HOOK"
}

FAKE_NUDGE_SAYS="[nudge] careful with that PR" out="$(run_nudge_hook)"
if [ "$out" = "[nudge] careful with that PR" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'fake rigops nudge line is printed'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (got: %s)\n' 'fake rigops nudge line is printed' "$out"
fi

FAKE_NUDGE_SAYS="" out="$(run_nudge_hook)"
if [ -z "$out" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'fake rigops nudge prints nothing: hook prints nothing'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected empty, got: %s)\n' \
    'fake rigops nudge prints nothing: hook prints nothing' "$out"
fi

FAKE_NUDGE_SAYS="[nudge] careful with that PR" FAKE_CONTEXT_EXTRA=',"prompt_nudge":false' \
  out="$(run_nudge_hook)"
if [ -z "$out" ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' 'context.prompt_nudge false: nudge is skipped'
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected empty, got: %s)\n' \
    'context.prompt_nudge false: nudge is skipped' "$out"
fi

rm -rf "$NUDGE_WORK"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
