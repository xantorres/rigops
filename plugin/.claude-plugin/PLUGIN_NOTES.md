# Plugin Notes

Maintainer notes for the Claude Code plugin packaged in this repo.

## Layout

The plugin lives in `plugin/` because the repo root is the marketplace:
`.claude-plugin/marketplace.json` must sit there, not inside the plugin dir.

## Hooks

`plugin.json` deliberately has no `hooks` key. Claude Code auto-loads
`hooks/hooks.json` by convention; declaring it explicitly triggers a
"Duplicate hooks file detected" error.

## Validation

Run `claude plugin validate .` from the repo root, then again from
`plugin/`. Both must pass before release.

## Known risk

Some Claude Code versions may reject a marketplace entry whose `source`
points at a subdirectory (`"./plugin"`). Fallback: flatten the plugin to
the repo root. Check this against the current release before publishing.
