#!/usr/bin/env bash
# Table test for plugin/hooks/rigops-hook.sh and its hooks.json registration:
# event and stdin reach `rigops hook`, and a missing or failing CLI stays silent with exit 0.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOOK="$ROOT/plugin/hooks/rigops-hook.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/ok" "$WORK/fail" "$WORK/home" "$WORK/local-home/.local/bin"
cat >"$WORK/ok/rigops" <<'RIGOPS_EOF'
#!/usr/bin/env bash
[ "$1" = "hook" ] || exit 1
printf 'hook %s: %s\n' "$2" "$(cat)"
RIGOPS_EOF
cat >"$WORK/fail/rigops" <<'RIGOPS_EOF'
#!/usr/bin/env bash
echo "error: unknown command 'hook'" >&2
exit 2
RIGOPS_EOF
chmod +x "$WORK/ok/rigops" "$WORK/fail/rigops"
cp "$WORK/ok/rigops" "$WORK/local-home/.local/bin/rigops"

PAYLOAD='{"session_id":"sess-hook-test"}'
PASS=0; FAIL=0

check() { # check <name> <expected-output> <path> <home> <event>
  local name="$1" want="$2" out rc
  out="$(printf '%s' "$PAYLOAD" | env -i PATH="$3" HOME="$4" bash "$HOOK" "$5" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ] && [ "$out" = "$want" ]; then
    PASS=$((PASS + 1)); printf 'ok    %s\n' "$name"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (rc %s, got: %s)\n' "$name" "$rc" "$out"
  fi
}

registered="$(jq -r '.hooks[][].hooks[].command' "$ROOT/plugin/hooks/hooks.json" 2>/dev/null)"
for event in session-start user-prompt-submit; do
  case "$registered" in
    *"/hooks/rigops-hook.sh\" $event"*) PASS=$((PASS + 1)); printf 'ok    %s\n' "hooks.json runs the shim for $event" ;;
    *) FAIL=$((FAIL + 1)); printf 'FAIL  %s (got: %s)\n' "hooks.json runs the shim for $event" "$registered" ;;
  esac
done

check 'rigops on PATH: event and stdin forwarded' "hook session-start: $PAYLOAD" "$WORK/ok:/usr/bin:/bin" "$WORK/home" session-start
check 'prompt event forwarded' "hook user-prompt-submit: $PAYLOAD" "$WORK/ok:/usr/bin:/bin" "$WORK/home" user-prompt-submit
check 'rigops only in ~/.local/bin: still found' "hook session-start: $PAYLOAD" "/usr/bin:/bin" "$WORK/local-home" session-start
check 'no rigops CLI: silent, exit 0' "" "/usr/bin:/bin" "$WORK/home" session-start
check 'older CLI exiting 2 with stderr: silent, exit 0' "" "$WORK/fail:/usr/bin:/bin" "$WORK/home" session-start

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
