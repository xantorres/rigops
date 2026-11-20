from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import cli, launchd  # noqa: E402
from rigops.errors import RigopsError  # noqa: E402

from tests.support import path_with_failing_sources  # noqa: E402
from tests.test_doctor import (  # noqa: E402
    FAILING_ITEM,
    HUNG_ITEM,
    OK_ITEM,
    _write_registry,
    doctor,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RIGOPS = REPO_ROOT / "bin" / "rigops"

OWN_ITEM = FAILING_ITEM.replace("job-failing", "doctor-watchdog").replace(
    "entry_point: x", "entry_point: /usr/local/bin/rigops doctor --exit-zero",
)
STALE_ITEM = """\
  - id: doctor-watchdog
    domain: x
    trigger: cron
    entry_point: {entry}
    output: x
    cadence: every 1 min
    last_verified: 2026-08-01
    health: {health}
    group: AUTOMATED-LAUNCHD
"""


class SubprocessExitCodes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.state = self.home / "state"
        self.config = self.home / "config.json"

    def run_cmd(self, args, config=None, mode=None, extra_env=None) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "HOME": str(self.home),
            "RIGOPS_STATE_DIR": str(self.state),
            "RIGOPS_CONFIG": str(self.config),
            "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
            "XDG_STATE_HOME": str(self.home / "xdg-state"),
            **(extra_env or {}),
        }
        self.config.unlink(missing_ok=True)
        if config is not None:
            self.config.write_text(config if isinstance(config, str) else json.dumps(config))
            if mode is not None:
                os.chmod(self.config, mode)
        return subprocess.run(
            [str(RIGOPS), *args], capture_output=True, text=True, check=False, timeout=60,
            env=env, stdin=subprocess.DEVNULL,
        )

    def _malformed_ledger(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "ledger.jsonl").write_text("{not json\n")
        (self.state / "eval.jsonl").write_text("{not json\n")

    def _import_failure_env(self) -> dict:
        hook = self.home / "hook"
        hook.mkdir()
        (hook / "sitecustomize.py").write_text(
            "import importlib\n"
            "_real = importlib.import_module\n"
            "def _fake(name, package=None):\n"
            "    if name.endswith('check_mcp'):\n"
            "        raise ImportError('boom')\n"
            "    return _real(name, package)\n"
            "importlib.import_module = _fake\n"
        )
        return {"PYTHONPATH": str(hook)}

    def _unreadable_rule(self) -> None:
        rules = self.home / ".claude" / "rules"
        rules.mkdir(parents=True)
        locked = rules / "locked.md"
        locked.write_text("always on\n")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o644)

    def _janitor_error_config(self) -> dict:
        watched = self.home / "watched"
        watched.mkdir()
        (watched / "a.txt").write_text("x")
        return {"janitor": {"watermarks": [
            {"name": "w", "path": str(watched), "max_files": 0, "area": "no-such-area"},
        ]}}

    def test_exit_code_table(self):
        self._malformed_ledger()
        empty_cases = self.home / "cases"
        empty_cases.mkdir()
        (self.home / "roots").mkdir()
        root = ["--root", str(self.home / "roots")]
        rows = [
            ("config get missing key", ["config", "get", "no.such.key"], {}, 1),
            ("config get present key", ["config", "get", "doctor.heal_cooldown_h"], {}, 0),
            ("config get unreadable config", ["config", "get", "x"], "{bad", 2),
            ("config get non-object config", ["config", "get", "x"], "[]", 2),
            ("config check invalid JSON", ["config", "check"], "{bad", 2),
            ("config check ok", ["config", "check"], {}, 0),
            ("config check bad type", ["config", "check"], {"reaper": {"grace_days": "14"}}, 2),
            ("config get bad type", ["config", "get", "x"], {"janitor": {"max_delete": True}}, 2),
            ("doctor invalid config", ["doctor", "--no-config", "--supervisor", "none"], "{bad", 2),
            ("ledger diff report skips a malformed row", ["ledger", "diff"], {}, 0),
            ("ledger show report skips a malformed row", ["ledger", "write", "--show", "--json"],
             {}, 0),
            ("tax history report skips a malformed row", ["tax", "--history"], {}, 0),
            ("ledger write gate on malformed state", ["ledger", "write", "--dry-run"], {}, 2),
            ("eval diff gate on malformed state", ["eval", "diff"], {}, 2),
            ("eval list on malformed state", ["eval", "list"], {}, 2),
            ("janitor runtime error", ["janitor", "--apply"], self._janitor_error_config(), 2),
            ("janitor bad config", ["janitor"], "{bad", 2),
            ("eval run empty cases dir",
             ["eval", "run", "--cases", str(empty_cases), "--endpoint", "http://127.0.0.1:9",
              "--model", "m"], {}, 2),
            ("eval run missing cases dir",
             ["eval", "run", "--cases", str(self.home / "nope"), "--endpoint",
              "http://127.0.0.1:9", "--model", "m"], {}, 2),
            ("doctor stale job does not fail",
             ["doctor", "--no-config", "--supervisor", "none", "--registry", "REG"], {}, 0),
            ("hook never fails on a broken config", ["hook", "session-start"], "{bad", 0),
            ("doctor refuses a group-writable config",
             ["doctor", "--no-config", "--supervisor", "none"], {}, 2, 0o664),
            ("doctor refuses an other-writable config",
             ["doctor", "--no-config", "--supervisor", "none"], {}, 2, 0o646),
            ("doctor refuses a writable config under --exit-zero",
             ["doctor", "--no-config", "--supervisor", "none", "--exit-zero"], {}, 2, 0o664),
            ("doctor runs with a 0644 config",
             ["doctor", "--no-config", "--supervisor", "none"], {}, 0, 0o644),
            ("doctor --list ignores a writable config", ["doctor", "--list"], {}, 0, 0o664),
            ("janitor --apply refuses a writable config", ["janitor", "--apply"], {}, 2, 0o664),
            ("janitor --apply refuses an other-writable config",
             ["janitor", "--apply"], {}, 2, 0o646),
            ("janitor --apply runs with a 0644 config", ["janitor", "--apply"], {}, 0, 0o644),
            ("janitor dry run ignores a writable config", ["janitor"], {}, 0, 0o664),
            ("reap --apply refuses a writable config", ["reap", "--apply", *root], {}, 2, 0o664),
            ("reap --apply runs with a 0644 config", ["reap", "--apply", *root], {}, 0, 0o644),
            ("reap preview ignores a writable config", ["reap", *root], {}, 0, 0o664),
            ("tax --starts unreadable --since", ["tax", "--starts", "--since", "garbage"], {}, 2),
            ("tax --dead unreadable --until", ["tax", "--dead", "--until", "garbage"], {}, 2),
            ("eit unreadable --since", ["eit", "--since", "garbage"], {}, 2),
        ]
        if os.geteuid() != 0:
            self._unreadable_rule()
            rows += [
                ("doctor unreadable always-on file", ["doctor", "--config-only"], {}, 2),
                ("doctor unreadable always-on file under --exit-zero",
                 ["doctor", "--config-only", "--exit-zero"], {}, 2),
            ]
        broken = {"extra_env": self._import_failure_env()}
        rows += [
            ("doctor check import failure", ["doctor", "--config-only"], {}, 2, broken),
            ("doctor check import failure under --exit-zero",
             ["doctor", "--config-only", "--exit-zero"], {}, 2, broken),
        ]
        for name, args, config, want, *extra in rows:
            mode = [x for x in extra if isinstance(x, int)]
            env = next((x["extra_env"] for x in extra if isinstance(x, dict)), None)
            with self.subTest(name):
                if "REG" in args:
                    evidence = self.home / "evidence"
                    evidence.write_text("x")
                    os.utime(evidence, (1, 1))
                    reg = _write_registry(
                        str(self.home),
                        STALE_ITEM.format(health=evidence, entry="rigops ledger write"),
                    )
                    args = [str(reg) if a == "REG" else a for a in args]
                proc = self.run_cmd(args, config, *mode, extra_env=env)
                self.assertEqual(proc.returncode, want, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_ledger_exit_codes_on_missing_and_thin_data(self):
        empty = self.home / "empty-transcripts"
        empty.mkdir()
        self.state.mkdir(parents=True)
        thin = "".join(
            json.dumps({"version": 1, "date": day, "turns": 3, "eit_per_turn": 100}) + "\n"
            for day in ("2026-08-01", "2026-08-08"))
        (self.state / "ledger.jsonl").write_text(thin)
        rows = [
            ("ledger write missing transcripts dir",
             ["ledger", "write", "--at", "2026-08-15", "--force"],
             {"transcripts_dir": str(self.home / "nope")}, 2),
            ("ledger write empty transcripts dir",
             ["ledger", "write", "--at", "2026-08-15", "--force"],
             {"transcripts_dir": str(empty), "fixed_tax": {"paths": [], "globs": []}}, 0),
            ("ledger diff with too few turns", ["ledger", "diff"], {}, 0),
        ]
        with mock.patch.dict(os.environ, {"PATH": path_with_failing_sources(os.environ["PATH"])}):
            for name, args, config, want in rows:
                with self.subTest(name):
                    proc = self.run_cmd(args, config)
                    self.assertEqual(proc.returncode, want, proc.stderr)
                    self.assertNotIn("Traceback", proc.stderr)

    def test_a_report_over_a_malformed_row_says_how_many_it_skipped(self):
        self._malformed_ledger()
        for args in (["ledger", "diff"], ["tax", "--history"]):
            with self.subTest(args):
                proc = self.run_cmd(args, {})
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("skipped 1", proc.stderr)

    def test_an_unreadable_state_file_exits_2_everywhere(self):
        self.state.mkdir(parents=True, exist_ok=True)
        for name in ("ledger.jsonl", "eval.jsonl"):
            (self.state / name).mkdir()
        for args in (["ledger", "diff"], ["tax", "--history"], ["ledger", "write", "--dry-run"],
                     ["eval", "diff"], ["eval", "list"]):
            with self.subTest(args):
                proc = self.run_cmd(args, {})
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_load_failure_prints_one_error_line_without_a_doubled_prefix(self):
        proc = self.run_cmd(["config", "get", "x"], "{bad")

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr.count("error:"), 1, proc.stderr)
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1, proc.stderr)


class RunnerTests(unittest.TestCase):
    def test_typed_error_exits_2_with_one_line_on_stderr(self):
        def main() -> int:
            raise RigopsError("config is broken")

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.run(main)

        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "error: config is broken\n")

    def test_malformed_state_exits_2(self):
        from rigops import state

        def main() -> int:
            raise state.MalformedJsonl("bad row in x:1")

        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.run(main)

        self.assertEqual(code, 2)
        self.assertIn("bad row in x:1", err.getvalue())

    def test_return_value_passes_through(self):
        self.assertEqual(cli.run(lambda: 1), 1)


class DoctorExitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        for key, sub in (("RIGOPS_STATE_DIR", "state"), ("XDG_STATE_HOME", "xdg-state")):
            patch = mock.patch.dict(os.environ, {key: str(Path(self.tmp) / sub)})
            patch.start()
            self.addCleanup(patch.stop)

    def run_doctor(self, args, *items, snapshot=(True, None, 1), runtime_h=None):
        registry = _write_registry(self.tmp, *items)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=snapshot),
            mock.patch.object(launchd, "kickstart", return_value=True) as kick,
            mock.patch.object(launchd, "runtime_hours", return_value=runtime_h),
            mock.patch.object(launchd, "job_pid", return_value=4242),
            mock.patch.object(launchd, "terminate_process_group", return_value=True) as term,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            code = doctor.main([*args, "--json", "--no-config", "--registry", str(registry)])
        return code, json.loads(out.getvalue()), kick, term

    def test_failing_job_exits_1(self):
        code, _, _, _ = self.run_doctor([], FAILING_ITEM)
        self.assertEqual(code, 1)

    def test_hung_job_exits_1(self):
        code, _, _, _ = self.run_doctor([], HUNG_ITEM, snapshot=(True, 99, 0), runtime_h=5.0)
        self.assertEqual(code, 1)

    def test_heal_no_longer_forces_zero(self):
        code, _, kick, _ = self.run_doctor(["--heal"], FAILING_ITEM)
        self.assertEqual(code, 1)
        kick.assert_called_once()

    def test_exit_zero_turns_one_into_zero_and_still_heals(self):
        code, payload, kick, _ = self.run_doctor(["--heal", "--exit-zero"], FAILING_ITEM)
        self.assertEqual(code, 0)
        self.assertEqual(payload["fail_count"], 1)
        kick.assert_called_once()

    def test_failing_custom_check_exits_1(self):
        config = Path(self.tmp) / "config.json"
        check = {"name": "always-red", "command": "exit 3", "fail_exit": 3}
        config.write_text(json.dumps({"doctor": {"checks": {"custom": [check]}}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"RIGOPS_CONFIG": str(config)}):
            code, payload, _, _ = self.run_doctor([], OK_ITEM, snapshot=(True, None, 0))

        self.assertEqual([c["status"] for c in payload["checks"]], ["fail"])
        self.assertEqual(code, 1)

    def test_healthy_job_exits_0(self):
        code, _, _, _ = self.run_doctor([], OK_ITEM, snapshot=(True, None, 0))
        self.assertEqual(code, 0)

    def test_unknown_job_does_not_exit_1(self):
        code, payload, _, _ = self.run_doctor(
            ["--supervisor", "none"], OK_ITEM.replace("health: -", "health: /no/such/file"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["jobs"][0]["status"], "unknown")

    def test_own_row_is_not_judged_or_healed_while_another_job_fails(self):
        code, payload, kick, _ = self.run_doctor(["--heal"], OWN_ITEM, FAILING_ITEM)
        by_id = {j["id"]: j for j in payload["jobs"]}
        self.assertEqual((by_id["doctor-watchdog"]["status"], by_id["doctor-watchdog"]["action"]),
                         ("ok", "none"))
        self.assertEqual(by_id["job-failing"]["status"], "failing")
        kick.assert_called_once_with("local.job-failing")
        self.assertEqual(code, 1)

    def test_own_row_alone_exits_0_under_launchd(self):
        code, payload, kick, _ = self.run_doctor(["--heal"], OWN_ITEM)
        self.assertEqual(payload["jobs"][0]["status"], "ok")
        kick.assert_not_called()
        self.assertEqual(code, 0)

    def test_own_row_is_skipped_without_a_supervisor(self):
        stale = Path(self.tmp) / "evidence"
        stale.write_text("x")
        os.utime(stale, (1, 1))
        entry = "python3 /opt/rigops/libexec/rigops-doctor --supervisor none"
        row = STALE_ITEM.format(health=stale, entry=entry)
        _, payload, _, _ = self.run_doctor(["--supervisor", "none"], row)
        self.assertEqual((payload["jobs"][0]["status"], payload["jobs"][0]["action"]),
                         ("ok", "none"))

    def test_other_entry_point_is_still_judged(self):
        _, payload, _, _ = self.run_doctor([], OWN_ITEM.replace("rigops doctor", "rigops ledger"))
        self.assertEqual(payload["jobs"][0]["status"], "failing")


if __name__ == "__main__":
    unittest.main()
