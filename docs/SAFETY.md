# Safety

What each rigops command can destroy, what's opt-in, and the caps that bound it.

## Defaults and caps

| Command | Default | Destructive opt-in | Caps |
|---|---|---|---|
| `rigops doctor` | Report-only: judges and prints, never kills or kickstarts. Custom checks and the notify command still run their configured commands regardless. | `--heal` applies every judged `kill`/`kickstart` from that run. `--exit-zero` makes a failing finding exit 0; the scheduled job always passes it. | Kill only fires on a job judged `hung` (live past `max_runtime_h`); process-group guarded; `heal_cooldown_h` (default 12h) turns a repeat kickstart of the same job id into a notify, and never holds back the kill of a hung job. |
| `rigops reap` | Read-only report. | `--apply` performs removals. `--dry-run` (only meaningful together with `--apply`) prints the exact git commands instead of running them. The opt-in scheduled `reap` job (`install.sh --jobs reap`, daily 08:30) runs with `--apply`, so it removes on schedule. | `max_kills_per_tree` (default 20) - more matched processes under one worktree than this and the whole tree is left alone, no signals sent at all. Removal is fail-closed: any live process under a to-be-removed tree that can't be positively cleared blocks that removal. Each tree is re-judged from its current git record and sessions before anything in it is signalled, so one that turned dirty, detached, locked or in use since the scan is kept, and so is one the run deadline leaves no time to re-judge. Unregistered dirs under `.claude/worktrees` are judged stale against the registry and sessions as they stand at the sweep, never the scan's. If the `ps` or `lsof` snapshot fails, the run signals and removes nothing and says so on stderr. `--selftest` exercises the kill module against disposable throwaway processes, never your rig. |
| `rigops janitor` | Dry-run: reports every match, deletes/truncates nothing. | `--apply` performs the rules' actions. The opt-in scheduled `janitor` job (`install.sh --jobs janitor`, Sunday 10:00) runs with `--apply`, so it deletes on schedule. | `janitor.max_delete` (default 200) - a rule whose match total would push the running delete count over the cap is skipped **whole**, never partially applied. `action: "command"` runs an arbitrary shell command under `--apply`, outside the path-safety gating and outside `max_delete` accounting - a janitor rule with `action: command` deserves the same scrutiny as code. |
| `install.sh` | Plan-only: prints every action, touches nothing. | `--apply` performs the install or uninstall. | Uninstall acts only on what its manifest recorded - it never guesses. `--purge` additionally requires `--uninstall` and passes every deletion target through a `purge_safe` check before any `rm -rf` (see [INSTALL.md](INSTALL.md)). |
| `rigops authprobe` | Ships disabled (`authprobe.enabled: false`); running it reports "disabled" and does nothing else. | `authprobe.enabled: true` in config, or `--force` on the command line. | Runs under a scrubbed environment (`HOME`/`USER`/`PATH`/`SHELL`/`TERM` only, plus an optional injected token) - never inherits the calling shell's environment. |

`rigops reap` keeps a worktree that holds a gitignored file whose name matches `reaper.precious` (default `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`) or an ignored nested git repository, because removing the tree would delete the only copy. The report, the dry run, the log and `--json` name the file, and the text names the `reaper.precious` key to change; an empty list makes no file name precious but keeps the nested-repository guard. Files under `node_modules` or `site-packages` are not checked; list a noisy directory in `reaper.ignored_untracked_dirs` to exclude it too.

For a tree that will be removed, reap also lists the gitignored files that go with it, collapsed to top-level entries (`node_modules/` is one entry) with at most ten printed, and a real removal writes the same list to the reap log first. `--json` carries it as `ignored_files` on the worktree entry.

Config and registry are code. A custom check, notify command, probe, janitor `command` rule or `sources.custom` entry runs with your rights, and the reaper kills and removes on the word of the config, so a config or registry file that group or others can write is refused with exit `2` before anything runs (`doctor` fleet runs and `--heal`, `janitor --apply`, `reap --apply`, `authprobe`, `ledger write` with `sources.custom`). Custom checks and janitor commands run in their own process group, killed whole on timeout. Janitor never truncates a file written in the last 60 seconds. An eval API key is only sent over https or to a loopback host. Janitor base containment is unchanged (it refuses `/`, `$HOME` and the parent of `$HOME`); the permission check is the guard against a config someone else can edit.

`doctor --heal` fails closed on its own state: if `heal-stamps.json` (or `holds.json`, when `cause_policy` reads it) is unreadable, not a JSON object, or holds a non-numeric stamp, the run kickstarts nothing and releases no hold, moves the file aside as `<name>.corrupt-<timestamp>`, and raises a failing finding. A run that cannot heal reports the finding and leaves the file where it is.

A doctor check that cannot measure fails closed too. A check module that fails to import, a check that crashes, or an always-on file the `budget` check cannot read is an `error` finding: the run exits `2` and `--exit-zero` does not hide it. The other checks still run.

## State files

- The state directory is `0700`; a wider one is tightened on first use. So is `${XDG_STATE_HOME:-~/.local/state}/rigops`, which holds the doctor gate record and the hook's nudge state even when `RIGOPS_STATE_DIR` points elsewhere. Keep it on a local disk, because locking is `flock` and appends are `O_APPEND`.
- JSON state (`heal-stamps.json`, `holds.json`, `kicked.json`, `notified.json`, the nudge file, `report-latest.txt`) is written to a temp file and renamed, so a reader never sees half a file.
- A JSONL row is appended with one write, and a row over 1 MiB (`MAX_ROW_BYTES`, 1048576 bytes) is refused with exit `2` instead of being truncated or interleaved.
- Every JSONL row and every JSON state file carries an integer `version` (currently `1`). A row without one reads as version 0. The stamp files are `{"version": 1, "entries": {...}}`; the older flat object, and for `kicked.json` the older list, still read.
- Two writers never interleave: `rigops doctor` runs that write state (`--heal` or a fleet run) and `rigops reap --apply` hold a lock, and a second run waits for the first.
- Readers split by what they decide. Gates and writers are strict: a malformed or newer-version row, or an unreadable file, exits `2`. Reports (`ledger diff`, `ledger --show`, `tax --history`, `doctor` lever checks) skip rows they cannot read, print `skipped N ...` on stderr and exit `0`; `ledger diff` marks the comparison `incomplete`. An unreadable file is exit `2` everywhere.
- Guard state fails closed: a newer-version stamp file, like a corrupt one, makes `doctor --heal` act on nothing and exit `2`, even with `--exit-zero`. `rigops reap --apply` refuses to remove anything when a live-session file is unreadable.

## Kill-path design

Two independent kill paths, same shape, different scope.

**`rigops doctor`** (`lib/rigops/launchd.py`, `terminate_process_group`) signals exactly one already-identified pid - the one launchd reports as running that job's label:

- Refuses to signal if the pid's process-group id is `<= 1`, or equals the doctor process's own group (self-protection, and a pgid of 0/1 can only be a lookup artifact here, never a real job's group).
- Signals the whole process group only when the pid is that group's own leader; otherwise it signals the pid alone, so a non-leader match never takes unrelated siblings down with it.
- SIGTERM, poll every 0.5s up to `kill_grace_s` (default 5s), then SIGKILL once unless the target reads as gone: the pid alone, where a failed `ps` reads as gone, or the whole group while any member remains, even after the leader has exited.

**`rigops reap`** (`lib/rigops/reap_procs.py`, `kill_pids` / `_filter_killable` / `removal_blockers`) signals a *set* of pids matched by cwd/argv under a worktree, plus their full descendant closure:

- Only own-uid, terminal-less (no controlling tty) pids are eligible - an interactive shell parked in an old worktree is never a target.
- A terminal-less process is signalled only when nothing alive outside the tree owns it: walking up through parents that are themselves matched, the top-most matched ancestor must have been reparented to init (`ppid` 1). A matched process whose top-most ancestor has a live parent outside the tree, and every descendant of it, is "owned": it is not signalled, the kill preview and `--json` (`owned` on the kill entry) list it with reason `owned`, the log gets a `SKIP-OWNED` line, and it blocks removal of the tree. This holds for kill-only stale directories too. On Linux with a per-user service manager (systemd `--user`), orphans are reparented to that manager rather than pid 1, so such processes are left alone and the tree is kept with the reason printed.
- A never-signal set (init, the reaper's own pid, and its full ancestor chain) is subtracted first.
- Every pid is re-verified against its original command-line snapshot immediately before a signal is sent, to dodge PID reuse between snapshot and signal.
- The orphan-daemon sweep (launchd-reparented `node_modules` daemons rooted under `reaper.roots`) is off unless `reaper.orphan_sweep` is `true`; with the default config reap signals no orphan.
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
