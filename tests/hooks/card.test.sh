#!/usr/bin/env bash
# Table test for plugin/hooks/card.sh and its SessionStart registration: the
# payload reaches `rigops card --hook` from PATH or $HOME/.local/bin, and no
# reachable CLI means no output and exit 0.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOOK="$ROOT/plugin/hooks/card.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN" "$WORK/home" "$WORK/local-home/.local/bin"
cat >"$FAKE_BIN/rigops" <<'RIGOPS_EOF'
#!/usr/bin/env bash
[ "$1" = "card" ] && [ "$2" = "--hook" ] || exit 1
printf 'card for %s\n' "$(cat)"
RIGOPS_EOF
chmod +x "$FAKE_BIN/rigops"
cp "$FAKE_BIN/rigops" "$WORK/local-home/.local/bin/rigops"

PAYLOAD='{"session_id":"sess-card-test","cwd":"/tmp","source":"startup"}'
PASS=0; FAIL=0

check() { # check <name> <expected-output> <path> <home>
  local name="$1" want="$2" out rc
  out="$(printf '%s' "$PAYLOAD" | env -i PATH="$3" HOME="$4" bash "$HOOK" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ] && [ "$out" = "$want" ]; then
    PASS=$((PASS + 1)); printf 'ok    %s\n' "$name"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (rc %s, got: %s)\n' "$name" "$rc" "$out"
  fi
}

registered="$(jq -r '.hooks.SessionStart[]?.hooks[]?.command' "$ROOT/plugin/hooks/hooks.json" 2>/dev/null)"
case "$registered" in
  *'/hooks/card.sh"'*) PASS=$((PASS + 1)); printf 'ok    %s\n' 'hooks.json runs card.sh on SessionStart' ;;
  *) FAIL=$((FAIL + 1)); printf 'FAIL  %s (got: %s)\n' 'hooks.json runs card.sh on SessionStart' "$registered" ;;
esac

check 'rigops on PATH: payload in, card out' "card for $PAYLOAD" "$FAKE_BIN:/usr/bin:/bin" "$WORK/home"
check 'rigops only in ~/.local/bin: still found' "card for $PAYLOAD" "/usr/bin:/bin" "$WORK/local-home"
check 'no rigops CLI: silent, exit 0' "" "/usr/bin:/bin" "$WORK/home"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
