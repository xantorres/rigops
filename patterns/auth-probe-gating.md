# Auth Probe Gating

**What this pattern buys you:** catches a dead headless credential at the next
health check instead of at the moment a scheduled job depending on it fails,
silently or otherwise.

## Problem

Headless and scheduled agent jobs authenticate the same way an interactive
session does, but their credential can expire independently. A job that ran
fine every night for months starts failing at 3am because the interactive
login token it depends on lapsed - and the failure mode is rarely a clear
error. Downstream jobs sharing the same credential either fail noisily,
burying the real cause in a pile of unrelated-looking errors, or fail silently
and just stop producing output. Either way, the operator finds out days later,
from a stale dashboard or a complaint, not from the moment the credential
actually died.

## Pattern

A small, dedicated probe exercises the real CLI headlessly, under an
environment deliberately scrubbed to the minimum, so it can't accidentally
inherit the interactive session's working auth and report a false "ok" that
isn't representative of a genuinely headless run. The probe expects one exact,
narrow output; anything else - wrong text, nonzero exit, timeout - is a
failure. Feed that result into the fleet-health check that gates other
scheduled work, so an expensive or important job never even starts against a
credential already known to be dead. Because the probe spawns a real CLI
invocation that may consume quota or tokens, it should ship disabled by
default - running it automatically is a policy decision about spend, not a
default any tool should make for the operator.

## How rigops implements it

[../lib/rigops/authprobe.py](../lib/rigops/authprobe.py) is the probe.
`build_env` constructs the subprocess environment from scratch rather than
inheriting the caller's: only `HOME`, `USER`, `PATH` (from config, not the
caller's own `PATH`), `SHELL` (hardcoded `/bin/sh`), `TERM` (hardcoded
`dumb`), and an optional OAuth token. The module's own docstring states the
reasoning directly: "an interactive Claude Code session exports variables that
route auth through the host session and would produce a false ok." The token
can come from the macOS keychain via `keychain_token`, gated by a configured
service name and a short timeout - the function's own docstring explains why
the timeout matters: "A keychain item added without -T triggers a GUI consent
dialog on first read; the timeout keeps that from hanging an unattended
probe."

`probe()` is built to never raise. Config validation failures return `(False,
"invalid authprobe.command")` rather than throwing; a bad or non-numeric
`timeout_s` in config is coerced to a safe 60-second default rather than
crashing or hanging; `subprocess.run` failures (timeout, OS error) are caught
and turned into `(False, reason)`. Every code path returns the same `(bool,
str)` status pair, so a caller always gets a clean, structured answer instead
of having to handle an unexpected exception from what's supposed to be a plain
health check.

[../libexec/rigops-authprobe](../libexec/rigops-authprobe) is the CLI wrapper.
It ships disabled by default (`authprobe.enabled: false` in
[../lib/rigops/config.py](../lib/rigops/config.py)'s `DEFAULTS`) - running it
requires either flipping that flag or passing `--force`. Disabled is not an
error state: `--json` still emits a clean, complete JSON object rather than an
empty response or a nonzero exit, so a caller parsing the output gets a
well-formed answer whether the probe actually ran or not.

Sample disabled-state output:

```text
authprobe disabled (set authprobe.enabled true or pass --force)
```

Sample `--json` in the same disabled state:

```text
{"ok": null, "detail": "disabled (set authprobe.enabled true or pass --force)", "enabled": false}
```

The gate into fleet health is documented directly in
[../config/config.schema.json](../config/config.schema.json)'s own description
of the `authprobe` section: "Wire it from doctor via checks.custom: rigops
authprobe." `rigops doctor`'s `run_custom_check` (in
[../libexec/rigops-doctor](../libexec/rigops-doctor)) runs any shell command
and classifies its exit code as ok/warn/fail against configured thresholds -
pointing one `checks.custom` entry at `rigops authprobe` turns the probe into
one more line in the doctor report, so a dead credential shows up next to hung
and stale jobs instead of only being discovered when a downstream job that
depends on it fails.

## Adopting it without rigops

- Run the real CLI or tool your scheduled jobs depend on, not a mock of it - the point is catching what a genuine headless invocation would hit, and a mock can't reproduce an expired-credential failure.
- Scrub the subprocess environment deliberately rather than inheriting the caller's; an interactive session's exported variables are exactly what would mask the failure you're trying to catch.
- Expect one exact, narrow output and treat everything else as failure - a probe that accepts "close enough" stops being a reliable signal.
- Make the probe never raise: every failure path (bad config, timeout, missing binary) should return a status, not an exception, so callers can treat it as a plain health check.
- Ship it disabled by default if it spawns something with a real cost (API quota, tokens, rate limits); let the operator opt in explicitly.
- Gate expensive or important scheduled work on the probe's result, so a dead credential is caught before the work starts, not after it fails.

[job-registry.md](./job-registry.md) covers the fleet-health watchdog this
probe plugs into as one more check.
