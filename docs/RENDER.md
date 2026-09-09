# Render

`rigops render <target>... [--check] [--json] [--source DIR] [--home DIR]` renders a
single source tree onto every surface that reads from it, so surfaces never
drift from each other or get hand-edited out of sync.

## Source contract

Source tree default `~/rig`, override via config `render.source`: `law.md`
(always-on rules, read by every target), `rules/**/*.md` (path-scoped,
frontmatter `paths:`), `agents/<name>.md` (frontmatter `name`, `purpose`,
`tier`, `tools`, `memory`, `voice`, body is the prompt), `skills/<name>/**`
(arbitrary files, `SKILL.md` at each root), `registry/roots.json` (realm map
`roots` plus the repos that get a pointer AGENTS.md, `pointer_repos`).

## Targets

- `claude` - `~/.claude/CLAUDE.md`, `~/.claude/rules/`, `~/.claude/agents/`,
  `~/.claude/skills/`.
- `codex` - `~/.codex/AGENTS.md` (a <=25 line digest of the law) and
  `~/.codex/agents/<name>.toml`.
- `local` - `~/.config/ai-agent/system-prompt.md` (law + voice only).
- `agents` - `~/.claude/references/agents.md`, a generated roster table.
- `repos` - a fixed `AGENTS.md` in every `pointer_repos` entry.
- `all` - every target above.

## Markers

Every rendered markdown file opens with an HTML comment naming its source and
warning not to hand-edit it; the marker lands after frontmatter when the source
has any, otherwise on line 1. TOML agent files get the same warning as a `#`
comment on line 1. Skill files other than `SKILL.md` are copied verbatim, mode
bit included, with no marker.

## Check semantics

`--check` never writes. Each rendered path is `missing`, `differs` from what's
on disk, or `stale` (an extra file inside a managed directory that render
didn't produce). A pointer repo whose directory doesn't exist yet is reported
`skipped`, not a drift. Exit `1` iff any path is missing, differs, or stale.
