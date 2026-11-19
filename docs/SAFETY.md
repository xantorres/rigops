# Safety

What each rigops command can destroy, what's opt-in, and the caps that bound it.

## Defaults and caps

| Command | Default | Destructive opt-in | Caps |
|---|---|---|---|
| `rigops doctor` | Report-only: judges and prints, never kills or kickstarts. Custom checks and the notify command still run their configured commands regardless. | `--heal` applies every judged `kill`/`kickstart` from that run. | Kill only fires on a job judged `hung` (live past `max_runtime_h`); process-group guarded; `heal_cooldown_h` (default 12h) turns a repeat kickstart of the same job id into a notify, and never holds back the kill of a hung job. |
| `rigops reap` | Read-only report. | `--apply` performs removals. `--dry-run` (only meaningful together with `--apply`) prints the exact git commands instead of running them. The opt-in scheduled `reap` job (`install.sh --jobs reap`, daily 08:30) runs with `--apply`, so it removes on schedule. | `max_kills_per_tree` (default 20) - more matched processes under one worktree than this and the whole tree is left alone, no signals sent at all. Removal is fail-closed: any live process under a to-be-removed tree that can't be positively cleared blocks that removal. Each tree is re-judged from its current git record and sessions before anything in it is signalled, so one that turned dirty, detached, locked or in use since the scan is kept, and so is one the run deadline leaves no time to re-judge. Unregistered dirs under `.claude/worktrees` are judged stale against the registry and sessions as they stand at the sweep, never the scan's. If the `ps` or `lsof` snapshot fails, the run signals and removes nothing and says so on stderr. `--selftest` exercises the kill module against disposable throwaway processes, never your rig. |
| `rigops janitor` | Dry-run: reports every match, deletes/truncates nothing. | `--apply` performs the rules' actions. The opt-in scheduled `janitor` job (`install.sh --jobs janitor`, Sunday 10:00) runs with `--apply`, so it deletes on schedule. | `janitor.max_delete` (default 200) - a rule whose match total would push the running delete count over the cap is skipped **whole**, never partially applied. `action: "command"` runs an arbitrary shell command under `--apply`, outside the path-safety gating and outside `max_delete` accounting - a janitor rule with `action: command` deserves the same scrutiny as code. |
| `install.sh` | Plan-only: prints every action, touches nothing. | `--apply` performs the install or uninstall. | Uninstall acts only on what its manifest recorded - it never guesses. `--purge` additionally requires `--uninstall` and passes every deletion target through a `purge_safe` check before any `rm -rf` (see [INSTALL.md](INSTALL.md)). |
| `rigops authprobe` | Ships disabled (`authprobe.enabled: false`); running it reports "disabled" and does nothing else. | `authprobe.enabled: true` in config, or `--force` on the command line. | Runs under a scrubbed environment (`HOME`/`USER`/`PATH`/`SHELL`/`TERM` only, plus an optional injected token) - never inherits the calling shell's environment. |

`doctor --heal` fails closed on its own state: if `heal-stamps.json` (or `holds.json`, when `cause_policy` reads it) is unreadable, not a JSON object, or holds a non-numeric stamp, the run kickstarts nothing and releases no hold, moves the file aside as `<name>.corrupt-<timestamp>`, and raises a failing finding. A run that cannot heal reports the finding and leaves the file where it is.

## Kill-path design

Two independent kill paths, same shape, different scope.

**`rigops doctor`** (`lib/rigops/launchd.py`, `terminate_process_group`) signals exactly one already-identified pid - the one launchd reports as running that job's label:

- Refuses to signal if the pid's process-group id is `<= 1`, or equals the doctor process's own group (self-protection, and a pgid of 0/1 can only be a lookup artifact here, never a real job's group).
- Signals the whole process group only when the pid is that group's own leader; otherwise it signals the pid alone, so a non-leader match never takes unrelated siblings down with it.
- SIGTERM, poll every 0.5s up to `kill_grace_s` (default 5s), then SIGKILL once unless the target reads as gone: the pid alone, where a failed `ps` reads as gone, or the whole group while any member remains, even after the leader has exited.

**`rigops reap`** (`lib/rigops/reap_procs.py`, `kill_pids` / `_filter_killable` / `removal_blockers`) signals a *set* of pids matched by cwd/argv under a worktree, plus their full descendant closure:

- Only own-uid, terminal-less (no controlling tty) pids are eligible - an interactive shell parked in an old worktree is never a target.
- A never-signal set (init, the reaper's own pid, and its full ancestor chain) is subtracted first.
- Every pid is re-verified against its original command-line snapshot immediately before a signal is sent, to dodge PID reuse between snapshot and signal.
- A matched-set size over `max_kills_per_tree` skips signalling entirely for that tree - at that size the match itself isn't trusted, it isn't "be more careful."
- SIGTERM, wait `kill_grace_s`, SIGKILL survivors.
- The actual `git worktree remove` is gated separately from the kill and fails closed: any pid under that tree still alive, and either policy-excluded from the kill attempt or surviving it, blocks the removal. The liveness check backing this gate treats "can't tell" as "alive."

## Example: janitor dry-run

```text
janitor dry-run
rule stale-session-spool [delete] matched=4 acted=0
  ~/.local/state/demo/spool/req-000.json
  ~/.local/state/demo/spool/req-001.json
  ~/.local/state/demo/spool/req-002.json
  ~/.local/state/demo/spool/req-003.json
rule cap-run-log [truncate] matched=1 acted=0
  ~/.local/state/demo/run.log
rule rolling-settings-backups [keep_newest_n] matched=2 acted=0
  ~/.claude/settings.json.backup.20260801
  ~/.claude/settings.json.backup.20260800
watermark memory-store ~/.claude/projects/demo-app/memory: files=70 kb=0 would-append
total deleted: 0 (files, directory contents included)
```

`matched` lists what the rule found; `acted=0` throughout because this is a dry-run. Re-run with `--apply` to actually delete, truncate, or prune.

## Redaction gate

`make check` runs `tools/redaction-gate.sh` over the working tree (file contents and paths), and - via `make gate-history` - the full git history: commit diffs, authors, committers, subjects, bodies, and tag names, taggers and messages, against public and private pattern layers. Every example output block in this repository's docs, README, or tests is generated on a throwaway sandbox `$HOME` from seeded demo data, or hand-authored from scratch - never captured from a real rig; see `CONTRIBUTING.md` for the process contributors follow.

## Scope of these guarantees

These are the guards the code actually enforces, described at the level the code enforces them - not a claim that any of this is foolproof against, say, a misconfigured registry pointing `health` at the wrong path, or a `janitor` rule whose `path` legitimately contains what you meant to keep. Read a plan, dry-run, or report before reaching for `--apply` or `--heal`.

## See also

- [REGISTRY.md](REGISTRY.md) - the judgment model that decides when `doctor` kills something.
- [INSTALL.md](INSTALL.md) - uninstall and `--purge` in full.
- [CONFIG.md](CONFIG.md) - every cap's config key.
