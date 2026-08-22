#!/usr/bin/env bash
# Runs every tests/hooks/*.test.sh, aggregates pass/fail counts, and exits
# nonzero if any of them failed.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
TOTAL_PASS=0
TOTAL_FAIL=0
ANY_FAILED=0

for t in "$DIR"/*.test.sh; do
  [ -e "$t" ] || continue
  echo "== $(basename "$t") =="
  out="$(bash "$t" 2>&1)"
  rc=$?
  printf '%s\n' "$out"
  summary="$(printf '%s\n' "$out" | grep -E '^[0-9]+ passed, [0-9]+ failed$' | tail -1)"
  if [ -n "$summary" ]; then
    p="$(printf '%s' "$summary" | sed -E 's/^([0-9]+) passed.*/\1/')"
    f="$(printf '%s' "$summary" | sed -E 's/.*, ([0-9]+) failed$/\1/')"
    TOTAL_PASS=$((TOTAL_PASS + p))
    TOTAL_FAIL=$((TOTAL_FAIL + f))
  fi
  [ "$rc" -eq 0 ] || ANY_FAILED=1
  echo
done

echo "== totals: $TOTAL_PASS passed, $TOTAL_FAIL failed =="
[ "$ANY_FAILED" -eq 0 ] && [ "$TOTAL_FAIL" -eq 0 ]
