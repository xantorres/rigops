"""Doctor's exit contract: the error severity, frozen ids, registry path and supervisor default."""

from __future__ import annotations

import ast
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from rigops import core
from rigops import doctor as checks
from rigops.doctor import check_budget, check_levers, ids

from tests.test_doctor import OK_ITEM, _write_registry, doctor
from tests.test_state_contract import Sandbox

DOCTOR_DIR = Path(checks.__file__).parent
ERROR = core.Finding(check="budget", area="docs", symptom="cannot read", evidence="e",
                     fix="f", key="unreadable", severity="error")
FAIL = core.Finding(check="pointers", area="docs", symptom="s", evidence="e", fix="f", key="k")
WARN = core.Finding(check="levers", area="metrics", symptom="s", evidence="e", fix="f",
                    key="w", severity="warn")


def _restore(path: Path) -> None:
    if path.exists():
        path.chmod(0o644)


def run_main(argv, findings=None):
    out = io.StringIO()
    patches = [mock.patch.object(doctor.rigops_config, "load", return_value={})]
    if findings is not None:
        patches.append(mock.patch.object(doctor.config_checks, "run_checks",
                                         return_value=findings))
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        stack.enter_context(contextlib.redirect_stdout(out))
        code = doctor.main(argv)
    return code, out.getvalue()


def import_failing(module_suffix):
    real = importlib.import_module

    def fake(name, package=None):
        if name.endswith(module_suffix):
            raise ImportError("boom from import")
        return real(name, package)

    return mock.patch.object(checks.importlib, "import_module", side_effect=fake)


class ErrorSeverityTests(unittest.TestCase):
    def test_a_check_module_that_raises_on_import_yields_a_finding_and_exit_2(self):
        with import_failing("check_mcp"):
            found = checks.run_checks({}, only={"mcp", "budget"})
            listed = checks.names()
            codes = [run_main(["--config-only", *extra])[0] for extra in ([], ["--exit-zero"])]
            payload = json.loads(run_main(["--config-only", "--json"])[1])

        self.assertIn("mcp", listed)
        broken = [f for f in found if f.check == "mcp"]
        self.assertEqual([(f.key, f.severity) for f in broken], [("import", "error")])
        self.assertIn("boom from import", broken[0].evidence)
        self.assertEqual(codes, [2, 2])
        self.assertIn(("mcp", "import", "error"),
                      [(f["check"], "import", f["severity"]) for f in payload["findings"]])

    def test_the_other_checks_still_run_when_one_fails_to_import(self):
        ran = []
        with import_failing("check_mcp"), \
             mock.patch.object(check_budget, "run", lambda cfg: ran.append(1) or []):
            checks.run_checks({}, only={"mcp", "budget"})
        self.assertEqual(ran, [1])

    def test_a_crashed_check_is_an_error_finding_and_exits_2(self):
        def explode(_cfg):
            raise RuntimeError("check is broken")

        with mock.patch.object(check_budget, "run", explode):
            found = checks.run_checks({}, only={"budget"})
        self.assertEqual([(f.key, f.severity) for f in found], [("crashed", "error")])

    def test_an_error_finding_exits_2_with_or_without_exit_zero(self):
        for extra in ([], ["--exit-zero"]):
            with self.subTest(extra):
                self.assertEqual(run_main(["--config-only", *extra], [ERROR])[0], 2)

    def test_two_wins_over_one(self):
        code, _ = run_main(["--config-only"], [FAIL, ERROR])
        self.assertEqual(code, 2)

    def test_a_warning_beside_an_error_still_exits_2(self):
        self.assertEqual(run_main(["--config-only"], [WARN, ERROR])[0], 2)

    def test_text_report_labels_errors_apart_from_failures(self):
        _, out = run_main(["--config-only"], [FAIL, ERROR])
        self.assertIn("config checks: 1 findings", out)
        self.assertIn("errors: 1", out)
        self.assertIn("cannot read", out)

    def test_json_carries_the_error_severity_with_unchanged_keys(self):
        _, out = run_main(["--config-only", "--json"], [ERROR])
        finding = json.loads(out)["findings"][0]
        self.assertEqual(finding["severity"], "error")
        self.assertEqual(set(finding),
                         {"id", "check", "area", "severity", "symptom", "evidence", "fix"})

    def test_a_full_run_with_an_error_finding_exits_2_even_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = _write_registry(tmp, OK_ITEM)
            for extra in ([], ["--exit-zero"]):
                with self.subTest(extra):
                    argv = ["--supervisor", "none", "--registry", str(registry), *extra]
                    self.assertEqual(run_main(argv, [ERROR, FAIL])[0], 2)


class GateRecordTests(unittest.TestCase):
    def test_a_group_holding_an_error_finding_keeps_the_fail_status_and_the_shape(self):
        entries = doctor._config_gate_entries([ERROR])
        self.assertEqual(entries, [
            {"kind": "config", "name": "budget", "detail": "1 findings", "status": "fail"},
        ])

    def test_the_recorded_gates_use_only_the_known_status_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = _write_registry(tmp, OK_ITEM)
            run_main(["--supervisor", "none", "--registry", str(registry)], [ERROR, WARN])
            record = json.loads(core.local_state_path(core.GATES_STATE_NAME).read_text())
        self.assertEqual(set(record), {"version", "ts", "gates"})
        gates = {g["name"]: g for g in record["gates"]}
        self.assertEqual(gates["budget"]["status"], "fail")
        self.assertEqual(set(gates["budget"]), {"kind", "name", "status", "detail", "since"})
        self.assertLessEqual({g["status"] for g in record["gates"]},
                             {"fail", "warn", "failing", "hung", "stale", "ok", "unknown"})


class UnreadableBudgetMemberTests(unittest.TestCase):
    def _home(self, tmp):
        claude = Path(tmp) / ".claude"
        (claude / "rules").mkdir(parents=True)
        return claude

    def _unreadable(self, path: Path):
        path.write_text("secret rule body\n")
        path.chmod(0)
        self.addCleanup(_restore, path)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode 000 file")
    def test_an_unreadable_member_is_an_error_finding_naming_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = self._home(tmp)
            self._unreadable(claude / "rules" / "locked.md")
            (claude / "rules" / "big.md").write_text("x" * 400)
            with mock.patch.object(core, "CLAUDE_DIR", claude):
                found = check_budget.run({"doctor": {"budget": {"always_on_tokens": 10}}})
        by_key = {f.key: f for f in found}
        self.assertEqual(set(by_key), {"unreadable", "always_on"})
        self.assertEqual(by_key["unreadable"].severity, "error")
        self.assertIn("locked.md", by_key["unreadable"].evidence)
        self.assertEqual(by_key["always_on"].severity, "fail")

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode 000 file")
    def test_an_unreadable_law_file_is_reported_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = self._home(tmp)
            self._unreadable(claude / "CLAUDE.md")
            with mock.patch.object(core, "CLAUDE_DIR", claude):
                found = check_budget.run({})
        self.assertEqual([(f.key, f.severity) for f in found], [("unreadable", "error")])


class UnreadableBudgetMemberExitTests(Sandbox):
    @unittest.skipIf(os.geteuid() == 0, "root reads a mode 000 file")
    def test_an_unreadable_always_on_file_makes_doctor_exit_2_not_pass(self):
        rules = self.home / ".claude" / "rules"
        rules.mkdir(parents=True)
        locked = rules / "locked.md"
        locked.write_text("always on\n")
        locked.chmod(0)
        self.addCleanup(_restore, locked)

        for extra in ([], ["--exit-zero"]):
            with self.subTest(extra):
                proc = self.run_cmd(["doctor", "--config-only", "--check", "budget", *extra])
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("locked.md", proc.stdout)
        proc = self.run_cmd(["doctor", "--config-only", "--json", "--check", "budget"])
        self.assertEqual(json.loads(proc.stdout)["findings"][0]["severity"], "error")


class LeverValueTests(unittest.TestCase):
    LEDGER = Path("/state/ledger.jsonl")

    def _judge(self, rows, rules, builtin=frozenset()):
        return check_levers.judge(rows, [], rules, date(2026, 9, 16), self.LEDGER,
                                  builtin=builtin)

    def _rows(self, last):
        return [{"date": "2026-09-07", "tokens": 10}, {"date": "2026-09-14", "tokens": last}]

    def test_a_configured_rule_over_a_non_numeric_latest_value_warns(self):
        for bad in (None, "n/a", True):
            with self.subTest(bad):
                found = self._judge(self._rows(bad), {"tokens": {"max": 5}})
                self.assertEqual([(f.key, f.severity) for f in found], [("tokens:value", "warn")])
                self.assertIn("tokens", found[0].symptom)

    def test_a_builtin_rule_over_a_non_numeric_latest_value_warns(self):
        found = self._judge(self._rows("n/a"), {"tokens": {"max": 5}}, builtin={"tokens"})
        self.assertEqual([f.key for f in found], ["tokens:value"])

    def test_a_builtin_rule_over_a_null_latest_value_stays_quiet(self):
        found = self._judge(self._rows(None), {"tokens": {"max": 5}}, builtin={"tokens"})
        self.assertEqual(found, [])

    def test_a_builtin_rule_whose_column_is_absent_stays_quiet(self):
        rows = [{"date": "2026-09-14", "other": 1}]
        self.assertEqual(self._judge(rows, {"tokens": {"max": 5}}, builtin={"tokens"}), [])

    def test_a_configured_rule_whose_column_is_absent_from_the_latest_row_warns(self):
        found = self._judge([{"date": "2026-09-14", "other": 1}], {"tokens": {"max": 5}})
        self.assertEqual([f.key for f in found], ["tokens:column"])

    def test_an_accepted_lever_stays_quiet_about_its_value(self):
        note = {"date": "2026-09-14", "accept": ["tokens"]}
        found = check_levers.judge(self._rows("n/a"), [note], {"tokens": {"max": 5}},
                                   date(2026, 9, 16), self.LEDGER)
        self.assertEqual(found, [])

    def test_a_numeric_value_inside_its_limit_is_quiet(self):
        self.assertEqual(self._judge(self._rows(3), {"tokens": {"max": 5}}), [])


class FrozenIdTests(unittest.TestCase):
    def test_the_ids_that_can_make_doctor_exit_1_are_exactly_these(self):
        self.assertEqual(ids.EXIT_1_FINDINGS, frozenset({
            ("agents", "read_only_edit:*"),
            ("budget", "always_on"),
            ("jobs", "program:*"),
            ("mcp", "stdio:*"),
            ("plugin_cache_clean", "stale:*"),
            ("pointers", "agent:*"),
            ("pointers", "link:*"),
            ("pointers", "launchd:*"),
            ("pointers", "mcp:*"),
            ("pointers", "model:*"),
            ("pointers", "path:*"),
            ("pointers", "plugin:*"),
            ("pointers", "root:*"),
            ("pointers", "skill:*"),
            ("pointers", "source:*"),
        }))
        self.assertEqual(ids.EXIT_1_STATUSES, ("failing", "hung", "fail"))
        self.assertEqual(doctor.RED, ids.EXIT_1_STATUSES)

    def test_the_ids_that_make_doctor_exit_2_are_exactly_these(self):
        self.assertEqual(ids.EXIT_2_FINDINGS, frozenset({
            ("*", "crashed"),
            ("*", "import"),
            ("budget", "unreadable"),
            ("jobs", "unreadable:*"),
        }))

    def test_no_check_builds_a_fail_finding_outside_the_frozen_set(self):
        found = _constructed(["check_*.py"], {"fail"})
        self.assertTrue(found)
        self.assertEqual(found - ids.EXIT_1_FINDINGS, set())
        self.assertEqual(ids.EXIT_1_FINDINGS - found, set())

    def test_no_module_builds_an_error_finding_outside_the_frozen_set(self):
        found = _constructed(["*.py"], {"error"})
        self.assertEqual(found, set(ids.EXIT_2_FINDINGS))


def _severity(call):
    for kw in call.keywords:
        if kw.arg == "severity":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
            raise AssertionError(f"severity of a Finding at line {call.lineno} is not a literal")
    return "fail"


def _key_patterns(call, module_tree):
    key = next((kw.value for kw in call.keywords if kw.arg == "key"), None)
    if isinstance(key, ast.Constant):
        return {key.value}
    if isinstance(key, ast.JoinedStr):
        head = key.values[0]
        prefix = head.value if isinstance(head, ast.Constant) else ""
        return {f"{prefix}*"}
    if isinstance(key, ast.Call) and getattr(key.func, "id", "") == "finding_key":
        kinds = {
            c.args[0].value for c in ast.walk(module_tree)
            if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_finding"
            and c.args and isinstance(c.args[0], ast.Constant)
        }
        return {f"{kind}:*" for kind in kinds}
    raise AssertionError(f"cannot classify the key of the Finding at line {call.lineno}")


def _constructed(globs, severities):
    found = set()
    for path in sorted(p for g in globs for p in DOCTOR_DIR.glob(g)):
        tree = ast.parse(path.read_text())
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "Finding"):
                continue
            if _severity(call) not in severities:
                continue
            check = next(kw.value for kw in call.keywords if kw.arg == "check")
            name = path.stem[len("check_"):] if path.stem.startswith("check_") else "*"
            if name != "*" and not (isinstance(check, ast.Name) and check.id == "CHECK"):
                raise AssertionError(f"unexpected check expression in {path.name}")
            found |= {(name, pattern) for pattern in _key_patterns(call, tree)}
    return found


class RegistryPathTests(unittest.TestCase):
    def _list(self, *argv, cfg=None, xdg=None):
        env = {"XDG_CONFIG_HOME": xdg} if xdg is not None else {}
        cfg = cfg if cfg is not None else {"doctor": {"registry": "~/.config/rigops/registry.md"}}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(doctor.rigops_config, "load", return_value=cfg), \
             mock.patch.object(doctor.rigops_registry, "load_registry",
                               return_value=[]) as load, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            doctor.main(["--list", *argv])
        return load.call_args.args[0] if load.called else None

    def test_an_unset_registry_follows_xdg_config_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Path(tmp) / "rigops"
            reg.mkdir()
            (reg / "registry.md").write_text("schema: rigops.v1\nitems:\n---\n")
            self.assertEqual(self._list(xdg=tmp), reg / "registry.md")

    def test_an_explicit_config_value_wins_over_xdg(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mine.md"
            path.write_text("x")
            cfg = {"doctor": {"registry": str(path)}}
            self.assertEqual(self._list(cfg=cfg, xdg="/elsewhere"), path)

    def test_the_registry_flag_wins_over_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flag.md"
            path.write_text("x")
            self.assertEqual(self._list("--registry", str(path), xdg="/elsewhere"), path)

    def test_an_unset_xdg_falls_back_to_dot_config(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("XDG_CONFIG_HOME", None)
            self.assertEqual(doctor.default_registry(),
                             Path(os.path.expanduser("~/.config/rigops/registry.md")))


class DefaultSupervisorTests(unittest.TestCase):
    def test_the_default_supervisor_is_launchd_only_on_macos(self):
        want = "launchd" if sys.platform == "darwin" else "none"
        self.assertEqual(doctor.default_supervisor(), want)

    def test_patched_platforms(self):
        for platform, want in (("darwin", "launchd"), ("linux", "none"), ("win32", "none")):
            with self.subTest(platform), mock.patch.object(doctor.sys, "platform", platform):
                self.assertEqual(doctor.default_supervisor(), want)

    def test_a_run_without_the_flag_reports_the_platform_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = _write_registry(tmp)
            with mock.patch.object(doctor.sys, "platform", "linux"):
                _, out = run_main(["--no-config", "--json", "--registry", str(registry)])
        self.assertEqual(json.loads(out)["supervisor"], "none")


if __name__ == "__main__":
    unittest.main()
