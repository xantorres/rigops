from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBEXEC = REPO_ROOT / "libexec"


class CliContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.state = self.home / "state"
        self.config = self.home / "config.json"

    def run_cmd(self, cmd, *args, config=None):
        env = {
            **os.environ,
            "HOME": str(self.home),
            "RIGOPS_STATE_DIR": str(self.state),
            "RIGOPS_CONFIG": str(self.config),
            "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
            "XDG_STATE_HOME": str(self.home / "xdg-state"),
        }
        if config is not None:
            self.config.write_text(config if isinstance(config, str) else json.dumps(config))
        return subprocess.run(
            [sys.executable, str(LIBEXEC / f"rigops-{cmd}"), *args],
            capture_output=True, text=True, check=False, timeout=60, env=env,
            stdin=subprocess.DEVNULL,
        )

    def test_reap_help_works_with_a_broken_config(self):
        proc = self.run_cmd("reap", "--help", config="{not json")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage:", proc.stdout)

    def test_reap_rejects_a_bad_max_worktrees_naming_the_key(self):
        empty = self.home / "tree"
        empty.mkdir()
        for bad in ("5", True, -1, 2.5):
            with self.subTest(value=bad):
                config = {"reaper": {"max_worktrees": bad}}
                proc = self.run_cmd("reap", "--root", str(empty), config=config)

                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("reaper.max_worktrees", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(proc.stdout, "")

    def test_reap_accepts_a_zero_or_positive_max_worktrees(self):
        empty = self.home / "tree"
        empty.mkdir()
        for good in (0, 5):
            with self.subTest(value=good):
                config = {"reaper": {"max_worktrees": good}}
                proc = self.run_cmd("reap", "--root", str(empty), config=config)

                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_reap_rejects_list_settings_that_are_not_lists_of_non_empty_strings(self):
        empty = self.home / "tree"
        empty.mkdir()
        bad_values = ("~/projects", None, {"a": "b"}, 3, [""], ["  "], [1], [["x"]])
        for key in ("roots", "ignored_untracked_dirs", "ignored_untracked_prefixes"):
            for bad in bad_values:
                with self.subTest(key=key, value=bad):
                    config = {"reaper": {key: bad}}
                    proc = self.run_cmd("reap", "--root", str(empty), config=config)

                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertIn(f"reaper.{key}", proc.stderr)
                    self.assertNotIn("Traceback", proc.stderr)
                    self.assertEqual(proc.stdout, "")

    def test_reap_accepts_empty_list_settings(self):
        empty = self.home / "tree"
        empty.mkdir()
        config = {"reaper": {"ignored_untracked_dirs": [], "ignored_untracked_prefixes": []}}
        proc = self.run_cmd("reap", "--root", str(empty), config=config)

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_reap_rejects_a_non_boolean_orphan_sweep(self):
        empty = self.home / "tree"
        empty.mkdir()
        for bad in ("false", "true", 1, None, []):
            with self.subTest(value=bad):
                config = {"reaper": {"orphan_sweep": bad}}
                proc = self.run_cmd("reap", "--root", str(empty), config=config)

                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("reaper.orphan_sweep", proc.stderr)
                self.assertEqual(proc.stdout, "")

    def json_out(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_tax_json_is_valid_when_fixed_tax_is_not_configured(self):
        payload = self.json_out(self.run_cmd("tax", "--json"))

        self.assertEqual(payload, {"files": [], "total_bytes": 0})

    def test_tax_json_is_valid_when_no_file_matches(self):
        config = {"fixed_tax": {"paths": [str(self.home / "missing.md")]}}
        payload = self.json_out(self.run_cmd("tax", "--json", config=config))

        self.assertEqual(payload, {"files": [], "total_bytes": 0})

    def test_tax_history_json_is_an_empty_list_without_ledger_rows(self):
        self.assertEqual(self.json_out(self.run_cmd("tax", "--history", "--json")), [])

    def test_ledger_diff_json_is_valid_with_fewer_than_two_rows(self):
        payload = self.json_out(self.run_cmd("ledger", "diff", "--json"))

        self.assertEqual(payload, {
            "baseline": None, "latest": None, "deltas": {}, "interventions": [], "derived": [],
            "fingerprints": {"always_loaded_files": "unknown", "measurement_config": "unknown"},
        })

    def test_backlog_lint_json_is_valid_without_a_backlog_file(self):
        missing = str(self.home / "none.md")
        payload = self.json_out(self.run_cmd("backlog", "lint", "--json", "--file", missing))

        self.assertEqual(payload, {
            "open": 0, "done": 0, "malformed": [], "unknown_area": [], "ok": True,
        })

    def test_janitor_json_is_valid_when_nothing_is_configured(self):
        payload = self.json_out(self.run_cmd("janitor", "--json"))

        self.assertEqual(payload, {
            "rules": [], "total_deleted": 0, "watermarks": [], "apply": False,
        })

    def test_reap_dry_run_without_apply_is_a_usage_error(self):
        proc = self.run_cmd("reap", "--dry-run")

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("--dry-run", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_tax_rejects_a_missing_transcripts_dir(self):
        missing = self.home / "no-such-dir"
        for mode in ("--starts", "--dead"):
            with self.subTest(mode=mode):
                proc = self.run_cmd("tax", mode, "--dir", str(missing))

                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn(f"error: --dir not found: {missing}", proc.stderr)

    def assert_malformed_state_exit(self, proc, path):
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(
            proc.stderr.startswith(f"error: malformed JSON line in {path}:2:"), proc.stderr,
        )
        self.assertNotIn("Traceback", proc.stderr)

    def test_tax_history_reports_a_malformed_ledger_line_and_exits_1(self):
        self.state.mkdir()
        (self.state / "ledger.jsonl").write_text('{"date":"2026-01-01"}\nnot json\n')

        proc = self.run_cmd("tax", "--history")

        self.assert_malformed_state_exit(proc, self.state / "ledger.jsonl")

    def test_ledger_reports_a_malformed_line_and_exits_1(self):
        self.state.mkdir()
        (self.state / "ledger.jsonl").write_text('{"date":"2026-01-01"}\nnot json\n')
        for args in (["diff"], ["write", "--show", "--json"], ["write", "--dry-run"]):
            with self.subTest(args=args):
                proc = self.run_cmd("ledger", *args)

                self.assert_malformed_state_exit(proc, self.state / "ledger.jsonl")

    def test_ledger_diff_reports_a_malformed_interventions_line_and_exits_1(self):
        self.state.mkdir()
        (self.state / "ledger.jsonl").write_text(
            '{"date":"2026-01-01"}\n{"date":"2026-01-08"}\n'
        )
        (self.state / "interventions.jsonl").write_text('{"date":"2026-01-02"}\nnot json\n')

        proc = self.run_cmd("ledger", "diff")

        self.assert_malformed_state_exit(proc, self.state / "interventions.jsonl")

    def test_eval_list_reports_a_malformed_results_line_and_exits_2(self):
        self.state.mkdir()
        (self.state / "eval.jsonl").write_text('{"a":1}\nnot json\n')

        proc = self.run_cmd("eval", "list")

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertTrue(
            proc.stderr.startswith(f"error: malformed JSON line in {self.state / 'eval.jsonl'}:2:"),
            proc.stderr,
        )

    def test_every_command_answers_help_with_exit_0_under_an_empty_home(self):
        commands = sorted(p.name.removeprefix("rigops-") for p in LIBEXEC.glob("rigops-*"))

        for cmd in commands:
            with self.subTest(cmd=cmd):
                proc = self.run_cmd(cmd, "--help")

                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.strip(), "empty --help output")

    def test_empty_json_payloads_keep_the_populated_key_set(self):
        doc = self.home / "fixed.md"
        doc.write_text("x" * 10)
        backlog = self.home / "backlog.md"
        backlog.write_text("- [ ] 2026-01-01 rig: one\n")
        ledger = self.state / "ledger.jsonl"
        self.state.mkdir()
        ledger.write_text('{"date":"2026-01-01"}\n{"date":"2026-01-08"}\n')
        cases = [
            (("tax", "--json"), {"fixed_tax": {"paths": [str(doc)]}},
             ("tax", "--json"), {}),
            (("backlog", "lint", "--json", "--file", str(backlog)), {"backlog": {"areas": ["rig"]}},
             ("backlog", "lint", "--json", "--file", str(self.home / "none.md")), {}),
            (("ledger", "diff", "--json"), {}, ("ledger", "diff", "--json"), {}),
        ]
        for full_args, full_cfg, empty_args, empty_cfg in cases:
            with self.subTest(cmd=full_args[0]):
                ledger.write_text('{"date":"2026-01-01"}\n{"date":"2026-01-08"}\n')
                populated = self.json_out(self.run_cmd(*full_args, config=full_cfg))
                ledger.unlink()
                empty = self.json_out(self.run_cmd(*empty_args, config=empty_cfg))

                self.assertEqual(set(empty), set(populated))

    def test_doctor_without_a_default_registry_notes_it_and_still_records_gates(self):
        proc = self.run_cmd("doctor", "--supervisor", "none")
        config_only = self.run_cmd("doctor", "--config-only")

        self.assertEqual(proc.returncode, config_only.returncode, proc.stderr)
        note = "note: no job registry at ~/.config/rigops/registry.md; job checks skipped"
        self.assertEqual(proc.stderr.strip().splitlines(), [note])
        self.assertTrue((self.home / "xdg-state" / "rigops" / "doctor" / "gates.json").exists())

    def test_doctor_list_without_a_default_registry_is_empty_and_exits_0(self):
        proc = self.run_cmd("doctor", "--list", "--json")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [])

    def test_doctor_with_a_missing_explicit_registry_still_exits_1(self):
        proc = self.run_cmd("doctor", "--registry", str(self.home / "none.md"))

        self.assertEqual(proc.returncode, 1)
        self.assertIn("registry file not found", proc.stderr)

    def test_stable_commands_without_a_config_file_never_traceback(self):
        forms = [
            ("authprobe",), ("backlog", "lint"), ("config", "check"),
            ("doctor", "--supervisor", "none"), ("eit",), ("eval", "list"), ("janitor",),
            ("ledger", "diff"), ("ledger", "write", "--dry-run"), ("ledger", "note", "a note"),
            ("mine", "--dry-run"), ("reap",), ("tax",), ("version",), ("hook", "session-start"),
        ]
        for cmd, *args in forms:
            with self.subTest(cmd=" ".join((cmd, *args))):
                proc = self.run_cmd(cmd, *args)

                self.assertNotIn("Traceback", proc.stdout + proc.stderr)
                lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
                self.assertLessEqual(len(lines), 1, proc.stderr)


if __name__ == "__main__":
    unittest.main()
