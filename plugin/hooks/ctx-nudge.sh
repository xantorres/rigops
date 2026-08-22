#!/usr/bin/env bash
# Nudges toward a fresh chat once context grows large. Fires once per
# rearm-step token climb per session; a big drop (e.g. post-/compact)
# re-arms it. Tiers/rearm-step come from `rigops config get context.*` when
# the rigops CLI is on PATH or at $HOME/.local/bin/rigops; otherwise
# hardcoded fallbacks. Always silent, always exit 0.
INPUT=$(cat 2>/dev/null || true)
command -v jq >/dev/null 2>&1 || exit 0
TP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || true)
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)
[ -n "$TP" ] && [ -n "$SID" ] || exit 0
SID=${SID//[^A-Za-z0-9_-]/_}

DIR="${XDG_STATE_HOME:-$HOME/.local/state}/rigops/ctx-nudge"
mkdir -p "$DIR" 2>/dev/null || true
find "$DIR" -type f -mtime +7 -delete 2>/dev/null || true
STATE="$DIR/$SID"

RIGOPS_BIN=""
if command -v rigops >/dev/null 2>&1; then
  RIGOPS_BIN="rigops"
elif [ -x "$HOME/.local/bin/rigops" ]; then
  RIGOPS_BIN="$HOME/.local/bin/rigops"
fi

TIER1=250000
TIER2=350000
TIER3=500000
REARM=50000

if [ -n "$RIGOPS_BIN" ]; then
  TIERS_JSON=$("$RIGOPS_BIN" config get context.nudge_tiers 2>/dev/null || true)
  REARM_RAW=$("$RIGOPS_BIN" config get context.rearm_tokens 2>/dev/null || true)
  T1=$(printf '%s' "$TIERS_JSON" | jq -r '.[0] // empty' 2>/dev/null || true)
  T2=$(printf '%s' "$TIERS_JSON" | jq -r '.[1] // empty' 2>/dev/null || true)
  T3=$(printf '%s' "$TIERS_JSON" | jq -r '.[2] // empty' 2>/dev/null || true)
  if [[ "$T1" =~ ^[0-9]+$ ]] && [[ "$T2" =~ ^[0-9]+$ ]] && [[ "$T3" =~ ^[0-9]+$ ]]; then
    TIER1="$T1"; TIER2="$T2"; TIER3="$T3"
  fi
  [[ "$REARM_RAW" =~ ^[0-9]+$ ]] && REARM="$REARM_RAW"
fi

CTX=$(bash "$(dirname "$0")/ctx-probe.sh" "$TP" 2>/dev/null || true)
[[ "$CTX" =~ ^[0-9]+$ ]] || CTX=""
PREV=""
[ -f "$STATE" ] && PREV=$(cat "$STATE" 2>/dev/null || true)
[[ "$PREV" =~ ^[0-9]+$ ]] || PREV=""

# Re-arm only below the lowest tier: routine microcompaction can drop tens of
# thousands of tokens while staying above it and must not reset the rate
# limit; a real /compact lands far lower.
if [ -n "$CTX" ] && [ -n "$PREV" ] && [ "$CTX" -lt "$TIER1" ]; then
  rm -f "$STATE" 2>/dev/null || true
  PREV=""
fi

[ -n "$CTX" ] && [ "$CTX" -ge "$TIER1" ] 2>/dev/null || exit 0

if [ -z "$PREV" ] || [ "$CTX" -ge $((PREV + REARM)) ]; then
  echo "$CTX" > "$STATE" 2>/dev/null || true
  K=$((CTX / 1000))
  if [ "$CTX" -ge "$TIER3" ]; then
    echo "[ctx-nudge] Context ${K}k — past the point where this session is worth continuing. Take no new work. Finish or hand off the step in flight, write down what is left, and tell the user to start a fresh chat now."
  elif [ "$CTX" -ge "$TIER2" ]; then
    echo "[ctx-nudge] Context ${K}k — heavy. Finish and verify the current step ONLY; at the next natural stopping point, tell the user: fresh chat now. All reads/edits via subagents."
  else
    echo "[ctx-nudge] Context ${K}k (>$((TIER1 / 1000))k). Wrap the current task; delegate mechanical edits and broad reads to subagents; then suggest a fresh chat."
  fi
fi

exit 0
