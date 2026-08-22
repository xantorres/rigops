#!/usr/bin/env bash
# Blocks ripgrep invocations that misuse -r.
#
# In grep, -r means recursive. In ripgrep it means --replace=TEXT, and ripgrep
# is already recursive by default. So `rg -rn pat dir` silently becomes
# --replace=n: every match is printed as the letter "n", and the flag that was
# meant to be -n never applies. `rg -rl` loses --files-with-matches the same
# way, so a `| wc -l` counts matching LINES while reading as a file count.
#
# Failure is silent: the command exits 0 and prints plausible-looking output
# with the matched text replaced by one or two letters.
#
# Exit 2 with a message on stderr blocks the call and shows the reason.

payload=$(cat)

cmd=$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("command", ""))
except Exception:
    pass
' 2>/dev/null)

[ -z "$cmd" ] && exit 0

# Match an rg invocation whose short-flag cluster contains r, e.g. -r -rn -rl
# -ril -nr. Long forms (--replace, --regexp) are deliberate and pass through.
if printf '%s' "$cmd" | grep -Eq '(^|[|;&(]|[[:space:]])rg[[:space:]]+(-[a-qs-zA-Z]*r[a-zA-Z]*)([[:space:]]|$)'; then
  cat >&2 <<'MSG'
BLOCKED: `rg -r` is --replace in ripgrep, not recursive.

ripgrep searches recursively by default, so -r is never needed for that. As a
short flag it swallows the next characters as replacement text:

  rg -rn pat dir   ->  --replace=n   every match prints as "n", -n never applies
  rg -rl pat dir   ->  --replace=l   no --files-with-matches, so `| wc -l`
                                     counts matching lines, not files
  rg -ril pat dir  ->  --replace=il  matches print as "il"

All three exit 0 and print plausible output, so the corruption is invisible.

Use instead:
  rg pat dir            recursive already
  rg -n pat dir         line numbers
  rg -l pat dir         files with matches
  rg -li pat dir        files with matches, case-insensitive
  rg --replace=X pat    only if you genuinely want substitution
MSG
  exit 2
fi

exit 0
