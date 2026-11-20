#!/usr/bin/env bash
# stdout: current context tokens (int) or empty. Always exit 0.
T="$1"
[ -f "$T" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
# select(. > 0) drops synthetic zero-usage entries (interrupt/API-error rows).
tail -n 400 "$T" 2>/dev/null | jq -r 'select(.type=="assistant") | (.message.usage // empty)
  | ((.input_tokens//0)+(.cache_creation_input_tokens//0)+(.cache_read_input_tokens//0))
  | select(. > 0)' 2>/dev/null | tail -1
exit 0
