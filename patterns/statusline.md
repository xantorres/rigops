# Statusline

**What this pattern buys you:** the day's token spend and the session's context size in the line under the prompt, so a long session or an expensive day shows up before it costs anything.

## What it shows

Two segments, joined by a bar:

- Today's EIT so far (`EIT 5.0k/20t`: effective input tokens over turns), read from a cache that `rigops eit --since=today --json` refreshes in the background.
- The current context size (`ctx 312k`), read off the transcript. It turns yellow at the first of `context.nudge_tiers` and red at the second.

A segment goes blank when its tool is missing (`jq`, the `rigops` binary). The script always prints and exits 0, so it never blocks the UI.

## An example to copy

This is not an installed component. [statusline/](statusline/) holds the two scripts as a starting point: `rigops-statusline.sh` and `ctx-probe.sh`, which it finds beside itself. Copy both into one directory you own and edit them to fit. Earlier versions of `install.sh` wired it with `--statusline`; that option is gone, and `install.sh --uninstall` from tag `v0.12.0` still undoes an earlier wiring (see [../docs/INSTALL.md](../docs/INSTALL.md)).

## Wiring it by hand

Point the `statusLine` key of your agent settings at the copied script:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/opt/statusline/rigops-statusline.sh"
  }
}
```

The agent pipes a JSON payload with `transcript_path` to the script on stdin. The refresh pattern behind the EIT cache is in [tiered-refresh.md](tiered-refresh.md).
