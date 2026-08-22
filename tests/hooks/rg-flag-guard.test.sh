#!/usr/bin/env bash
# Table test for plugin/hooks/rg-flag-guard.sh.
set -u
HOOK="$(cd "$(dirname "$0")/../../plugin/hooks" && pwd)/rg-flag-guard.sh"
PASS=0; FAIL=0

check() { # check <blocked|pass> <command> <name>
  local expect="$1" cmd="$2" name="$3" rc got
  jq -n --arg c "$cmd" '{tool_input:{command:$c}}' | bash "$HOOK" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 2 ]; then got=blocked; else got=pass; fi
  if [ "$got" = "$expect" ]; then
    PASS=$((PASS + 1)); printf 'ok    %s\n' "$name"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (expected %s, got %s, rc=%s)\n' "$name" "$expect" "$got" "$rc"
  fi
}

check blocked 'rg -rn pat dir'         'rg -rn blocked'
check blocked 'rg -rl pat dir'         'rg -rl blocked'
check blocked 'rg -ril pat dir'        'rg -ril blocked'
check pass    'rg -n pat dir'          'rg -n passes'
check pass    'rg --replace=x pat'     'rg --replace=x passes (long form)'
check pass    'grep -r pat'            'grep -r passes (not rg)'
check blocked 'echo foo | rg -rn pat'  'blocked mid-pipeline'
check pass    'foo-rg -rn pat'         'foo-rg passes (word boundary)'
check pass    ''                       'empty command passes'

jq_rc_name='non-JSON stdin passes'
printf 'not json at all' | bash "$HOOK" >/dev/null 2>&1
rc=$?
if [ "$rc" -ne 2 ]; then
  PASS=$((PASS + 1)); printf 'ok    %s\n' "$jq_rc_name"
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s (rc=%s)\n' "$jq_rc_name" "$rc"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
