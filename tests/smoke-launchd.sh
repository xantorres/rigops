#!/usr/bin/env bash
# Real launchd bootstrap/bootout smoke test. macOS only -- skips cleanly
# elsewhere. Uses a per-run label prefix so it can never collide with a
# developer's real jobs, and a trap that always tries to tear the job
# down again even if an assertion fails partway through.
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
    echo "skip: smoke-launchd.sh only runs on macOS"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_SH="$REPO_ROOT/install.sh"

fail() {
    echo "smoke-launchd FAIL: $*" >&2
    exit 1
}

run() {
    set +e
    OUT="$("$@" 2>&1)"
    RC=$?
    set -e
}

SMOKE_HOME="$(mktemp -d)"
export RIGOPS_INSTALL_LABEL_PREFIX="com.rigops.smoke$$"
LABEL="${RIGOPS_INSTALL_LABEL_PREFIX}.doctor"
ALL_JOBS="doctor ledger janitor reap"
UID_NUM="$(id -u)"
BROKEN_SRC=""

cleanup() {
    local job
    for job in $ALL_JOBS; do
        launchctl bootout "gui/$UID_NUM/$RIGOPS_INSTALL_LABEL_PREFIX.$job" >/dev/null 2>&1 || true
        launchctl bootout "user/$UID_NUM/$RIGOPS_INSTALL_LABEL_PREFIX.$job" >/dev/null 2>&1 || true
    done
    rm -rf "$SMOKE_HOME"
    if [ -n "$BROKEN_SRC" ]; then rm -rf "$BROKEN_SRC"; fi
}
trap cleanup EXIT

unset RIGOPS_CONFIG RIGOPS_STATE_DIR XDG_CONFIG_HOME XDG_STATE_HOME
export HOME="$SMOKE_HOME"

echo "smoke-launchd: install doctor job"
run bash "$INSTALL_SH" --apply --jobs doctor --yes
[ "$RC" -eq 0 ] || fail "install exit $RC: $OUT"

PLIST="$SMOKE_HOME/Library/LaunchAgents/$LABEL.plist"
[ -f "$PLIST" ] || fail "plist not rendered: $PLIST"

if command -v plutil >/dev/null 2>&1; then
    run plutil -lint "$PLIST"
    [ "$RC" -eq 0 ] || fail "plutil -lint failed: $OUT"
fi

run launchctl list "$LABEL"
[ "$RC" -eq 0 ] || fail "label not loaded after install: $LABEL ($OUT)"

echo "smoke-launchd: uninstall --purge"
run bash "$INSTALL_SH" --uninstall --apply --purge --yes
[ "$RC" -eq 0 ] || fail "uninstall --purge exit $RC: $OUT"

set +e
launchctl list "$LABEL" >/dev/null 2>&1
listed_rc=$?
set -e
[ "$listed_rc" -ne 0 ] || fail "label still loaded after uninstall: $LABEL"

[ -f "$PLIST" ] && fail "plist still present after uninstall: $PLIST"

leftover="$(find "$SMOKE_HOME" -name '*rigops*' 2>/dev/null || true)"
[ -z "$leftover" ] || fail "leftover rigops paths after purge: $leftover"

echo "smoke-launchd: manifest job record survives a later mid-install failure"
run bash "$INSTALL_SH" --apply --jobs doctor --yes
[ "$RC" -eq 0 ] || fail "second install exit $RC: $OUT"

run launchctl list "$LABEL"
[ "$RC" -eq 0 ] || fail "label not loaded after second install: $LABEL ($OUT)"

BROKEN_SRC="$(mktemp -d)"
cp -R "$REPO_ROOT/." "$BROKEN_SRC/"
rm -f "$BROKEN_SRC/templates/launchd/doctor.plist.in"

run bash "$BROKEN_SRC/install.sh" --apply --jobs doctor --yes
[ "$RC" -eq 1 ] || fail "missing-template install expected exit 1, got $RC: $OUT"

MANIFEST="$SMOKE_HOME/.local/share/rigops/install.manifest.json"
python3 - "$MANIFEST" "$LABEL" <<'PYEOF' || fail "manifest lost job record after mid-install failure"
import json
import sys

manifest_path, label = sys.argv[1], sys.argv[2]
data = json.load(open(manifest_path, encoding="utf-8"))
labels = [j.get("label") for j in data.get("jobs", [])]
sys.exit(0 if label in labels else 1)
PYEOF
rm -rf "$BROKEN_SRC"
BROKEN_SRC=""

run bash "$INSTALL_SH" --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "final uninstall exit $RC: $OUT"

set +e
launchctl list "$LABEL" >/dev/null 2>&1
listed_rc=$?
set -e
[ "$listed_rc" -ne 0 ] || fail "label still loaded after final uninstall: $LABEL"

[ -f "$PLIST" ] && fail "plist still present after final uninstall: $PLIST"


echo "smoke-launchd: every shipped job loads and unloads"
run bash "$INSTALL_SH" --apply --jobs "$(printf '%s' "$ALL_JOBS" | tr ' ' ',')" --yes
[ "$RC" -eq 0 ] || fail "all-jobs install exit $RC: $OUT"
for job in $ALL_JOBS; do
    run launchctl list "$RIGOPS_INSTALL_LABEL_PREFIX.$job"
    [ "$RC" -eq 0 ] || fail "$job not loaded after install ($OUT)"
done

run bash "$INSTALL_SH" --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "all-jobs uninstall exit $RC: $OUT"
for job in $ALL_JOBS; do
    set +e
    launchctl list "$RIGOPS_INSTALL_LABEL_PREFIX.$job" >/dev/null 2>&1
    listed_rc=$?
    set -e
    [ "$listed_rc" -ne 0 ] || fail "$job still loaded after uninstall"
    if [ -f "$SMOKE_HOME/Library/LaunchAgents/$RIGOPS_INSTALL_LABEL_PREFIX.$job.plist" ]; then
        fail "$job plist still present after uninstall"
    fi
done

echo "smoke-launchd: OK"
