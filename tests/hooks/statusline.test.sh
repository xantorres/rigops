#!/usr/bin/env bash
# Table test for plugin/statusline/rigops-statusline.sh: the GNU/BSD stat
# mtime fallback in _mtime() (order matters -- GNU's own -f is filesystem
# mode, where %m is an unsupported directive that prints "?" and exits 0,
# so a -f-first fallback never reaches -c on Linux), the rigops-not-on-PATH
# fallback location, and the no-CLI degrade path.
set -u
STATUSLINE_DIR="$(cd "$(dirname "$0")/../../plugin/statusline" && pwd)"
STATUSLINE="$STATUSLINE_DIR/rigops-statusline.sh"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FIXTURE_TP="$REPO_ROOT/tests/fixtures/transcripts/proj-alpha/session-a.jsonl"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
ok() { PASS=$((PASS + 1)); printf 'ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL  %s (%s)\n' "$1" "$2"; }

# jq's own directory, so the scrubbed-PATH scenarios below can find it
# without inheriting the rest of the host PATH (and whatever real rigops
# might live on it) -- jq isn't reliably at any one fixed path across hosts.
JQ_DIR="$(dirname "$(command -v jq)")"

# --- stub rigops CLI: only the two calls the statusline script makes. The
# eit call touches $SENTINEL when set, so a test can tell whether the
# background refresh actually ran without depending on its timing.
BIN="$WORK/bin"
mkdir -p "$BIN"
cat >"$BIN/rigops" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  "config get context.nudge_tiers") echo '[250000,350000,500000]' ;;
  "eit --since=today --json")
    [ -n "${SENTINEL:-}" ] && touch "$SENTINEL"
    echo '{"eit_total": 154000, "turns": 12}'
    ;;
esac
exit 0
EOF
chmod +x "$BIN/rigops"

# --- GNU stat emulation shim: -f (GNU filesystem mode) prints "?" and exits
# 0 for the unsupported %m directive, exactly like real GNU stat; -c returns
# a genuine mtime via python3 (portable -- delegating to this machine's own
# `stat -f %m` would hit real GNU stat's own -f bug on actual Linux, making
# the shim's -c branch just as broken as the thing it's emulating a fix for).
GNU_SHIM="$WORK/gnu-shim"
mkdir -p "$GNU_SHIM"
cat >"$GNU_SHIM/stat" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  -f) echo "?"; exit 0 ;;
  -c) python3 -c 'import os, sys; print(int(os.path.getmtime(sys.argv[1])))' "$3" 2>/dev/null; exit $? ;;
esac
exit 1
EOF
chmod +x "$GNU_SHIM/stat"

run_statusline() { # run_statusline <path> <state-dir> <home>
  jq -n --arg tp "$FIXTURE_TP" '{transcript_path:$tp}' \
    | PATH="$1" HOME="$3" XDG_STATE_HOME="$2" bash "$STATUSLINE" 2>"$WORK/err"
}

# --- (a) GNU stat shim on PATH: -c must be tried first so -f's "?" is
# never hit; a pre-populated cache file must render without any stderr.
STATE_A="$WORK/state-a/rigops"
mkdir -p "$STATE_A"
echo "EIT 5.0k/20t" >"$STATE_A/statusline-eit-today"
out_a="$(run_statusline "$GNU_SHIM:$BIN:$PATH" "$WORK/state-a" "$WORK/home")"
rc_a=$?
err_a="$(cat "$WORK/err" 2>/dev/null || true)"

case "$out_a" in
  *"EIT "*) ok 'GNU stat shim: EIT segment present' ;;
  *) bad 'GNU stat shim: EIT segment present' "stdout=$out_a" ;;
esac
if [ -z "$err_a" ] && [ "$rc_a" -eq 0 ]; then
  ok 'GNU stat shim: stderr empty, rc 0'
else
  bad 'GNU stat shim: stderr empty, rc 0' "rc=$rc_a stderr=$err_a"
fi

# --- ordering assert: the EIT-segment/stderr checks above pass regardless
# of _mtime's stat order (the numeric guard coerces "?" to 0 either way, so
# a -f-first mutant wouldn't crash or blank the segment -- that made (a)
# vacuous for the actual ordering claim). This checks the ordering directly
# by its side effect instead: with a cache file fresher than 120s, a correct
# -c-first read gets a real, small age and must NOT trigger the background
# refresh; a -f-first mutant gets "?" -> 0 from the guard, reads as a huge
# age, and DOES trigger it. $SENTINEL, touched only by the stub's eit call,
# tells them apart.
STATE_ORDER="$WORK/state-order/rigops"
mkdir -p "$STATE_ORDER"
echo "EIT 5.0k/20t" >"$STATE_ORDER/statusline-eit-today"
SENTINEL="$WORK/sentinel-touched"
rm -f "$SENTINEL"
export SENTINEL
run_statusline "$GNU_SHIM:$BIN:$PATH" "$WORK/state-order" "$WORK/home" >/dev/null
sleep 1
if [ ! -f "$SENTINEL" ]; then
  ok 'GNU stat shim, fresh cache: background refresh not triggered (stat order correct)'
else
  bad 'GNU stat shim, fresh cache: background refresh not triggered (stat order correct)' 'sentinel appeared'
fi
unset SENTINEL

# --- (b) real BSD stat (no shim): -c genuinely errors on this platform, so
# the fallback to -f %m must still produce a clean render.
STATE_B="$WORK/state-b/rigops"
mkdir -p "$STATE_B"
echo "EIT 5.0k/20t" >"$STATE_B/statusline-eit-today"
out_b="$(run_statusline "$BIN:$PATH" "$WORK/state-b" "$WORK/home")"
rc_b=$?
err_b="$(cat "$WORK/err" 2>/dev/null || true)"

case "$out_b" in
  *"EIT "*) ok 'real BSD stat: EIT segment present' ;;
  *) bad 'real BSD stat: EIT segment present' "stdout=$out_b" ;;
esac
if [ -z "$err_b" ] && [ "$rc_b" -eq 0 ]; then
  ok 'real BSD stat: stderr empty, rc 0'
else
  bad 'real BSD stat: stderr empty, rc 0' "rc=$rc_b stderr=$err_b"
fi

# --- (c) no rigops CLI anywhere (PATH or $HOME/.local/bin): ctx-only.
CLEAN_HOME="$WORK/home-clean"
mkdir -p "$CLEAN_HOME" "$WORK/state-c"
out_c="$(run_statusline "$JQ_DIR:/usr/bin:/bin" "$WORK/state-c" "$CLEAN_HOME")"
rc_c=$?
err_c="$(cat "$WORK/err" 2>/dev/null || true)"

case "$out_c" in
  *"EIT "*) bad 'no rigops CLI: ctx-only (no EIT segment)' "stdout=$out_c" ;;
  *) ok 'no rigops CLI: ctx-only (no EIT segment)' ;;
esac
case "$out_c" in
  *"ctx "*) ok 'no rigops CLI: ctx segment present' ;;
  *) bad 'no rigops CLI: ctx segment present' "stdout=$out_c" ;;
esac
if [ -z "$err_c" ] && [ "$rc_c" -eq 0 ]; then
  ok 'no rigops CLI: stderr empty, rc 0'
else
  bad 'no rigops CLI: stderr empty, rc 0' "rc=$rc_c stderr=$err_c"
fi

# --- (d) rigops not on PATH, only at $HOME/.local/bin/rigops: exercises the
# fallback-location branch.
FALLBACK_HOME="$WORK/home-fallback"
STATE_D="$WORK/state-d/rigops"
mkdir -p "$FALLBACK_HOME/.local/bin" "$STATE_D"
cp "$BIN/rigops" "$FALLBACK_HOME/.local/bin/rigops"
chmod +x "$FALLBACK_HOME/.local/bin/rigops"
echo "EIT 5.0k/20t" >"$STATE_D/statusline-eit-today"

out_d="$(run_statusline "$JQ_DIR:/usr/bin:/bin" "$WORK/state-d" "$FALLBACK_HOME")"
rc_d=$?
err_d="$(cat "$WORK/err" 2>/dev/null || true)"

case "$out_d" in
  *"EIT "*) ok 'rigops at HOME/.local/bin (not on PATH): EIT segment present' ;;
  *) bad 'rigops at HOME/.local/bin (not on PATH): EIT segment present' "stdout=$out_d" ;;
esac
if [ -z "$err_d" ] && [ "$rc_d" -eq 0 ]; then
  ok 'rigops at HOME/.local/bin: stderr empty, rc 0'
else
  bad 'rigops at HOME/.local/bin: stderr empty, rc 0' "rc=$rc_d stderr=$err_d"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
