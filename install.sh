#!/usr/bin/env bash
# rigops installer.
#
# Plan-by-default: run with no flags to preview every action with fully
# resolved paths; nothing is written until you pass --apply. See
# `install.sh --help` for the full flag list.
#
# Env:
#   RIGOPS_INSTALL_LABEL_PREFIX   launchd label prefix for installed jobs
#                                 (default: com.rigops). Override so smoke
#                                 tests never collide with a developer's
#                                 real jobs.
set -euo pipefail

resolve_dir() {
    local target="$1" link
    while [ -L "$target" ]; do
        link="$(readlink "$target")"
        case "$link" in
            /*) target="$link" ;;
            *) target="$(dirname "$target")/$link" ;;
        esac
    done
    cd -P "$(dirname "$target")" && pwd
}

usage() {
    cat <<'EOF'
usage: install.sh [options]

Plan-by-default: with no --apply, prints what would happen and changes
nothing. Pass --apply to actually install.

  --apply         perform the actions (default: plan only)
  --plugin-only   install tree + symlink only; skip jobs, templates,
                  statusline; still scaffold config/registry
  --no-jobs       skip launchd job installation
  --jobs a,b      comma list of jobs to install (available: doctor, ledger)
  --prefix DIR    install prefix (default: $HOME/.local/share/rigops)
  --python PATH   python3 interpreter to use (default: first on PATH)
  --statusline    wire the plugin statusline into ~/.claude/settings.json
  --uninstall     remove a previous install, using its manifest
  --purge         with --uninstall, also delete config/state/log dirs
  --yes           skip confirmation prompts
  --home DIR      override home for every derived path (testing)
  -h, --help      show this help

Env:
  RIGOPS_INSTALL_LABEL_PREFIX   launchd label prefix (default: com.rigops)
EOF
}

ROOT_DIR="$(resolve_dir "$0")"

APPLY=0
PLUGIN_ONLY=0
NO_JOBS=0
JOBS_FILTER=""
PREFIX=""
PYTHON_BIN=""
STATUSLINE=0
UNINSTALL=0
PURGE=0
YES=0
HOME_OVERRIDE=""
STEP_N=0
COUNT_INSTALLED=0
COUNT_UPDATED=0
COUNT_UNCHANGED=0
COUNT_JOBS_LOADED=0
CONFIG_DIR_MANAGED=1
STATE_DIR_MANAGED=1
EXTRA_ENV_RIGOPS_CONFIG=""
EXTRA_ENV_RIGOPS_STATE_DIR=""
EXTRA_ENV_XDG_CONFIG_HOME=""
EXTRA_ENV_XDG_STATE_HOME=""
PURGE_REFUSED=0
PURGE_REFUSED_PATHS=()
INSTALL_VERSION=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --apply)
            APPLY=1
            shift
            ;;
        --plugin-only)
            PLUGIN_ONLY=1
            shift
            ;;
        --no-jobs)
            NO_JOBS=1
            shift
            ;;
        --jobs)
            [ "$#" -ge 2 ] || { echo "error: --jobs requires an argument" >&2; exit 2; }
            JOBS_FILTER="$2"
            shift 2
            ;;
        --prefix)
            [ "$#" -ge 2 ] || { echo "error: --prefix requires an argument" >&2; exit 2; }
            PREFIX="$2"
            shift 2
            ;;
        --python)
            [ "$#" -ge 2 ] || { echo "error: --python requires an argument" >&2; exit 2; }
            PYTHON_BIN="$2"
            shift 2
            ;;
        --statusline)
            STATUSLINE=1
            shift
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        --purge)
            PURGE=1
            shift
            ;;
        --yes)
            YES=1
            shift
            ;;
        --home)
            [ "$#" -ge 2 ] || { echo "error: --home requires an argument" >&2; exit 2; }
            HOME_OVERRIDE="$2"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown flag: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ "$PURGE" -eq 1 ] && [ "$UNINSTALL" -ne 1 ]; then
    echo "error: --purge requires --uninstall" >&2
    exit 2
fi

if [ "$NO_JOBS" -eq 1 ] && [ -n "$JOBS_FILTER" ]; then
    echo "error: --jobs conflicts with --no-jobs" >&2
    exit 2
fi

if [ "$PLUGIN_ONLY" -eq 1 ] && [ "$STATUSLINE" -eq 1 ]; then
    echo "error: --plugin-only conflicts with --statusline" >&2
    exit 2
fi

# Jobs shipped by this checkout. --jobs filters this set; naming anything
# else is a usage error, not a silent no-op.
AVAILABLE_JOBS=(doctor ledger)
SELECTED_JOBS=()
if [ -n "$JOBS_FILTER" ]; then
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        found=0
        for avail in "${AVAILABLE_JOBS[@]}"; do
            [ "$avail" = "$name" ] && found=1
        done
        if [ "$found" -eq 0 ]; then
            echo "error: unknown job '$name' (available: ${AVAILABLE_JOBS[*]})" >&2
            exit 2
        fi
        SELECTED_JOBS+=("$name")
    done <<EOF
$(printf '%s' "$JOBS_FILTER" | tr ',' '\n')
EOF
else
    SELECTED_JOBS=("${AVAILABLE_JOBS[@]}")
fi

if [ -n "$JOBS_FILTER" ] && [ "${#SELECTED_JOBS[@]}" -eq 0 ]; then
    echo "error: --jobs produced an empty job list (available: ${AVAILABLE_JOBS[*]})" >&2
    exit 2
fi

# Every path below derives from EFFECTIVE_HOME, never from this machine's
# real $HOME directly, so --home can retarget the whole install (smoke
# tests use a fresh $HOME instead; --home exists for the rarer case of
# installing into a different account's home).
if [ -n "$HOME_OVERRIDE" ]; then
    EFFECTIVE_HOME="$HOME_OVERRIDE"
    if [ -n "${XDG_CONFIG_HOME:-}" ] || [ -n "${XDG_STATE_HOME:-}" ]; then
        echo "warning: --home overrides XDG_CONFIG_HOME/XDG_STATE_HOME for this run" >&2
    fi
    CONFIG_DIR="$EFFECTIVE_HOME/.config/rigops"
    STATE_DIR="$EFFECTIVE_HOME/.local/state/rigops"
else
    EFFECTIVE_HOME="$HOME"
    # RIGOPS_CONFIG/RIGOPS_STATE_DIR point outside any dir this installer
    # created, so they're never purge-eligible: MANIFEST_*_DIR records ""
    # for them (see purge_safe) even though CONFIG_DIR/STATE_DIR above
    # still resolve normally so the config scaffold step keeps working.
    if [ -n "${RIGOPS_CONFIG:-}" ]; then
        CONFIG_DIR="$(dirname "$RIGOPS_CONFIG")"
        CONFIG_DIR_MANAGED=0
        EXTRA_ENV_RIGOPS_CONFIG="$RIGOPS_CONFIG"
    else
        CONFIG_DIR="${XDG_CONFIG_HOME:-$EFFECTIVE_HOME/.config}/rigops"
        [ -n "${XDG_CONFIG_HOME:-}" ] && EXTRA_ENV_XDG_CONFIG_HOME="$XDG_CONFIG_HOME"
    fi
    if [ -n "${RIGOPS_STATE_DIR:-}" ]; then
        STATE_DIR="$RIGOPS_STATE_DIR"
        STATE_DIR_MANAGED=0
        EXTRA_ENV_RIGOPS_STATE_DIR="$RIGOPS_STATE_DIR"
    else
        STATE_DIR="${XDG_STATE_HOME:-$EFFECTIVE_HOME/.local/state}/rigops"
        [ -n "${XDG_STATE_HOME:-}" ] && EXTRA_ENV_XDG_STATE_HOME="$XDG_STATE_HOME"
    fi
fi

MANIFEST_CONFIG_DIR="$CONFIG_DIR"
[ "$CONFIG_DIR_MANAGED" -eq 1 ] || MANIFEST_CONFIG_DIR=""
MANIFEST_STATE_DIR="$STATE_DIR"
[ "$STATE_DIR_MANAGED" -eq 1 ] || MANIFEST_STATE_DIR=""

PREFIX="${PREFIX:-$EFFECTIVE_HOME/.local/share/rigops}"
LOG_DIR="$EFFECTIVE_HOME/Library/Logs/rigops"
LAUNCH_AGENTS_DIR="$EFFECTIVE_HOME/Library/LaunchAgents"
BIN_LINK="$EFFECTIVE_HOME/.local/bin/rigops"
CLAUDE_SETTINGS="$EFFECTIVE_HOME/.claude/settings.json"
LABEL_PREFIX="${RIGOPS_INSTALL_LABEL_PREFIX:-com.rigops}"

WORK_TMP="$(mktemp -d "${TMPDIR:-/tmp}/rigops-install.XXXXXX")"
trap 'rm -rf "$WORK_TMP"' EXIT
MANIFEST_TSV="$WORK_TMP/manifest-data.tsv"
: >"$MANIFEST_TSV"

if [ ! -d "$ROOT_DIR/bin" ] || [ ! -d "$ROOT_DIR/libexec" ] || [ ! -d "$ROOT_DIR/lib" ]; then
    echo "error: run install.sh from a rigops checkout (git clone ...)" >&2
    exit 1
fi

if command -v shasum >/dev/null 2>&1; then
    SHA_TOOL_NAME="shasum -a 256"
    sha256_file() { shasum -a 256 "$1" | awk '{print $1}'; }
elif command -v sha256sum >/dev/null 2>&1; then
    SHA_TOOL_NAME="sha256sum"
    sha256_file() { sha256sum "$1" | awk '{print $1}'; }
else
    echo "error: no sha256 tool found (need shasum or sha256sum)" >&2
    exit 1
fi

if [ -n "$PYTHON_BIN" ]; then
    PY_CANDIDATE="$PYTHON_BIN"
else
    PY_CANDIDATE="$(command -v python3 || true)"
fi
if [ -z "$PY_CANDIDATE" ]; then
    echo "error: no python3 found (pass --python PATH)" >&2
    exit 1
fi
PY_CHECK="$("$PY_CANDIDATE" -c 'import sys
print(sys.executable)
print("OK" if sys.version_info >= (3, 9) else "OLD")' 2>&1)" || {
    echo "error: failed to run python interpreter: $PY_CANDIDATE" >&2
    exit 1
}
PY_ABS="$(printf '%s\n' "$PY_CHECK" | sed -n '1p')"
PY_STATUS="$(printf '%s\n' "$PY_CHECK" | sed -n '2p')"
if [ "$PY_STATUS" != "OK" ]; then
    echo "error: python at $PY_ABS is older than 3.9 (need >=3.9)" >&2
    exit 1
fi

if [ "$UNINSTALL" -eq 0 ] && [ "$STATUSLINE" -eq 1 ] && [ "$PLUGIN_ONLY" -eq 0 ]; then
    if [ ! -f "$ROOT_DIR/plugin/statusline/rigops-statusline.sh" ]; then
        echo "error: statusline script ships with the plugin half; not present in this checkout" >&2
        exit 1
    fi
fi

JOBS_ENABLED=1
JOBS_SKIP_REASON=""
if [ "$PLUGIN_ONLY" -eq 1 ]; then
    JOBS_ENABLED=0
    JOBS_SKIP_REASON="--plugin-only"
elif [ -n "$JOBS_FILTER" ]; then
    JOBS_ENABLED=1
elif [ "$NO_JOBS" -eq 1 ]; then
    JOBS_ENABLED=0
    JOBS_SKIP_REASON="--no-jobs"
fi
if [ "$JOBS_ENABLED" -eq 1 ] && ! command -v launchctl >/dev/null 2>&1; then
    JOBS_ENABLED=0
    JOBS_SKIP_REASON="no launchctl"
    echo "note: launchd not found; jobs skipped (Linux: see templates/cron/crontab.example)"
fi

# Snapshot of the previous install (if any), read once up front so both
# plan and apply can flag "locally modified, overwriting" and recover a
# loaded job's domain without re-deriving it.
OLD_DATA_TSV="$WORK_TMP/old-manifest.tsv"
# Full (label, plist, sha256, domain) records for every job in the
# previous manifest, so a non-authoritative checkpoint write (see
# write_manifest) can forward them verbatim instead of wiping "jobs" to
# [] just because step_jobs hasn't run yet this invocation.
OLD_JOBS_TSV="$WORK_TMP/old-jobs.tsv"
: >"$OLD_DATA_TSV"
: >"$OLD_JOBS_TSV"
if [ "$UNINSTALL" -eq 0 ] && [ -f "$PREFIX/install.manifest.json" ]; then
    "$PY_ABS" - "$PREFIX/install.manifest.json" "$OLD_JOBS_TSV" >"$OLD_DATA_TSV" <<'PYEOF'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(0)

for f in data.get("files", []):
    p, h = f.get("path"), f.get("sha256")
    if p and h:
        print("FILE\t" + p + "\t" + h)
for j in data.get("jobs", []):
    label, domain = j.get("label"), j.get("domain")
    if label and domain:
        print("JOB\t" + label + "\t" + domain)

with open(sys.argv[2], "w", encoding="utf-8") as fh:
    for j in data.get("jobs", []):
        label, plist = j.get("label", ""), j.get("plist", "")
        sha, domain = j.get("sha256", ""), j.get("domain", "")
        if label and plist and sha and domain:
            fh.write(label + "\t" + plist + "\t" + sha + "\t" + domain + "\n")
PYEOF
fi

old_hash_for() {
    local rel="$1" tag p h
    while IFS=$'\t' read -r tag p h; do
        [ "$tag" = "FILE" ] || continue
        if [ "$p" = "$rel" ]; then
            printf '%s' "$h"
            return 0
        fi
    done <"$OLD_DATA_TSV"
    return 1
}

old_domain_for() {
    local label="$1" tag p d
    while IFS=$'\t' read -r tag p d; do
        [ "$tag" = "JOB" ] || continue
        if [ "$p" = "$label" ]; then
            printf '%s' "$d"
            return 0
        fi
    done <"$OLD_DATA_TSV"
    return 1
}

step_log() {
    STEP_N=$((STEP_N + 1))
    if [ "$APPLY" -eq 1 ]; then
        printf '%d. apply: %s\n' "$STEP_N" "$1"
    else
        printf '%d. plan: %s\n' "$STEP_N" "$1"
    fi
}

# Single per-file decision point: hash source vs. dest, classify
# installed/updated/unchanged, warn when a file changed outside of this
# installer since the last recorded install, and (only in --apply) write
# it. Every tree-copy call and the statusline copy share this one path.
copy_file_step() {
    local src="$1" dest="$2" rel="$3" exec_mode="$4"
    local src_hash dest_hash status old_hash locally_modified note tmp_dest

    src_hash="$(sha256_file "$src")"
    dest_hash=""
    if [ -e "$dest" ] && [ ! -L "$dest" ]; then
        dest_hash="$(sha256_file "$dest")"
    fi

    status="installed"
    locally_modified=0
    if [ -n "$dest_hash" ]; then
        if [ "$dest_hash" = "$src_hash" ]; then
            status="unchanged"
        else
            status="updated"
            old_hash="$(old_hash_for "$rel")" || old_hash=""
            if [ -n "$old_hash" ] && [ "$old_hash" != "$dest_hash" ]; then
                locally_modified=1
            fi
        fi
    fi

    case "$status" in
        installed) COUNT_INSTALLED=$((COUNT_INSTALLED + 1)) ;;
        updated) COUNT_UPDATED=$((COUNT_UPDATED + 1)) ;;
        unchanged) COUNT_UNCHANGED=$((COUNT_UNCHANGED + 1)) ;;
    esac

    note=""
    [ "$locally_modified" -eq 1 ] && note=" (locally modified, overwriting)"

    if [ "$APPLY" -eq 1 ]; then
        if [ "$status" != "unchanged" ]; then
            mkdir -p "$(dirname "$dest")"
            tmp_dest="${dest}.rigops-tmp.$$"
            cp "$src" "$tmp_dest" || { rm -f "$tmp_dest"; echo "error: failed to copy $src -> $tmp_dest" >&2; exit 1; }
            mv "$tmp_dest" "$dest" || { rm -f "$tmp_dest"; echo "error: failed to move $tmp_dest -> $dest" >&2; exit 1; }
        fi
        if [ "$exec_mode" = "1" ]; then
            chmod 0755 "$dest"
        else
            chmod 0644 "$dest"
        fi
        printf 'FILE\t%s\t%s\n' "$rel" "$src_hash" >>"$MANIFEST_TSV"
    fi

    printf '     %-9s %s%s\n' "$status" "$dest" "$note"
}

copy_tree() {
    local src_root="$1" dest_label="$2" exec_mode="$3" f rel
    [ -d "$src_root" ] || return 0
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        rel="${f#"$src_root"/}"
        copy_file_step "$f" "$PREFIX/$dest_label/$rel" "$dest_label/$rel" "$exec_mode"
    done < <(find "$src_root" -type f -not -path '*/__pycache__/*' -not -name '*.pyc' | sort)
}

copy_lib_py() {
    local f rel
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        rel="${f#"$ROOT_DIR"/lib/}"
        copy_file_step "$f" "$PREFIX/lib/$rel" "lib/$rel" 0
    done < <(find "$ROOT_DIR/lib" -type f -name '*.py' -not -path '*/__pycache__/*' | sort)
}

step_preflight() {
    step_log "preflight"
    printf '     payload dirs OK under %s\n' "$ROOT_DIR"
    printf '     python %s (>=3.9 OK)\n' "$PY_ABS"
    printf '     sha256 tool: %s\n' "$SHA_TOOL_NAME"
    if [ "$JOBS_ENABLED" -eq 1 ]; then
        printf '     launchd jobs enabled: %s\n' "${SELECTED_JOBS[*]}"
    else
        printf '     launchd jobs disabled: %s\n' "$JOBS_SKIP_REASON"
    fi
}

step_copy_tree() {
    step_log "copy tree -> $PREFIX"
    copy_tree "$ROOT_DIR/bin" "bin" 1
    copy_tree "$ROOT_DIR/libexec" "libexec" 1
    copy_lib_py
    if [ "$PLUGIN_ONLY" -eq 0 ]; then
        copy_tree "$ROOT_DIR/templates" "templates" 0
        if [ -d "$ROOT_DIR/plugin/statusline" ]; then
            copy_tree "$ROOT_DIR/plugin/statusline" "plugin/statusline" 1
        fi
    fi
    copy_file_step "$ROOT_DIR/LICENSE" "$PREFIX/LICENSE" "LICENSE" 0
}

do_symlink() {
    local target="$PREFIX/bin/rigops" current
    if [ -L "$BIN_LINK" ]; then
        current="$(readlink "$BIN_LINK")"
        if [ "$current" = "$target" ]; then
            printf '     unchanged %s\n' "$BIN_LINK"
        else
            if [ "$APPLY" -eq 1 ]; then
                rm -f "$BIN_LINK"
                ln -s "$target" "$BIN_LINK"
            fi
            printf '     relinked  %s (was -> %s)\n' "$BIN_LINK" "$current"
        fi
    elif [ -e "$BIN_LINK" ]; then
        echo "error: $BIN_LINK exists and is not a symlink; refusing to clobber" >&2
        exit 1
    else
        if [ "$APPLY" -eq 1 ]; then
            mkdir -p "$(dirname "$BIN_LINK")"
            ln -s "$target" "$BIN_LINK"
        fi
        printf '     installed %s\n' "$BIN_LINK"
    fi
    [ "$APPLY" -eq 1 ] && printf 'SYMLINK\t%s\t%s\n' "$BIN_LINK" "$target" >>"$MANIFEST_TSV"

    case ":$PATH:" in
        *":$EFFECTIVE_HOME/.local/bin:"*) : ;;
        *) echo "note: $EFFECTIVE_HOME/.local/bin is not on PATH" ;;
    esac
}

step_symlink() {
    step_log "symlink $BIN_LINK -> $PREFIX/bin/rigops"
    do_symlink
}

do_scaffold() {
    local src="$1" dest="$2" label="$3"
    if [ -e "$dest" ]; then
        printf '     unchanged %s (%s already exists)\n' "$dest" "$label"
    else
        if [ "$APPLY" -eq 1 ]; then
            mkdir -p "$(dirname "$dest")"
            cp "$src" "$dest"
        fi
        printf '     scaffolded %s\n' "$dest"
    fi
    if [ "$APPLY" -eq 1 ]; then
        printf 'SCAFFOLD\t%s\n' "$dest" >>"$MANIFEST_TSV"
    fi
}

step_scaffold() {
    step_log "scaffold config + registry under $CONFIG_DIR"
    do_scaffold "$ROOT_DIR/config/config.example.json" "$CONFIG_DIR/config.json" "config"
    do_scaffold "$ROOT_DIR/config/registry.example.md" "$CONFIG_DIR/registry.md" "registry"
}

step_statusline() {
    [ "$STATUSLINE" -eq 1 ] && [ "$PLUGIN_ONLY" -eq 0 ] || return 0
    step_log "wire statusLine into $CLAUDE_SETTINGS"
    local src="$ROOT_DIR/plugin/statusline/rigops-statusline.sh"
    local dest_rel="plugin/statusline/rigops-statusline.sh"
    copy_file_step "$src" "$PREFIX/$dest_rel" "$dest_rel" 1
    local mode="plan"
    [ "$APPLY" -eq 1 ] && mode="apply"
    "$PY_ABS" - "$CLAUDE_SETTINGS" "$PREFIX/$dest_rel" "$mode" <<'PYEOF'
import difflib
import json
import os
import sys
from datetime import datetime

settings_path, command_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]

if os.path.exists(settings_path):
    old_text = open(settings_path, encoding="utf-8").read()
    try:
        data = json.loads(old_text) if old_text.strip() else {}
    except ValueError as exc:
        print(f"error: {settings_path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print(f"error: {settings_path} must contain a JSON object", file=sys.stderr)
        sys.exit(1)
    existed = True
else:
    old_text = ""
    data = {}
    existed = False

data["statusLine"] = {"type": "command", "command": command_path}
new_text = json.dumps(data, indent=2, sort_keys=True) + "\n"

diff = difflib.unified_diff(
    old_text.splitlines(keepends=True),
    new_text.splitlines(keepends=True),
    fromfile=settings_path,
    tofile=settings_path,
)
for line in diff:
    sys.stdout.write("     " + line)
print()

if mode == "apply":
    if existed:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = settings_path + ".rigops-backup." + stamp
        with open(backup_path, "w", encoding="utf-8") as fh:
            fh.write(old_text)
    else:
        print(f"     note: {settings_path} did not exist; created minimal file with statusLine (no backup needed)")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
PYEOF
}

render_template() {
    local tmpl="$1" out="$2" label="$3" py="$4" prefix="$5" home="$6" logdir="$7"
    local extra_config="$8" extra_state="$9" extra_xdg_config="${10}" extra_xdg_state="${11}"
    "$PY_ABS" - "$tmpl" "$out" "$label" "$py" "$prefix" "$home" "$logdir" \
        "$extra_config" "$extra_state" "$extra_xdg_config" "$extra_xdg_state" <<'PYEOF'
import os
import re
import sys

(tmpl_path, out_path, label, python_bin, prefix, home, log_dir,
 extra_config, extra_state, extra_xdg_config, extra_xdg_state) = sys.argv[1:12]


def xml_escape(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


extra_env_pairs = [
    ("RIGOPS_CONFIG", extra_config),
    ("RIGOPS_STATE_DIR", extra_state),
    ("XDG_CONFIG_HOME", extra_xdg_config),
    ("XDG_STATE_HOME", extra_xdg_state),
]
extra_env_lines = []
for name, value in extra_env_pairs:
    if not value:
        continue
    extra_env_lines.append("\t\t<key>" + name + "</key>")
    extra_env_lines.append("\t\t<string>" + xml_escape(value) + "</string>")

text = open(tmpl_path, encoding="utf-8").read()
replacements = {
    "@LABEL@": xml_escape(label),
    "@PYTHON@": xml_escape(python_bin),
    "@PREFIX@": xml_escape(prefix),
    "@HOME@": xml_escape(home),
    "@LOG_DIR@": xml_escape(log_dir),
    "@EXTRA_ENV@": "\n".join(extra_env_lines),
}
for placeholder, value in replacements.items():
    text = text.replace(placeholder, value)

leftover = sorted(set(re.findall(r"@[A-Z_]+@", text)))
if leftover:
    sys.stderr.write(
        "error: unresolved placeholder(s) in rendered template: " + ", ".join(leftover) + "\n"
    )
    sys.exit(1)

tmp_path = out_path + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as fh:
    fh.write(text)
os.replace(tmp_path, out_path)
PYEOF
}

load_launchd_job() {
    local label="$1" plist="$2" uid primary fallback
    uid="$(id -u)"
    primary="gui/$uid"
    launchctl bootout "$primary/$label" >/dev/null 2>&1 || true
    # A previously `launchctl disable`d label persists that override across
    # bootout, and bootstrap of a disabled label silently fails -- enable
    # must run before the bootstrap attempt, not just after a success.
    launchctl enable "$primary/$label" >/dev/null 2>&1 || true
    if launchctl bootstrap "$primary" "$plist" >/dev/null 2>&1; then
        launchctl enable "$primary/$label" >/dev/null 2>&1 || true
        printf '%s' "$primary"
        return 0
    fi
    fallback="user/$uid"
    launchctl bootout "$fallback/$label" >/dev/null 2>&1 || true
    launchctl enable "$fallback/$label" >/dev/null 2>&1 || true
    if launchctl bootstrap "$fallback" "$plist" >/dev/null 2>&1; then
        launchctl enable "$fallback/$label" >/dev/null 2>&1 || true
        printf '%s' "$fallback"
        return 0
    fi
    echo "error: failed to bootstrap launchd job $label (tried $primary and $fallback)" >&2
    exit 1
}

job_step() {
    local name="$1" tmpl label dest_plist tmp_plist rendered_hash dest_hash
    local content_unchanged is_loaded file_status action used_domain

    tmpl="$ROOT_DIR/templates/launchd/$name.plist.in"
    if [ ! -f "$tmpl" ]; then
        echo "error: missing launchd template for job '$name': $tmpl" >&2
        exit 1
    fi
    label="${LABEL_PREFIX}.$name"
    dest_plist="$LAUNCH_AGENTS_DIR/$label.plist"
    tmp_plist="$WORK_TMP/$label.plist"
    render_template "$tmpl" "$tmp_plist" "$label" "$PY_ABS" "$PREFIX" "$EFFECTIVE_HOME" "$LOG_DIR" \
        "$EXTRA_ENV_RIGOPS_CONFIG" "$EXTRA_ENV_RIGOPS_STATE_DIR" \
        "$EXTRA_ENV_XDG_CONFIG_HOME" "$EXTRA_ENV_XDG_STATE_HOME"

    if command -v plutil >/dev/null 2>&1; then
        if ! plutil -lint "$tmp_plist" >/dev/null 2>&1; then
            echo "error: rendered plist failed plutil -lint: $tmp_plist" >&2
            exit 1
        fi
    fi

    rendered_hash="$(sha256_file "$tmp_plist")"
    dest_hash=""
    [ -f "$dest_plist" ] && dest_hash="$(sha256_file "$dest_plist")"
    content_unchanged=0
    [ -n "$dest_hash" ] && [ "$dest_hash" = "$rendered_hash" ] && content_unchanged=1

    is_loaded=0
    if command -v launchctl >/dev/null 2>&1 && launchctl list "$label" >/dev/null 2>&1; then
        is_loaded=1
    fi

    if [ -z "$dest_hash" ]; then
        file_status="installed"
    elif [ "$content_unchanged" -eq 1 ]; then
        file_status="unchanged"
    else
        file_status="updated"
    fi
    case "$file_status" in
        installed) COUNT_INSTALLED=$((COUNT_INSTALLED + 1)) ;;
        updated) COUNT_UPDATED=$((COUNT_UPDATED + 1)) ;;
        unchanged) COUNT_UNCHANGED=$((COUNT_UNCHANGED + 1)) ;;
    esac

    if [ "$content_unchanged" -eq 1 ] && [ "$is_loaded" -eq 1 ]; then
        action="unchanged"
        used_domain="$(old_domain_for "$label")" || used_domain="gui/$(id -u)"
    else
        action="loaded"
        used_domain="gui/$(id -u)"
        if [ "$APPLY" -eq 1 ]; then
            if [ "$file_status" != "unchanged" ]; then
                mkdir -p "$(dirname "$dest_plist")"
                cp "$tmp_plist" "$dest_plist"
                chmod 0644 "$dest_plist"
            fi
            used_domain="$(load_launchd_job "$label" "$dest_plist")"
        fi
    fi

    [ "$action" = "loaded" ] && COUNT_JOBS_LOADED=$((COUNT_JOBS_LOADED + 1))

    if [ "$APPLY" -eq 1 ]; then
        printf 'JOB\t%s\t%s\t%s\t%s\n' "$label" "$dest_plist" "$rendered_hash" "$used_domain" >>"$MANIFEST_TSV"
    fi

    printf '     %-9s %-9s label=%s plist=%s\n' "$file_status" "$action" "$label" "$dest_plist"
}

step_jobs() {
    step_log "jobs under $LAUNCH_AGENTS_DIR"
    if [ "$JOBS_ENABLED" -ne 1 ]; then
        printf '     skipped (%s)\n' "$JOBS_SKIP_REASON"
        return 0
    fi
    if [ "$APPLY" -eq 1 ]; then
        mkdir -p "$LOG_DIR" "$LAUNCH_AGENTS_DIR"
    fi
    local name
    for name in "${SELECTED_JOBS[@]}"; do
        job_step "$name"
    done
}

write_manifest() {
    # jobs_authoritative=1 (final, post-step_jobs) trusts MANIFEST_TSV's own
    # JOB lines as the complete truth for this run -- including "none",
    # e.g. a `--jobs doctor` run legitimately dropping a previously
    # installed ledger job. jobs_authoritative=0 (mid-run checkpoints, before
    # step_jobs has had a chance to run) instead forwards the PREVIOUS
    # manifest's job records untouched, so a crash between a checkpoint and
    # step_jobs can never wipe "jobs" down to [] in the file --uninstall
    # reads.
    local jobs_authoritative="$1"
    mkdir -p "$PREFIX"
    "$PY_ABS" - "$MANIFEST_TSV" "$INSTALL_VERSION" "$PY_ABS" "$PREFIX" "$EFFECTIVE_HOME" \
        "$MANIFEST_CONFIG_DIR" "$MANIFEST_STATE_DIR" "$LOG_DIR" "$PREFIX/install.manifest.json" \
        "$jobs_authoritative" "$OLD_JOBS_TSV" "$EXTRA_ENV_RIGOPS_CONFIG" "$EXTRA_ENV_RIGOPS_STATE_DIR" <<'PYEOF'
import datetime
import json
import os
import sys

(tsv_path, version, python_abs, prefix, home,
 config_dir, state_dir, log_dir, out_path,
 jobs_authoritative, old_jobs_path,
 config_file_external, state_dir_external) = sys.argv[1:14]

files, symlinks, jobs, scaffolded = [], [], [], []
with open(tsv_path, encoding="utf-8") as fh:
    for raw in fh:
        line = raw.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        tag = parts[0]
        if tag == "FILE":
            files.append({"path": parts[1], "sha256": parts[2]})
        elif tag == "SYMLINK":
            symlinks.append({"path": parts[1], "target": parts[2]})
        elif tag == "JOB":
            jobs.append(
                {"label": parts[1], "plist": parts[2], "sha256": parts[3], "domain": parts[4]}
            )
        elif tag == "SCAFFOLD":
            scaffolded.append(parts[1])

if jobs_authoritative != "1":
    jobs = []
    if os.path.exists(old_jobs_path):
        with open(old_jobs_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) == 4:
                    jobs.append(
                        {"label": parts[0], "plist": parts[1], "sha256": parts[2], "domain": parts[3]}
                    )

existing = None
if os.path.exists(out_path):
    try:
        with open(out_path, encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, ValueError):
        existing = None

created_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
if isinstance(existing, dict) and existing.get("created_utc"):
    created_utc = existing["created_utc"]

manifest = {
    "schema": "rigops.install.v1",
    "version": version,
    "created_utc": created_utc,
    "python": python_abs,
    "prefix": prefix,
    "home": home,
    "config_dir": config_dir,
    "state_dir": state_dir,
    "log_dir": log_dir,
    "config_file_external": config_file_external,
    "state_dir_external": state_dir_external,
    "files": files,
    "symlinks": symlinks,
    "jobs": jobs,
    "scaffolded": scaffolded,
}


def without_created(data):
    return {k: v for k, v in data.items() if k != "created_utc"}


if isinstance(existing, dict) and without_created(existing) == without_created(manifest):
    print("     manifest unchanged: " + out_path)
else:
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, out_path)
    print("     wrote " + out_path)
PYEOF
}

step_manifest() {
    step_log "write manifest -> $PREFIX/install.manifest.json"
    if [ "$APPLY" -eq 1 ]; then
        write_manifest 1
    else
        printf '     would write %s\n' "$PREFIX/install.manifest.json"
    fi
}

step_summary() {
    step_log "summary"
    printf '     installed=%d updated=%d unchanged=%d jobs_loaded=%d\n' \
        "$COUNT_INSTALLED" "$COUNT_UPDATED" "$COUNT_UNCHANGED" "$COUNT_JOBS_LOADED"
    printf '     next: add %s/.local/bin to PATH if needed, then run "rigops help"\n' "$EFFECTIVE_HOME"
}

# Guard required before every purge rm -rf: refuses to delete anything
# that isn't an absolute, real (non-symlink) directory named "rigops" and
# distinct from both "/" and the install's own $HOME, so a bad manifest
# or env override can never turn --purge into an accidental wipe.
purge_safe() {
    local path="$1" home_value="$2" reason="" real_path="" real_home=""
    case "$path" in
        /*) : ;;
        *) reason="not an absolute path" ;;
    esac
    if [ -z "$reason" ] && [ -L "$path" ]; then
        reason="is a symlink"
    fi
    if [ -z "$reason" ] && [ ! -d "$path" ]; then
        reason="not a directory"
    fi
    if [ -z "$reason" ] && [ "$path" = "/" ]; then
        reason="refusing to touch /"
    fi
    # A blank manifest home can't be compared against, so refuse instead of
    # silently skipping the $HOME-equality check below.
    if [ -z "$reason" ] && [ -z "$home_value" ]; then
        reason="manifest home is blank, cannot verify target isn't \$HOME"
    fi
    if [ -z "$reason" ] && [ "$path" = "$home_value" ]; then
        reason="equals \$HOME, not a rigops-owned dir"
    fi
    if [ -z "$reason" ] && [ "$(basename "$path")" != "rigops" ]; then
        reason="basename is not 'rigops'"
    fi
    # A symlinked ancestor (not $path itself, already caught by -L above)
    # can make basename/HOME checks above pass while rm -rf actually lands
    # somewhere else entirely -- re-check both against the resolved path.
    if [ -z "$reason" ]; then
        real_path="$(cd "$path" 2>/dev/null && pwd -P)" || real_path=""
        if [ -z "$real_path" ]; then
            reason="could not resolve real path"
        elif [ "$(basename "$real_path")" != "rigops" ]; then
            reason="resolved real path basename is not 'rigops' (symlinked ancestor)"
        else
            real_home="$(cd "$home_value" 2>/dev/null && pwd -P)" || real_home=""
            if [ -n "$real_home" ] && [ "$real_path" = "$real_home" ]; then
                reason="resolved real path equals \$HOME (symlinked ancestor)"
            fi
        fi
    fi
    if [ -n "$reason" ]; then
        echo "warn: refusing to purge $path ($reason)" >&2
        return 1
    fi
    return 0
}

do_uninstall() {
    local manifest_path="$PREFIX/install.manifest.json"
    if [ ! -f "$manifest_path" ]; then
        echo "error: no install manifest at $manifest_path (nothing to uninstall; run install.sh --apply first)" >&2
        exit 1
    fi

    local dump="$WORK_TMP/uninstall-data.tsv"
    "$PY_ABS" - "$manifest_path" >"$dump" <<'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    "META\t" + data.get("home", "") + "\t" + data.get("prefix", "") + "\t" + data.get("config_dir", "")
    + "\t" + data.get("state_dir", "") + "\t" + data.get("log_dir", "")
    + "\t" + data.get("config_file_external", "") + "\t" + data.get("state_dir_external", "")
)
for j in data.get("jobs", []):
    print(
        "JOB\t" + j.get("label", "") + "\t" + j.get("plist", "")
        + "\t" + j.get("sha256", "") + "\t" + j.get("domain", "")
    )
for f in data.get("files", []):
    print("FILE\t" + f.get("path", "") + "\t" + f.get("sha256", ""))
for s in data.get("symlinks", []):
    print("SYMLINK\t" + s.get("path", "") + "\t" + s.get("target", ""))
PYEOF

    local meta_line m_home m_prefix m_config_dir m_state_dir m_log_dir
    local m_config_file_external m_state_dir_external
    meta_line="$(head -n1 "$dump")"
    m_home="$(printf '%s\n' "$meta_line" | cut -f2)"
    m_prefix="$(printf '%s\n' "$meta_line" | cut -f3)"
    m_config_dir="$(printf '%s\n' "$meta_line" | cut -f4)"
    m_state_dir="$(printf '%s\n' "$meta_line" | cut -f5)"
    m_log_dir="$(printf '%s\n' "$meta_line" | cut -f6)"
    m_config_file_external="$(printf '%s\n' "$meta_line" | cut -f7)"
    m_state_dir_external="$(printf '%s\n' "$meta_line" | cut -f8)"

    step_log "uninstall jobs recorded in manifest"
    local tag label plist sha domain current_hash disabled_path
    while IFS=$'\t' read -r tag label plist sha domain; do
        [ "$tag" = "JOB" ] || continue
        if [ "$APPLY" -eq 1 ] && command -v launchctl >/dev/null 2>&1; then
            launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
        fi
        if [ -f "$plist" ]; then
            current_hash="$(sha256_file "$plist")"
            if [ "$current_hash" = "$sha" ]; then
                [ "$APPLY" -eq 1 ] && rm -f "$plist"
                printf '     removed  %s (%s)\n' "$plist" "$label"
            else
                disabled_path="${plist}.rigops-disabled"
                if [ "$APPLY" -eq 1 ]; then
                    mv -f "$plist" "$disabled_path"
                    echo "warn: $plist modified since install; disabled (renamed to $disabled_path) so launchd won't re-bootstrap it against the removed prefix.
restore: mv \"$disabled_path\" \"$plist\" && launchctl bootstrap $domain \"$plist\"
delete instead: rm \"$disabled_path\"" >&2
                    printf '     disabled %s -> %s (%s, modified since install)\n' "$plist" "$disabled_path" "$label"
                else
                    printf '     plan: would disable %s (rename to %s) (%s, modified since install)\n' \
                        "$plist" "$disabled_path" "$label"
                fi
            fi
        else
            printf '     absent   %s (%s)\n' "$plist" "$label"
        fi
    done <"$dump"

    step_log "uninstall files under $m_prefix"
    local rel fpath
    while IFS=$'\t' read -r tag rel sha; do
        [ "$tag" = "FILE" ] || continue
        fpath="$m_prefix/$rel"
        if [ -f "$fpath" ]; then
            current_hash="$(sha256_file "$fpath")"
            if [ "$current_hash" = "$sha" ]; then
                [ "$APPLY" -eq 1 ] && rm -f "$fpath"
                printf '     removed  %s\n' "$fpath"
            else
                echo "warn: $fpath modified since install, leaving in place" >&2
                printf '     kept     %s (modified since install)\n' "$fpath"
            fi
        else
            printf '     absent   %s\n' "$fpath"
        fi
    done <"$dump"

    if [ "$APPLY" -eq 1 ]; then
        if [ -d "$m_prefix" ]; then
            # __pycache__ isn't in the manifest (python writes it lazily the
            # first time an installed script runs) but it's exactly the kind
            # of build artifact a clean uninstall should still sweep.
            local pc
            while IFS= read -r pc; do
                [ -n "$pc" ] || continue
                rm -rf "$pc"
            done < <(find "$m_prefix" -type d -name '__pycache__' 2>/dev/null)
            local d
            while IFS= read -r d; do
                [ -n "$d" ] || continue
                rmdir "$d" 2>/dev/null || true
            done < <(find "$m_prefix" -depth -type d 2>/dev/null)
        fi
        rm -f "$manifest_path"
        rmdir "$m_prefix" 2>/dev/null || true
    fi

    step_log "uninstall symlink"
    local link_path target cur
    while IFS=$'\t' read -r tag link_path target; do
        [ "$tag" = "SYMLINK" ] || continue
        if [ -L "$link_path" ]; then
            cur="$(readlink "$link_path")"
            case "$cur" in
                "$m_prefix"/*)
                    [ "$APPLY" -eq 1 ] && rm -f "$link_path"
                    printf '     removed  %s\n' "$link_path"
                    ;;
                *)
                    printf '     kept     %s (points outside prefix: %s)\n' "$link_path" "$cur"
                    ;;
            esac
        else
            printf '     absent   %s\n' "$link_path"
        fi
    done <"$dump"

    if [ "$PURGE" -eq 1 ]; then
        step_log "purge config/state/log dirs"
        if [ -n "$m_config_dir" ]; then
            printf '     %s\n' "$m_config_dir"
        else
            printf '     note: config file %s managed externally (RIGOPS_CONFIG), not purged\n' \
                "${m_config_file_external:-(unknown; not recorded at install time)}"
        fi
        if [ -n "$m_state_dir" ]; then
            printf '     %s\n' "$m_state_dir"
        else
            printf '     note: state dir %s managed externally (RIGOPS_STATE_DIR), not purged\n' \
                "${m_state_dir_external:-(unknown; not recorded at install time)}"
        fi
        printf '     %s\n' "$m_log_dir"
        if [ "$APPLY" -eq 1 ]; then
            if [ "$YES" -ne 1 ]; then
                local prompt_targets="" pt
                for pt in "$m_config_dir" "$m_state_dir" "$m_log_dir"; do
                    [ -n "$pt" ] || continue
                    prompt_targets="${prompt_targets:+$prompt_targets }$pt"
                done
                printf 'permanently delete %s? [y/N] ' "$prompt_targets"
                read -r ans || ans=""
                case "$ans" in
                    y | Y | yes | YES) : ;;
                    *)
                        echo "purge cancelled"
                        exit 0
                        ;;
                esac
            fi
            local purge_target
            for purge_target in "$m_config_dir" "$m_state_dir" "$m_log_dir"; do
                [ -n "$purge_target" ] || continue
                [ -e "$purge_target" ] || continue
                if purge_safe "$purge_target" "$m_home"; then
                    rm -rf "$purge_target"
                else
                    PURGE_REFUSED=1
                    PURGE_REFUSED_PATHS+=("$purge_target")
                fi
            done
            if [ "$PURGE_REFUSED" -eq 1 ]; then
                echo "error: --purge refused one or more targets; verify and remove manually if correct:" >&2
                local refused_path
                for refused_path in "${PURGE_REFUSED_PATHS[@]}"; do
                    printf '  rm -rf %q\n' "$refused_path" >&2
                done
            fi
        fi
    fi
}

if [ "$UNINSTALL" -eq 1 ]; then
    ACTION_WORD="uninstall"
else
    ACTION_WORD="install"
fi
if [ "$APPLY" -eq 1 ]; then
    echo "rigops $ACTION_WORD: applying to $EFFECTIVE_HOME"
else
    echo "rigops $ACTION_WORD: plan for $EFFECTIVE_HOME (dry run; re-run with --apply to act)"
fi

if [ "$UNINSTALL" -eq 1 ]; then
    do_uninstall
    if [ "$PURGE_REFUSED" -eq 1 ]; then
        exit 1
    fi
else
    INSTALL_VERSION="$("$PY_ABS" -c 'import json, sys
print(json.load(open(sys.argv[1]))["version"])' "$ROOT_DIR/plugin/.claude-plugin/plugin.json")"
    step_preflight
    step_copy_tree
    step_symlink
    step_scaffold
    # Checkpoint after tree+symlink+scaffold, and again after statusline, so
    # a crash before step_jobs still leaves a manifest --uninstall can act
    # on instead of an orphaned, unmanifested partial install. Both are
    # non-authoritative for "jobs" (arg 0): step_jobs hasn't run yet this
    # invocation, so write_manifest forwards the PREVIOUS manifest's job
    # records rather than wiping them to [].
    if [ "$APPLY" -eq 1 ]; then
        write_manifest 0
    fi
    step_statusline
    if [ "$APPLY" -eq 1 ] && [ "$STATUSLINE" -eq 1 ] && [ "$PLUGIN_ONLY" -eq 0 ]; then
        write_manifest 0
    fi
    step_jobs
    step_manifest
    step_summary
fi

exit 0
