#!/bin/bash
# Runs the README quickstart's bash fences as written, in a temp HOME, with the
# built wheel in place of the index. Loads real launchd jobs on macOS, so it
# refuses anywhere but GitHub Actions.
set -eu

if [ "${GITHUB_ACTIONS:-}" != "true" ] || [ -z "${RUNNER_TEMP:-}" ]; then
    echo "refusing: real launchd; runs only on GitHub Actions" >&2
    exit 2
fi
if [ $# -ne 2 ]; then
    echo "usage: quickstart.sh README WHEEL" >&2
    exit 2
fi
here="$(cd "$(dirname "$0")" && pwd)"
readme="$1"
wheel="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
[ -f "$wheel" ] || { echo "no such wheel: $wheel" >&2; exit 2; }

die() {
    echo "FAIL $1" >&2
    exit 1
}
ok() { echo "ok   $1"; }

work="$(mktemp -d "$RUNNER_TEMP/quickstart.XXXXXX")"
python3 -I "$here/quickstart-script.py" "$readme" "$wheel" > "$work/quickstart.sh"

export HOME="$work/home"
mkdir -p "$HOME/.claude/projects"
unset PYTHONPATH XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME XDG_CACHE_HOME RIGOPS_CONFIG RIGOPS_STATE_DIR
unset PIPX_HOME PIPX_BIN_DIR PIPX_LOCAL_VENVS PIPX_SHARED_LIBS PIPX_VENV_CACHEDIR PIPX_TRASH_DIR
PATH="$(pipx environment --value PIPX_BIN_DIR):$PATH"
export PATH

macos=false
[ "$(uname -s)" = "Darwin" ] && macos=true
cleanup() {
    if $macos && command -v rigops >/dev/null 2>&1; then
        rigops jobs uninstall --apply || true
    fi
}
trap cleanup EXIT

bash -eux -o pipefail "$work/quickstart.sh" || die "a quickstart command failed"
ok "every quickstart command exited 0"

expected="$(python3 -I -c '
import re, sys, zipfile
zf = zipfile.ZipFile(sys.argv[1])
name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
print(re.search(r"^Version: (.*)$", zf.read(name).decode(), re.M).group(1))
' "$wheel")"
got="$(rigops version)"
[ "$got" = "$expected" ] || die "rigops version is '$got', wanted '$expected'"
ok "installed the built wheel, $expected"

if $macos; then
    status="$(rigops jobs status)" || die "jobs status: $status"
    for label in doctor ledger; do
        printf '%s\n' "$status" | grep -q "com.rigops.$label .*loaded=yes" || die "$label not loaded: $status"
    done
    ok "doctor and ledger loaded"
else
    lines="$(rigops jobs install --apply)" || die "jobs install --apply: $lines"
    [ "$(printf '%s\n' "$lines" | grep -Ec '(doctor|ledger)\.cron\.log')" -eq 2 ] \
        || die "jobs install --apply did not print two crontab lines: $lines"
    ok "two crontab lines printed"
fi
