# Tiered Refresh

**What this pattern buys you:** a per-render surface that shows expensive data
without ever blocking the render on it, and without hammering the system
computing that data on every single frame.

## Problem

A statusline, or any per-render UI surface, that shells out to an expensive
probe on every render has two bad options. Block until the probe returns, and
every render lags by however long the probe takes - a shell that visibly
pauses every prompt. Or skip the probe when it's expensive, and the surface
just never shows that data, which defeats the point of having it.

## Pattern

Split the data by cost. A cheap tier gets computed inline, synchronously, on
every render - cheap enough that blocking on it is effectively free. An
expensive tier gets served from a cache file, refreshed by a background job
that's kicked off, not waited on, when the cache goes stale. The refresh needs
its own lock so two renders firing in close succession don't both kick off
duplicate background work; that lock needs a staleness cutoff of its own,
because a refresh that crashed mid-run would otherwise leave a lock file that
blocks every future refresh forever. The surface always renders immediately
from whatever it currently has - cache hit, cache miss, or cheap-tier-only -
and never blocks waiting for the expensive tier to catch up.

## How rigops implements it

[../plugin/statusline/rigops-statusline.sh](../plugin/statusline/rigops-statusline.sh)
runs both tiers side by side. The cheap tier is the context-size segment: it
shells out to [../plugin/hooks/ctx-probe.sh](../plugin/hooks/ctx-probe.sh) on
every render with no cache at all, on the reasoning in the script's own
comment: "Cheap enough to call every render; no cache." `ctx-probe.sh` tails
the last 400 lines of the transcript file and reads the token usage off the
last assistant message with `jq`, so the cost stays flat regardless of how
long the session has run - it never scans the whole transcript.

The expensive tier is the EIT-so-far segment. It reads from a cache file under
state, and only kicks off a background refresh (`rigops eit --since=today
--json`, backgrounded and `disown`ed) when the cache is more than 120 seconds
old and no lock file exists. The refresh subshell writes to a `.tmp` file and
moves it into place, so a render reading the cache mid-refresh gets either the
old value or the new one, never a partial write. The lock itself has a
60-second staleness cutoff: a lock older than that is treated as a dead
refresh and deleted so the next render can retry, rather than the segment
going dark forever because one refresh died mid-run. The script's own comment
names the accepted race directly: "two renders in the same instant can both
see no lock and both refresh -- benign (double API fetch, atomic tmp+mv
write), not corruption." When the expensive tier's cache file doesn't exist
yet, the statusline degrades to the cheap tier alone - the segment-join loop
skips any empty segment rather than blocking or showing nothing.

One more sharp edge the script carries: BSD `stat` (macOS) and GNU `stat`
(Linux) take incompatible flags for reading an mtime, and the wrong probe
order silently returns the wrong thing instead of erroring - GNU's own `-f`
flag means filesystem mode, not file mtime, and still exits 0 while printing
`?`, so a `-f`-first fallback chain would never reach `-c` on Linux.
`_mtime()` tries GNU's `stat -c %Y` first, falls back to BSD's `stat -f %m`,
and guards the result with a numeric check so a failed call becomes a safe `0`
rather than a non-numeric string leaking into arithmetic later.

[../plugin/hooks/ctx-nudge.sh](../plugin/hooks/ctx-nudge.sh) applies the same
tiering philosophy to a different surface: instead of cache-vs-live data, it's
tiers of severity for a nudge. Three ascending context-token thresholds
(`context.nudge_tiers`, default `[250000, 350000, 500000]`) each produce a
different message - wrap up soon, this session is heavy, this session is past
worth continuing - and a fourth value (`context.rearm_tokens`, default 50000)
governs when the nudge is allowed to fire again after already firing once per
session. The re-arm logic only resets below the lowest tier, not on any drop,
on the reasoning in its own comment: "routine microcompaction can drop tens of
thousands of tokens while staying above it and must not reset the rate limit;
a real /compact lands far lower." Both scripts read their thresholds from
`rigops config get context.*` when the CLI is available and fall back to
hardcoded defaults otherwise, so a missing CLI degrades the surface instead of
breaking it - the same instinct as the cache-miss path in the statusline.

## Adopting it without rigops

- Classify each piece of data your surface shows as cheap (compute inline, every render) or expensive (cache plus background refresh) before writing any caching code - mixing the two in one code path produces either lag or staleness.
- Refresh in the background, never inside the render's own execution path; the render should read a cache, not wait on a subprocess.
- Give any refresh lock a staleness cutoff. A lock with no expiry turns one crashed refresh into a permanently broken surface.
- Write cache updates atomically (temp file plus rename), so a reader never sees a half-written value.
- Decide explicitly what the surface shows when the expensive tier has nothing yet - showing the cheap tier alone beats showing nothing, and both beat blocking.
- If the surface runs cross-platform, test the platform-specific commands you lean on for silent wrong-answer failure modes, not just outright errors - a flag that's misparsed but still exits 0 is worse than one that fails loudly.

[hardened-launchd.md](./hardened-launchd.md) covers the same "background job,
don't block the foreground" idea for scheduled automation rather than a
per-render UI surface.
