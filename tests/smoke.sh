#!/usr/bin/env bash
# Fresh-HOME end-to-end smoke test for install.sh: plan mode, apply,
# idempotent re-apply, the shipped CLIs against repo fixtures, statusline
# wiring, and the hash-checked uninstall/purge path. Runs on macOS and
# Linux. No real launchd interaction here -- see smoke-launchd.sh (macOS
# only) for that.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_SH="$REPO_ROOT/install.sh"

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

# Content-hash-inclusive tree snapshot (path + sha256, or symlink target) so
# a plan-mode run that rewrote a file's bytes in place -- without adding or
# removing any path -- still shows up as a diff. Pure stdlib, BSD/GNU-find
# agnostic.
snapshot_tree() {
    python3 - "$1" <<'PYEOF'
import hashlib
import os
import sys

root = sys.argv[1]
entries = []
for dirpath, dirnames, filenames in os.walk(root):
    for name in filenames:
        path = os.path.join(dirpath, name)
        if os.path.islink(path):
            entries.append(path + "\tSYMLINK\t" + os.readlink(path))
            continue
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            digest = "ERROR:" + str(exc)
        entries.append(path + "\t" + digest)
    for name in dirnames:
        entries.append(os.path.join(dirpath, name) + "\tDIR")
entries.sort()
for line in entries:
    print(line)
PYEOF
}

SMOKE_HOME="$(mktemp -d)"
SRC_COPY=""
STATUSLINE_HOME=""
FIX1_HOME=""
PURGE_SAFE_HOME=""
cleanup() {
    rm -rf "$SMOKE_HOME"
    if [ -n "$SRC_COPY" ]; then rm -rf "$SRC_COPY"; fi
    if [ -n "$STATUSLINE_HOME" ]; then rm -rf "$STATUSLINE_HOME"; fi
    if [ -n "$FIX1_HOME" ]; then rm -rf "$FIX1_HOME"; fi
    if [ -n "$PURGE_SAFE_HOME" ]; then rm -rf "$PURGE_SAFE_HOME"; fi
}
trap cleanup EXIT

unset RIGOPS_CONFIG RIGOPS_STATE_DIR XDG_CONFIG_HOME XDG_STATE_HOME
export HOME="$SMOKE_HOME"

echo "smoke: plan mode"
run bash "$INSTALL_SH"
[ "$RC" -eq 0 ] || fail "plan mode exit $RC: $OUT"
case "$OUT" in
    *"plan:"*) : ;;
    *) fail "plan mode output missing 'plan:'" ;;
esac
leftover="$(find "$SMOKE_HOME" -mindepth 1 2>/dev/null || true)"
[ -z "$leftover" ] || fail "plan mode wrote into \$HOME: $leftover"

echo "smoke: --jobs , rejected (empty job list)"
run bash "$INSTALL_SH" --jobs ,
[ "$RC" -eq 2 ] || fail "--jobs , expected exit 2, got $RC: $OUT"
case "$OUT" in
    *"error:"*) : ;;
    *) fail "--jobs , missing error message: $OUT" ;;
esac

echo "smoke: --no-jobs --jobs conflict rejected"
run bash "$INSTALL_SH" --no-jobs --jobs doctor
[ "$RC" -eq 2 ] || fail "--no-jobs --jobs doctor expected exit 2, got $RC: $OUT"
case "$OUT" in
    *"--jobs conflicts with --no-jobs"*) : ;;
    *) fail "--no-jobs --jobs doctor missing conflict message: $OUT" ;;
esac

echo "smoke: apply (--no-jobs)"
run bash "$INSTALL_SH" --apply --no-jobs --yes
[ "$RC" -eq 0 ] || fail "apply exit $RC: $OUT"

RIGOPS_LINK="$SMOKE_HOME/.local/bin/rigops"
[ -L "$RIGOPS_LINK" ] || fail "symlink missing: $RIGOPS_LINK"

run "$RIGOPS_LINK" help
[ "$RC" -eq 0 ] || fail "rigops help exit $RC: $OUT"
for cmd in config doctor eit ledger tax; do
    case "$OUT" in
        *"$cmd"*) : ;;
        *) fail "rigops help missing command: $cmd" ;;
    esac
done

MANIFEST="$SMOKE_HOME/.local/share/rigops/install.manifest.json"
[ -f "$MANIFEST" ] || fail "manifest missing: $MANIFEST"
python3 -m json.tool "$MANIFEST" >/dev/null || fail "manifest not valid JSON"

CONFIG_JSON="$SMOKE_HOME/.config/rigops/config.json"
REGISTRY_MD="$SMOKE_HOME/.config/rigops/registry.md"
[ -f "$CONFIG_JSON" ] || fail "config.json not scaffolded"
[ -f "$REGISTRY_MD" ] || fail "registry.md not scaffolded"

echo "smoke: exercise CLIs against repo fixtures"
# Overwrites the scaffolded config.json -- fine, it's this run's own HOME,
# and install.sh never touches a config.json that already exists.
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

run "$RIGOPS_LINK" eit --since=-3650d --json
[ "$RC" -eq 0 ] || fail "rigops eit exit $RC: $OUT"
assert_json "$OUT" "rigops eit --json"

run "$RIGOPS_LINK" tax --json
[ "$RC" -eq 0 ] || fail "rigops tax exit $RC: $OUT"
assert_json "$OUT" "rigops tax --json"

# Fresh ledger: 0-1 rows print a plain "not enough rows" message and exit
# 0 rather than emit JSON (see libexec/rigops-ledger cmd_diff) -- tolerate
# that instead of asserting JSON unconditionally.
run "$RIGOPS_LINK" ledger diff --json
[ "$RC" -eq 0 ] || fail "rigops ledger diff exit $RC: $OUT"
case "$OUT" in
    *"not enough ledger rows"*) : ;;
    *) assert_json "$OUT" "rigops ledger diff --json" ;;
esac

REGISTRY_EXAMPLE="$REPO_ROOT/config/registry.example.md"
run "$RIGOPS_LINK" doctor --report --supervisor=none --registry="$REGISTRY_EXAMPLE"
[ "$RC" -eq 0 ] || fail "rigops doctor exit $RC: $OUT"

run "$RIGOPS_LINK" config get doctor.kill_grace_s
[ "$RC" -eq 0 ] || fail "rigops config get exit $RC: $OUT"
[ "$OUT" = "5" ] || fail "expected doctor.kill_grace_s=5, got: $OUT"

echo "smoke: idempotent re-apply"
before_hashes="$(python3 - "$MANIFEST" <<'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(sorted((f["path"], f["sha256"]) for f in data["files"]))
PYEOF
)"

run bash "$INSTALL_SH" --apply --no-jobs --yes
[ "$RC" -eq 0 ] || fail "idempotent re-apply exit $RC: $OUT"
summary_line="$(printf '%s\n' "$OUT" | grep 'installed=' || true)"
case "$summary_line" in
    *"installed=0 updated=0"*) : ;;
    *) fail "idempotent re-apply not all-unchanged: $summary_line" ;;
esac

after_hashes="$(python3 - "$MANIFEST" <<'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(sorted((f["path"], f["sha256"]) for f in data["files"]))
PYEOF
)"
[ "$before_hashes" = "$after_hashes" ] || fail "manifest file hashes changed across idempotent re-run"

echo "smoke: uninstall plan purity (no --apply must not touch disk)"
before_tree="$(snapshot_tree "$SMOKE_HOME")"
run bash "$INSTALL_SH" --uninstall
[ "$RC" -eq 0 ] || fail "uninstall plan exit $RC: $OUT"
case "$OUT" in
    *"plan:"*) : ;;
    *) fail "uninstall plan output missing 'plan:'" ;;
esac
after_tree="$(snapshot_tree "$SMOKE_HOME")"
[ "$before_tree" = "$after_tree" ] || fail "uninstall plan mode modified \$SMOKE_HOME"

echo "smoke: statusline wiring"
SRC_COPY="$(mktemp -d)"
STATUSLINE_HOME="$(mktemp -d)"

cp -R "$REPO_ROOT/." "$SRC_COPY/"
mkdir -p "$SRC_COPY/plugin/statusline"
cat >"$SRC_COPY/plugin/statusline/rigops-statusline.sh" <<'EOF'
#!/bin/sh
echo "rigops"
EOF
chmod +x "$SRC_COPY/plugin/statusline/rigops-statusline.sh"

mkdir -p "$STATUSLINE_HOME/.claude"
printf '{"existing": true}' >"$STATUSLINE_HOME/.claude/settings.json"

run env HOME="$STATUSLINE_HOME" bash "$SRC_COPY/install.sh" --apply --no-jobs --statusline --yes
[ "$RC" -eq 0 ] || fail "statusline apply exit $RC: $OUT"
printf '%s\n' "$OUT" | grep -E '^[[:space:]]*\+.*statusLine' >/dev/null \
    || fail "statusline output missing diff line matching '+.*statusLine': $OUT"

SETTINGS="$STATUSLINE_HOME/.claude/settings.json"
python3 - "$SETTINGS" "$STATUSLINE_HOME" <<'PYEOF' || fail "settings.json statusLine wiring incorrect"
import json
import sys

settings_path, home = sys.argv[1], sys.argv[2]
data = json.load(open(settings_path, encoding="utf-8"))
expected = home + "/.local/share/rigops/plugin/statusline/rigops-statusline.sh"
if data.get("statusLine", {}).get("command") != expected:
    sys.exit(1)
if data.get("existing") is not True:
    sys.exit(1)
PYEOF

backups="$(find "$STATUSLINE_HOME/.claude" -name 'settings.json.rigops-backup.*' 2>/dev/null || true)"
[ -n "$backups" ] || fail "no settings.json backup found"

# The real checkout ships no plugin/statusline/ yet (M5); confirm --statusline
# fails loudly there instead of silently no-op'ing.
run bash "$INSTALL_SH" --statusline
[ "$RC" -eq 1 ] || fail "expected exit 1 for --statusline without the script, got $RC"
case "$OUT" in
    *"statusline script ships with the plugin half; not present in this checkout"*) : ;;
    *) fail "missing expected statusline error message: $OUT" ;;
esac

echo "smoke: hash-mismatch uninstall path"
TAX_PATH="$SMOKE_HOME/.local/share/rigops/libexec/rigops-tax"
printf '\n# smoke-test local edit\n' >>"$TAX_PATH"

run bash "$INSTALL_SH" --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall (no purge) exit $RC: $OUT"
case "$OUT" in
    *"modified since install"*) : ;;
    *) fail "uninstall output missing 'modified since install' warning" ;;
esac
[ -f "$TAX_PATH" ] || fail "modified file was removed by uninstall: $TAX_PATH"
[ -L "$RIGOPS_LINK" ] && fail "symlink still present after uninstall"
[ -f "$SMOKE_HOME/.local/share/rigops/bin/rigops" ] && fail "bin/rigops still present after uninstall"
[ -f "$MANIFEST" ] && fail "manifest still present after uninstall"

rm -f "$TAX_PATH"
run bash "$INSTALL_SH" --apply --no-jobs --yes
[ "$RC" -eq 0 ] || fail "re-install after cleanup exit $RC: $OUT"

run bash "$INSTALL_SH" --uninstall --apply --purge --yes
[ "$RC" -eq 0 ] || fail "uninstall --purge exit $RC: $OUT"

leftover="$(find "$SMOKE_HOME" -name '*rigops*' 2>/dev/null || true)"
[ -z "$leftover" ] || fail "leftover rigops paths after purge: $leftover"
[ -d "$SMOKE_HOME/.local/share/rigops" ] && fail "prefix dir still present after purge"
[ -d "$SMOKE_HOME/.config/rigops" ] && fail "config dir still present after purge"

echo "smoke: externally-managed config survives purge (RIGOPS_CONFIG)"
FIX1_HOME="$(mktemp -d)"
printf 'decoy\n' >"$FIX1_HOME/decoy.txt"
run env HOME="$FIX1_HOME" RIGOPS_CONFIG="$FIX1_HOME/rigops.json" bash "$INSTALL_SH" --apply --no-jobs --yes
[ "$RC" -eq 0 ] || fail "FIX1 install exit $RC: $OUT"

run env HOME="$FIX1_HOME" RIGOPS_CONFIG="$FIX1_HOME/rigops.json" bash "$INSTALL_SH" --uninstall --apply --purge --yes
[ "$RC" -eq 0 ] || fail "FIX1 uninstall --purge exit $RC: $OUT"
case "$OUT" in
    *"managed externally"*) : ;;
    *) fail "FIX1 purge output missing 'managed externally': $OUT" ;;
esac
case "$OUT" in
    *"refus"*) fail "FIX1 purge unexpectedly refused a dir: $OUT" ;;
    *) : ;;
esac
[ -f "$FIX1_HOME/decoy.txt" ] || fail "FIX1 regression: decoy.txt was purged (externally-managed RIGOPS_CONFIG dir got deleted)"

rm -rf "$FIX1_HOME"
FIX1_HOME=""

echo "smoke: purge_safe refuses a manifest pointing config_dir at \$HOME"
PURGE_SAFE_HOME="$(mktemp -d)"
run env HOME="$PURGE_SAFE_HOME" bash "$INSTALL_SH" --apply --no-jobs --yes
[ "$RC" -eq 0 ] || fail "purge_safe-test install exit $RC: $OUT"

PURGE_SAFE_MANIFEST="$PURGE_SAFE_HOME/.local/share/rigops/install.manifest.json"
python3 - "$PURGE_SAFE_MANIFEST" "$PURGE_SAFE_HOME" <<'PYEOF' || fail "could not corrupt manifest config_dir"
import json
import sys

path, home = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
data["config_dir"] = home
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, sort_keys=True)
PYEOF

printf 'decoy\n' >"$PURGE_SAFE_HOME/decoy2.txt"

run env HOME="$PURGE_SAFE_HOME" bash "$INSTALL_SH" --uninstall --apply --purge --yes
[ "$RC" -eq 1 ] || fail "purge_safe-test uninstall expected exit 1, got $RC: $OUT"
case "$OUT" in
    *"refusing to purge"*) : ;;
    *) fail "purge_safe-test missing 'refusing to purge': $OUT" ;;
esac
case "$OUT" in
    *"rm -rf"*) : ;;
    *) fail "purge_safe-test missing 'rm -rf' manual-command summary: $OUT" ;;
esac
[ -f "$PURGE_SAFE_HOME/decoy2.txt" ] || fail "purge_safe-test regression: decoy2.txt was purged"

rm -rf "$PURGE_SAFE_HOME"
PURGE_SAFE_HOME=""

echo "smoke: OK"
