#!/usr/bin/env bash
# Table test for plugin/hooks/ctx-probe.sh.
set -u
HOOK="$(cd "$(dirname "$0")/../../plugin/hooks" && pwd)/ctx-probe.sh"
PASS=0; FAIL=0

check() { # check <name> <expected> <actual>
  local name="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    PASS=$((PASS + 1)); printf 'ok    %s\n' "$name"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected "%s", got "%s")\n' "$name" "$expected" "$actual"
  fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

SUMS="$WORK/sums.jsonl"
cat >"$SUMS" <<'EOF'
{"type": "assistant", "message": {"usage": {"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 500}}}
EOF
check 'usage sums input+cache_creation+cache_read' '1700' "$(bash "$HOOK" "$SUMS")"

TRAILING_ZERO="$WORK/trailing-zero.jsonl"
cat >"$TRAILING_ZERO" <<'EOF'
{"type": "assistant", "message": {"usage": {"input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
{"type": "assistant", "message": {"usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
EOF
check 'trailing zero-usage synthetic row skipped, returns previous positive' '1000' "$(bash "$HOOK" "$TRAILING_ZERO")"

NON_ASSISTANT="$WORK/non-assistant.jsonl"
cat >"$NON_ASSISTANT" <<'EOF'
{"type": "user", "message": {"usage": {"input_tokens": 5000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
{"type": "assistant", "message": {"usage": {"input_tokens": 300, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
EOF
check 'non-assistant rows ignored' '300' "$(bash "$HOOK" "$NON_ASSISTANT")"

check 'missing file -> empty output, exit 0' '' "$(bash "$HOOK" "$WORK/does-not-exist.jsonl")"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
