# Hardened launchd

**What this pattern buys you:** a scheduled job that keeps working after a Homebrew upgrade,
a machine restart, or a config edit, instead of one that silently stops and leaves no trace
of why.

## Problem

A naive launchd agent - `launchctl load -w some.plist`, no explicit environment, default
scheduling priority - breaks in a small set of predictable ways that only show up days or
weeks later. The interpreter path resolved at plist-authoring time stops existing after an
upgrade moves it. `HOME`/`PATH` aren't what an interactive shell would give the process, so
anything relying on `~` expansion or a tool found via `$PATH` fails silently. A background
job competes for I/O and CPU with whatever the user is doing interactively, because nothing
told launchd this job should yield. `load -w` and `bootstrap`/`bootout` are different,
partially-incompatible mental models for registering the same job, and mixing them produces
stale duplicate labels that both claim to own it. None of this shows up as a crash - it
shows up as a job that silently stopped doing its work.

## Pattern

A hardened plist template plus a bootstrap ritual, each defending against a specific failure
above. Template defenses: an absolute, install-time-resolved program path, not
whatever's on `PATH` months later; explicit `EnvironmentVariables` for `HOME` and `PATH`
rather than trusting launchd's minimal default environment; `ProcessType Background`, a
positive `Nice`, and low-priority I/O so the job is a background citizen by construction;
`RunAtLoad false` so the schedule owns firing, not the act of loading; real log paths for
stdout/stderr, since a background job with nowhere to report failures might as well not
report them. Ritual defenses: render the template with fully resolved paths at install time;
`enable` the label before attempting `bootstrap`, not only after, because a
previously-disabled label keeps that override across a `bootout` and silently fails to
bootstrap while still disabled; on any change, `bootout` the old registration and
`bootstrap` the new one, never `load -w`; and put any wall-clock runtime cap in the script
itself, driven by an environment variable, since a plist has no timeout key.

## How rigops implements it

The four shipped templates,
[../lib/rigops/templates/launchd/doctor.plist.in](../lib/rigops/templates/launchd/doctor.plist.in),
[../lib/rigops/templates/launchd/ledger.plist.in](../lib/rigops/templates/launchd/ledger.plist.in),
[../lib/rigops/templates/launchd/janitor.plist.in](../lib/rigops/templates/launchd/janitor.plist.in) and
[../lib/rigops/templates/launchd/reap.plist.in](../lib/rigops/templates/launchd/reap.plist.in), carry every
template defense above. Quoting doctor's environment block exactly:

```xml
<key>ProcessType</key>
<string>Background</string>
<key>Nice</key>
<integer>10</integer>
<key>LowPriorityIO</key>
<true/>
<key>EnvironmentVariables</key>
<dict>
	<key>HOME</key>
	<string>@HOME@</string>
	<key>PATH</key>
	<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
@EXTRA_ENV@
</dict>
```

`@LABEL@`, `@RIGOPS@`, `@HOME@`, `@LOG_DIR@`, `@EXTRA_ENV@` and `@HEAL_ARG@` are placeholders
that `render_plist` in [../lib/rigops/jobs.py](../lib/rigops/jobs.py) fills with fully resolved absolute
paths - and it raises `unresolved placeholder(s) in the <name> template` if any placeholder survives
substitution, rather than shipping a plist with a literal `@RIGOPS@` in it. It also parses the
result with `plistlib`, so a malformed plist is refused before it is written.
`doctor.plist.in` fires every 30 minutes (`StartInterval 1800`), with `--heal` only when installed with `rigops jobs install --heal`;
`ledger.plist.in` fires weekly, Monday 09:05 local (`StartCalendarInterval`). Both set
`RunAtLoad` false.

The bootstrap ritual lives in `bootstrap` in [../lib/rigops/launchd.py](../lib/rigops/launchd.py), quoted exactly:

```python
for domain in domains:
    _launchctl("bootout", f"{domain}/{label}")
    _launchctl("enable", f"{domain}/{label}")
    if _launchctl("bootstrap", domain, str(plist_path)):
        _launchctl("enable", f"{domain}/{label}")
        return domain
```

Bootout first, enable before the bootstrap attempt, enable again after success for good
measure, with the `gui/<uid>` domain tried first and `user/<uid>` as the fallback if that bootstrap
fails. `load -w` never appears anywhere in the code.

The runtime-deadline defense lives in [../lib/rigops/cli/reap.py](../lib/rigops/cli/reap.py), not
a plist key: `RIGOPS_RUN_DEADLINE_S` (default 3600s) bounds how long a single run may scan
before it stops and reports partial results, on the reasoning in its own comment: "Per-fetch
timeouts alone let a run crawl: three slow remotes stretched one run to 2h41m." `reap` is
an opt-in launchd job (`rigops jobs install --jobs ...,reap`, daily 08:30), and this deadline is what
bounds its scheduled run: no plist key can express a wall-clock cap.

For hosts without launchd,
[../lib/rigops/templates/cron/crontab.example](../lib/rigops/templates/cron/crontab.example) is the fallback: healing
is gated on launchd - `doctor` only kills or kickstarts with `--heal` under `--supervisor
launchd` - so the cron template runs `doctor --supervisor none` without `--heal`; staleness is
judged from evidence-file ages only, since there's no launchd to inspect for hung PIDs, and on
cron hosts `doctor` judges and reports, never heals.

## Adopting it without rigops

- Resolve every path (interpreter, script, logs) to an absolute path before it goes into the plist; never rely on PATH resolution inside a background agent.
- Set `HOME` and `PATH` explicitly in `EnvironmentVariables`; don't assume the process inherits a usable environment.
- Mark the job a background citizen: `ProcessType Background`, a positive `Nice`, low-priority I/O.
- Let the schedule key own firing (`RunAtLoad false`); loading or reloading a job should never itself execute it.
- Standardize on `bootstrap`/`bootout`/`enable`, never `load -w`; always enable before bootstrap, never only after.
- If the job needs a wall-clock cap, enforce it inside the script via an environment variable - there's no plist timeout key to reach for instead.

[job-registry.md](./job-registry.md) covers what rigops does with the jobs these templates
schedule once they're running; [tiered-refresh.md](./tiered-refresh.md) applies the same
"cheap now, refresh in the background, degrade gracefully" idea to a UI surface instead of a
scheduled job.
