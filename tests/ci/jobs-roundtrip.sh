#!/bin/bash
# Installs the previous wheel, schedules the jobs, upgrades to the new wheel and
# removes the jobs, against the runner's real launchd. Refuses anywhere else.
set -eu

if [ "${GITHUB_ACTIONS:-}" != "true" ] || [ -z "${RUNNER_TEMP:-}" ]; then
    echo "refusing: real launchd; runs only on GitHub Actions" >&2
    exit 2
fi
if [ $# -ne 2 ]; then
    echo "usage: jobs-roundtrip.sh PREVIOUS_WHEEL NEW_WHEEL" >&2
    exit 2
fi
previous="$1"
new="$2"

die() {
    echo "FAIL $1" >&2
    exit 1
}
ok() { echo "ok   $1"; }

bin_dir="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")"
PATH="$bin_dir:$PATH"
export PATH

expected="$(python3 -I -c '
import re, sys, zipfile
zf = zipfile.ZipFile(sys.argv[1])
name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
print(re.search(r"^Version: (.*)$", zf.read(name).decode(), re.M).group(1))
' "$new")"
agents="$HOME/Library/LaunchAgents"
doctor="$agents/com.rigops.doctor.plist"
ledger="$agents/com.rigops.ledger.plist"

pipx install "$previous"
rigops jobs install --apply || die "install with the previous wheel"
ok "previous wheel scheduled the jobs"

status="$(rigops jobs status)" || die "status after install: $status"
for label in doctor ledger; do
    printf '%s\n' "$status" | grep -q "com.rigops.$label .*loaded=yes" || die "$label not loaded: $status"
done
launchctl list com.rigops.doctor >/dev/null || die "launchctl does not list com.rigops.doctor"
ok "both jobs loaded"

before="$(shasum -a 256 "$doctor" "$ledger")"

pipx install --force "$new"
got="$(rigops version)"
[ "$got" = "$expected" ] || die "version after upgrade is '$got', wanted '$expected'"
ok "upgraded to $expected"

after="$(shasum -a 256 "$doctor" "$ledger")"
[ "$before" = "$after" ] || die "plists changed in the upgrade"
ok "plists byte-identical"

status="$(rigops jobs status)" || die "status after upgrade: $status"
for label in doctor ledger; do
    printf '%s\n' "$status" | grep -q "com.rigops.$label .*loaded=yes" || die "$label not loaded after upgrade: $status"
done
ok "jobs still loaded"

again="$(rigops jobs install --apply)" || die "reinstall: $again"
[ "$(printf '%s\n' "$again" | grep -Ec '^(unchanged|updated) ')" -eq 2 ] || die "reinstall did not report both jobs: $again"
ok "reinstall reports unchanged or updated"

rigops jobs uninstall --apply || die "uninstall"
if ls "$agents"/com.rigops.*.plist >/dev/null 2>&1; then
    die "a com.rigops plist is left"
fi
if launchctl list com.rigops.doctor >/dev/null 2>&1; then
    die "com.rigops.doctor is still loaded"
fi
ok "uninstalled, nothing left"
