"""Moving off the shell installer ends in one job set and the settings from before."""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import wrapper

REPO = Path(__file__).resolve().parent.parent
LABELS = {"com.rigops.doctor", "com.rigops.ledger"}
OLD_SUBCOMMANDS = {"bootout", "bootstrap", "disable", "enable", "list"}
FAKE_LAUNCHCTL = f"""#!{sys.executable}
import json, os, plistlib, sys

root = os.environ["FAKE_LAUNCHCTL_DIR"]
args = sys.argv[1:]
with open(os.path.join(root, "calls.log"), "a") as fh:
    fh.write(json.dumps(args) + "\\n")
state = os.path.join(root, "loaded.json")
loaded = json.load(open(state)) if os.path.exists(state) else []
cmd = args[0] if args else ""
if cmd == "bootstrap":
    label = plistlib.load(open(args[2], "rb"))["Label"]
    if label in loaded:
        sys.exit(5)
    loaded.append(label)
elif cmd == "bootout":
    target = args[1]
    label = target.rsplit("/", 1)[-1]
    if label not in loaded:
        sys.exit(3)
    loaded.remove(label)
elif cmd == "list":
    if len(args) > 1:
        if args[1] not in loaded:
            sys.exit(113)
        print('{{\\n\\t"Label" = "%s";\\n\\t"LastExitStatus" = 0;\\n}};' % args[1])
    else:
        print("PID\\tStatus\\tLabel")
        for label in loaded:
            print("-\\t0\\t" + label)
json.dump(loaded, open(state, "w"))
"""


def archive(tag: str, dest: Path) -> Path:
    """The tag's tree under dest/tag, or a skip (a failure when CI is set) if it is not local."""
    probe = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        capture_output=True, check=False,
    )
    if probe.returncode != 0:
        if os.environ.get("CI"):
            raise AssertionError(f"tag {tag} is missing; CI must fetch tags")
        raise unittest.SkipTest(f"tag {tag} is not available locally")
    tree = dest / tag
    tree.mkdir()
    packed = subprocess.Popen(["git", "-C", str(REPO), "archive", tag], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(tree)], stdin=packed.stdout, check=True)
    packed.stdout.close()
    if packed.wait() != 0:
        raise AssertionError(f"git archive {tag} failed")
    return tree


@unittest.skipUnless(sys.platform == "darwin", "launchd jobs are macOS only")
class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.trees = {}
        for tag in ("v0.10.1", "v0.12.0"):
            cls.trees[tag] = archive(tag, Path(tmp.name).resolve())

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.fake_dir = self.tmp / "fake"
        self.fake_dir.mkdir()
        fake = self.fake_dir / "launchctl"
        fake.write_text(FAKE_LAUNCHCTL)
        fake.chmod(0o755)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.wrapper = wrapper(self.bin)
        (self.tmp / "tmp").mkdir()
        self.settings = self.home / ".claude" / "settings.json"
        self.before = {
            "statusLine": {"type": "command", "command": "/home/alice/bin/statusline.sh"},
            "theme": "dark",
        }
        self.settings.write_text(json.dumps(self.before))

    def env(self, with_lib: bool = False) -> dict:
        env = {
            "HOME": str(self.home),
            "TMPDIR": str(self.tmp / "tmp"),
            "PATH": f"{self.fake_dir}{os.pathsep}{self.bin}{os.pathsep}/usr/bin:/bin",
            "RIGOPS_STATE_DIR": str(self.tmp / "state"),
            "XDG_STATE_HOME": str(self.tmp / "xdg-state"),
            "XDG_CONFIG_HOME": str(self.tmp / "xdg-config"),
            "FAKE_LAUNCHCTL_DIR": str(self.fake_dir),
        }
        if with_lib:
            env["PYTHONPATH"] = str(REPO / "lib")
        return env

    def run_old(self, tag: str, *flags: str) -> None:
        argv = [
            str(self.trees[tag] / "install.sh"), *flags,
            "--yes", "--python", sys.executable, "--home", str(self.home),
        ]
        proc = subprocess.run(
            argv, capture_output=True, text=True, check=False, env=self.env(), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def loaded(self) -> list:
        state = self.fake_dir / "loaded.json"
        return json.loads(state.read_text()) if state.exists() else []

    def calls(self) -> list:
        log = self.fake_dir / "calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def plist(self, label: str) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{label}.plist"

    def migrate(self, install_tag: str) -> None:
        prefix = self.home / ".local" / "share" / "rigops"
        flags = ["--statusline"] if install_tag == "v0.10.1" else []
        self.run_old(install_tag, "--apply", *flags)
        self.assertTrue(prefix.is_dir())
        self.assertEqual(set(self.loaded()), LABELS)
        for label in LABELS:
            self.assertTrue(self.plist(label).exists(), label)
        if flags:
            self.assertNotEqual(json.loads(self.settings.read_text()), self.before)

        self.run_old("v0.12.0", "--uninstall", "--apply")
        self.assertFalse(prefix.exists())
        self.assertEqual(self.loaded(), [])

        proc = subprocess.run(
            [str(self.wrapper), "jobs", "install", "--apply"],
            capture_output=True, text=True, check=False, env=self.env(with_lib=True), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def assert_end_state(self) -> None:
        self.assertEqual(set(self.loaded()), LABELS)
        self.assertEqual(len(self.loaded()), len(LABELS))
        for label in LABELS:
            with self.plist(label).open("rb") as fh:
                self.assertEqual(plistlib.load(fh)["ProgramArguments"][0], str(self.wrapper))
        self.assertEqual(json.loads(self.settings.read_text()), self.before)
        home = str(self.home)
        for args in self.calls():
            for arg in args:
                if arg.endswith(".plist"):
                    self.assertTrue(arg.startswith(home + os.sep), arg)
        self.assertEqual(self.duplicate_bootstraps(), [])

    def duplicate_bootstraps(self) -> list:
        live, duplicates = set(), []
        for args in self.calls():
            if args[0] == "bootstrap":
                label = plistlib.loads(Path(args[2]).read_bytes())["Label"]
                if label in live:
                    duplicates.append(label)
                live.add(label)
            elif args[0] == "bootout":
                live.discard(args[1].rsplit("/", 1)[-1])
        return duplicates

    def test_old_scripts_reach_launchctl_only_by_name(self):
        for tag, tree in self.trees.items():
            text = (tree / "install.sh").read_text()
            self.assertNotIn("/bin/launchctl", text, tag)
            used = set(re.findall(r"\blaunchctl\s+([a-z]+)", text))
            self.assertTrue(used, tag)
            self.assertLessEqual(used, OLD_SUBCOMMANDS, tag)

    def test_a_0_10_prefix_install_ends_as_one_job_set_with_settings_restored(self):
        self.migrate("v0.10.1")
        self.assert_end_state()

    def test_a_0_12_prefix_install_ends_as_one_job_set_with_settings_untouched(self):
        self.migrate("v0.12.0")
        self.assert_end_state()


if __name__ == "__main__":
    unittest.main()
