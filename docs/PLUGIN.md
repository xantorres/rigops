# Plugin notes

Maintainer notes for the Claude Code plugin packaged in this repo. Install steps for users are in [INSTALL.md](INSTALL.md#plugin-install).

## Layout

The plugin lives in `plugin/` because the repo root is the marketplace: `.claude-plugin/marketplace.json` must sit there, not inside the plugin dir. Its entry points at the plugin with `"source": "./plugin"`.

## Hooks

`plugin.json` deliberately has no `hooks` key. Claude Code auto-loads `hooks/hooks.json` by convention; declaring it explicitly triggers a "Duplicate hooks file detected" error. Both hooks (`SessionStart`, `UserPromptSubmit`) run `hooks/rigops-hook.sh <event>`, which forwards stdin to `rigops hook <event>` and always exits 0, silently, when the CLI is missing or too old.

## Validation

`make plugin-validate` runs `claude plugin validate --strict .` from the repo root and `claude plugin validate --strict plugin/`. It is skipped when `claude` is not installed. `make validate-manifests` is the CLI-free backstop. Both must pass before release.

## Version

One string, `rigops.__version__` (`lib/rigops/__init__.py`), written as PEP 440 (`1.0.0rc1`, tag `v1.0.0rc1`). `plugin/.claude-plugin/plugin.json` and the marketplace entry carry the same string, `tools/validate-manifests.py` compares them exactly, and `tests/test_version.py` asserts both equal `__version__`. The README does not repeat it.

The plugin loader and its strict validator accept `1.0.0rc1`, checked on a temp copy. The loader treats any change of the version string as an update and does no semver ordering ([versions and updates](https://code.claude.com/docs/en/plugins/loading.md#versions-and-updates)); in a sandboxed config, `plugin update` moved an installed `1.0.0rc1` to `1.0.0`.

## Version handshake

`hooks/rigops-hook.sh` exports `RIGOPS_PLUGIN_VERSION`, read from the `plugin.json` beside it, before it calls `rigops hook`. An environment variable is used because an older CLI rejects an unknown flag and the wrapper hides that error. On `session-start` only, the CLI adds one line after its own when the plugin's and the CLI's `major.minor` differ: `rigops: plugin <M.m> and CLI <M.m> differ; upgrade the older one (see docs/INSTALL.md#upgrade)`. Patch and pre-release differences print nothing.

## Always-loaded bytes

The skill and command `name` plus `description` load into every session. `tests/test_plugin_surface.py` pins their total; changing one means updating the pin and adding a changelog line.

## Relative source

A relative `"source": "./plugin"` is supported and resolves from the marketplace root ([relative path plugin source](https://code.claude.com/docs/en/plugins/marketplace-reference.md#relative-path-plugin-source)). It works when the marketplace is added from GitHub, git or a local directory, and cannot resolve for a marketplace added by URL to a bare `marketplace.json`. The path must start with `./`. A sandboxed `marketplace add` of a local copy followed by `plugin install rigops@rigops` installed from `./plugin`.
