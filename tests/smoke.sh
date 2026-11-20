#!/usr/bin/env bash
# Fresh-HOME end-to-end smoke test for install.sh: plan mode, apply,
# idempotent re-apply, the shipped CLIs against repo fixtures, the removed
# statusline option, and the hash-checked uninstall/purge path. Runs on macOS and
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

# Puts a fake launchctl in $1/bin that keeps each loaded label's domain in
# $1/loaded/<label>, so job paths run on any OS without the real launchd.
# FAKE_LAUNCHD_FAIL lists labels and FAKE_LAUNCHD_FAIL_DOMAIN names a domain
# kind (gui) whose bootstrap fails; FAKE_LAUNCHD_LOCK is a dir made read-only
# after a load, to interrupt the install right after it.
make_fake_launchctl() {
    mkdir -p "$1/bin" "$1/loaded"
    cat >"$1/bin/launchctl" <<'EOF'
#!/bin/sh
state="${FAKE_LAUNCHD:?}"
echo "$*" >>"$state/calls"
case "$1" in
    bootstrap)
        label="$(basename "$3" .plist)"
        case " ${FAKE_LAUNCHD_FAIL:-} " in *" $label "*) exit 5 ;; esac
        case "$2" in "${FAKE_LAUNCHD_FAIL_DOMAIN:-none}"/*) exit 5 ;; esac
        echo "$2" >"$state/loaded/$label"
        [ -z "${FAKE_LAUNCHD_LOCK:-}" ] || chmod a-w "$FAKE_LAUNCHD_LOCK"
        ;;
    bootout)
        label="${2##*/}"
        [ "$(cat "$state/loaded/$label" 2>/dev/null)" = "${2%/*}" ] || exit 3
        rm -f "$state/loaded/$label"
        ;;
    list)
        [ -f "$state/loaded/$2" ] || exit 113
        ;;
esac
exit 0
EOF
    chmod +x "$1/bin/launchctl"
}

# Labels recorded in manifest $1, one per line.
manifest_labels() {
    python3 - "$1" <<'PYEOF'
import json
import sys

for job in json.load(open(sys.argv[1], encoding="utf-8")).get("jobs", []):
    print(job.get("label", ""))
PYEOF
}

SMOKE_HOME="$(mktemp -d)"
SRC_COPY=""
STATUSLINE_HOME=""
FIX1_HOME=""
PURGE_SAFE_HOME=""
JOBS_HOME=""
cleanup() {
    rm -rf "$SMOKE_HOME"
    if [ -n "$SRC_COPY" ]; then rm -rf "$SRC_COPY"; fi
    if [ -n "$STATUSLINE_HOME" ]; then rm -rf "$STATUSLINE_HOME"; fi
    if [ -n "$FIX1_HOME" ]; then rm -rf "$FIX1_HOME"; fi
    if [ -n "$PURGE_SAFE_HOME" ]; then rm -rf "$PURGE_SAFE_HOME"; fi
    if [ -n "$JOBS_HOME" ]; then rm -rf "$JOBS_HOME"; fi
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

echo "smoke: bare re-install keeps the jobs the previous manifest lists"
if command -v launchctl >/dev/null 2>&1; then
    KEEP_HOME="$(mktemp -d)"
    mkdir -p "$KEEP_HOME/prefix"
    printf '%s' '{"jobs":[{"label":"com.rigops.doctor","plist":"/x/d.plist","sha256":"a","domain":"gui/1"}]}' \
        >"$KEEP_HOME/prefix/install.manifest.json"
    run bash "$INSTALL_SH" --prefix "$KEEP_HOME/prefix"
    rm -rf "$KEEP_HOME"
    case "$OUT" in
        *"launchd jobs enabled: doctor ledger"*) fail "bare plan re-enabled a job the manifest dropped: $OUT" ;;
        *"launchd jobs enabled: doctor"*) : ;;
        *) fail "bare plan with a doctor-only manifest should enable only doctor: $OUT" ;;
    esac
fi

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
for cmd in config doctor eit eval ledger tax; do
    case "$OUT" in
        *"$cmd"*) : ;;
        *) fail "rigops help missing command: $cmd" ;;
    esac
done

echo "smoke: the public redaction patterns install beside the package and mine runs without a registry"
PATTERNS_REL="lib/rigops/redaction-patterns.public.txt"
INSTALLED_PATTERNS="$SMOKE_HOME/.local/share/rigops/$PATTERNS_REL"
[ -f "$INSTALLED_PATTERNS" ] || fail "public patterns not installed: $INSTALLED_PATTERNS"
cmp -s "$INSTALLED_PATTERNS" "$REPO_ROOT/$PATTERNS_REL" || fail "installed public patterns differ from the repo copy"
mkdir -p "$SMOKE_HOME/mine-transcripts"
run "$RIGOPS_LINK" mine --dry-run --transcripts-dir "$SMOKE_HOME/mine-transcripts"
[ "$RC" -eq 0 ] || fail "installed mine --dry-run exit $RC: $OUT"
case "$OUT" in
    *"# rigops mine --dry-run"*) : ;;
    *) fail "installed mine --dry-run missing report header: $OUT" ;;
esac

MANIFEST="$SMOKE_HOME/.local/share/rigops/install.manifest.json"
[ -f "$MANIFEST" ] || fail "manifest missing: $MANIFEST"
python3 -m json.tool "$MANIFEST" >/dev/null || fail "manifest not valid JSON"

CONFIG_JSON="$SMOKE_HOME/.config/rigops/config.json"
REGISTRY_MD="$SMOKE_HOME/.config/rigops/registry.md"
[ -f "$CONFIG_JSON" ] || fail "config.json not scaffolded"
[ -f "$REGISTRY_MD" ] || fail "registry.md not scaffolded"

echo "smoke: a fresh install scaffolds a minimal config and an empty registry"
python3 - "$CONFIG_JSON" <<'PYEOF' || fail "scaffolded config.json is not an empty object: $(cat "$CONFIG_JSON")"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    sys.exit(0 if json.load(fh) == {} else 1)
PYEOF
run "$RIGOPS_LINK" config check
[ "$RC" -eq 0 ] || fail "fresh config check exit $RC: $OUT"
case "$OUT" in
    *warn*) fail "fresh config check warns: $OUT" ;;
esac
run "$RIGOPS_LINK" doctor --report --supervisor=none
[ "$RC" -eq 0 ] || fail "fresh doctor exit $RC: $OUT"
case "$OUT" in
    *"no registry items"*) : ;;
    *) fail "fresh doctor does not report an empty registry: $OUT" ;;
esac

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

# Fresh ledger: 0-1 rows still emit JSON (empty deltas) under --json.
run "$RIGOPS_LINK" ledger diff --json
[ "$RC" -eq 0 ] || fail "rigops ledger diff exit $RC: $OUT"
assert_json "$OUT" "rigops ledger diff --json"

REGISTRY_EXAMPLE="$REPO_ROOT/config/registry.example.md"
run "$RIGOPS_LINK" doctor --report --supervisor=none --registry="$REGISTRY_EXAMPLE"
[ "$RC" -eq 0 ] || fail "rigops doctor exit $RC: $OUT"

run "$RIGOPS_LINK" config get doctor.kill_grace_s
[ "$RC" -eq 0 ] || fail "rigops config get exit $RC: $OUT"
[ "$OUT" = "5" ] || fail "expected doctor.kill_grace_s=5, got: $OUT"

echo "smoke: authprobe ships disabled"
run "$RIGOPS_LINK" authprobe
[ "$RC" -eq 0 ] || fail "rigops authprobe exit $RC: $OUT"
case "$OUT" in
    *"disabled"*) : ;;
    *) fail "rigops authprobe missing 'disabled': $OUT" ;;
esac

run "$RIGOPS_LINK" authprobe --json
[ "$RC" -eq 0 ] || fail "rigops authprobe --json exit $RC: $OUT"
assert_json "$OUT" "rigops authprobe --json"
case "$OUT" in
    *'"enabled"'*) : ;;
    *) fail "rigops authprobe --json missing 'enabled': $OUT" ;;
esac

echo "smoke: ledger dry-run row carries sources"
run "$RIGOPS_LINK" ledger --dry-run --json
[ "$RC" -eq 0 ] || fail "rigops ledger --dry-run --json exit $RC: $OUT"
assert_json "$OUT" "rigops ledger --dry-run --json"
case "$OUT" in
    *'"sources"'*) : ;;
    *) fail "rigops ledger --dry-run --json missing sources key: $OUT" ;;
esac

echo "smoke: eval records every case as an error against an unreachable endpoint"
# stdout only: the per-case progress lines go to stderr and would break the JSON.
set +e
OUT="$("$RIGOPS_LINK" eval run --cases "$REPO_ROOT/examples/eval/cases" \
    --endpoint http://127.0.0.1:9/v1 --model none --dry-run --json 2>/dev/null)"
RC=$?
set -e
[ "$RC" -eq 0 ] || fail "rigops eval run --dry-run exit $RC: $OUT"
assert_json "$OUT" "rigops eval run --json"
case "$OUT" in
    *'"status": "error"'*) : ;;
    *) fail "rigops eval run against a dead endpoint recorded no error: $OUT" ;;
esac
run "$RIGOPS_LINK" eval diff
[ "$RC" -eq 2 ] || fail "rigops eval diff with no runs expected exit 2, got $RC: $OUT"

echo "smoke: backlog lint/add on a scratch file"
BACKLOG_MD="$SMOKE_HOME/backlog-scratch.md"
run "$RIGOPS_LINK" backlog add --file "$BACKLOG_MD" "hooks: smoke entry lands. Fix: nothing."
[ "$RC" -eq 0 ] || fail "backlog add exit $RC: $OUT"
[ -f "$BACKLOG_MD" ] || fail "backlog add did not scaffold $BACKLOG_MD"
run "$RIGOPS_LINK" backlog lint --file "$BACKLOG_MD" --json
[ "$RC" -eq 0 ] || fail "backlog lint exit $RC: $OUT"
assert_json "$OUT" "rigops backlog lint --json"
run "$RIGOPS_LINK" backlog lint --file "$REPO_ROOT/config/backlog.example.md"
[ "$RC" -eq 0 ] || fail "backlog.example.md does not lint clean: $OUT"
run "$RIGOPS_LINK" backlog add --file "$BACKLOG_MD" "notanarea: rejected entry"
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
run env RIGOPS_CONFIG="$JANITOR_CFG" "$RIGOPS_LINK" janitor
[ "$RC" -eq 0 ] || fail "janitor dry-run exit $RC: $OUT"
[ -f "$JANITOR_TREE/spool/old.json" ] || fail "janitor dry-run deleted a file"
run env RIGOPS_CONFIG="$JANITOR_CFG" "$RIGOPS_LINK" janitor --json
[ "$RC" -eq 0 ] || fail "janitor --json exit $RC: $OUT"
assert_json "$OUT" "rigops janitor --json"
run env RIGOPS_CONFIG="$JANITOR_CFG" "$RIGOPS_LINK" janitor --apply
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
run env RIGOPS_RUN_DEADLINE_S=60 "$RIGOPS_LINK" reap --root "$REAP_ROOT" --no-fetch --json
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
    run "$RIGOPS_LINK" reap --selftest
    [ "$RC" -eq 0 ] || fail "reap --selftest exit $RC: $OUT"
    case "$OUT" in
        *"PASS selftest"*) : ;;
        *) fail "reap --selftest missing PASS line: $OUT" ;;
    esac
else
    echo "smoke: reap --selftest skipped (lsof not installed)"
fi

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

echo "smoke: a re-install prunes files the new version no longer ships"
PRUNE_PREFIX="$SMOKE_HOME/.local/share/rigops"
printf 'retired\n' >"$PRUNE_PREFIX/lib/rigops/retired.py"
printf 'edited\n' >"$PRUNE_PREFIX/lib/rigops/retired_edited.py"
printf 'hand\n' >"$PRUNE_PREFIX/lib/rigops/hand_copied.py"
printf 'outside\n' >"$SMOKE_HOME/.local/share/outside.txt"
python3 - "$MANIFEST" "$PRUNE_PREFIX" <<'PYEOF'
import hashlib
import json
import sys

manifest, prefix = sys.argv[1:3]
data = json.load(open(manifest, encoding="utf-8"))
for rel, src in (("lib/rigops/retired.py", None), ("lib/rigops/retired_edited.py", None),
                 ("../outside.txt", None), ("lib/rigops/Config.py", "lib/rigops/config.py")):
    with open(prefix + "/" + (src or rel), "rb") as fh:
        data["files"].append({"path": rel, "sha256": hashlib.sha256(fh.read()).hexdigest()})
with open(manifest, "w", encoding="utf-8") as fh:
    json.dump(data, fh)
PYEOF
printf 'local edit\n' >>"$PRUNE_PREFIX/lib/rigops/retired_edited.py"

run bash "$INSTALL_SH" --no-jobs
[ "$RC" -eq 0 ] || fail "prune plan exit $RC: $OUT"
case "$OUT" in
    *"plan: would remove $PRUNE_PREFIX/lib/rigops/retired.py"*) : ;;
    *) fail "prune plan does not name the file it would remove: $OUT" ;;
esac
[ -f "$PRUNE_PREFIX/lib/rigops/retired.py" ] || fail "prune plan removed a file"

run bash "$INSTALL_SH" --apply --no-jobs --yes
[ "$RC" -eq 0 ] || fail "prune apply exit $RC: $OUT"
case "$OUT" in
    *"removed  $PRUNE_PREFIX/lib/rigops/retired.py"*) : ;;
    *) fail "prune apply does not name the file it removed: $OUT" ;;
esac
case "$OUT" in
    *"warn: $PRUNE_PREFIX/lib/rigops/retired_edited.py modified since install"*) : ;;
    *) fail "prune apply does not warn about the edited file: $OUT" ;;
esac
[ -e "$PRUNE_PREFIX/lib/rigops/retired.py" ] && fail "prune apply left the retired file"
[ -f "$PRUNE_PREFIX/lib/rigops/retired_edited.py" ] || fail "prune apply removed an edited file"
[ -f "$PRUNE_PREFIX/lib/rigops/hand_copied.py" ] || fail "prune apply removed a file in neither manifest"
[ -f "$SMOKE_HOME/.local/share/outside.txt" ] || fail "prune apply removed a file outside the prefix"
[ -f "$PRUNE_PREFIX/lib/rigops/config.py" ] || fail "prune apply removed a shipped file through a case-only rename"
if grep -q 'retired' "$MANIFEST"; then fail "manifest still lists a pruned file"; fi
rm -f "$PRUNE_PREFIX/lib/rigops/retired_edited.py" "$PRUNE_PREFIX/lib/rigops/hand_copied.py" \
    "$SMOKE_HOME/.local/share/outside.txt"

echo "smoke: uninstall plan purity (no --apply must not touch disk)"
before_tree="$(snapshot_tree "$SMOKE_HOME")"
run bash "$INSTALL_SH" --uninstall
[ "$RC" -eq 0 ] || fail "uninstall plan exit $RC: $OUT"
case "$OUT" in
    *"plan:"*) : ;;
    *) fail "uninstall plan output missing 'plan:'" ;;
esac
case "$OUT" in
    *"removed  "*) fail "uninstall plan reports removals it did not make: $OUT" ;;
    *"plan: would remove $SMOKE_HOME/.local/share/rigops/"*) : ;;
    *) fail "uninstall plan does not say which files it would remove: $OUT" ;;
esac
after_tree="$(snapshot_tree "$SMOKE_HOME")"
[ "$before_tree" = "$after_tree" ] || fail "uninstall plan mode modified \$SMOKE_HOME"

echo "smoke: --statusline was removed"
for args in "--statusline" "--no-jobs --statusline --apply --yes"; do
    # shellcheck disable=SC2086
    run bash "$INSTALL_SH" $args
    [ "$RC" -eq 2 ] || fail "install.sh $args expected exit 2, got $RC: $OUT"
    case "$OUT" in
        *"error: --statusline was removed; see patterns/statusline.md"*) : ;;
        *) fail "install.sh $args missing the removal error: $OUT" ;;
    esac
done

STATUSLINE_HOME="$(mktemp -d)"
SETTINGS="$STATUSLINE_HOME/.claude/settings.json"
OLD_PREFIX="$STATUSLINE_HOME/.local/share/rigops"
OLD_CMD="$OLD_PREFIX/plugin/statusline/rigops-statusline.sh"
OLD_PREV='{"type": "command", "command": "/usr/bin/true"}'

# Writes what an earlier install run with the removed statusline option left
# behind: the copied script, its manifest file record and the statusline record.
seed_old_install() {
    python3 - "$STATUSLINE_HOME" "$1" "$2" <<'PYEOF'
import hashlib
import json
import os
import sys

home, previous, created = sys.argv[1:4]
prefix = home + "/.local/share/rigops"
rel = "plugin/statusline/rigops-statusline.sh"
script = prefix + "/" + rel
os.makedirs(os.path.dirname(script), exist_ok=True)
body = b"#!/bin/sh\necho old\n"
with open(script, "wb") as fh:
    fh.write(body)
record = {"settings": home + "/.claude/settings.json", "command": script}
if previous != "-":
    record["previous"] = json.loads(previous)
if created == "1":
    record["created"] = True
manifest = {
    "version": "0.10.0",
    "home": home,
    "prefix": prefix,
    "config_dir": home + "/.config/rigops",
    "state_dir": home + "/.local/state/rigops",
    "log_dir": home + "/Library/Logs/rigops",
    "config_file_external": "",
    "state_dir_external": "",
    "files": [{"path": rel, "sha256": hashlib.sha256(body).hexdigest()}],
    "symlinks": [],
    "jobs": [],
    "scaffolded": [],
    "statusline": record,
}
with open(prefix + "/install.manifest.json", "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
PYEOF
}

uninstall_old() {
    run env HOME="$STATUSLINE_HOME" bash "$INSTALL_SH" --uninstall --apply --yes
    [ "$RC" -eq 0 ] || fail "$1: uninstall exit $RC: $OUT"
}

echo "smoke: uninstall restores the statusLine an earlier install replaced and removes its script"
mkdir -p "$STATUSLINE_HOME/.claude"
printf '{"existing": true, "statusLine": {"type": "command", "command": "%s"}}' "$OLD_CMD" >"$SETTINGS"
seed_old_install "$OLD_PREV" 0
uninstall_old "restore"
python3 - "$SETTINGS" <<'PYEOF' || fail "uninstall did not restore the previous statusLine: $(cat "$SETTINGS")"
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
if data.get("statusLine") != {"type": "command", "command": "/usr/bin/true"}:
    sys.exit(1)
sys.exit(0 if data.get("existing") is True else 1)
PYEOF
[ -e "$OLD_CMD" ] && fail "uninstall left the statusline script an earlier install copied"
[ -d "$OLD_PREFIX/plugin/statusline" ] && fail "uninstall left the plugin/statusline directory"

echo "smoke: uninstall drops a statusLine an earlier install added"
printf '{"existing": true, "statusLine": {"type": "command", "command": "%s"}}' "$OLD_CMD" >"$SETTINGS"
seed_old_install - 0
uninstall_old "drop"
python3 - "$SETTINGS" <<'PYEOF' || fail "uninstall left the rigops statusLine: $(cat "$SETTINGS")"
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if "statusLine" not in data and data.get("existing") is True else 1)
PYEOF
[ -e "$OLD_CMD" ] && fail "uninstall left the statusline script (added case)"

echo "smoke: uninstall keeps a statusLine the user changed"
printf '{"statusLine": {"type": "command", "command": "/usr/bin/true"}}' >"$SETTINGS"
seed_old_install - 0
uninstall_old "user-changed"
case "$OUT" in
    *"kept     statusLine (no longer runs"*) : ;;
    *) fail "uninstall over a user statusLine did not say it kept it: $OUT" ;;
esac
[ "$(cat "$SETTINGS")" = '{"statusLine": {"type": "command", "command": "/usr/bin/true"}}' ] \
    || fail "uninstall changed a statusLine the user set: $(cat "$SETTINGS")"

echo "smoke: uninstall finishes when settings.json is not UTF-8"
printf '{"x": "\377"}' >"$SETTINGS"
seed_old_install - 0
uninstall_old "non-utf8"
case "$OUT" in
    *"kept     statusLine"*) : ;;
    *) fail "uninstall over non-UTF-8 settings.json did not keep statusLine: $OUT" ;;
esac
[ -f "$OLD_PREFIX/install.manifest.json" ] \
    && fail "uninstall over non-UTF-8 settings.json stopped before the manifest"

echo "smoke: uninstall deletes a settings.json an earlier install created"
printf '{"statusLine": {"type": "command", "command": "%s"}}' "$OLD_CMD" >"$SETTINGS"
seed_old_install - 1
uninstall_old "created"
[ -e "$SETTINGS" ] && fail "uninstall left the settings.json it created: $(cat "$SETTINGS")"

echo "smoke: uninstall writes through a symlinked settings.json"
DOTFILE="$STATUSLINE_HOME/dotfiles/settings.json"
mkdir -p "$STATUSLINE_HOME/dotfiles"
printf '{"theme": "dark", "statusLine": {"type": "command", "command": "%s"}}' "$OLD_CMD" >"$DOTFILE"
rm -f "$SETTINGS"
ln -s "$DOTFILE" "$SETTINGS"
seed_old_install - 0
uninstall_old "symlink"
[ -L "$SETTINGS" ] || fail "uninstall replaced the settings.json symlink"
python3 - "$DOTFILE" <<'PYEOF' || fail "uninstall left the statusLine in the symlink target: $(cat "$DOTFILE")"
import json
import sys

sys.exit(0 if json.load(open(sys.argv[1], encoding="utf-8")) == {"theme": "dark"} else 1)
PYEOF

echo "smoke: re-applying over an earlier statusline install unwires it"
printf '{"existing": true, "statusLine": {"type": "command", "command": "%s"}}' "$OLD_CMD" >"$SETTINGS"
seed_old_install "$OLD_PREV" 0
before_settings="$(cat "$SETTINGS")"
run env HOME="$STATUSLINE_HOME" bash "$INSTALL_SH" --no-jobs --yes
[ "$RC" -eq 0 ] || fail "upgrade plan exit $RC: $OUT"
[ "$(cat "$SETTINGS")" = "$before_settings" ] || fail "upgrade plan changed settings.json"
case "$OUT" in
    *"plan: would restore"*) : ;;
    *) fail "upgrade plan did not say it would restore the statusLine: $OUT" ;;
esac
run env HOME="$STATUSLINE_HOME" bash "$INSTALL_SH" --apply --no-jobs --yes
[ "$RC" -eq 0 ] || fail "upgrade apply exit $RC: $OUT"
case "$OUT" in
    *"note: statusline unwired; it is now an example, see patterns/statusline.md"*) : ;;
    *) fail "upgrade apply missing the unwired note: $OUT" ;;
esac
python3 - "$SETTINGS" "$OLD_PREFIX/install.manifest.json" <<'PYEOF' || fail "upgrade left settings or manifest wrong: $(cat "$SETTINGS")"
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
if data.get("statusLine") != {"type": "command", "command": "/usr/bin/true"} or data.get("existing") is not True:
    sys.exit(1)
manifest = json.load(open(sys.argv[2], encoding="utf-8"))
sys.exit(1 if "statusline" in manifest else 0)
PYEOF
[ -e "$OLD_CMD" ] && fail "upgrade left the old statusline script"
run env HOME="$STATUSLINE_HOME" bash "$INSTALL_SH" --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after upgrade exit $RC: $OUT"

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

echo "smoke: a declined purge keeps the manifest for a later --purge"
set +e
OUT="$(printf 'n\n' | bash "$INSTALL_SH" --uninstall --apply --purge 2>&1)"
RC=$?
set -e
[ "$RC" -eq 0 ] || fail "declined purge exit $RC: $OUT"
case "$OUT" in
    *"purge cancelled"*) : ;;
    *) fail "declined purge output missing 'purge cancelled': $OUT" ;;
esac
[ -f "$MANIFEST" ] || fail "declined purge removed the manifest, so a later --purge cannot run"

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

echo "smoke: uninstall with the prefix set to the home removes manifest-derived paths only"
HOMEPFX_HOME="$(mktemp -d)"
run env HOME="$HOMEPFX_HOME" bash "$INSTALL_SH" --apply --no-jobs --yes --prefix "$HOMEPFX_HOME"
[ "$RC" -eq 0 ] || fail "home-prefix install exit $RC: $OUT"
HOMEPFX_MANIFEST="$HOMEPFX_HOME/install.manifest.json"
[ -f "$HOMEPFX_MANIFEST" ] || fail "home-prefix manifest missing"
run env -u PYTHONDONTWRITEBYTECODE HOME="$HOMEPFX_HOME" "$HOMEPFX_HOME/bin/rigops" config get doctor.kill_grace_s
[ "$RC" -eq 0 ] || fail "home-prefix rigops config get exit $RC: $OUT"
mkdir -p "$HOMEPFX_HOME/projects/app/__pycache__" "$HOMEPFX_HOME/keep-empty"
: >"$HOMEPFX_HOME/projects/app/__pycache__/mod.cpython-311.pyc"
HOMEPFX_PYC_DIR="$(python3 - "$HOMEPFX_HOME/lib" <<'PYEOF'
import os
import sys

for dirpath, dirnames, _ in os.walk(sys.argv[1]):
    if "__pycache__" in dirnames:
        print(os.path.join(dirpath, "__pycache__"))
        break
PYEOF
)"
[ -n "$HOMEPFX_PYC_DIR" ] || fail "running an installed command left no bytecode"
: >"$HOMEPFX_PYC_DIR/foreign.txt"
HOMEPFX_FILES="$(python3 - "$HOMEPFX_MANIFEST" <<'PYEOF'
import json
import sys

for entry in json.load(open(sys.argv[1], encoding="utf-8"))["files"]:
    print(entry["path"])
PYEOF
)"
[ -n "$HOMEPFX_FILES" ] || fail "home-prefix manifest lists no files"
run env HOME="$HOMEPFX_HOME" bash "$INSTALL_SH" --uninstall --apply --yes --prefix "$HOMEPFX_HOME"
[ "$RC" -eq 0 ] || fail "home-prefix uninstall exit $RC: $OUT"
[ -f "$HOMEPFX_HOME/projects/app/__pycache__/mod.cpython-311.pyc" ] || fail "uninstall removed a foreign __pycache__ file"
[ -d "$HOMEPFX_HOME/keep-empty" ] || fail "uninstall removed an unrelated empty directory"
[ -f "$HOMEPFX_PYC_DIR/foreign.txt" ] || fail "uninstall removed a foreign file in an installed __pycache__"
[ -d "$HOMEPFX_HOME" ] || fail "uninstall removed the prefix itself"
while IFS= read -r rel; do
    [ ! -e "$HOMEPFX_HOME/$rel" ] || fail "uninstall left manifest file $rel"
done <<<"$HOMEPFX_FILES"
leftover_pyc="$(find "$HOMEPFX_HOME/lib" -name '*.pyc' 2>/dev/null || true)"
[ -z "$leftover_pyc" ] || fail "uninstall left bytecode of manifest modules: $leftover_pyc"
[ ! -d "$HOMEPFX_HOME/libexec" ] || fail "uninstall left an emptied installed directory"
rm -rf "$HOMEPFX_HOME"

JOBS_HOME="$(mktemp -d)"
FAKE_LAUNCHD="$JOBS_HOME/fake-launchd"
make_fake_launchctl "$FAKE_LAUNCHD"
JOB_PREFIX="com.rigops.smoke$$"
JOBS_MANIFEST="$JOBS_HOME/.local/share/rigops/install.manifest.json"
JOBS_AGENTS="$JOBS_HOME/Library/LaunchAgents"
jobs_install() {
    run env HOME="$JOBS_HOME" PATH="$FAKE_LAUNCHD/bin:$PATH" FAKE_LAUNCHD="$FAKE_LAUNCHD" \
        RIGOPS_INSTALL_LABEL_PREFIX="$JOB_PREFIX" bash "$INSTALL_SH" "$@"
}

echo "smoke: a failed job load keeps every job already placed in the manifest"
FAKE_LAUNCHD_FAIL="$JOB_PREFIX.ledger" jobs_install --apply --jobs doctor,ledger --yes
[ "$RC" -eq 1 ] || fail "failing ledger load expected exit 1, got $RC: $OUT"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.doctor" ] || fail "doctor not loaded before the ledger failure"
labels="$(manifest_labels "$JOBS_MANIFEST")"
for job in doctor ledger; do
    case "$labels" in
        *"$JOB_PREFIX.$job"*) : ;;
        *) fail "manifest lost $job after a failed load: $labels" ;;
    esac
done
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after failed load exit $RC: $OUT"
[ -z "$(ls "$FAKE_LAUNCHD/loaded")" ] || fail "uninstall left jobs loaded: $(ls "$FAKE_LAUNCHD/loaded")"
[ -z "$(ls "$JOBS_AGENTS")" ] || fail "uninstall left plists: $(ls "$JOBS_AGENTS")"

echo "smoke: uninstall unloads a job an interrupted install loaded in the user domain"
FAKE_LAUNCHD_FAIL_DOMAIN=gui FAKE_LAUNCHD_LOCK="$JOBS_HOME/.local/share/rigops" \
    jobs_install --apply --jobs doctor --yes
chmod u+w "$JOBS_HOME/.local/share/rigops"
[ "$RC" -ne 0 ] || fail "install interrupted after the load exited 0: $OUT"
[ "$(cat "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.doctor" 2>/dev/null)" = "user/$(id -u)" ] \
    || fail "doctor not loaded in the user domain before the interruption"
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after the interrupted install exit $RC: $OUT"
[ -z "$(ls "$FAKE_LAUNCHD/loaded")" ] || fail "uninstall left the user-domain job loaded: $(ls "$FAKE_LAUNCHD/loaded")"

echo "smoke: --jobs unloads a job it drops"
jobs_install --apply --jobs doctor,ledger --yes
[ "$RC" -eq 0 ] || fail "two-job install exit $RC: $OUT"
jobs_install --jobs doctor
[ "$RC" -eq 0 ] || fail "drop plan exit $RC: $OUT"
case "$OUT" in
    *"unload jobs dropped"*"($JOB_PREFIX.ledger)"*) : ;;
    *) fail "drop plan does not list the ledger unload: $OUT" ;;
esac
case "$OUT" in
    *"removed  "*) fail "drop plan reports a removal it did not make: $OUT" ;;
    *"would remove"*"($JOB_PREFIX.ledger)"*) : ;;
    *) fail "drop plan does not say it would remove the ledger plist: $OUT" ;;
esac
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.ledger" ] || fail "drop plan unloaded ledger"
jobs_install --apply --jobs doctor --yes
[ "$RC" -eq 0 ] || fail "drop apply exit $RC: $OUT"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.ledger" ] && fail "dropped ledger job still loaded"
[ -f "$JOBS_AGENTS/$JOB_PREFIX.ledger.plist" ] && fail "dropped ledger plist still present"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.doctor" ] || fail "kept doctor job not loaded"
[ "$(manifest_labels "$JOBS_MANIFEST")" = "$JOB_PREFIX.doctor" ] \
    || fail "manifest after drop: $(manifest_labels "$JOBS_MANIFEST")"
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after drop exit $RC: $OUT"

echo "smoke: runs that skip jobs keep the loaded jobs in the manifest"
jobs_install --apply --jobs doctor --yes
[ "$RC" -eq 0 ] || fail "doctor install exit $RC: $OUT"
for skip_flag in --no-jobs --plugin-only; do
    jobs_install --apply "$skip_flag" --yes
    [ "$RC" -eq 0 ] || fail "$skip_flag re-install exit $RC: $OUT"
    [ "$(manifest_labels "$JOBS_MANIFEST")" = "$JOB_PREFIX.doctor" ] \
        || fail "$skip_flag run dropped the loaded doctor job from the manifest"
done
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after skipped-jobs runs exit $RC: $OUT"
[ -z "$(ls "$FAKE_LAUNCHD/loaded")" ] || fail "doctor still loaded after uninstall"

echo "smoke: a bare install schedules only the default jobs"
jobs_install --apply --yes
[ "$RC" -eq 0 ] || fail "bare jobs install exit $RC: $OUT"
[ "$(manifest_labels "$JOBS_MANIFEST" | tr '\n' ' ')" = "$JOB_PREFIX.doctor $JOB_PREFIX.ledger " ] \
    || fail "bare install job set: $(manifest_labels "$JOBS_MANIFEST")"
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after bare install exit $RC: $OUT"

echo "smoke: a bare re-run keeps an opt-in job"
jobs_install --apply --jobs doctor,janitor --yes
[ "$RC" -eq 0 ] || fail "opt-in janitor install exit $RC: $OUT"
jobs_install --apply --yes
[ "$RC" -eq 0 ] || fail "bare re-run over janitor exit $RC: $OUT"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.janitor" ] || fail "bare re-run unloaded the janitor job"
[ "$(manifest_labels "$JOBS_MANIFEST" | tr '\n' ' ')" = "$JOB_PREFIX.doctor $JOB_PREFIX.janitor " ] \
    || fail "bare re-run job set: $(manifest_labels "$JOBS_MANIFEST")"
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after janitor re-run exit $RC: $OUT"

echo "smoke: a declined purge leaves no removed job for the next install"
jobs_install --apply --jobs doctor,janitor --yes
[ "$RC" -eq 0 ] || fail "opt-in janitor install exit $RC: $OUT"
jobs_install --uninstall --apply --purge <<<n
[ "$RC" -eq 0 ] || fail "declined purge exit $RC: $OUT"
[ -f "$JOBS_MANIFEST" ] || fail "declined purge removed the manifest"
[ -z "$(manifest_labels "$JOBS_MANIFEST")" ] \
    || fail "declined purge kept removed jobs: $(manifest_labels "$JOBS_MANIFEST")"
jobs_install --apply --yes
[ "$RC" -eq 0 ] || fail "install after declined purge exit $RC: $OUT"
[ "$(manifest_labels "$JOBS_MANIFEST" | tr '\n' ' ')" = "$JOB_PREFIX.doctor $JOB_PREFIX.ledger " ] \
    || fail "install after declined purge job set: $(manifest_labels "$JOBS_MANIFEST")"
jobs_install --uninstall --apply --purge --yes
[ "$RC" -eq 0 ] || fail "purge after declined purge exit $RC: $OUT"

echo "smoke: --jobs disables a dropped plist the user edited"
jobs_install --apply --jobs doctor,ledger --yes
[ "$RC" -eq 0 ] || fail "two-job install before the edit exit $RC: $OUT"
printf '<!-- local edit -->\n' >>"$JOBS_AGENTS/$JOB_PREFIX.ledger.plist"
jobs_install --apply --jobs doctor --yes
[ "$RC" -eq 0 ] || fail "drop of an edited plist exit $RC: $OUT"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.ledger" ] && fail "dropped edited ledger job still loaded"
[ -f "$JOBS_AGENTS/$JOB_PREFIX.ledger.plist" ] && fail "dropped edited ledger plist left where launchd loads it"
[ -f "$JOBS_AGENTS/$JOB_PREFIX.ledger.plist.rigops-disabled" ] || fail "dropped edited ledger plist was not kept as .rigops-disabled"
rm -f "$JOBS_AGENTS/$JOB_PREFIX.ledger.plist.rigops-disabled"
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after the edited drop exit $RC: $OUT"

# Writes LaunchAgent $1 whose ProgramArguments are the remaining args.
hand_agent() {
    local path="$1" label arg
    shift
    label="$(basename "$path" .plist)"
    mkdir -p "$(dirname "$path")"
    {
        printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>\n'
        printf '<key>Label</key><string>%s</string>\n' "$label"
        printf '<key>ProgramArguments</key><array>\n'
        for arg in "$@"; do printf '<string>%s</string>\n' "$arg"; done
        printf '</array></dict></plist>\n'
    } >"$path"
}

echo "smoke: install skips a job another LaunchAgent already runs"
HAND_JANITOR="$JOBS_AGENTS/com.example.own-janitor.plist"
hand_agent "$HAND_JANITOR" /usr/local/bin/rigops janitor --apply
jobs_install --jobs doctor,janitor
[ "$RC" -eq 0 ] || fail "plan with a hand-made janitor exit $RC: $OUT"
case "$OUT" in
    *"$JOB_PREFIX.janitor"*"scheduled by $HAND_JANITOR"*) : ;;
    *) fail "plan does not name the plist already running janitor: $OUT" ;;
esac
jobs_install --apply --jobs doctor,janitor --yes
[ "$RC" -eq 0 ] || fail "install with a hand-made janitor exit $RC: $OUT"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.janitor" ] && fail "janitor scheduled a second time"
[ -f "$JOBS_AGENTS/$JOB_PREFIX.janitor.plist" ] && fail "janitor plist placed beside the hand-made one"
[ "$(manifest_labels "$JOBS_MANIFEST")" = "$JOB_PREFIX.doctor" ] \
    || fail "manifest with a hand-made janitor: $(manifest_labels "$JOBS_MANIFEST")"
/bin/rm -f "$HAND_JANITOR"
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after the skipped janitor exit $RC: $OUT"

echo "smoke: install unloads its own job once another LaunchAgent runs it"
jobs_install --apply --jobs doctor,reap --yes
[ "$RC" -eq 0 ] || fail "doctor and reap install exit $RC: $OUT"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.reap" ] || fail "reap not loaded before the hand-made one"
HAND_REAP="$JOBS_AGENTS/com.example.own-reap.plist"
hand_agent "$HAND_REAP" /usr/bin/python3 /opt/elsewhere/libexec/rigops-reap --apply
jobs_install --apply --yes
[ "$RC" -eq 0 ] || fail "re-run with a hand-made reap exit $RC: $OUT"
[ -f "$FAKE_LAUNCHD/loaded/$JOB_PREFIX.reap" ] && fail "reap still loaded beside the hand-made one"
[ -f "$JOBS_AGENTS/$JOB_PREFIX.reap.plist" ] && fail "own reap plist left beside the hand-made one"
[ -f "$HAND_REAP" ] || fail "install touched the hand-made reap plist"
[ "$(manifest_labels "$JOBS_MANIFEST")" = "$JOB_PREFIX.doctor" ] \
    || fail "manifest after the hand-made reap: $(manifest_labels "$JOBS_MANIFEST")"
/bin/rm -f "$HAND_REAP"
jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "uninstall after the hand-made reap exit $RC: $OUT"

echo "smoke: rendered job plists parse as well-formed plists"
ALL_JOBS="doctor ledger janitor reap"
jobs_install --apply --jobs "$(printf '%s' "$ALL_JOBS" | tr ' ' ',')" --yes
[ "$RC" -eq 0 ] || fail "all-jobs install exit $RC: $OUT"
for job in $ALL_JOBS; do
    python3 - "$JOBS_AGENTS/$JOB_PREFIX.$job.plist" <<'PYEOF' || fail "$job plist does not parse with plistlib"
import plistlib
import sys

with open(sys.argv[1], "rb") as fh:
    plistlib.load(fh)
PYEOF
done

echo "smoke: rendered job PATH reaches homebrew tools"
for job in $ALL_JOBS; do
    python3 - "$JOBS_AGENTS/$JOB_PREFIX.$job.plist" <<'PYEOF' || fail "$job plist PATH misses a homebrew prefix"
import plistlib
import sys

with open(sys.argv[1], "rb") as fh:
    path = plistlib.load(fh)["EnvironmentVariables"]["PATH"].split(":")
sys.exit(0 if "/opt/homebrew/bin" in path and "/usr/local/bin" in path else 1)
PYEOF
done

echo "smoke: each job plist runs its own installed command"
for job in $ALL_JOBS; do
    python3 - "$JOBS_AGENTS/$JOB_PREFIX.$job.plist" "$JOBS_HOME/.local/share/rigops/libexec/rigops-$job" \
        <<'PYEOF' || fail "$job plist does not run the installed rigops-$job"
import os
import plistlib
import sys

with open(sys.argv[1], "rb") as fh:
    args = plistlib.load(fh)["ProgramArguments"]
sys.exit(0 if args[1] == sys.argv[2] and os.path.isfile(args[1]) else 1)
PYEOF
done
# Prints the doctor plist's ProgramArguments after the interpreter, one per line.
doctor_args() {
    python3 - "$JOBS_AGENTS/$JOB_PREFIX.doctor.plist" <<'PYEOF'
import plistlib
import sys

with open(sys.argv[1], "rb") as fh:
    print("\n".join(plistlib.load(fh)["ProgramArguments"][2:]))
PYEOF
}

echo "smoke: the doctor job is report-only unless --heal is passed"
[ "$(doctor_args)" = "--exit-zero" ] || fail "default doctor plist arguments: $(doctor_args)"
jobs_install --apply --jobs doctor,ledger --yes
[ "$RC" -eq 0 ] || fail "doctor,ledger install exit $RC: $OUT"
grep -q -e "--exit-zero" "$JOBS_AGENTS/$JOB_PREFIX.ledger.plist" && fail "ledger plist carries --exit-zero"
case "$OUT" in
    *"doctor job: report-only"*) : ;;
    *) fail "apply output does not state the doctor mode: $OUT" ;;
esac
jobs_install --apply --heal --jobs doctor --yes
[ "$RC" -eq 0 ] || fail "--heal install exit $RC: $OUT"
[ "$(doctor_args)" = "--exit-zero
--heal" ] || fail "--heal doctor plist arguments: $(doctor_args)"
case "$OUT" in
    *"doctor job: self-heal"*) : ;;
    *) fail "--heal apply output does not state the doctor mode: $OUT" ;;
esac
jobs_install --apply --jobs doctor --yes
[ "$RC" -eq 0 ] || fail "install after --heal exit $RC: $OUT"
[ "$(doctor_args)" = "--exit-zero" ] || fail "--heal was remembered by a later run: $(doctor_args)"
jobs_install --jobs doctor
case "$OUT" in
    *"doctor job: report-only"*"--heal"*) : ;;
    *) fail "plan output does not state the doctor mode: $OUT" ;;
esac
before_heal="$(snapshot_tree "$JOBS_HOME")"
for conflict in "--no-jobs" "--plugin-only" "--jobs ledger" "--uninstall"; do
    # shellcheck disable=SC2086
    jobs_install --apply --heal $conflict --yes
    [ "$RC" -eq 2 ] || fail "--heal $conflict expected exit 2, got $RC: $OUT"
    case "$OUT" in
        *"--heal"*) : ;;
        *) fail "--heal $conflict missing usage error: $OUT" ;;
    esac
done
[ "$before_heal" = "$(snapshot_tree "$JOBS_HOME")" ] || fail "a rejected --heal run wrote to the home"

jobs_install --uninstall --apply --yes
[ "$RC" -eq 0 ] || fail "all-jobs uninstall exit $RC: $OUT"

rm -rf "$JOBS_HOME"
JOBS_HOME=""

echo "smoke: OK"
