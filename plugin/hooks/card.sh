#!/usr/bin/env bash
# The card lives in the rigops CLI; without one the plugin stays silent rather than failing the session.
if command -v rigops >/dev/null 2>&1; then
  RIGOPS_BIN="rigops"
elif [ -x "$HOME/.local/bin/rigops" ]; then
  RIGOPS_BIN="$HOME/.local/bin/rigops"
else
  exit 0
fi
"$RIGOPS_BIN" card --hook 2>/dev/null
exit 0
