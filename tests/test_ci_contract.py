from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"
MATRIX = REPO_ROOT / "docs" / "SUPPORT-MATRIX.md"

USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
TAG_COMMENT = re.compile(r"^\s+#\s+v\d+\.\d+\.\d+\s*$")


def _jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def _flow_list(block: str, key: str) -> list[str]:
    match = re.search(rf"^\s+{key}:\s*\[(.*?)\]", block, flags=re.MULTILINE)
    assert match, f"no `{key}: [...]` list"
    return [item.strip().strip("\"'") for item in match.group(1).split(",")]


def _ci_section() -> str:
    text = MATRIX.read_text(encoding="utf-8")
    match = re.search(r"^## CI coverage\n(.*?)(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert match, "docs/SUPPORT-MATRIX.md has no '## CI coverage' section"
    return match.group(1)


def _floor() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*">=([\d.]+)"', text, flags=re.MULTILINE)
    assert match, "pyproject.toml has no requires-python"
    return match.group(1)


class CiContractTests(unittest.TestCase):
    def test_the_test_job_matrix_is_in_the_support_matrix_ci_section(self):
        block = _jobs(CI.read_text(encoding="utf-8"))["test"]
        section = _ci_section()

        for os_name in _flow_list(block, "os"):
            self.assertIn(os_name, section)
        for version in _flow_list(block, "python-version"):
            self.assertIn("latest" if version == "3.x" else version, section)

    def test_every_ci_job_is_named_in_the_support_matrix_ci_section(self):
        section = _ci_section()

        missing = [name for name in _jobs(CI.read_text(encoding="utf-8"))
                   if f"`{name}`" not in section]

        self.assertEqual(missing, [])

    def test_the_ci_section_names_no_removed_smoke_target(self):
        self.assertNotIn("smoke-launchd", _ci_section())

    def test_the_lowest_matrix_python_is_the_declared_floor(self):
        block = _jobs(CI.read_text(encoding="utf-8"))["test"]
        numeric = [v for v in _flow_list(block, "python-version") if re.fullmatch(r"\d+\.\d+", v)]

        lowest = min(numeric, key=lambda v: tuple(int(p) for p in v.split(".")))

        self.assertEqual(lowest, _floor())

    def test_every_action_is_pinned_to_a_full_commit_with_a_tag_comment(self):
        problems = []
        for path in (CI, RELEASE):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = USES.match(line)
                if not match or match.group(1).startswith("./"):
                    continue
                if not PINNED.match(match.group(1)):
                    problems.append(f"{path.name}:{number}: not pinned by SHA: {match.group(1)}")
                elif not TAG_COMMENT.match(match.group(2)):
                    problems.append(f"{path.name}:{number}: no '# vX.Y.Z' comment")
        self.assertEqual(problems, [])

    def test_every_job_has_a_timeout(self):
        missing = [f"{path.name}:{name}" for path in (CI, RELEASE)
                   for name, block in _jobs(path.read_text(encoding="utf-8")).items()
                   if "timeout-minutes:" not in block and not block.startswith("    uses:")]
        self.assertEqual(missing, [])

    def test_ci_runs_for_pushes_to_branches_and_as_a_called_workflow(self):
        head = CI.read_text(encoding="utf-8").split("\njobs:\n", 1)[0]

        self.assertIn("branches: ['**']", head)
        self.assertIn("workflow_call:", head)
        self.assertIn("group: ci-${{ github.ref }}", head)

    def test_release_workflow_is_read_only_by_default_and_never_cancels(self):
        text = RELEASE.read_text(encoding="utf-8")
        head = text.split("\njobs:\n", 1)[0]

        self.assertIn("tags: ['v*']", head)
        self.assertRegex(head, r"(?m)^permissions:\n  contents: read$")
        self.assertIn("group: release-${{ github.ref }}", head)
        self.assertIn("cancel-in-progress: false", head)

    def test_only_the_publish_job_may_request_an_identity_token(self):
        jobs = _jobs(RELEASE.read_text(encoding="utf-8"))

        holders = [name for name, block in jobs.items() if "id-token: write" in block]

        self.assertEqual(holders, ["publish"])
        self.assertIn("environment: pypi", jobs["publish"])

    def test_jobs_that_run_the_runner_checks_do_not_cache_uv(self):
        jobs = _jobs(CI.read_text(encoding="utf-8"))

        self.assertIn("tests/ci/runners.sh", jobs["runners"])
        self.assertRegex(
            jobs["runners"],
            r"astral-sh/setup-uv@\w+ # v[\d.]+\n\s+with:\n\s+enable-cache: false",
        )

    def test_the_release_creates_a_prerelease_for_candidates(self):
        self.assertIn("--prerelease", _jobs(RELEASE.read_text(encoding="utf-8"))["github-release"])


if __name__ == "__main__":
    unittest.main()
