#!/bin/bash
# Runs a built wheel under uvx, pipx run and a plain venv in a temp HOME and
# checks that none of them can schedule jobs. Never touches the real home.
set -eu

if [ $# -ne 1 ]; then
    echo "usage: runners.sh WHEEL" >&2
    exit 2
fi
wheel="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
[ -f "$wheel" ] || { echo "no such wheel: $wheel" >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export HOME="$work/home"
mkdir -p "$HOME" "$work/fake"
unset PYTHONPATH XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME XDG_CACHE_HOME

launchctl_log="$work/launchctl.log"
: > "$launchctl_log"
cat > "$work/fake/launchctl" <<FAKE
#!/bin/sh
echo "\$*" >> "$launchctl_log"
exit 1
FAKE
chmod +x "$work/fake/launchctl"
PATH="$work/fake:$PATH"
export PATH

failed=0
pass() { echo "ok   $1"; }
fail() { echo "FAIL $1"; failed=1; }

plists() { find "$HOME/Library/LaunchAgents" -name '*.plist' 2>/dev/null || true; }

expected="$(python3 -I -c '
import re, sys, zipfile
zf = zipfile.ZipFile(sys.argv[1])
name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
print(re.search(r"^Version: (.*)$", zf.read(name).decode(), re.M).group(1))
' "$wheel")"

missing() {
    if [ -n "${CI:-}" ]; then
        fail "$1 is not installed"
    else
        echo "skip $1 is not installed"
    fi
}

refuses() {
    label="$1"
    shift
    status=0
    out="$("$@" jobs install --apply 2>&1)" || status=$?
    if [ "$status" -eq 2 ]; then
        pass "$label: jobs install --apply exits 2"
    else
        fail "$label: jobs install --apply exited $status: $out"
    fi
    if [ -z "$(plists)" ]; then
        pass "$label: no plist written"
    else
        fail "$label: plist written"
    fi
}

reports_version() {
    label="$1"
    shift
    got="$("$@" version 2>/dev/null)" || true
    if [ "$got" = "$expected" ]; then
        pass "$label: version $expected"
    else
        fail "$label: version printed '$got', wanted '$expected'"
    fi
}

if command -v uvx >/dev/null 2>&1; then
    reports_version uvx uvx --from "$wheel" rigops
    refuses uvx uvx --from "$wheel" rigops
else
    missing uvx
fi

if command -v pipx >/dev/null 2>&1; then
    reports_version "pipx run" pipx run --spec "$wheel" rigops
    refuses "pipx run" pipx run --spec "$wheel" rigops
else
    missing pipx
fi

venv="$work/venv"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --no-index --no-deps "$wheel"
reports_version venv "$venv/bin/rigops"
refuses "venv, empty PATH" env PATH= "$venv/bin/rigops"
if "$venv/bin/python" -c '
import importlib.resources as r
t = r.files("rigops") / "templates"
names = {p.name for p in (t / "launchd").iterdir()}
assert {"doctor.plist.in", "ledger.plist.in"} <= names, names
assert (t / "cron" / "crontab.example").is_file()
'; then
    pass "venv: templates shipped"
else
    fail "venv: templates missing"
fi

if [ -s "$launchctl_log" ]; then
    fail "launchctl was called: $(tr '\n' ';' < "$launchctl_log")"
else
    pass "launchctl never called"
fi

exit "$failed"
