"""The config spec table drives defaults, validation and the unknown and removed key reports."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import config, config_spec  # noqa: E402
from rigops.doctor import check_budget, check_levers, check_memory  # noqa: E402
from rigops.errors import RigopsError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RIGOPS = REPO_ROOT / "bin" / "rigops"

BAD_SHAPES = [
    ("string for a number", {"doctor": {"heal_cooldown_h": "12"}}, "doctor.heal_cooldown_h"),
    ("true for an int", {"reaper": {"max_worktrees": True}}, "reaper.max_worktrees"),
    ("float for an int", {"reaper": {"grace_days": 1.5}}, "reaper.grace_days"),
    ("object for a list", {"janitor": {"watermarks": {"a": 1}}}, "janitor.watermarks"),
    ("list for an open map", {"ledger": {"watch_projects": ["x"]}}, "ledger.watch_projects"),
    ("int in a string map", {"ledger": {"watch_projects": {"a": 1}}}, "ledger.watch_projects.a"),
    ("string for a list", {"fixed_tax": {"paths": "a"}}, "fixed_tax.paths"),
    ("int in a string list", {"fixed_tax": {"paths": ["a", 1]}}, "fixed_tax.paths[1]"),
    ("list for a section", {"doctor": []}, "doctor"),
    ("unknown janitor action", {"janitor": {"rules": [{"action": "nuke"}]}},
     "janitor.rules[0].action"),
    ("string age in a janitor rule",
     {"janitor": {"rules": [{"action": "delete", "older_than_days": "7"}]}},
     "janitor.rules[0].older_than_days"),
    ("string for a nullable object", {"doctor": {"checks": {"disk_free": "/"}}},
     "doctor.checks.disk_free"),
    ("string in an int list", {"context": {"nudge_tiers": [1, "2", 3]}},
     "context.nudge_tiers[1]"),
    ("string for a bool", {"authprobe": {"enabled": "yes"}}, "authprobe.enabled"),
]

REMOVED = {
    "context": {"preload": {"law": "~/x.md", "fixed_tokens": 1}, "card": {"max_tokens": 9},
                "nudges_path": "~/n.json", "prompt_nudge": False},
    "doctor": {"imports": {"max_lines": 9}, "plans": {"dir": "~/p"},
               "rtk": {"version": "rtk 1"}},
    "render": {"source": "~/rig"},
    "retrieval": {"registry": "~/r.json"},
    "sources": {"rtk": {"argv": ["rtk"]}, "caveman": {"timeout_s": 3}},
}


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.config = self.home / "config.json"

    def run_cmd(self, args, config_value=None):
        env = {
            **os.environ,
            "HOME": str(self.home),
            "RIGOPS_STATE_DIR": str(self.home / "state"),
            "RIGOPS_CONFIG": str(self.config),
            "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
            "XDG_STATE_HOME": str(self.home / "xdg-state"),
        }
        self.config.unlink(missing_ok=True)
        if config_value is not None:
            self.config.write_text(json.dumps(config_value))
        return subprocess.run(
            [str(RIGOPS), *args], capture_output=True, text=True, check=False, timeout=60,
            env=env, stdin=subprocess.DEVNULL,
        )


class SpecTableTests(unittest.TestCase):
    def test_defaults_are_generated_from_the_spec(self):
        generated = {}
        for key in config_spec.SPEC:
            if key.default is config_spec.UNSET:
                continue
            node = generated
            *parents, leaf = key.path.split(".")
            for part in parents:
                node = node.setdefault(part, {})
            node[leaf] = key.default
        self.assertEqual(config.DEFAULTS, generated)

    def test_every_default_passes_its_own_type(self):
        for key in config_spec.SPEC:
            if key.default is not config_spec.UNSET:
                key.type.check(key.default, key.path)

    def test_spec_paths_are_unique_and_documented_in_one_line(self):
        paths = [key.path for key in config_spec.SPEC]
        self.assertEqual(len(paths), len(set(paths)))
        for key in config_spec.SPEC:
            self.assertTrue(key.doc.strip() and "\n" not in key.doc, key.path)

    def test_added_defaults_equal_the_fallbacks_the_call_sites_pass(self):
        self.assertEqual(config.DEFAULTS["doctor"]["budget"]["always_on_tokens"],
                         check_budget.DEFAULT_CEILING)
        self.assertEqual(config.DEFAULTS["doctor"]["levers"]["max_age_days"],
                         check_levers.DEFAULT_MAX_AGE_DAYS)
        self.assertEqual(config.DEFAULTS["doctor"]["memory"]["index_max_lines"],
                         check_memory.DEFAULT_MAX_LINES)
        self.assertEqual(config.DEFAULTS["doctor"]["memory"]["index_max_bytes"],
                         check_memory.DEFAULT_MAX_BYTES)
        self.assertEqual(config.DEFAULTS["doctor"]["memory"]["sources"],
                         check_memory.DEFAULT_SOURCES)
        self.assertIs(config.DEFAULTS["doctor"]["levers"]["defaults"], True)
        self.assertEqual(config.DEFAULTS["context"]["card"]["doctor_max_age_h"], 2)

    def test_every_key_read_through_config_get_is_in_the_spec(self):
        import re
        spec = {key.path for key in config_spec.SPEC}
        pattern = re.compile(
            r"""(?:cfg_get|config\.get)\(\s*(?:rigops_config\.load\(\)|cfg),\s*"([a-z_.]+)\""""
        )
        found = set()
        sources = [*(REPO_ROOT / "lib").rglob("*.py"), *(REPO_ROOT / "libexec").glob("rigops-*")]
        for source in sources:
            found.update(pattern.findall(source.read_text(encoding="utf-8")))
        missing = sorted(p for p in found if not any(
            s == p or s.startswith(p + ".") for s in spec))
        self.assertEqual(missing, [])


class ValidationTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(value))
            return config.load(path)

    def test_number_accepts_float_and_int_but_not_bool(self):
        loaded = self.load({"doctor": {"kill_grace_s": 0.5}})
        self.assertEqual(loaded["doctor"]["kill_grace_s"], 0.5)
        self.assertEqual(self.load({"doctor": {"kill_grace_s": 3}})["doctor"]["kill_grace_s"], 3)
        with self.assertRaises(RigopsError) as ctx:
            self.load({"doctor": {"kill_grace_s": True}})
        self.assertIn("doctor.kill_grace_s", str(ctx.exception))

    def test_int_rejects_true(self):
        with self.assertRaises(RigopsError) as ctx:
            self.load({"janitor": {"max_delete": True}})
        self.assertIn("janitor.max_delete", str(ctx.exception))

    def test_nullable_object_accepts_null_and_object(self):
        self.load({"doctor": {"checks": {"disk_free": None}}})
        self.load({"doctor": {"checks": {"disk_free": {"path": "/", "warn_gb": 0.5}}}})

    def test_janitor_rule_union_checks_by_action(self):
        self.load({"janitor": {"rules": [
            {"action": "delete", "path": "/tmp/x", "older_than_days": 7},
            {"action": "truncate", "path": "/tmp/y", "keep_lines": 10, "archive": True},
            {"action": "command", "command": "true", "timeout_s": 2.5},
            {"name": "no action", "path": "/tmp/z"},
        ]}})
        with self.assertRaises(RigopsError) as ctx:
            self.load({"janitor": {"rules": [{"action": "truncate", "keep_lines": "9"}]}})
        self.assertIn("janitor.rules[0].keep_lines", str(ctx.exception))

    def test_unknown_and_removed_keys_never_raise(self):
        self.load({"zzz": 1, "doctor": {"nope": []}, **REMOVED})


class BadShapeTests(CliCase):
    def test_bad_shapes_exit_2_naming_the_key_path(self):
        for name, value, path in BAD_SHAPES:
            with self.subTest(name):
                proc = self.run_cmd(["config", "check"], value)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn(path, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_every_command_that_loads_config_exits_2_on_a_bad_type(self):
        bad = {"doctor": {"heal_cooldown_h": "12"}}
        for args in (["config", "get", "doctor"], ["tax"], ["janitor"],
                     ["doctor", "--list", "--registry", "x"], ["eit"], ["authprobe"]):
            with self.subTest(args):
                proc = self.run_cmd(args, bad)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("doctor.heal_cooldown_h", proc.stderr)

    def test_a_float_is_accepted_for_a_number_key(self):
        proc = self.run_cmd(["config", "check"], {"doctor": {"kill_grace_s": 0.5}})
        self.assertEqual(proc.returncode, 0, proc.stderr)


class UnknownKeyTests(CliCase):
    def test_config_check_reports_unknown_keys_at_every_depth_but_not_in_open_maps(self):
        proc = self.run_cmd(["config", "check"], {
            "zzz": 1,
            "doctor": {"nope": 1, "checks": {"deep": 2}},
            "ledger": {"watch_projects": {"free": "x"}},
            "sources": {"ccusage": {"extra": 1}},
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for key in ("zzz", "doctor.nope", "doctor.checks.deep", "sources.ccusage.extra"):
            self.assertIn(f"unknown config key: {key}", proc.stderr)
        self.assertNotIn("watch_projects.free", proc.stderr)
        self.assertIn("config: OK", proc.stdout)

    def test_unknown_keys_print_nothing_at_runtime(self):
        cfg = {"zzz": 1, "doctor": {"nope": 1}}
        for args in (["tax"], ["ledger", "diff"], ["hook", "session-start"]):
            with self.subTest(args):
                base = self.run_cmd(args, {})
                proc = self.run_cmd(args, cfg)
                self.assertEqual(proc.returncode, base.returncode)
                self.assertEqual(proc.stderr, base.stderr)
                self.assertNotIn("nope", proc.stdout + proc.stderr)

    def test_doctor_reports_unknown_keys_as_one_info_line_and_changes_nothing_else(self):
        args = ["doctor", "--no-config", "--supervisor", "none"]
        base = self.run_cmd(args, {})
        proc = self.run_cmd(args, {**REMOVED, "zzz": 1, "doctor": {"nope": 1, **REMOVED["doctor"]}})
        self.assertEqual(proc.returncode, base.returncode)
        info = [line for line in proc.stdout.splitlines() if "unknown config key" in line]
        self.assertEqual(len(info), 1, proc.stdout)
        self.assertTrue(info[0].startswith("info:"))
        self.assertIn("zzz", info[0])
        self.assertIn("doctor.nope", info[0])
        for removed in ("preload", "render", "retrieval", "rtk", "caveman"):
            self.assertNotIn(removed, info[0])
        self.assertNotIn("unknown config key", base.stdout)

    def test_doctor_json_carries_the_unknown_keys_and_gates_stay_untouched(self):
        args = ["doctor", "--json", "--no-config", "--supervisor", "none"]
        base = json.loads(self.run_cmd(args, {}).stdout)
        proc = self.run_cmd(args, {**REMOVED, "zzz": 1})
        payload = json.loads(proc.stdout)
        self.assertEqual(base["unknown_config_keys"], [])
        self.assertEqual(payload["unknown_config_keys"], ["zzz"])
        self.assertEqual(payload["gates"], base["gates"])
        self.assertEqual(payload["findings"], base["findings"])
        self.assertEqual(proc.returncode, 0)


class RemovedKeyTests(CliCase):
    def test_config_check_prints_the_migration_note_instead_of_unknown(self):
        proc = self.run_cmd(["config", "check"], REMOVED)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("unknown", proc.stderr)
        for key in ("context.preload", "context.card.max_tokens", "context.nudges_path",
                    "doctor.imports", "doctor.plans", "doctor.rtk", "render", "retrieval",
                    "sources.rtk", "sources.caveman"):
            self.assertRegex(proc.stderr, rf"removed in 0\.11: {key}\b")

    def test_removed_map_covers_the_documented_0_10_subsystems(self):
        for key in ("context.preload.law", "context.card.max_tokens", "context.nudges_path",
                    "doctor.imports.max_lines", "doctor.plans.dir", "doctor.rtk.path",
                    "render.source", "retrieval.registry", "sources.rtk.argv"):
            self.assertIsNotNone(config_spec.removed_note(key), key)
        self.assertIsNone(config_spec.removed_note("context.card.doctor_max_age_h"))
        self.assertIsNone(config_spec.removed_note("context.nudge_tiers"))

    def test_a_live_config_with_removed_keys_behaves_as_without_them(self):
        live = {"context": {"preload": {"law": "~/x.md", "budget_tokens": {"a": 1}},
                            "card": {"max_tokens": 250}}}
        commands = (["tax"], ["ledger", "diff"], ["hook", "session-start"],
                    ["hook", "user-prompt-submit"], ["config", "get", "context.nudge_tiers"],
                    ["doctor", "--no-config", "--supervisor", "none"], ["janitor"])
        for args in commands:
            with self.subTest(args):
                base = self.run_cmd(args, {})
                proc = self.run_cmd(args, live)
                self.assertEqual(proc.returncode, base.returncode)
                self.assertEqual(proc.stderr, base.stderr)
                combined = proc.stdout + proc.stderr
                for word in ("preload", "removed", "unknown", "max_tokens"):
                    self.assertNotIn(word, combined)

    def test_removed_keys_are_not_type_checked(self):
        proc = self.run_cmd(["config", "check"], {"context": {"preload": [1], "card": "x"},
                                                  "render": 5})
        self.assertEqual(proc.returncode, 0, proc.stderr)


class ConfigGetJsonTests(CliCase):
    def test_get_prints_json_for_every_scalar_kind(self):
        cfg = {"doctor": {"notify_command": "echo hi"}}
        cases = [
            ("doctor.notify_command", '"echo hi"'),
            ("doctor.heal_cooldown_h", "12"),
            ("authprobe.enabled", "false"),
            ("doctor.levers.defaults", "true"),
            ("doctor.checks.disk_free", "null"),
            ("context.nudge_tiers", "[250000,350000,500000]"),
        ]
        for key, want in cases:
            with self.subTest(key):
                proc = self.run_cmd(["config", "get", key], cfg)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, want + "\n")

    def test_get_missing_key_still_exits_1(self):
        self.assertEqual(self.run_cmd(["config", "get", "no.such"], {}).returncode, 1)
        self.assertEqual(self.run_cmd(["config", "get", "ledger.week_reports"], {}).returncode, 1)


if __name__ == "__main__":
    unittest.main()
