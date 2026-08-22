#!/usr/bin/env bash
# Composite statusline: today's EIT-so-far (rigops eit) + current context
# size. Every segment degrades to empty if its tool (jq, the rigops CLI) is
# missing; never blocks the UI, never exits nonzero.
set -u

HOOKS_DIR="$(cd "$(dirname "$0")/../hooks" 2>/dev/null && pwd)"

HAVE_JQ=0
command -v jq >/dev/null 2>&1 && HAVE_JQ=1

RIGOPS_BIN=""
if command -v rigops >/dev/null 2>&1; then
  RIGOPS_BIN="rigops"
elif [ -x "$HOME/.local/bin/rigops" ]; then
  RIGOPS_BIN="$HOME/.local/bin/rigops"
fi

INPUT=$(cat 2>/dev/null || echo "{}")

TP=""
if [ "$HAVE_JQ" -eq 1 ]; then
  TP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || true)
fi

# Context-size thresholds: same knob ctx-nudge.sh uses, so the color and the
# nudge fire at the same points. Cheap enough to call every render; no cache.
TIER1=250000
TIER2=350000
if [ -n "$RIGOPS_BIN" ] && [ "$HAVE_JQ" -eq 1 ]; then
  TIERS_JSON=$("$RIGOPS_BIN" config get context.nudge_tiers 2>/dev/null || true)
  T1=$(printf '%s' "$TIERS_JSON" | jq -r '.[0] // empty' 2>/dev/null || true)
  T2=$(printf '%s' "$TIERS_JSON" | jq -r '.[1] // empty' 2>/dev/null || true)
  if [[ "$T1" =~ ^[0-9]+$ ]] && [[ "$T2" =~ ^[0-9]+$ ]]; then
    TIER1="$T1"; TIER2="$T2"
  fi
fi

CTX_SEG=""
if [ -n "$TP" ] && [ -n "$HOOKS_DIR" ] && [ -f "$HOOKS_DIR/ctx-probe.sh" ]; then
  CTX=$(bash "$HOOKS_DIR/ctx-probe.sh" "$TP" 2>/dev/null || true)
  if [[ "$CTX" =~ ^[0-9]+$ ]]; then
    K=$((CTX / 1000))
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
    C_RESET=$'\033[0m'
    if [ "$CTX" -ge "$TIER2" ]; then
      CTX_SEG="${C_RED}ctx ${K}k${C_RESET}"
    elif [ "$CTX" -ge "$TIER1" ]; then
      CTX_SEG="${C_YELLOW}ctx ${K}k${C_RESET}"
    else
      CTX_SEG="ctx ${K}k"
    fi
  fi
fi

# GNU stat (Linux) first: GNU's own -f is filesystem mode, where %m is an
# unsupported directive that prints "?" and still exits 0 -- a -f-first
# fallback would never reach -c on Linux. BSD stat has no -c at all and
# exits nonzero on it, so GNU-first correctly falls through to BSD's -f %m.
_mtime() {
  local m
  m=$(stat -c %Y "$1" 2>/dev/null) || m=$(stat -f %m "$1" 2>/dev/null)
  case "$m" in
    ''|*[!0-9]*) m=0 ;;
  esac
  printf '%s' "$m"
}

EIT_SEG=""
if [ -n "$RIGOPS_BIN" ] && [ "$HAVE_JQ" -eq 1 ]; then
  STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}/rigops"
  EIT_CACHE="$STATE_HOME/statusline-eit-today"
  now=$(date +%s)
  cache_age=999999
  if [ -f "$EIT_CACHE" ]; then
    cache_age=$(( now - $(_mtime "$EIT_CACHE") ))
  fi
  # Refresh in the background (the scan takes seconds); the statusline
  # always returns instantly. Check-then-touch race: two renders in the same
  # instant can both see no lock and both refresh -- benign (double API
  # fetch, atomic tmp+mv write), not corruption.
  if [ "$cache_age" -gt 120 ] && [ ! -f "$EIT_CACHE.lock" ]; then
    mkdir -p "$STATE_HOME"
    (
      touch "$EIT_CACHE.lock"
      EIT_JSON=$("$RIGOPS_BIN" eit --since=today --json 2>/dev/null)
      EIT_TOTAL=$(printf '%s' "$EIT_JSON" | jq -r '.eit_total // empty' 2>/dev/null)
      TURNS=$(printf '%s' "$EIT_JSON" | jq -r '.turns // empty' 2>/dev/null)
      if awk -v e="$EIT_TOTAL" -v t="$TURNS" 'BEGIN {
        if (t == "" || t+0 == 0) { exit }
        if (e+0 >= 1000000) printf "EIT %.1fM/%st\n", (e+0)/1000000, t;
        else printf "EIT %.0fk/%st\n", (e+0)/1000, t;
      }' > "$EIT_CACHE.tmp" 2>/dev/null; then
        mv "$EIT_CACHE.tmp" "$EIT_CACHE" 2>/dev/null || rm -f "$EIT_CACHE.tmp"
      else
        rm -f "$EIT_CACHE.tmp"
      fi
      rm -f "$EIT_CACHE.lock"
    ) >/dev/null 2>&1 &
    disown 2>/dev/null || true
  fi
  # Stale lock (>60s) means a previous refresh died; clear so the next call retries.
  if [ -f "$EIT_CACHE.lock" ]; then
    lock_age=$(( now - $(_mtime "$EIT_CACHE.lock") ))
    [ "$lock_age" -gt 60 ] && rm -f "$EIT_CACHE.lock"
  fi
  EIT_SEG=$(cat "$EIT_CACHE" 2>/dev/null || echo "")
fi

TODAY_SEG=""
[ -n "$EIT_SEG" ] && TODAY_SEG="📊 $EIT_SEG"

OUT=""
for SEG in "$TODAY_SEG" "$CTX_SEG"; do
  [ -n "$SEG" ] || continue
  if [ -n "$OUT" ]; then
    OUT="$OUT | $SEG"
  else
    OUT="$SEG"
  fi
done
echo "$OUT"
exit 0
