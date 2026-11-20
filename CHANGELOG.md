# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.11.0] - 2026-11-20

### Upgrading from 0.10.x

- `reaper.roots` now defaults to `[]`; it was the home projects directory. Set `reaper.roots` to the directories to scan to keep the old behaviour. Until then `rigops reap` prints one line to stderr and exits 0, so a scheduled run no longer scans anything; `--root` and `--repo` still do.
- `rigops doctor` with no job registry at the default `doctor.registry` path no longer exits 1. It prints one `note:` line to stderr, skips the job checks, runs the config checks and writes the gates record. An explicit `--registry`, or a `doctor.registry` set to another path, that is missing still exits 1.
- The environment variables `RIGOPS_ROOTS`, `RIGOPS_RETRIEVAL_LOG` and `RIGOPS_PROBES` are no longer read; they belonged to the removed retrieval code.
- `reaper.ignored_untracked_dirs` lost one author-specific build directory from its default. Add it back to the list if your repos need it.
- `sources.rtk.argv` and `sources.rtk.timeout_s` are gone: the `rtk` source is no longer built in, and a config that still holds `sources.rtk` is silently ignored. Move it to a `sources.custom` entry (the example is in `docs/CONFIG.md`) to keep recording it. The `caveman` source is gone the same way. Rows written by 0.10 keep their columns.
- The corrections, denials and denials_headless metrics are redefined: Spanish handling is no longer built in, and two author-specific denial patterns are dropped. `ledger diff` marks all three `not comparable` across the 0.10 to 0.11 row change. To keep the old counting, put the old rules in `ledger.extra_correction_patterns`, `ledger.correction_exclude_patterns` and `ledger.extra_denial_patterns` (all three default to `[]`; see `docs/CONFIG.md`).
- `pointers.memory_roots` no longer lists the author's personal rig path by default. Add it under `doctor.pointers.memory_roots` if your memory notes point there.
- `install.sh --statusline` is removed and exits 2. Wire the statusline by hand from `patterns/statusline.md`. Re-running `install.sh --apply` over a 0.10 statusline install unwires it and prints a note.
- The plugin `card.sh` and `ctx-nudge.sh` hook scripts are replaced by `plugin/hooks/rigops-hook.sh`, which calls the hidden `rigops hook <event>`. Update the plugin; a hand-wired copy of either script must point at the new one. The prompt-scoped nudge is gone, so `context.prompt_nudge` has no effect. `ctx-probe.sh` moved to `patterns/statusline/`.
- The `imports` and `rtk` doctor checks and `doctor.imports.max_lines` are removed, and `doctor --config-only` no longer runs them. `doctor.rtk.version` and `doctor.rtk.path` are ignored. To keep an rtk check, copy the `checks.custom` example from `docs/CONFIG.md`.
- `ledger diff` output changed. Text output gains `always-loaded files:` and `measurement config:` lines, and a redefined metric's delta cell reads `not comparable`. `--json` gains a top-level `fingerprints` object (also present, as `unknown`, when fewer than two rows exist) and a boolean `comparable` on each delta. Parsers of either form must tolerate the new lines and keys, and treat a delta with `comparable` false as no trend.
- The doctor `budget` check no longer counts a registered repo's instruction file, so `budget.always_on_tokens` measures the global file, unscoped rules and the largest memory index only; re-tune the ceiling if you set it. The `agents` check reads the installed agents dir and looks for read-only claims in each file's `description` and body, not `purpose`, so a claim kept only in `purpose` is no longer checked; move it to `description`.
- `rigops mine` lists every group over the session threshold: no `realm:` or `nearest memory:` line and no "Dropped as already in memory" section, and the author slang is gone from the stopwords. Anything that parsed those lines or relied on dedupe against memory must filter the report itself.
- `rigops mine --registry` is removed and fails as an unknown argument. Registry client tokens are no longer masked; list them in your private redaction pattern files.
- `rigops janitor` no longer routes watermarks to realm backlogs and has no `unrouted` status, so it no longer exits non-zero for one. Every tripped watermark goes to `backlog.path`.
- Gate records and custom doctor check results no longer carry `realm`; a `realm` on `checks.custom[]` is accepted and ignored.
- The public redaction pattern file moved from `tools/` to `lib/rigops/redaction-patterns.public.txt`. Point any script that read the old path at the new one.
- `rigops card` is removed. Nothing in rigops replaces it; the session-start hook prints the stale-gates line. The 0.10.1 sources stay in git history at tag `v0.10.1`.
- `rigops nudge` is removed, with its prompt-time retrieval prefetch. Nothing in rigops replaces it; the context-size nudge stays in `rigops hook user-prompt-submit`. The 0.10.1 sources stay in git history at tag `v0.10.1`.
- `rigops render` is removed. Nothing in rigops replaces it, and its `--check` drift findings are gone with the `render` doctor check. The 0.10.1 sources stay in git history at tag `v0.10.1`.
- `rigops retrieval` is removed, with the roots registry, the realm model and the FTS5 index. Nothing in rigops replaces it. The 0.10.1 sources stay in git history at tag `v0.10.1`.
- The `plans`, `realms` and `render` doctor checks are removed, and `doctor --config-only` no longer runs them. Nothing in rigops replaces them. The 0.10.1 sources stay in git history at tag `v0.10.1`.
- These config keys are ignored: `render.source`, `doctor.plans.dir`, `context.prompt_nudge`, `context.nudges_path`, `context.card.max_tokens` and the `context.preload` keys. Of the `context.card` keys only `context.card.doctor_max_age_h` is still read, by the hook; `context.nudge_tiers` and `context.rearm_tokens` are read too. The `needs` and `prefetch` keys of a roots registry are unread with the registry.

### Added

- `rigops hook <event>` (`session-start`, `user-prompt-submit`) replaces the plugin's card and nudge scripts. It always exits 0, prints at most 1000 bytes and is held to a one-second budget in CI. It is hidden from `rigops help`.
- The session-start hook prints one line of stale gates, or one line saying the doctor gate record is missing or unreadable.
- `ledger.extra_correction_patterns`, `ledger.correction_exclude_patterns` and `ledger.extra_denial_patterns` extend the correction and denial detector shared by the ledger and `rigops mine`. The Spanish rules are no longer built in; `tests/fixtures/corrections/es.json` holds them as a config overlay.
- Ledger rows carry `version`, `fixed_tax_sha` and `config_sha`. `ledger diff` marks a delta `not comparable` when the metric was redefined between the two rows' versions, and reports whether the always-loaded files and the measurement config changed.

### Changed

- The statusline moved from `plugin/statusline/` to `patterns/statusline/`, with `ctx-probe.sh` beside it, and is an example to copy rather than an installed component. `install.sh --statusline` is removed and exits 2; re-running `install.sh --apply` over such an install restores the `statusLine` it replaced and removes the copied script, and `install.sh --uninstall` still does the same for an install not yet upgraded.
- The built-in `rtk` and `caveman` ledger sources are removed, and `sources.rtk` is no longer a default. Rows carry only `sources.ccusage` plus custom entries, and the summary line no longer prints `rtk saved` or `caveman proxy`. A `sources.custom` entry named `rtk` or `caveman` now works and stores the command's whole JSON output; `docs/CONFIG.md` shows both. Rows written by 0.10 keep their `rtk_*` columns without showing as deltas.
- `reaper.roots` defaults to `[]` (was `["~/projects"]`). `rigops reap` with no roots and no `--root` or `--repo` prints one line to stderr naming the config path and exits 0; with `--json` stdout still carries the empty report.
- `reaper.ignored_untracked_dirs` defaults to `["node_modules", "dist", ".turbo"]` (was with `"@mf-types"`), and `pointers.memory_roots` drops `~/rig` from its default.
- The `imports` and `rtk` doctor checks are removed. The module rules now run as `tests/test_module_rules.py`; `doctor.imports.max_lines` is gone, and a config that still sets `doctor.rtk` is ignored. `docs/CONFIG.md` shows the rtk pin as a `checks.custom` example.
- The prompt-scoped nudge left the plugin hook and `context.prompt_nudge` is ignored; only the context-size nudge remains.
- The plugin hooks no longer need `jq`.
- The plugin `week` command no longer names an exit code for an unset `ledger.week_reports`, and the `doctor` command says the skills work without the CLI while the hooks print nothing.
- The always-on budget no longer counts a registered repo's instruction file.
- `check_agents` reads the installed agents dir and the `description` field, not the render source and `purpose`.
- The built-in correction rule no longer has Spanish handling, and the denial set drops `checkout behind origin` and `stale backend checkout`. Add them back with `ledger.extra_correction_patterns`, `ledger.correction_exclude_patterns` and `ledger.extra_denial_patterns`.
- `rigops mine` reports "extra correction patterns" and "correction exclude patterns" counts instead of the Spanish ones.
- Watermarks no longer route to realm backlogs: every tripped watermark appends to `backlog.path`, and the `unrouted` status is gone.
- `checks.custom[].realm` is accepted and ignored.
- Gate records in `gates.json` no longer carry `realm`; `version` stays 1.
- `rigops mine` needs no registry or index. `--registry` is removed and now fails as an unknown argument.
- The `rigops mine` report drops memory dedupe and realm labels: every group over the session threshold is listed, with no `realm:` or `nearest memory:` line and no "Dropped as already in memory" section.
- Registry `client_tokens` are no longer masked in the report; list client names in the private pattern files instead.
- The public redaction pattern file moved from `tools/` to `lib/rigops/redaction-patterns.public.txt` and installs with the package.

### Removed

- `rigops card`, `rigops nudge`, `rigops render` and `rigops retrieval`, with their libexec scripts, the `retrieval`, `render` and `context` packages and `docs/RENDER.md`.
- The `plans`, `realms` and `render` doctor checks.
- `rigops mine`'s memory dedupe and Spanish modules (`mine_dedupe`, `mine_es`).
- The matching config keys and example entries (`render`, `context.prompt_nudge`, `context.nudges_path`, `context.card.max_tokens`, `context.preload`, `doctor.plans.dir`).

### Fixed

- `install.sh --apply` over an earlier prefix install now also removes the byte-code cache of the modules it retires and the directories they leave empty, so a later `install.sh --uninstall` can remove the prefix.

## [0.10.1] - 2026-11-20

### Upgrading from 0.10.0

- The scheduled doctor job only reports once the installer runs again. To
  keep self-heal, pass `--heal` on that run and on every later `--apply`.
- reap no longer signals orphan daemons. Set `reaper.orphan_sweep` to `true`
  to turn the sweep back on.
- An install that already holds the example config and the sample registry
  keeps them, because the installer never overwrites either. Replace them by
  hand with your own, or with `config/config.scaffold.json` and
  `config/registry.scaffold.md`.
- `render` leaves every file without its marker in place, including files
  rendered before the marker existed and copied skill scripts. Remove the
  ones you no longer want by hand.
- reap exits 2 on a config it used to accept: a `reaper.roots`,
  `reaper.ignored_untracked_dirs` or `reaper.ignored_untracked_prefixes` that
  is not a list of non-empty strings.

### Added

- doctor: `--exit-zero` makes a run that would exit 1 on a failing finding
  exit 0. Output, findings and heal behaviour do not change.
- install: `--heal` schedules the doctor job with `--heal`. It applies to
  that run only, and is a usage error with `--no-jobs`, `--plugin-only`,
  `--uninstall`, or a `--jobs` list without `doctor`.
- reap: `reaper.orphan_sweep` (default `false`). Anything but a JSON boolean
  exits 2.

### Changed

- install: the scheduled doctor job is report-only unless `--heal` is passed,
  and the installer prints which of the two it installs. The job always
  passes `--exit-zero`, so a failing finding does not make the job itself
  exit 1.
- install: a first install scaffolds an empty config and an empty registry.
  The example config and the sample registry stay under `config/` as
  examples.
- reap: the orphan-daemon sweep runs only when `reaper.orphan_sweep` is
  `true`. With the default config reap signals no orphan.
- reap: a merged worktree that holds a gitignored secret or key file
  (`.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa`, `id_dsa`,
  `id_ecdsa`, `id_ed25519`) or an ignored nested git repository is kept with
  the verdict `skip: precious`, and the log names the file. Paths under
  `node_modules`, `site-packages` and `reaper.ignored_untracked_dirs` are not
  checked.
- ci: the step that requires the private redaction secret is skipped on a
  fork, where the gate runs the public patterns only.

### Fixed

- render: only files that carry the render marker are deleted, and a missing
  source directory exits 2 before anything is written. A hand-made skill or
  agent in a rendered directory used to be pruned as stale.
- install: `--uninstall` removes the paths its manifest lists, their compiled
  files and the directories that leaves empty. It used to sweep `__pycache__`
  and empty directories under the whole prefix, which with `--prefix "$HOME"`
  meant the home directory.
- reap: `reaper.roots`, `reaper.ignored_untracked_dirs` and
  `reaper.ignored_untracked_prefixes` must be lists of non-empty strings, or
  reap exits 2 before any scan. A string was read character by character, and
  an empty prefix marked every untracked file as noise, so a tree holding
  real work could be removed.
- reap: the tree is judged again after the kill and grace wait, right before
  removal. A file written during the wait keeps the tree.
- doctor: `--heal` fails closed on its own state. When `heal-stamps.json` or
  `holds.json` does not parse, is not an object or holds a non-numeric stamp,
  the run kickstarts nothing and releases no hold, moves the file aside as
  `<name>.corrupt-<timestamp>` and raises a failing `heal-state` finding. It
  used to load as empty, which re-armed every cooldown.
- tests: the suite sets its own `HOME`, state and config directories, so a
  run never reads or writes the real ones.

## [0.10.0] - 2026-11-19

### Added

- plugin: `/rigops:week` ends by listing proposed changes: repeated
  corrections from `rigops mine`, backlog lines added since the baseline row,
  and the newest report matching the new `ledger.week_reports` key. Nothing
  is applied without a yes, and each applied change gets a ledger note.
- reap: a repository left with more linked worktrees than the new
  `reaper.max_worktrees` (default 5, `0` for off) is flagged in the report,
  the JSON and the log line. It is a report only; nothing extra is removed.
- install: `--jobs` also ships a janitor job (weekly, Sunday 10:00) and a
  reap job (daily, 08:30), both running `--apply`. They delete on a schedule,
  so a bare first install does not place them, and a bare re-run keeps the ones
  the manifest recorded.
- install: a job that another enabled LaunchAgent already runs is skipped and
  the plist that runs it is named. A copy this install placed earlier is booted
  out and dropped from the manifest, like a job `--jobs` leaves out.

### Changed

- registry: an item missing a required field exits naming the item and every
  missing or blank field, where only `id` was checked before. Presence only;
  `last_verified` is not parsed.
- gate: with no `RIGOPS_REDACTION_PATTERNS_B64`, both default private pattern
  files load, as in CI, and `RIGOPS_REDACTION_PATTERNS` takes a
  colon-separated list. A listed file that is missing exits 2; the two
  defaults stay optional and warn.
- gate: a hit prints where it is (file and line, or commit, part and layer
  rule) and never the matched text. A tag hit prints the tag object sha, and a
  tracked path that matches prints its `git ls-files` entry number.
- gate: `--history` also scans committer name and email and every tag, name,
  tagger and message, and the tree scan covers tracked file paths.
- mine: redaction layers load as the gate loads them, so a colon list, the
  base64 layer and the default files behave the same in both.

### Removed

- config: `config/config.schema.json` is gone. Nothing read it, it required
  nothing, and it lagged the code by about twenty keys. `docs/CONFIG.md` is
  the reference.

### Fixed

- reap: `--help` and usage errors no longer need a valid config, and the
  `--grace-days` help names the config key instead of a resolved number.
- tax, ledger diff, backlog lint, janitor: `--json` prints valid JSON on empty
  state, with the keys the populated payload has. Prose stays without `--json`.
- render: `--json` without `--check` is a usage error, exit 2.
- tax: a `--dir` that does not exist exits 2 with `error: --dir not found`, as
  eit does.
- reap: worktree status is read NUL-delimited, so a path with a space or a
  non-ASCII character stays dirty instead of reading clean and letting a merged
  branch go with uncommitted edits. A rename's source path is compared too, and
  a name that is not UTF-8 no longer aborts the run.
- reap: each worktree is judged again from its current record and session
  files before anything in it is signalled or removed. One that gained work or
  a session since the scan is kept with `verdict changed`.
- reap: when the process snapshot fails (`ps` or `lsof` times out, is missing,
  or lists nothing), the run is report-only: nothing is removed and no signal
  is sent. The failure goes to stderr, the log and a `process_observation_ok`
  JSON key. A liveness check that cannot be read counts as alive.
- reap: a stale-directory sweep only takes real directories that sit directly
  in the repository's own worktrees dir. A symlink there, or a worktrees dir
  that is itself a link, no longer makes the target's processes kill targets,
  and the sweep reads the current worktree list and sessions first.
- janitor: a directory ages by its newest content, not its own mtime, and
  `only_empty` removes it with `rmdir`, so a directory that gained a file is
  left in place and reported.
- janitor: a dry run charges the `max_delete` cap as `--apply` would, and a
  home base reached through a symlinked ancestor is refused.
- launchd: a hung job's process group keeps getting SIGKILL after its leader
  exits, so a worker that ignores SIGTERM no longer outlives the doctor's
  kill.
- launchd: the doctor plist is well-formed XML, so `cause.log_paths` finds
  the installed job's logs, and both job plists put the Homebrew prefixes first
  on `PATH`, so a scheduled run finds `rtk` and `jq`.
- doctor: a `heal-stamps` or `notified` state file holding a number, list or
  string no longer crashes the run.
- fixed tax: one test decides whether a rule loads in every session, so the
  ledger, the context card and the doctor budget count the same rules, and a
  rule file that is not UTF-8 counts toward the tax.
- gate: a missing public layer, or a layer with no rules, exits 2 instead of
  passing everything. So do an unreadable or undecodable layer (it exited 1,
  the "hits found" code), a match file that cannot be written, and a failing
  `git log` in `--history`, where grep's status had hidden it.
- gate: a message holding a NUL byte is scanned, a rule file with CRLF
  endings matches, a failed hit mapping in `--history` exits 2, an author hit
  prints once, a hit in a renamed file names the new path, and same-named
  layers get a position suffix such as `p.txt#2`.
- install: the manifest is written around each launchd job, so a failed
  bootstrap no longer leaves earlier jobs and the failing job's plist out of it.
  Removal boots a job out of both the `gui` and `user` domains.
- install: a `--jobs` run that leaves out an installed job unloads it and
  removes its plist, and `--no-jobs` or `--plugin-only` keeps the job records.
- install: `--uninstall` restores the `statusLine` the user had, or drops the
  key if there was none, after a backup. It keeps a symlinked `settings.json` a
  symlink, deletes a `settings.json` install created once it is empty, and
  finishes when the file cannot be read. A second prefix records the user's
  value, not the first prefix's script, and a settings backup is never
  overwritten.
- install: the manifest goes last in an uninstall, so a declined `--purge`
  keeps it, and the records the uninstall removed are dropped so the next bare
  install does not reschedule them. A plan says `would remove`, not `removed`.
- install: a re-install removes files the previous install recorded that
  this version no longer ships. An edited one is kept with a warning, and a
  file neither manifest lists is never touched.
- reap: a `reaper.max_worktrees` that is not a non-negative integer exits 2
  with an error naming the key, instead of a traceback.
- reap: `--dry-run` without `--apply` exits 2, the usage-error code every
  other command uses, instead of 64.
- doctor: a job with an `always-on` or `keepalive` cadence is no longer
  judged hung and killed once it outlives `max_runtime_h`.
- doctor, ledger: two runs writing the same state file at once both finish.
  Each write uses a temp file of its own instead of one fixed `.tmp` name.
- ledger: friction counts no longer stop at a transcript row holding bytes
  that are not UTF-8.
- tax, eit, rebuilds: a transcript row holding bytes that are not UTF-8 no
  longer stops the run.
- doctor, context card: a path beside the home directory that only shares its
  name prefix, like `/Users/alice2` for home `/Users/alice`, is no longer
  shown as `~2`.
- tools: `memory-path-sweep` exempts a macOS temp path under
  `/private/var/folders` as well as `/var/folders`.

## [0.9.0] - 2026-11-17

### Added

- doctor: built-in lever rules watch turn-1 context (overall and per
  `ledger.watch_projects` group), EIT per turn, cache hit rate, fixed tax,
  corrections and tool errors against the trailing weekly baseline, with no
  config. A configured rule replaces a built-in, `null` turns one off,
  `doctor.levers.defaults: false` drops them all, and `{group}` in a rule key
  expands over the watch groups. Each warning names the command that shows the
  cause. `tools/replay-levers.py` replays a ledger through the rules.
- tax: `rigops tax --starts` attributes session-start context to surface,
  system prompt and tool group, from the request shapes transcripts record;
  `--session ID` shows one start's largest tools. Ledger rows record
  `start_tools` and `start_tool_chars`, and a built-in lever rule watches the
  latter.
- tax: `rigops tax --dead` joins start-time tool schemas with tool-call counts
  over 30 days and ranks the tools and server groups that load but go unused
  by characters carried.
- eit: `rigops eit --rebuilds` finds main-thread turns that re-created a
  cached prefix and classes them as compaction, quick restart, idle resume or
  other, with the model, version and request-shape changes and entry types
  around each. Ledger rows record the EIT share per class, and a built-in lever
  rule watches the total.
- ledger: `rigops ledger diff` lists commits that touched always-loaded config
  in the repos under `ledger.derive_interventions`, tagged as derived, beside
  the manual notes.
- ledger: rows record `main_default_model_pct`, the main thread's EIT share on
  the model settings name as default, and a built-in lever floor of 50% warns
  when sessions run on something else.
- eit: `rigops eit --by group` splits EIT over the `ledger.watch_projects`
  groups, with unmatched turns under `other`.

- eit: `rigops eit --by surface` splits usage over desktop, cli and headless
  sessions, `--by day` lists the last N local days in order, and every group
  reports an input-equivalent cost split into cache reads, cache writes,
  output and input, with output at 5x and 1-hour cache writes at 2x. Ledger
  rows record `cost_by_surface` and the summary line prints each surface at
  1% or more.
- ledger: a built-in `caveman` source records proxy requests and caveman's own
  saved-token estimate, shown on the summary line when `caveman` is on PATH.
- mine: `rigops mine --dry-run` reports the corrections that repeat across
  sessions over a window (`--since`, default 90d). Corrections group as
  complete-linkage cliques, so A~B and B~C never unite A with C, and
  `--min-sessions 1` lists the single ones instead of dropping them. Dry-run
  is the only mode. Paths, amounts, domains, ids and addresses in the report
  are masked, and broken redaction rules are counted and reported.
- doctor: `cause_policy` (`off`, `shadow` or `on`, default `off`) classifies
  each failing job from the tail of its plist log as auth, quota, network,
  deterministic or unknown. `shadow` changes nothing and writes what `on`
  would do to `events.jsonl`; `on` holds auth, quota and network failures
  instead of kicking them and never kicks a deterministic one.
  Exit 0 from `cause_probe_command` releases one kick, and a network hold asks
  it on every run.
- doctor: registry health evidence takes glob patterns. A glob picks the
  newest match; one with no match is no evidence, so the row reads unknown
  instead of healthy.
- doctor: a memory store lint warns on index pointers with no note, unindexed
  notes and an index past its line or byte cap. Wikilinks without a note stay
  allowed and are not counted.
- doctor: flags agents whose prose claims read-only while their tool grants
  allow edits.
- render: rig workflows are copied into the workflows dir. The directory
  stays unmanaged, so a reapply never prunes workflows the harness saves
  there itself.
- render: an agent's `effort` passes through to the rendered frontmatter, so
  an agent can pin its own reasoning effort. Tools and effort are validated
  at render time and the roster gains an Effort column.
- realm: trusted realms resolve for watermarks and probes. A realm is the dir
  behind a realm link that sits at a non-personal registry root. Memory
  watermarks for a realm's projects go to that realm's backlog, or stay
  unrouted when it has none, and never fall back to the personal backlog.
- janitor: a truncate rule archives the head it drops to a dated sibling, so a
  log can be bounded without losing history on the first cut; a delete rule
  ages the siblings out.
- redaction: the gate adds shape rules for tax ids, IBANs, phone numbers and
  street addresses, word-bounded so hashes, uuids and versions stay clear.
- tools: `tools/memory-path-sweep.py` counts dead absolute paths across memory
  notes, archives included. The default mode classes other-host, ephemeral,
  cut-name and template paths separately and never touches token or env
  paths; `--no-exempt` and `--plain` give comparable baselines.

### Changed

- doctor: `notify_command` runs for a finding not yet sent, or one last sent
  24 hours ago or more. A failure that flaps inside 6 hours is the same
  finding. A job the doctor kicked in that run does not page, once per key per
  7 days; kills always page, and at most 2 other pages go out per local day.
  Sent keys live in `doctor/notified.json`, and `tools/replay-notify.py`
  replays an events log through the rules.
- doctor: jobs sharing a launchd label get one kick per run, and a custom
  check that times out is a warning.

### Fixed

- ledger: `corrections` counted Spanish questions and statements that open
  with "no" as corrections and missed Spanish rebukes; it now applies the
  Spanish rule `rigops mine` already keeps.
- eit: `--dir` naming one project directory read only its subagent files and
  labelled them by session; it now reads every session file under the
  project's own label.
- doctor: the summary line printed `OK` while custom checks or config checks
  failed; it now counts failing checks and failing config findings beside
  failing jobs, and a `red for:` line shows how long each has been failing,
  from a `since` the gates record carries between runs.
- retrieval: an explicit search from inside a repo also runs the loose query
  restricted to that repo and ranks the merged pool by score, so a repo
  document that holds only some of the question's words reaches the ranking.
  Prefetch is unchanged.
- realm: trusted mounts match by canonical path, so a symlinked start dir or
  mount resolves and a relative registry path never trusts a dir by cwd. A
  missing realm registry fails closed.
- janitor: watermarks stay unrouted when the registry is unreadable, and the
  janitor reports that as an error; routing them to the personal backlog
  would file client findings there.
- install: a bare re-install keeps the previous manifest's job set, so a job
  dropped on purpose no longer comes back with the next `--apply`. First
  installs and manifests without jobs still install every job.
- doctor: a corrupt stamp in a state file (NaN, infinite, in the future or a
  huge integer) counts as no stamp.

## [0.8.0] - 2026-10-04

### Added

- doctor: `check_mcp` flags every stdio MCP server reachable from the main
  CLI's config -- `~/.claude.json` (user and project scope), `settings.json`,
  `.mcp.json` and every enabled plugin's `.mcp.json` or inline manifest. A
  stdio server spawns one process per live session and the desktop app never
  reaps an idle one; `doctor.mcp.stdio_allow` names servers that stay stdio on
  purpose.
- card: `rigops card` prints a short session-start card for the working
  directory: the realm and prefetch scopes in force, one line per scope from the
  registry's `scope_notes`, a stale line only when a gate the doctor recorded has
  tripped and is visible to that realm, and a budget line only when the estimated
  preload exceeds `context.preload.budget_tokens`. `--hook` reads the
  SessionStart payload and never fails the session. The plugin's `card.sh`
  runs it at session start. The registry's optional `needs` map adds up to 15
  `- need <need>: <route>` lines, the cwd realm's entries then `mixed`'s.
- nudge: `rigops nudge` injects prompt-time context under one token budget:
  the `say` line of every pattern in a `nudges.json` beside the registry that
  matches the prompt (at most once per `repeat_after` prompts in a session),
  then the realm-scoped prefetch with whatever budget remains. Silent when
  nothing matches. The plugin's `ctx-nudge.sh` calls it on every prompt.
- doctor: a fleet run records the gates it found tripped (jobs, custom checks,
  config findings) in `doctor/gates.json` under the local state directory,
  custom checks may carry a `realm`, and a failing check's first stdout line
  becomes its detail.

### Changed

- retrieval: prefetch returns a row only when it clears `min_score` (default now
  -14), matches at least `min_terms` distinct prompt words (2), and matches at
  least `min_head_terms` of them in its title, section, answers or path (1).
  Rows under `memory/archive/` are left to explicit search. Replaying real
  coding prompts, the prefetch had fired on nine turns in ten with fewer than
  half its rows relevant.
- doctor: the pointer check reads the vault's `AGENTS.md` and `playbooks/*.md`
  by default, and ignores `YYYY` date templates as placeholders. It also builds
  its launchd label pattern from `doctor.label_prefix`: `com.` and `local.`
  always count, and so does each configured prefix that ends in a dot when a
  hyphenated name follows (`user.sync-job`, not `user.email` in a code sample).

### Fixed

- ledger: `rigops ledger write` re-checks the date under an exclusive lock
  after its transcript scan, so two overlapping writers can no longer both
  append a row for the same week; the later one refuses, or replaces the row
  with `--force`. It had only checked before a scan that can run for minutes.
- doctor: the rtk check runs the binary named by `doctor.rtk.path` when set.
  The scheduled doctor runs under launchd's minimal `PATH`, which misses a
  Homebrew rtk, so every scheduled run reported rtk missing while an
  interactive `rigops doctor` passed.
- render: `rigops render` leaves a regular file that already holds the
  rendered bytes untouched, still applies its mode, and prints `wrote` only
  for real writes. Rewriting every target bumped each mtime, so a no-op
  render tripped any check comparing a run time against config mtimes.
- render: `rigops render` keeps a target it just wrote over a symlink in a
  managed directory. The stale pass matched files against the symlink's
  destination instead of the target path, so it deleted the fresh file and
  `rigops render all --check` reported it missing until the next render.
- doctor: a notify command that exits non-zero is recorded in `events.jsonl`
  as `notify command exited N`. Only a timeout or a failure to start was
  recorded, so a notifier that ran and failed (command not found, a missing
  dependency) left no trace and alerts stopped without any signal.
- render: `rigops render` and `--check` leave alone the type files the
  function-hook engine writes into a plugin directory each time it loads the
  plugin: everything under `types/` in the plugin manifest directory (the one
  holding `plugin.json`), and a `tsconfig.json` beside that directory when the
  source tree has none. Both were reported as stale and deleted, so a
  doctor-gated commit blocked until the next render removed them, and the next
  plugin load wrote them again.

## [0.7.0] - 2026-09-16

### Added

- doctor: a `levers` config check that reads `ledger.jsonl` and warns when a
  lever configured under `doctor.levers.rules` breaks its limit (`max`) or moves
  past a band around the median of earlier weekly rows (`rise_pct`, `drop_pct`).
  Findings carry a new `warn` severity: printed under `warnings`, never a
  nonzero exit, so a regression in last week's sessions cannot block a commit.
  A missing, stale or malformed ledger is a warning, never a crash.
- ledger: `rigops ledger note --accept LEVER` records a deliberate change, so
  doctor stops warning about that lever's current level.

- ledger: a `text/prompt` column, visible main-thread text characters per human
  prompt, as the prose-verbosity lever. `out/turn` counts thinking and tool-call
  arguments as well as text, so it moves with effort level and batching and no
  longer says anything about verbosity on its own; its description now says so.

- retrieval: a document may declare what it answers, as `answers: ...` in
  frontmatter, in an HTML comment or in a leading line comment. The value is
  indexed into its own column and ranks above a passing mention of the same
  words, so a question phrased in a reader's vocabulary reaches the document
  written in the author's. A document that declares nothing is unchanged.

- retrieval: a `rigops.retrieval` package and `rigops retrieval` command. One
  section-granular FTS5 index over every tree a roots registry claims, each row
  tagged at index time with the realm that owns it, its store, its repository,
  its heading, its start line and its `verified` date. `search` ranks sections
  under a token budget and a scope filter; `prefetch` derives realm and scopes
  from the working directory, never widens, and stays silent below a score
  threshold; `roots` explains what a directory may read; `probe` scores a
  labelled question suite as precision at 3 per realm, with negative probes
  asserting that no row crosses a realm; `stats` reports query latency, returned
  tokens, prefetch silence and how often a query is followed by a raw scan of an
  already indexed tree. Every query appends a row to `retrieval.jsonl` under the
  state directory. `build` and `update` walk only the subtrees an include
  pattern can match, so a large repository contributing two files costs two
  files' worth of work.
- doctor: `check_realms` reports a registry that defines a root with no realm, a
  working directory granted a scope its realm cannot produce, or a root that
  matches nothing on disk, and an index that is missing, stale or holds a realm
  or scope the registry does not define.

### Changed

- doctor: memory notes are a pointer source. `~/.claude/projects/*/memory/*.md`
  joins the default sources, held to the path rule alone and only for paths
  under the new `doctor.pointers.memory_roots` (the rig's own trees by default).
  A note is recalled and acted on, so a dead script or plan path in one misleads
  like a dead rule; it is also a dated record, so another host's filesystem and a
  label, model id, plugin, agent, skill or server it names are left alone.

## [0.6.0] - 2026-09-16

### Added

- doctor: `--staged` (with `--config-only`) judges the commit instead of the
  tree. The checks read the staged content of every staged file through a
  content overlay in `rigops.core`, and only findings about the files the
  commit touches are reported. As a pre-commit hook the unscoped run failed
  every commit while any unrelated edit sat in the working tree, and judged a
  staged fix on the unstaged copy of the same file; the unscoped run still
  answers the other question, whether the live configuration is sound.
- eval: a `rigops.eval` package and `rigops eval` command that gates prompt
  and model changes on a versioned case suite, run against any
  OpenAI-compatible endpoint. A case is one JSON file: a prompt plus
  `contains`, `not_contains`, `regex`, `not_regex`, `json` and `json_keys`
  assertions. `run` scores pass rate and p50/p95 latency per group and appends
  one row per run to `eval.jsonl`; `diff` compares two runs on the cases they
  share unchanged and exits 1 when any case that passed in the base run stops
  passing, or, with `--latency-tolerance`, when a group's p95 grows past the
  tolerance. It exits 2 rather than 0 when the comparison cannot be trusted: a
  baseline case that errored, or a passing case edited or deleted since. `list`
  prints the run history. A seeded demo suite ships under `examples/eval/`.

### Changed

- doctor: `check_budget` measures the instruction file of the largest
  repository in `<render.source>/registry/roots.json` too. A session pays for
  one project file on top of everything the instruction tree authors, so the
  ceiling used to guard a fraction of the surface and call it the whole.
- doctor: the module line cap is a repository rule, judging every module under
  the package root rather than only those inside a subpackage. The
  sibling-import rule stays a package rule: the flat modules are the layer
  `core` wraps. `check_imports.LINE_ALLOWANCES` records the two flat modules
  that predate the cap at the size they were on the day it became a repository
  rule -- they may shrink, never grow -- instead of leaving them advertising a
  limit they are twice over.
- doctor: a plugin id that is neither enabled nor installed is a finding
  rather than a silent skip, so a retired plugin cannot keep being
  recommended. The id pattern now requires a marketplace that starts with a
  letter and rejects dist tags, scoped packages, email addresses and ssh
  remotes, none of which are plugin ids and all of which share their shape.
- doctor: a configured root that does not exist is reported rather than
  disabling its check silently -- `doctor.plans.dir` and the pointer check's
  `skill_roots`/`agent_roots`, matching what the source list already did. A
  default root that is absent stays quiet: not every rig has the layout.
- doctor: the pointer check no longer ignores the transcripts directory
  wholesale. Only session transcripts are skipped, matched by shape (a
  uuid-named `.jsonl` file or its sidecar directory), so a pointer into the
  memory store beside them is verified like any other.

### Fixed

- doctor: `check_rtk` runs only when `doctor.rtk.version` pins a version. It
  used to demand one specific rtk release on every host, so the install smoke
  test, and CI with it, failed wherever rtk was not installed.
- render: an agent source with no tool class list renders no `tools:` line at
  all. It used to render an empty one, which reads as "no tools" rather than
  the "every tool" the omission means.

### Removed

- plugin: the skill-forcing gate hook (`skill-gate.sh`, its example config and
  its table tests) no longer ships with the plugin, and neither does the
  `RIGOPS_SKILL_GATES` variable. Which workflow a prompt must be routed
  through is rig policy, so the engine belongs in the operator's own
  `UserPromptSubmit` dispatcher, where the `rg -r`/`-rn`/`-rl` guard went in
  0.5.0. `patterns/scope-gated-hooks.md` keeps the pattern itself.

## [0.5.0] - 2026-09-09

### Added

- render: a `rigops.render` package and `rigops render` command that renders a
  single source tree onto every surface that consumes it: `claude` (CLAUDE.md,
  rules, agents, skills into `~/.claude`), `codex` (a trimmed AGENTS.md digest
  and per-agent TOML into `~/.codex`), `local` (the law and voice sections into
  `~/.config/ai-agent/system-prompt.md`), `agents` (a generated agent roster at
  `~/.claude/references/agents.md`), and `repos` (a fixed AGENTS.md pointer
  into every registered repo). `--check` reports drift without writing; `all`
  renders every target in one pass.
- doctor: `check_render` runs `rigops render all --check` and turns every
  drifted or stale path into a finding; `check_rtk` pins the token-reduction
  proxy to a configured version and flags a missing install or a mismatch.

### Removed

- plugin: the `rg -r`/`-rn`/`-rl` guard hook is no longer shipped with the
  plugin. It now lives in the user's own `PreToolUse` dispatcher.

## [0.4.0] - 2026-09-08

### Added

- doctor: a `rigops/doctor/` package of config checks, one module per check,
  discovered by naming convention (`check_*.py`). Each module imports
  `rigops.core` and nothing else from the package, and `check_imports` fails the
  run when a module reaches for a sibling or grows past its line cap.
- doctor: `check_pointers` extracts every path, agent, skill, plugin id, MCP
  server, launchd label and model id from the instruction surface (CLAUDE.md,
  rules, references, skills, agents, settings.json, the vault entry points and
  personal-repo CLAUDE.md files) and asserts each one resolves against the
  filesystem, `enabledPlugins`, `installed_plugins.json` and `launchctl list`.
  Prose cannot be type-checked, so a pointer to something deleted is the one
  defect class that rots silently; this makes it a build failure.
- doctor: `check_budget` caps the always-on instruction surface, meaning
  CLAUDE.md plus the rules that carry no `paths:` frontmatter plus the memory
  index, measured as bytes over four. Ceiling is `doctor.budget.always_on_tokens`.
- doctor: `check_plans` lints plan filenames, frontmatter and archive placement
  against the grammar the plans directory documents.
- doctor: `--config-only`, `--no-config` and `--check NAME` flags. Findings print
  in the backlog line grammar with a stable id derived from the defect, so a run
  can be deduplicated against an existing backlog. A finding exits nonzero
  everywhere except under `--heal`, the unattended supervisor path whose own
  registry row is judged by that exit code.
- core: a `rigops/core/` package holding the shared config, state and `Finding`
  primitives, the single import hop the modular packages are allowed.

## [0.3.2] - 2026-09-07

### Fixed

- reap: `discover_repos` no longer aborts a whole `--root` scan when one
  directory under it cannot be read. The `.git` probe raises EACCES on
  Linux where macOS answers False, so a single unreadable directory ended
  a nightly sweep of the tree.

## [0.3.1] - 2026-09-07

### Added

- CLI: `rigops version` (and `rigops --version`) prints the installed
  version, so a report can name the copy it came from.
- reap: the run summary carries `listing_skipped`, the repositories whose
  worktree listing the deadline cut short. `deadline_skipped` counts
  worktrees, so it read 0 when none were ever enumerated.

### Changed

- The install smoke test runs as part of `make check`. It is hermetic --
  a throwaway `HOME`, `--no-jobs`, no network -- and adds about 14 s.
  `make smoke-launchd` stays manual: it loads real launchd jobs.

### Fixed

- reap: the worktree size probe is capped at what is left of the run
  deadline instead of a fixed 120 s, the last call in a repository scan
  that could carry a run past its deadline. A budget too thin to spend
  leaves the size at 0 and marks the repository timed out.
- janitor: the `max_files ignored` warning is printed once per watermark
  instead of once per matched file.
- ledger: `diff` no longer lets the per-model share lifted out of
  `eit_pct_by_model` overwrite a top-level `<model>_pct`, matching the
  precedence the cave score lift already had.
- tests: `tests/__init__.py` makes `python3 -m unittest tests.<module>`
  work alongside `unittest discover`.

## [0.3.0] - 2026-09-06

### Added

- reap: `--repo` is repeatable and resolved paths are deduped, so one run
  can scan several checkouts and a checkout named twice is scanned once.
  Discovery now accepts a `.git` directory one or two levels below the
  root, not only `root/<group>/<repo>`.
- reap: the run summary carries `deadline_skipped`, the worktrees a spent
  budget left unjudged.
- ledger: `diff` lifts the per-model shares and the cave score out of the
  nested source payloads, so the Opus share is visible again. rtk counters
  are all-time snapshots and stay out of the diff.
- janitor: a watermark whose glob matches a single file is judged by
  `max_kb` instead of being skipped. `max_files` against a file is a
  configuration error and says so on stderr.
- doctor: the events log and `--json` carry `stale_count` beside
  `fail_count`, so report-only stale rows stay countable without
  opening the report. Staleness still never triggers the notify
  command.

### Changed

- doctor: a completed run exits 0. Fleet health flows through the report,
  the notify command, the events log and `--json`; nonzero is left to an
  unreadable registry or a bad argument. A red job elsewhere in the fleet
  no longer made the doctor kickstart itself.
- doctor: an item with no launchd label and no evidence collapses into one
  footer line under the table. The JSON payload still lists every job.

### Fixed

- reap: the run deadline is enforced inside each repository scan. The
  worktree listing, every per-worktree git call, and merge-target and
  default-branch resolution take what is left of the budget; worktrees
  with nothing left are reported as deadline skips rather than judged.
  Nightly runs no longer finish hours past a 3600 s deadline.
- doctor: an item whose launchd label does not resolve is judged by
  evidence age. Unknown is reserved for an item with neither a label nor
  evidence.
- doctor: interval cadences are tested before the on-demand pattern, and
  monthly and quarterly are understood, so "manual, expected at least
  monthly" is staleness-checked.
- doctor: a stale item with no label reports no action, since the heal
  loop skips those and nothing would act on a kickstart.
- janitor: a tripped single-file watermark reports one file, not zero.
- reap tests: every case redirects the state directory and the config path
  into a temporary directory, so a run can no longer append fake kill and
  overflow lines to the operator's reaper log.

## [0.2.0] - 2026-08-27

### Added

- Ledger friction columns: `denials`, `denials_headless`, `corrections`,
  `tier3_breaches`, `tool_err_per_100`, computed from user-role transcript
  rows each `ledger write`. Historical rows render `-`. New config key
  `friction.headless_projects` scopes the headless split.
- skill-gate: each gate now fires at most once per session (marker files
  under the state dir, 7-day GC). `RIGOPS_SKILL_GATE_NO_DEDUPE=1` restores
  the old every-prompt behavior.

### Fixed

- reap: git subprocesses run in their own process group and are SIGKILLed
  group-wide on timeout, so a hung remote can no longer stall a run for
  hours past the deadline. The fetch and worktree-list timeouts are also
  clamped to the remaining run budget. Deeper local git calls are not yet
  deadline-clamped; worst-case overrun is one repo scan.

## [0.1.0] - 2026-08-24

Initial release.

### Added

- Measure, intervene, diff loop: `rigops eit` and `rigops ledger` weekly
  rollups, with `ledger note` to record an intervention and `ledger diff` to
  compare the weeks around it.
- Context tax loop: `rigops tax` tracks the config-regrowth series so
  always-loaded context earns its keep.
- Fleet doctor loop: `rigops doctor` reports on registered automation jobs,
  report-only by default with `--heal` as an explicit opt-in.
- Hygiene loop: `rigops reap` for stale worktrees and processes, a
  rules-driven janitor for state directories, and a linted backlog for
  deferred findings.
- Ledger source adapters for `rtk`, `ccusage`, and custom commands or JSON
  files, annotating ledger rows without ever breaking a write.
- Headless auth probe for unattended rigs; ships disabled and off the
  default schedules.
- Claude Code plugin: five commands (doctor, ledger, tax, backlog, week),
  two skills (ops-loop, fleet-triage), three hooks (skill gate, context
  nudge, rg flag guard), and an EIT statusline.
- `install.sh` installer that prints its plan by default and only writes
  with `--apply`, plus launchd job templates for macOS and a cron example
  for Linux.
- Redaction gate over the working tree and full history, shellcheck, ruff,
  unit tests, hook table tests, plugin manifest validation, and fresh-HOME
  smoke tests wired into `make check` and CI.
