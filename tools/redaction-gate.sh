#!/usr/bin/env bash
# Blocks employer traces and AI-authorship attribution from the working
# tree, git history, and commit messages before they ship.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: redaction-gate.sh [--history] [--message <file>] [--help]

  (no args)         Scan tracked working-tree files for redaction hits.
  --history         Scan full git history (diffs, authors, subjects, bodies).
  --message <file>  Scan a single file (e.g. a commit message) for hits.
  --help            Show this help and exit.

Private pattern layers, on top of tools/redaction-patterns.public.txt:
  RIGOPS_REDACTION_PATTERNS_B64  base64 rule file, the only private layer when set (CI).
  RIGOPS_REDACTION_PATTERNS      colon-separated rule files, each required; defaults
                                 to ~/.config/rigops/redaction-patterns.txt and
                                 ~/.config/rigops/redaction-patterns.rigops.txt,
                                 which may be absent.
USAGE
}

ORIG_PWD="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PUBLIC_PATTERNS="tools/redaction-patterns.public.txt"
PRIVATE_FILES=()
PRIVATE_LABELS=()
WORK_DIR=""

cleanup() {
  if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rigops-redaction.XXXXXX")"

# Labels a layer by basename, suffixed with its position when another
# layer already holds that name, so every hit names one rule file.
layer_label() {
  local name seen
  name="$(basename "$1")"
  for seen in public ${PRIVATE_LABELS[@]+"${PRIVATE_LABELS[@]}"}; do
    if [ "$seen" = "$name" ]; then
      name="$name#$((${#PRIVATE_FILES[@]} + 1))"
      break
    fi
  done
  printf '%s' "$name"
}

if [ -n "${RIGOPS_REDACTION_PATTERNS_B64:-}" ]; then
  if ! printf '%s' "$RIGOPS_REDACTION_PATTERNS_B64" | base64 -d >"$WORK_DIR/private.txt" 2>/dev/null &&
    ! printf '%s' "$RIGOPS_REDACTION_PATTERNS_B64" | base64 -D >"$WORK_DIR/private.txt" 2>/dev/null; then
    echo "error: RIGOPS_REDACTION_PATTERNS_B64 is not valid base64" >&2
    exit 2
  fi
  PRIVATE_FILES=("$WORK_DIR/private.txt")
  PRIVATE_LABELS=("private")
else
  IFS=: read -r -a candidates <<<"${RIGOPS_REDACTION_PATTERNS:-$HOME/.config/rigops/redaction-patterns.txt:$HOME/.config/rigops/redaction-patterns.rigops.txt}"
  for candidate in ${candidates[@]+"${candidates[@]}"}; do
    [ -n "$candidate" ] || continue
    if [ -f "$candidate" ]; then
      PRIVATE_LABELS+=("$(layer_label "$candidate")")
      PRIVATE_FILES+=("$candidate")
    elif [ -n "${RIGOPS_REDACTION_PATTERNS:-}" ]; then
      # A layer named on purpose is required; only the defaults are optional.
      echo "error: private redaction patterns not found: $candidate" >&2
      exit 2
    else
      echo "warning: private redaction patterns not found: $candidate" >&2
    fi
  done
  if [ "${#PRIVATE_FILES[@]}" -eq 0 ]; then
    echo "warning: no private redaction patterns; running public layer only" >&2
  fi
fi

# Field separator for rule records; unlikely to appear in pattern text.
RULE_SEP=$'\x1f'

# Emits "<label><SEP><lineno><SEP><flag> <pattern>" lines from a rule
# file, skipping blanks/comments. label names the layer, lineno is the
# rule's 1-based line: both identify a rule without leaking its text.
emit_rules() {
  local f="$1" label="$2"
  local lineno=0
  while IFS= read -r raw || [ -n "$raw" ]; do
    lineno=$((lineno + 1))
    # A CRLF file would leave \r in every pattern, which then never matches.
    raw="${raw%$'\r'}"
    case "$raw" in
      '' | '#'*) continue ;;
    esac
    printf '%s%s%s%s%s\n' "$label" "$RULE_SEP" "$lineno" "$RULE_SEP" "$raw"
  done <"$f"
}

# A layer that is present but yields no rules would let every scan
# report clean, so it fails like a missing public layer.
load_rules() {
  local layer i=0
  if [ ! -f "$PUBLIC_PATTERNS" ]; then
    echo "error: public redaction patterns missing: $PUBLIC_PATTERNS" >&2
    exit 2
  fi
  if ! RULES="$(emit_rules "$PUBLIC_PATTERNS" "public")"; then
    echo "error: public redaction patterns unreadable: $PUBLIC_PATTERNS" >&2
    exit 2
  fi
  if [ -z "$RULES" ]; then
    echo "error: public redaction patterns load no rules: $PUBLIC_PATTERNS" >&2
    exit 2
  fi
  while [ "$i" -lt "${#PRIVATE_FILES[@]}" ]; do
    if ! layer="$(emit_rules "${PRIVATE_FILES[$i]}" "${PRIVATE_LABELS[$i]}")"; then
      echo "error: private redaction patterns unreadable: ${PRIVATE_LABELS[$i]}" >&2
      exit 2
    fi
    if [ -z "$layer" ]; then
      echo "error: private redaction patterns load no rules: ${PRIVATE_LABELS[$i]}" >&2
      exit 2
    fi
    RULES="$RULES"$'\n'"$layer"
    i=$((i + 1))
  done
}

list_files() {
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files -z
  else
    find . -type f -not -path './.git/*' -print0
  fi
}

# Sets the caller's rule_case and pattern from rule text $3.
parse_rule() {
  rule_case="${3%% *}"
  pattern="${3#* }"
  case "$rule_case" in
    i | s) : ;;
    *)
      echo "error: unparseable rule at line ${2} of ${1} patterns" >&2
      exit 2
      ;;
  esac
}

# Usage: grep_rule <context> <grep options...> -- <files...>
# Writes grep output for the current rule to $WORK_DIR/match. Returns 0
# on a match, 1 on none; a grep error ends the gate.
grep_rule() {
  local context="$1" rc=0
  local -a opts=(-E)
  shift
  while [ "$1" != "--" ]; do
    opts+=("$1")
    shift
  done
  shift
  if [ "$rule_case" = i ]; then
    opts+=(-i)
  fi
  # A failed redirect exits 1 like "no match", so it is opened on its own.
  if ! : >"$WORK_DIR/match"; then
    echo "redaction gate: cannot write match file on rule ${lineno} of ${label} patterns${context}" >&2
    exit 2
  fi
  grep "${opts[@]}" -- "$pattern" "$@" >"$WORK_DIR/match" || rc=$?
  if [ "$rc" -gt 1 ]; then
    echo "redaction gate: grep failed (exit $rc) on rule ${lineno} of ${label} patterns${context}" >&2
    exit "$rc"
  fi
  return "$rc"
}

# Turns grep -n output in $WORK_DIR/match into "<$1>:<line>" locations.
match_lines() {
  local n
  while IFS=: read -r n _; do
    printf '%s:%s\n' "$1" "$n"
  done <"$WORK_DIR/match"
}

# Maps grep -n hits on the history diff to commit, file and file line,
# so no log text reaches the output.
locate_diff_hits() {
  awk -v matches="$WORK_DIR/match" '
    BEGIN {
      while ((getline line < matches) > 0) {
        want[substr(line, 1, index(line, ":") - 1)] = 1
      }
    }
    {
      loc = ""
      lead = substr($0, 1, 1)
      if ($0 ~ /^commit [0-9a-f]+/) {
        sha = substr($2, 1, 12); path = ""; hunk = 0; state = "message"
      } else if ($0 ~ /^diff --git /) {
        path = substr($0, 12)
        i = index(path, " b/")
        if (substr(path, 1, 2) == "a/" && i > 0) path = substr(path, 3, i - 3)
        hunk = 0; state = "file"
      } else if (state == "file" && $0 ~ /^@@ /) {
        old = $0; sub(/^@@ -/, "", old); old = old + 0
        cur = $0; sub(/^@@ -[0-9,]+ \+/, "", cur); cur = cur + 0
        hunk = 1; loc = path ":" cur
      } else if (hunk && lead == "+") {
        loc = path ":" cur; cur++
      } else if (hunk && lead == "-") {
        loc = path ":" old " (removed)"; old++
      } else if (hunk && lead == " ") {
        loc = path ":" cur; cur++; old++
      } else if (hunk && lead == "\\") {
        loc = path
      } else {
        hunk = 0
      }
      if (NR in want) {
        if (loc == "") loc = (state == "message") ? "(message)" : "(file header)"
        print "commit " sha " " loc
      }
    }
  ' "$1"
}

# Maps grep -n hits on the metadata log to the commit they belong to.
locate_meta_hits() {
  awk -v matches="$WORK_DIR/match" '
    BEGIN {
      while ((getline line < matches) > 0) {
        want[substr(line, 1, index(line, ":") - 1)] = 1
      }
    }
    substr($0, 1, 1) == "\037" { sha = substr($0, 2, 12); first = 1; next }
    {
      if (NR in want) {
        print "commit " sha (first ? " (author, email or subject)" : " (message body)")
      }
      first = 0
    }
  ' "$1"
}

# Runs locator $1 over log $2 into $WORK_DIR/located. A failed or short
# mapping would drop hits and read as clean, so either ends the gate.
locate_hits() {
  local want got
  if ! "$1" "$2" >"$WORK_DIR/located"; then
    echo "redaction gate: locating hits failed on rule ${lineno} of ${label} patterns" >&2
    exit 2
  fi
  want="$(wc -l <"$WORK_DIR/match")"
  got="$(wc -l <"$WORK_DIR/located")"
  if [ "$want" -ne "$got" ]; then
    echo "redaction gate: located ${got} of ${want} hit(s) on rule ${lineno} of ${label} patterns" >&2
    exit 2
  fi
}

# Prints each location in $1 tagged with the current rule, and counts it.
report() {
  local loc
  while IFS= read -r loc; do
    [ -n "$loc" ] || continue
    printf '%s (%s rule %s)\n' "$loc" "$label" "$lineno"
    hits=$((hits + 1))
  done <<<"$1"
}

finish() {
  if [ "$hits" -gt 0 ]; then
    echo "redaction gate: ${hits} hit(s)"
    return 1
  fi
  echo "redaction gate: clean"
  return 0
}

run_default() {
  local hits=0
  local label lineno raw rule_case pattern f
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
    finish
    return
  fi

  while IFS="$RULE_SEP" read -r label lineno raw; do
    parse_rule "$label" "$lineno" "$raw"
    grep_rule "" -lI --null -- "${FILES[@]}" || continue
    mv "$WORK_DIR/match" "$WORK_DIR/files"
    while IFS= read -r -d '' f; do
      grep_rule "" -nI -- "$f" || continue
      report "$(match_lines "$f")"
    done <"$WORK_DIR/files"
  done <<<"$RULES"

  finish
}

run_history() {
  local hits=0
  local label lineno raw rule_case pattern
  local diff_log="$WORK_DIR/diff.log" meta_log="$WORK_DIR/meta.log"

  # Captured first: piped into grep, a failed git log read as "no match".
  # The fixed format and prefixes keep the location mapping exact.
  if ! git log --all --full-history -m -p --pretty=medium --no-color \
    --src-prefix=a/ --dst-prefix=b/ \
    -- . ':(exclude)tools/redaction-patterns.public.txt' >"$diff_log"; then
    echo "redaction gate: git log failed reading history diff" >&2
    exit 2
  fi
  if ! git log --all --no-color --format='%x1f%H%n%an %ae %s%n%b' >"$meta_log"; then
    echo "redaction gate: git log failed reading commit metadata" >&2
    exit 2
  fi

  while IFS="$RULE_SEP" read -r label lineno raw; do
    parse_rule "$label" "$lineno" "$raw"
    if grep_rule " scanning history diff" -an -- "$diff_log"; then
      locate_hits locate_diff_hits "$diff_log"
      report "$(cat "$WORK_DIR/located")"
    fi
    if grep_rule " scanning commit metadata" -an -- "$meta_log"; then
      locate_hits locate_meta_hits "$meta_log"
      report "$(cat "$WORK_DIR/located")"
    fi
  done <<<"$RULES"

  finish
}

run_message() {
  local shown="$1" file="$1"
  local hits=0
  local label lineno raw rule_case pattern

  case "$file" in
    /*) : ;;
    *) file="$ORIG_PWD/$file" ;;
  esac

  if [ ! -f "$file" ]; then
    echo "error: message file not found: $file" >&2
    exit 2
  fi

  while IFS="$RULE_SEP" read -r label lineno raw; do
    parse_rule "$label" "$lineno" "$raw"
    # -a: a message is never a binary to skip, whatever bytes it holds.
    grep_rule " scanning message" -na -- "$file" || continue
    report "$(match_lines "$shown")"
  done <<<"$RULES"

  finish
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
