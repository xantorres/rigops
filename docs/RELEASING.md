# Releasing

A release is a pushed tag. The `release` workflow tests, checks, builds, publishes to PyPI with attestations and creates the GitHub release; nothing else is done by hand.

## One-time setup (owner)

1. On PyPI, add a trusted publisher for the project `rigops`: this repository, workflow `release.yml`, environment `pypi`.
2. In the repository settings, create the environment `pypi`. Add required reviewers there if a release should wait for a click.
3. Make sure the repository secret the redaction gate needs is set; the test stage runs the same checks as any push.

## Cut a release candidate

1. Pick the version as one PEP 440 string, for example `1.2.0rc1`.
2. Set that string in the three version files: `lib/rigops/__init__.py`, `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
3. In `CHANGELOG.md`, add `## [1.2.0rc1] - YYYY-MM-DD` under an empty `## [Unreleased]`, moving the unreleased entries beneath it.
4. List the commits since the last tag that never touched `CHANGELOG.md` and backfill an entry for each user-visible change. `git log <last-tag>..HEAD --format='%h %s'` gives the commits; `git log <last-tag>..HEAD --format=%h -- CHANGELOG.md` gives the ones that did touch it.
5. Run `make check` with a throwaway state directory, so the suite never reads yours:

   ```sh
   RIGOPS_STATE_DIR="$(mktemp -d)" XDG_STATE_HOME="$(mktemp -d)" make check
   ```

6. Rerun the unit suite once with bytecode writing allowed, in case the shell sets `PYTHONDONTWRITEBYTECODE`:

   ```sh
   env -u PYTHONDONTWRITEBYTECODE make test
   ```

7. Commit, push the branch and wait for green CI. Do not tag before it is green.
8. Tag and push the branch and the tag together:

   ```sh
   git tag -a v1.2.0rc1 -m v1.2.0rc1
   git push --atomic origin main v1.2.0rc1
   ```

The workflow does the rest. A version with an `a`, `b` or `rc` suffix is published as a pre-release on PyPI and on GitHub.

## Cut the final from its last candidate

Between the last candidate and the final, only the version strings and the changelog heading change: set `1.2.0` in the three version files and rename the heading to `## [1.2.0] - YYYY-MM-DD`. Nothing else, not even a typo; fix those in a new candidate. Renaming the candidate heading to the final is the one permitted edit of a released changelog heading. Commit it and wait for green CI before tagging.

Check it before tagging, with the same annotated tag the push will carry:

```sh
git tag -a v1.2.0 -m v1.2.0
python3 tools/release-check.py v1.2.0
```

If the check fails, delete the tag (`git tag -d v1.2.0`), fix the diff and tag again. The release workflow runs the same check and refuses to publish a final that differs from its highest candidate in anything else, including renamed or re-moded files. A final with no candidate for that version fails: cut `1.2.0rc1` first, even for a patch release.

When the check passes, the tag is already in place; push it: `git push --atomic origin main v1.2.0`.

## After a release

- Upgrade with the tool that installed it: `pipx upgrade rigops`, or `uv tool upgrade rigops`. `rigops jobs status` should still show the jobs loaded; the plists name the same executable, so they do not change.
- Refresh the plugin through its marketplace so the plugin and the CLI report the same version. The session line warns when their major.minor differ.

## What the workflow does

`release.yml` runs on every pushed `v*` tag. One run per ref; a second push of the same tag waits instead of cancelling.

| Job | What it does |
|---|---|
| `test` | Runs `ci.yml` as a called workflow: lint and `make check`, the unit, hook and smoke tests on macOS and Linux with Python 3.11 and the latest release, the wheel under `uvx`, `pipx run` and a venv, and the scheduled-jobs install, upgrade and uninstall roundtrip on macOS. |
| `verify` | `tools/release-check.py`: the tag, `__version__`, `plugin.json` and the marketplace entry agree, and a final has a candidate for its version and differs from the highest one only in version strings and changelog headings. |
| `build` | Takes the sdist and wheel that the `package` job of `test` built and the runner jobs installed, so the files published are the files tested; checks the wheel again with `tools/check-wheel.py`, writes the release notes from the changelog with `tools/release-notes.py`, and uploads both as an artifact. Needs `test` and `verify`. |
| `publish` | In the `pypi` environment, with the only `id-token: write` grant, uploads `dist/` to PyPI by trusted publishing with attestations. |
| `github-release` | With the only `contents: write` grant, creates the GitHub release for the tag with the notes and the files, marked pre-release for `a`, `b` and `rc` versions. |

A failed run is rerun from the Actions page. If `publish` succeeded and `github-release` did not, rerun only the failed job; PyPI never accepts the same version twice, so a fix after a publish is a new version.
