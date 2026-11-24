#!/usr/bin/env bash
# Forwards the hook event and stdin to `rigops hook`; an older CLI without it fails silently.
# Exports the plugin version so the CLI can say when the two drift apart.
RIGOPS_BIN=""
if command -v rigops >/dev/null 2>&1; then
  RIGOPS_BIN="rigops"
elif [ -x "$HOME/.local/bin/rigops" ]; then
  RIGOPS_BIN="$HOME/.local/bin/rigops"
fi
[ -n "$RIGOPS_BIN" ] || exit 0
RIGOPS_PLUGIN_VERSION="$(sed -n 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$(dirname "$0")/../.claude-plugin/plugin.json" 2>/dev/null | head -n 1)"
export RIGOPS_PLUGIN_VERSION
"$RIGOPS_BIN" hook "${1:-}" 2>/dev/null
exit 0
