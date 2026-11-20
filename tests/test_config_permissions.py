from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import config  # noqa: E402
from rigops.errors import RigopsError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RIGOPS = REPO_ROOT / "bin" / "rigops"


class RequirePrivateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _file(self, name: str, mode: int) -> Path:
        path = self.dir / name
        path.write_text("{}")
        os.chmod(path, mode)
        return path

    def test_a_missing_file_passes(self):
        config.require_private(self.dir / "absent.json")

    def test_a_file_only_the_owner_can_write_passes(self):
        config.require_private(self._file("ok.json", 0o644), self._file("ok2.json", 0o600))

    def test_group_writable_is_refused_with_the_path_and_the_fix(self):
        path = self._file("g.json", 0o664)
        with self.assertRaises(RigopsError) as ctx:
            config.require_private(path)
        self.assertEqual(
            str(ctx.exception),
            f"{path} is writable by group or others; refusing to run commands from it "
            f"(chmod go-w {path})",
        )

    def test_other_writable_is_refused(self):
        with self.assertRaises(RigopsError):
            config.require_private(self._file("o.json", 0o646))

    def test_a_symlink_to_a_writable_file_is_refused(self):
        target = self._file("target.json", 0o666)
        link = self.dir / "link.json"
        link.symlink_to(target)
        with self.assertRaises(RigopsError):
            config.require_private(link)

    def test_the_second_path_is_checked_too(self):
        with self.assertRaises(RigopsError) as ctx:
            config.require_private(self._file("a.json", 0o644), self._file("b.json", 0o664))
        self.assertIn("b.json", str(ctx.exception))


class CommandPathsRefuseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.config = self.home / "config.json"
        self.marker = self.home / "ran"

    def run_cmd(self, args: list, cfg: dict, mode: int = 0o664) -> subprocess.CompletedProcess:
        self.config.write_text(json.dumps(cfg))
        os.chmod(self.config, mode)
        env = {
            **os.environ, "HOME": str(self.home), "RIGOPS_CONFIG": str(self.config),
            "RIGOPS_STATE_DIR": str(self.home / "state"),
            "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
            "XDG_STATE_HOME": str(self.home / "xdg-state"),
        }
        return subprocess.run(
            [str(RIGOPS), *args], capture_output=True, text=True, check=False, timeout=60,
            env=env, stdin=subprocess.DEVNULL,
        )

    def assertRefused(self, proc: subprocess.CompletedProcess) -> None:
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stderr.count("error:"), 1, proc.stderr)
        self.assertIn(f"error: {self.config} is writable by group or others", proc.stderr)

    def _touch(self) -> str:
        return f"touch '{self.marker}'"

    def test_doctor_fleet_run_refuses_before_a_custom_check_runs(self):
        cfg = {"doctor": {"checks": {"custom": [{"name": "c", "command": self._touch()}]}}}
        proc = self.run_cmd(["doctor", "--supervisor", "none", "--no-config"], cfg)
        self.assertRefused(proc)
        self.assertFalse(self.marker.exists())

    def test_doctor_heal_refuses(self):
        self.assertRefused(self.run_cmd(["doctor", "--supervisor", "none", "--heal"], {}))

    def test_doctor_refuses_a_writable_registry(self):
        registry = self.home / "registry.md"
        registry.write_text("schema: rigops.v1\nitems:\n---\n")
        os.chmod(registry, 0o666)
        proc = self.run_cmd(
            ["doctor", "--supervisor", "none", "--no-config", "--registry", str(registry)],
            {}, mode=0o644,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(f"error: {registry} is writable by group or others", proc.stderr)

    def test_doctor_list_and_config_only_still_run(self):
        for args in (["doctor", "--list"], ["doctor", "--config-only", "--check", "pointers"]):
            with self.subTest(args):
                proc = self.run_cmd(args, {})
                self.assertNotIn("writable by group or others", proc.stderr)

    def test_janitor_apply_refuses_before_a_command_rule_runs(self):
        rule = {"name": "r", "action": "command", "command": self._touch()}
        proc = self.run_cmd(["janitor", "--apply"], {"janitor": {"rules": [rule]}})
        self.assertRefused(proc)
        self.assertFalse(self.marker.exists())

    def test_janitor_dry_run_still_runs(self):
        proc = self.run_cmd(["janitor"], {})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_authprobe_refuses_before_the_probe_command_runs(self):
        cfg = {"authprobe": {"enabled": True, "command": ["/bin/sh", "-c", self._touch()]}}
        self.assertRefused(self.run_cmd(["authprobe"], cfg))
        self.assertFalse(self.marker.exists())

    def test_authprobe_disabled_runs_nothing_and_still_exits_0(self):
        proc = self.run_cmd(["authprobe"], {})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_ledger_write_refuses_before_a_custom_source_runs(self):
        cfg = {"sources": {"custom": [
            {"name": "x", "argv": ["/bin/sh", "-c", self._touch()]},
        ]}}
        self.assertRefused(self.run_cmd(["ledger", "write", "--dry-run"], cfg))
        self.assertFalse(self.marker.exists())

    def _trees(self) -> str:
        trees = self.home / "trees"
        trees.mkdir(exist_ok=True)
        return str(trees)

    def test_reap_apply_refuses(self):
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra):
                self.assertRefused(
                    self.run_cmd(["reap", "--apply", *extra, "--root", self._trees()], {}))

    def test_reap_preview_still_runs(self):
        proc = self.run_cmd(["reap", "--root", self._trees()], {})
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
