import base64
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tools" / "redaction-gate.sh"

PLACEHOLDER_RULE = "s zz-sample-[0-9]+\n"
PLACEHOLDER_HIT = "zz-sample-" + "42"

# Built by concatenation so no literal here matches the gate itself.
SHAPES = {
    "dni": "id " + "12345678" + "Z ok",
    "nie": "id " + "X" + "1234567" + "L ok",
    "iban": "iban " + "ES" + "00 1234 5678 9012 3456 7890",
    "iban_compact": "iban " + "ES" + "001234567890123456789012",
    "phone_plus": "call " + "+34" + " 600 111 222",
    "phone_zero": "call " + "0034" + " 600 111 222",
    "phone_compact": "call " + "+34" + "600111222",
    "phone_dash": "call " + "+1" + "-555-010-0199",
    "street_es": "vive en calle " + "Inventada" + " 12, 3B",
    "street_ca": "carrer " + "de la Prova" + " 7",
    "street_en": "ship to 12 " + "Invented" + " Road, London",
    "floor": "Invented " + "Nombre" + " 12, " + "Piso" + " 3",
}

CLEAN = {
    "sha": "index " + "0a1b2c3" + ".." + "d4e5f6a" + " 100644",
    "digest": "sha256 " + "e3b0c44298fc1c149afbf4c8996fb92427ae41e4" + "649b934ca495991b7852b855",
    "uuid": "id " + "123e4567" + "-e89b-12d3-a456-426614174000",
    "version": "bump to v0.3.0 and python 3.11.4",
    "tz": "Date: Wed Nov 4 10:00:00 2026 " + "+0100",
    "diff_date": "+" + "2026-11-04" + "-12",
    "offset_date": "date " + "+2026" + "-11-04",
    "diff_epoch": "+" + "1730000000",
    "plus_prose": "ratio " + "+12" + " and +3",
    "prose": "pay attention to 200k tokens and step 3 of 12",
}


def gate(text):
    with tempfile.TemporaryDirectory() as home:
        msg = Path(home) / "msg.txt"
        msg.write_text(text + "\n", encoding="utf-8")
        env = {**os.environ, "HOME": home}
        env.pop("RIGOPS_REDACTION_PATTERNS", None)
        env.pop("RIGOPS_REDACTION_PATTERNS_B64", None)
        out = subprocess.run(
            ["bash", str(GATE), "--message", str(msg)],
            capture_output=True,
            text=True,
            env=env,
        )
        return out.returncode


def sandbox_gate(root, public=PLACEHOLDER_RULE):
    """Copies the gate under root/tools, with `public` as its public layer."""
    tools = Path(root) / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    script = tools / "redaction-gate.sh"
    shutil.copy(GATE, script)
    if public is not None:
        (tools / "redaction-patterns.public.txt").write_text(public, encoding="utf-8")
    return script


def run_gate(script, *args, home, cwd=None, extra_env=None):
    env = {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CEILING_DIRECTORIES": str(Path(home).parent),
    }
    env.pop("RIGOPS_REDACTION_PATTERNS", None)
    env.pop("RIGOPS_REDACTION_PATTERNS_B64", None)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


class ZeroRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.msg = self.tmp / "msg.txt"
        self.msg.write_text("nothing to see\n", encoding="utf-8")

    def assert_fails_closed(self, out):
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertNotIn("clean", out.stdout)

    def test_missing_public_layer_fails(self):
        script = sandbox_gate(self.tmp / "repo", public=None)
        out = run_gate(script, "--message", str(self.msg), home=self.home)
        self.assert_fails_closed(out)

    def test_public_layer_without_rules_fails(self):
        script = sandbox_gate(self.tmp / "repo", public="# only a comment\n\n")
        out = run_gate(script, "--message", str(self.msg), home=self.home)
        self.assert_fails_closed(out)

    def test_private_layer_without_rules_fails(self):
        script = sandbox_gate(self.tmp / "repo")
        packed = base64.b64encode(b"# only a comment\n").decode()
        out = run_gate(
            script, "--message", str(self.msg), home=self.home,
            extra_env={"RIGOPS_REDACTION_PATTERNS_B64": packed},
        )
        self.assert_fails_closed(out)

    def test_loaded_rules_still_report_clean(self):
        script = sandbox_gate(self.tmp / "repo")
        out = run_gate(script, "--message", str(self.msg), home=self.home)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


def commit_all(root, home):
    env = {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    base = [
        "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Sample",
        "-c", "user.email=sample@example.com", "-C", str(root),
    ]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(base + args, check=True, capture_output=True, env=env)


def git_shim(bin_dir, failing_arg):
    """Puts a git on PATH that fails when called with `failing_arg`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        f'  case "$a" in {failing_arg}) echo "shim: git failed" >&2; exit 128 ;; esac\n'
        "done\n"
        f'exec "{shutil.which("git")}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


class HistoryGitFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.script = sandbox_gate(self.repo)

    def assert_fails_closed(self, out):
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertNotIn("clean", out.stdout)

    def test_history_outside_a_repository_fails(self):
        out = run_gate(self.script, "--history", home=self.home)
        self.assert_fails_closed(out)

    def test_history_diff_log_failure_fails(self):
        commit_all(self.repo, self.home)
        shim = git_shim(self.tmp / "bin", "-p")
        out = run_gate(self.script, "--history", home=self.home, extra_env=shim)
        self.assert_fails_closed(out)

    def test_history_metadata_log_failure_fails(self):
        commit_all(self.repo, self.home)
        shim = git_shim(self.tmp / "bin", "--format=*")
        out = run_gate(self.script, "--history", home=self.home, extra_env=shim)
        self.assert_fails_closed(out)

    def test_history_of_a_clean_repository_passes(self):
        commit_all(self.repo, self.home)
        out = run_gate(self.script, "--history", home=self.home)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class PersonalDataShapeTests(unittest.TestCase):
    def test_each_shape_is_flagged(self):
        for name, text in SHAPES.items():
            with self.subTest(name=name):
                self.assertEqual(gate(text), 1)

    def test_hashes_ids_versions_and_prose_pass(self):
        for name, text in CLEAN.items():
            with self.subTest(name=name):
                self.assertEqual(gate(text), 0)


if __name__ == "__main__":
    unittest.main()
