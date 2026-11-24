#!/usr/bin/env bash
# Fresh-HOME smoke test of `python3 -m rigops` from the repo: help, config, doctor,
# mine, and every command that runs against repo fixtures (eit, tax, ledger,
# authprobe, eval, backlog, janitor, reap, reap --selftest). Runs on macOS and
# Linux in a temp HOME; never touches the real home or launchd.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$REPO_ROOT/lib"
RIGOPS=(python3 -m rigops)

fail() {
    echo "smoke FAIL: $*" >&2
    exit 1
}

# Runs "$@", capturing combined output in OUT and exit status in RC. Never
# trips `set -e` itself so a deliberately-inspected failure can be turned
# into a readable fail() message instead of a bare abort.
run() {
    set +e
    OUT="$("$@" 2>&1)"
    RC=$?
    set -e
}

assert_json() {
    printf '%s' "$1" | python3 -m json.tool >/dev/null 2>&1 || fail "$2: not valid JSON: $1"
}

SMOKE_HOME="$(mktemp -d)"
cleanup() {
    rm -rf "$SMOKE_HOME"
}
trap cleanup EXIT

unset RIGOPS_CONFIG RIGOPS_STATE_DIR XDG_CONFIG_HOME XDG_STATE_HOME
export HOME="$SMOKE_HOME"

run "${RIGOPS[@]}" help
[ "$RC" -eq 0 ] || fail "rigops help exit $RC: $OUT"
for cmd in config doctor eit eval ledger tax; do
    case "$OUT" in
        *"$cmd"*) : ;;
        *) fail "rigops help missing command: $cmd" ;;
    esac
done

echo "smoke: help and mine without a registry"
mkdir -p "$SMOKE_HOME/mine-transcripts"
run "${RIGOPS[@]}" mine --dry-run --transcripts-dir "$SMOKE_HOME/mine-transcripts"
[ "$RC" -eq 0 ] || fail "mine --dry-run exit $RC: $OUT"
case "$OUT" in
    *"# rigops mine --dry-run"*) : ;;
    *) fail "mine --dry-run missing report header: $OUT" ;;
esac

CONFIG_JSON="$SMOKE_HOME/.config/rigops/config.json"
mkdir -p "$SMOKE_HOME/.config/rigops"
printf '{}' > "$CONFIG_JSON"

run "${RIGOPS[@]}" config check
[ "$RC" -eq 0 ] || fail "fresh config check exit $RC: $OUT"
case "$OUT" in
    *warn*) fail "fresh config check warns: $OUT" ;;
esac
run "${RIGOPS[@]}" doctor --report --supervisor=none
[ "$RC" -eq 0 ] || fail "fresh doctor exit $RC: $OUT"
case "$OUT" in
    *"no registry items"*) : ;;
    *) fail "fresh doctor does not report an empty registry: $OUT" ;;
esac

echo "smoke: exercise CLIs against repo fixtures"
# Overwrites the empty config.json: it is this run's own HOME.
python3 - "$CONFIG_JSON" "$REPO_ROOT" <<'PYEOF'
import json
import sys

cfg_path, repo_root = sys.argv[1], sys.argv[2]
data = {
    "transcripts_dir": repo_root + "/tests/fixtures/transcripts",
    "fixed_tax": {
        "paths": [repo_root + "/tests/fixtures/fixed_tax/explicit.md"],
        "globs": [repo_root + "/tests/fixtures/fixed_tax/globbed/*.md"],
    },
}
with open(cfg_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh)
PYEOF

run "${RIGOPS[@]}" eit --since=-3650d --json
[ "$RC" -eq 0 ] || fail "rigops eit exit $RC: $OUT"
assert_json "$OUT" "rigops eit --json"

run "${RIGOPS[@]}" tax --json
[ "$RC" -eq 0 ] || fail "rigops tax exit $RC: $OUT"
assert_json "$OUT" "rigops tax --json"

# Fresh ledger: 0-1 rows still emit JSON (empty deltas) under --json.
run "${RIGOPS[@]}" ledger diff --json
[ "$RC" -eq 0 ] || fail "rigops ledger diff exit $RC: $OUT"
assert_json "$OUT" "rigops ledger diff --json"

REGISTRY_EXAMPLE="$REPO_ROOT/config/registry.example.md"
run "${RIGOPS[@]}" doctor --report --supervisor=none --registry="$REGISTRY_EXAMPLE"
[ "$RC" -eq 0 ] || fail "rigops doctor exit $RC: $OUT"

run "${RIGOPS[@]}" config get doctor.kill_grace_s
[ "$RC" -eq 0 ] || fail "rigops config get exit $RC: $OUT"
[ "$OUT" = "5" ] || fail "expected doctor.kill_grace_s=5, got: $OUT"

echo "smoke: authprobe ships disabled"
run "${RIGOPS[@]}" authprobe
[ "$RC" -eq 0 ] || fail "rigops authprobe exit $RC: $OUT"
case "$OUT" in
    *"disabled"*) : ;;
    *) fail "rigops authprobe missing 'disabled': $OUT" ;;
esac

run "${RIGOPS[@]}" authprobe --json
[ "$RC" -eq 0 ] || fail "rigops authprobe --json exit $RC: $OUT"
assert_json "$OUT" "rigops authprobe --json"
case "$OUT" in
    *'"enabled"'*) : ;;
    *) fail "rigops authprobe --json missing 'enabled': $OUT" ;;
esac

echo "smoke: ledger dry-run row carries sources"
# stdout only: the empty-window warning goes to stderr and would break the JSON.
set +e
OUT="$("${RIGOPS[@]}" ledger --dry-run --json 2>/dev/null)"
RC=$?
set -e
[ "$RC" -eq 0 ] || fail "rigops ledger --dry-run --json exit $RC: $OUT"
assert_json "$OUT" "rigops ledger --dry-run --json"
case "$OUT" in
    *'"sources"'*) : ;;
    *) fail "rigops ledger --dry-run --json missing sources key: $OUT" ;;
esac

echo "smoke: eval records every case as an error against an unreachable endpoint"
# stdout only: the per-case progress lines go to stderr and would break the JSON.
set +e
OUT="$("${RIGOPS[@]}" eval run --cases "$REPO_ROOT/examples/eval/cases" \
    --endpoint http://127.0.0.1:9/v1 --model none --dry-run --json 2>/dev/null)"
RC=$?
set -e
[ "$RC" -eq 0 ] || fail "rigops eval run --dry-run exit $RC: $OUT"
assert_json "$OUT" "rigops eval run --json"
case "$OUT" in
    *'"status": "error"'*) : ;;
    *) fail "rigops eval run against a dead endpoint recorded no error: $OUT" ;;
esac
run "${RIGOPS[@]}" eval diff
[ "$RC" -eq 2 ] || fail "rigops eval diff with no runs expected exit 2, got $RC: $OUT"

echo "smoke: backlog lint/add on a scratch file"
BACKLOG_MD="$SMOKE_HOME/backlog-scratch.md"
run "${RIGOPS[@]}" backlog add --file "$BACKLOG_MD" "hooks: smoke entry lands. Fix: nothing."
[ "$RC" -eq 0 ] || fail "backlog add exit $RC: $OUT"
[ -f "$BACKLOG_MD" ] || fail "backlog add did not scaffold $BACKLOG_MD"
run "${RIGOPS[@]}" backlog lint --file "$BACKLOG_MD" --json
[ "$RC" -eq 0 ] || fail "backlog lint exit $RC: $OUT"
assert_json "$OUT" "rigops backlog lint --json"
run "${RIGOPS[@]}" backlog lint --file "$REPO_ROOT/config/backlog.example.md"
[ "$RC" -eq 0 ] || fail "backlog.example.md does not lint clean: $OUT"
run "${RIGOPS[@]}" backlog add --file "$BACKLOG_MD" "notanarea: rejected entry"
[ "$RC" -eq 2 ] || fail "backlog add unknown area expected exit 2, got $RC: $OUT"

echo "smoke: janitor dry-run leaves files, apply deletes"
JANITOR_TREE="$SMOKE_HOME/janitor-fixture"
mkdir -p "$JANITOR_TREE/spool"
printf 'old\n' > "$JANITOR_TREE/spool/old.json"
python3 - "$JANITOR_TREE/spool/old.json" <<'PYEOF'
import os
import sys
import time

old = time.time() - 30 * 86400
os.utime(sys.argv[1], (old, old))
PYEOF
JANITOR_CFG="$SMOKE_HOME/janitor-config.json"
python3 - "$JANITOR_CFG" "$JANITOR_TREE" "$BACKLOG_MD" <<'PYEOF'
import json
import sys

cfg_path, tree, backlog_md = sys.argv[1], sys.argv[2], sys.argv[3]
data = {
    "janitor": {
        "rules": [
            {"name": "smoke-spool", "path": tree + "/spool", "glob": "*.json",
             "older_than_days": 7, "action": "delete"}
        ],
        "watermarks": [],
        "max_delete": 10,
    },
    "backlog": {"path": backlog_md},
}
with open(cfg_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh)
PYEOF
run env RIGOPS_CONFIG="$JANITOR_CFG" "${RIGOPS[@]}" janitor
[ "$RC" -eq 0 ] || fail "janitor dry-run exit $RC: $OUT"
[ -f "$JANITOR_TREE/spool/old.json" ] || fail "janitor dry-run deleted a file"
run env RIGOPS_CONFIG="$JANITOR_CFG" "${RIGOPS[@]}" janitor --json
[ "$RC" -eq 0 ] || fail "janitor --json exit $RC: $OUT"
assert_json "$OUT" "rigops janitor --json"
run env RIGOPS_CONFIG="$JANITOR_CFG" "${RIGOPS[@]}" janitor --apply
[ "$RC" -eq 0 ] || fail "janitor --apply exit $RC: $OUT"
[ ! -f "$JANITOR_TREE/spool/old.json" ] || fail "janitor --apply did not delete aged file"

echo "smoke: reap read-only report on a fixture repo"
REAP_ROOT="$SMOKE_HOME/reap-root"
REAP_REPO="$REAP_ROOT/group/demo"
mkdir -p "$REAP_REPO"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
git -C "$REAP_REPO" -c init.defaultBranch=main init -q
git -C "$REAP_REPO" config user.email smoke@example.com
git -C "$REAP_REPO" config user.name smoke
printf 'demo\n' > "$REAP_REPO/README.md"
git -C "$REAP_REPO" add README.md
git -C "$REAP_REPO" commit -qm 'initial'
git -C "$REAP_REPO" worktree add -q -b merged-branch "$REAP_REPO/.claude/worktrees/merged-wt"
run env RIGOPS_RUN_DEADLINE_S=60 "${RIGOPS[@]}" reap --root "$REAP_ROOT" --no-fetch --json
[ "$RC" -eq 0 ] || fail "reap --json exit $RC: $OUT"
assert_json "$OUT" "rigops reap --json"
case "$OUT" in
    *'"reap"'*) : ;;
    *) fail "reap report missing a 'reap' verdict: $OUT" ;;
esac
[ -d "$REAP_REPO/.claude/worktrees/merged-wt" ] || fail "read-only reap removed a worktree"
unset GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM

if command -v lsof >/dev/null 2>&1; then
    echo "smoke: reap --selftest (bystander isolation)"
    run "${RIGOPS[@]}" reap --selftest
    [ "$RC" -eq 0 ] || fail "reap --selftest exit $RC: $OUT"
    case "$OUT" in
        *"PASS selftest"*) : ;;
        *) fail "reap --selftest missing PASS line: $OUT" ;;
    esac
else
    echo "smoke: reap --selftest skipped (lsof not installed)"
fi

echo "smoke: OK"
