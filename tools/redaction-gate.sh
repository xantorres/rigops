#!/usr/bin/env bash
# Blocks employer traces and AI-authorship attribution from the working
# tree, git history, and commit messages before they ship.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: redaction-gate.sh [--history] [--message <file>] [--help]

  (no args)         Scan tracked working-tree files for redaction hits.
  --history         Scan full git history (diffs, authors, subjects, bodies).
  --message <file>  Scan a single file (e.g. a commit message) for hits.
  --help            Show this help and exit.
EOF
}

ORIG_PWD="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PUBLIC_PATTERNS="tools/redaction-patterns.public.txt"
PRIVATE_PATTERNS=""
TMP_PRIVATE=""
WORK_DIR=""

cleanup() {
  if [ -n "$TMP_PRIVATE" ] && [ -f "$TMP_PRIVATE" ]; then
    rm -f "$TMP_PRIVATE"
  fi
  if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

if [ -n "${RIGOPS_REDACTION_PATTERNS_B64:-}" ]; then
  TMP_PRIVATE="$(mktemp "${TMPDIR:-/tmp}/rigops-redaction.XXXXXX")"
  if ! printf '%s' "$RIGOPS_REDACTION_PATTERNS_B64" | base64 -d >"$TMP_PRIVATE" 2>/dev/null; then
    printf '%s' "$RIGOPS_REDACTION_PATTERNS_B64" | base64 -D >"$TMP_PRIVATE"
  fi
  PRIVATE_PATTERNS="$TMP_PRIVATE"
else
  candidate="${RIGOPS_REDACTION_PATTERNS:-$HOME/.config/rigops/redaction-patterns.txt}"
  if [ -f "$candidate" ]; then
    PRIVATE_PATTERNS="$candidate"
  else
    echo "warning: private redaction patterns not found; running public layer only" >&2
  fi
fi

# Field separator for rule records; unlikely to appear in pattern text.
RULE_SEP=$'\x1f'

# Emits "<label><SEP><lineno><SEP><flag> <pattern>" lines from a rule
# file, skipping blanks/comments. label is the source (public/private),
# lineno its 1-based line number -- both used to report unparseable
# rules without leaking pattern text.
emit_rules() {
  local f="$1" label="$2"
  local lineno=0
  [ -n "$f" ] && [ -f "$f" ] || return 0
  while IFS= read -r raw || [ -n "$raw" ]; do
    lineno=$((lineno + 1))
    case "$raw" in
      '' | '#'*) continue ;;
    esac
    printf '%s%s%s%s%s\n' "$label" "$RULE_SEP" "$lineno" "$RULE_SEP" "$raw"
  done <"$f"
}

# A layer that is present but yields no rules would let every scan
# report clean, so it fails like a missing public layer.
load_rules() {
  local public private
  if [ ! -f "$PUBLIC_PATTERNS" ]; then
    echo "error: public redaction patterns missing: $PUBLIC_PATTERNS" >&2
    exit 2
  fi
  public="$(emit_rules "$PUBLIC_PATTERNS" "public")"
  if [ -z "$public" ]; then
    echo "error: public redaction patterns load no rules: $PUBLIC_PATTERNS" >&2
    exit 2
  fi
  private="$(emit_rules "$PRIVATE_PATTERNS" "private")"
  if [ -n "$PRIVATE_PATTERNS" ] && [ -z "$private" ]; then
    echo "error: private redaction patterns load no rules" >&2
    exit 2
  fi
  RULES="$public"
  if [ -n "$private" ]; then
    RULES="$RULES"$'\n'"$private"
  fi
}

# Counts lines in $1 without relying on wc's inconsistent padding.
count_lines() {
  local n=0
  while IFS= read -r _; do
    n=$((n + 1))
  done <<<"$1"
  printf '%s' "$n"
}

list_files() {
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files -z
  else
    find . -type f -not -path './.git/*' -print0
  fi
}

run_default() {
  local hits=0
  local rule_type pattern gflags label lineno raw matches add rc f
  local -a FILES=()

  while IFS= read -r -d '' f; do
    f="${f#./}"
    case "$f" in
      "$PUBLIC_PATTERNS" | .git/*) continue ;;
    esac
    [ -f "$f" ] || continue
    FILES+=("$f")
  done < <(list_files)

  if [ "${#FILES[@]}" -eq 0 ]; then
    echo "redaction gate: clean"
    return 0
  fi

  while IFS="$RULE_SEP" read -r label lineno raw; do
    rule_type="${raw%% *}"
    pattern="${raw#* }"
    case "$rule_type" in
      i) gflags="-HInEi" ;;
      s) gflags="-HInE" ;;
      *)
        echo "error: unparseable rule at line ${lineno} of ${label} patterns" >&2
        exit 2
        ;;
    esac

    set +e
    matches="$(grep "$gflags" -- "$pattern" "${FILES[@]}")"
    rc=$?
    set -e
    if [ "$rc" -gt 1 ]; then
      echo "redaction gate: grep failed (exit $rc) on rule ${lineno} of ${label} patterns" >&2
      exit "$rc"
    fi
    if [ -n "$matches" ]; then
      printf '%s\n' "$matches"
      add=$(count_lines "$matches")
      hits=$((hits + add))
    fi
  done <<<"$RULES"

  if [ "$hits" -gt 0 ]; then
    echo "redaction gate: ${hits} hit(s)"
    return 1
  fi
  echo "redaction gate: clean"
  return 0
}

run_history() {
  local hits=0
  local rule_type pattern gflags label lineno raw matches add grep_rc
  local diff_log meta_log

  # Captured first: piped into grep, a failed git log read as "no match".
  WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rigops-redaction.XXXXXX")"
  diff_log="$WORK_DIR/diff.log"
  meta_log="$WORK_DIR/meta.log"
  if ! git log --all --full-history -m -p -- . ':(exclude)tools/redaction-patterns.public.txt' >"$diff_log"; then
    echo "redaction gate: git log failed reading history diff" >&2
    exit 2
  fi
  if ! git log --all --format='%an %ae %s%n%b' >"$meta_log"; then
    echo "redaction gate: git log failed reading commit metadata" >&2
    exit 2
  fi

  while IFS="$RULE_SEP" read -r label lineno raw; do
    rule_type="${raw%% *}"
    pattern="${raw#* }"
    case "$rule_type" in
      i) gflags="-anEi" ;;
      s) gflags="-anE" ;;
      *)
        echo "error: unparseable rule at line ${lineno} of ${label} patterns" >&2
        exit 2
        ;;
    esac

    set +e
    matches="$(grep "$gflags" -- "$pattern" "$diff_log")"
    grep_rc=$?
    set -e
    if [ "$grep_rc" -gt 1 ]; then
      echo "redaction gate: grep failed (exit $grep_rc) on rule ${lineno} of ${label} patterns scanning history diff" >&2
      exit "$grep_rc"
    fi
    if [ -n "$matches" ]; then
      printf '%s\n' "$matches"
      add=$(count_lines "$matches")
      hits=$((hits + add))
    fi

    set +e
    matches="$(grep "$gflags" -- "$pattern" "$meta_log")"
    grep_rc=$?
    set -e
    if [ "$grep_rc" -gt 1 ]; then
      echo "redaction gate: grep failed (exit $grep_rc) on rule ${lineno} of ${label} patterns scanning commit metadata" >&2
      exit "$grep_rc"
    fi
    if [ -n "$matches" ]; then
      printf '%s\n' "$matches"
      add=$(count_lines "$matches")
      hits=$((hits + add))
    fi
  done <<<"$RULES"

  if [ "$hits" -gt 0 ]; then
    echo "redaction gate: ${hits} hit(s)"
    return 1
  fi
  echo "redaction gate: clean"
  return 0
}

run_message() {
  local file="$1"
  local hits=0
  local rule_type pattern gflags label lineno raw matches add rc

  case "$file" in
    /*) : ;;
    *) file="$ORIG_PWD/$file" ;;
  esac

  if [ ! -f "$file" ]; then
    echo "error: message file not found: $file" >&2
    exit 2
  fi

  while IFS="$RULE_SEP" read -r label lineno raw; do
    rule_type="${raw%% *}"
    pattern="${raw#* }"
    case "$rule_type" in
      i) gflags="-HInEi" ;;
      s) gflags="-HInE" ;;
      *)
        echo "error: unparseable rule at line ${lineno} of ${label} patterns" >&2
        exit 2
        ;;
    esac

    set +e
    matches="$(grep "$gflags" -- "$pattern" "$file")"
    rc=$?
    set -e
    if [ "$rc" -gt 1 ]; then
      echo "redaction gate: grep failed (exit $rc) on rule ${lineno} of ${label} patterns scanning message" >&2
      exit "$rc"
    fi
    if [ -n "$matches" ]; then
      printf '%s\n' "$matches"
      add=$(count_lines "$matches")
      hits=$((hits + add))
    fi
  done <<<"$RULES"

  if [ "$hits" -gt 0 ]; then
    echo "redaction gate: ${hits} hit(s)"
    return 1
  fi
  echo "redaction gate: clean"
  return 0
}

mode="default"
msg_file=""

if [ "$#" -gt 0 ]; then
  case "$1" in
    --history)
      mode="history"
      shift
      ;;
    --message)
      shift
      msg_file="${1:-}"
      if [ -z "$msg_file" ]; then
        echo "error: --message requires a file argument" >&2
        usage >&2
        exit 2
      fi
      mode="message"
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
fi

if [ "$#" -gt 0 ]; then
  usage >&2
  exit 2
fi

load_rules

case "$mode" in
  default) run_default ;;
  history) run_history ;;
  message) run_message "$msg_file" ;;
esac
