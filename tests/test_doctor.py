from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core, launchd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCTOR_SCRIPT = REPO_ROOT / "libexec" / "rigops-doctor"


def _load_doctor_module():
    # libexec/rigops-doctor has no .py suffix, so importlib can't guess a loader
    # from the file extension the way spec_from_file_location normally would.
    loader = importlib.machinery.SourceFileLoader("rigops_doctor_cli", str(DOCTOR_SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


doctor = _load_doctor_module()

HUNG_ITEM = """\
  - id: job-hung
    domain: x
    trigger: launchd.daily
    entry_point: x
    output: x
    cadence: daily 02:00
    last_verified: 2026-08-01
    health: -
    group: AUTOMATED-LAUNCHD
    plist: /fake/local.job-hung.plist
    max_runtime_h: 1
"""

FAILING_ITEM = """\
  - id: job-failing
    domain: x
    trigger: launchd.daily
    entry_point: x
    output: x
    cadence: on-demand
    last_verified: 2026-08-01
    health: -
    group: AUTOMATED-LAUNCHD
    plist: /fake/local.job-failing.plist
"""

OK_ITEM = """\
  - id: job-ok
    domain: x
    trigger: launchd.daily
    entry_point: x
    output: x
    cadence: on-demand
    last_verified: 2026-08-01
    health: -
    group: AUTOMATED-LAUNCHD
    plist: /fake/local.job-ok.plist
"""

UNRELATED_PLIST_ITEM = """\
  - id: job-hung
    domain: x
    trigger: launchd.daily
    entry_point: x
    output: x
    cadence: daily 02:00
    last_verified: 2026-08-01
    health: -
    group: AUTOMATED-LAUNCHD
    plist: /fake/com.unrelated.daemon.plist
    max_runtime_h: 1
"""


NO_LABEL_ITEM = """\
  - id: job-nolabel
    domain: x
    trigger: launchd.daily
    entry_point: x
    output: x
    cadence: daily 02:00
    last_verified: 2026-08-01
    health: -
    group: AUTOMATED-LAUNCHD
"""


def _write_registry(tmp: str, *item_texts: str) -> Path:
    path = Path(tmp) / "registry.md"
    path.write_text("schema: rigops.v1\nitems:\n" + "".join(item_texts) + "---\n")
    return path


def _run_doctor(args: list) -> tuple:
    """Run the fleet half only.

    These tests isolate the state dir and the config but not HOME, so the config
    checks would read the real instruction surface and let an unrelated finding
    decide the exit code. The config checks have their own suite.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor.main(list(args) + ["--no-config"])
    return code, buf.getvalue()


class CheckSelectionTests(unittest.TestCase):
    def test_unknown_check_name_is_rejected_rather_than_selecting_nothing(self):
        """A typo must not filter every check out and then report a clean rig."""
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as raised:
                doctor.main(["--config-only", "--check", "pointrs"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", err.getvalue())


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name
        os.environ["RIGOPS_STATE_DIR"] = str(Path(self.tmp) / "state")
        os.environ["RIGOPS_CONFIG"] = str(Path(self.tmp) / "config.json")
        # local_state_path (the gates record) reads XDG_STATE_HOME, not
        # RIGOPS_STATE_DIR, by design: it must stay off a shared state dir.
        os.environ["XDG_STATE_HOME"] = str(Path(self.tmp) / "xdg-state")

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self._env)


class ReportOnlyDefaultTests(EnvIsolatedTestCase):
    def test_hung_job_not_killed_without_heal_flag(self):
        registry_path = _write_registry(self.tmp, HUNG_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, 4242, None)),
            mock.patch.object(launchd, "runtime_hours", return_value=2.5),
            mock.patch.object(launchd, "terminate_process_group") as m_term,
            mock.patch.object(launchd, "kickstart") as m_kick,
        ):
            code, out = _run_doctor(["--json", "--registry", str(registry_path)])
        m_term.assert_not_called()
        m_kick.assert_not_called()
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["status"], "hung")
        self.assertEqual(payload["jobs"][0]["action"], "kill")
        self.assertGreaterEqual(payload["fail_count"], 1)
        self.assertEqual(code, 0)

    def test_failing_job_not_kickstarted_without_heal_flag(self):
        registry_path = _write_registry(self.tmp, FAILING_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 1)),
            mock.patch.object(launchd, "kickstart") as m_kick,
        ):
            code, out = _run_doctor(["--json", "--registry", str(registry_path)])
        m_kick.assert_not_called()
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["status"], "failing")
        self.assertGreaterEqual(payload["fail_count"], 1)
        self.assertEqual(code, 0)


class HealFlagTests(EnvIsolatedTestCase):
    def test_heal_kills_hung_job(self):
        registry_path = _write_registry(self.tmp, HUNG_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, 4242, None)),
            mock.patch.object(launchd, "runtime_hours", return_value=2.5),
            mock.patch.object(launchd, "job_pid", return_value=4242),
            mock.patch.object(launchd, "terminate_process_group", return_value=True) as m_term,
        ):
            code, out = _run_doctor(["--heal", "--json", "--registry", str(registry_path)])
        m_term.assert_called_once_with(4242, 5)
        payload = json.loads(out)
        self.assertIn("terminated hung job job-hung", " ".join(payload["healed"]))
        self.assertGreaterEqual(payload["fail_count"], 1)
        self.assertEqual(code, 0)

    def test_heal_kickstarts_failing_job(self):
        registry_path = _write_registry(self.tmp, FAILING_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 1)),
            mock.patch.object(launchd, "kickstart", return_value=True) as m_kick,
        ):
            code, out = _run_doctor(["--heal", "--json", "--registry", str(registry_path)])
        m_kick.assert_called_once_with("local.job-failing")
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["action"], "kickstart")
        self.assertGreaterEqual(payload["fail_count"], 1)
        self.assertEqual(code, 0)


class SupervisorNoneTests(EnvIsolatedTestCase):
    def test_skips_launchd_entirely(self):
        evidence = Path(self.tmp) / "fresh.log"
        evidence.write_text("ok\n")
        item = OK_ITEM.replace("health: -", f"health: {evidence}")
        registry_path = _write_registry(self.tmp, item)

        def _fail(*a, **k):
            raise AssertionError("launchd must not be called in --supervisor none")

        with (
            mock.patch.object(launchd, "job_pid", side_effect=_fail),
            mock.patch.object(launchd, "is_loaded", side_effect=_fail),
            mock.patch.object(launchd, "last_exit_status", side_effect=_fail),
            mock.patch.object(launchd, "job_snapshot", side_effect=_fail),
            mock.patch.object(launchd, "runtime_hours", side_effect=_fail),
            mock.patch.object(launchd, "kickstart", side_effect=_fail),
            mock.patch.object(launchd, "terminate_process_group", side_effect=_fail),
        ):
            code, out = _run_doctor(
                ["--heal", "--json", "--supervisor", "none", "--registry", str(registry_path)]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["status"], "ok")


class EvidenceFallbackTests(EnvIsolatedTestCase):
    def _judge_without_a_label(self, item_text: str) -> dict:
        registry_path = _write_registry(self.tmp, item_text)

        def _fail(*a, **k):
            raise AssertionError("an unresolved label must not reach launchctl")

        with (
            mock.patch.object(launchd, "is_loaded", return_value=False),
            mock.patch.object(launchd, "job_snapshot", side_effect=_fail),
        ):
            code, out = _run_doctor(["--json", "--registry", str(registry_path)])
        self.assertEqual(code, 0)
        return json.loads(out)["jobs"][0]

    def _item_with_evidence(self, age_h: float) -> str:
        evidence = Path(self.tmp) / "evidence.log"
        evidence.write_text("ran\n")
        mtime = time.time() - age_h * 3600
        os.utime(evidence, (mtime, mtime))
        return NO_LABEL_ITEM.replace("health: -", f"health: {evidence}")

    def test_fresh_evidence_judges_ok(self):
        job = self._judge_without_a_label(self._item_with_evidence(1))
        self.assertEqual(job["status"], "ok")
        self.assertIsNone(job["label"])

    def test_evidence_past_the_cadence_judges_stale(self):
        job = self._judge_without_a_label(self._item_with_evidence(40))
        self.assertEqual(job["status"], "stale")
        self.assertEqual(job["action"], "none")
        self.assertIsNone(job["label"])

    def test_no_label_and_no_evidence_stays_unknown(self):
        job = self._judge_without_a_label(NO_LABEL_ITEM)
        self.assertEqual(job["status"], "unknown")
        self.assertEqual(job["action"], "none")
        self.assertIsNone(job["label"])
        self.assertIsNone(job["evidence_age_h"])


class UnjudgedFooterTests(EnvIsolatedTestCase):
    def _mixed_registry(self) -> Path:
        second = NO_LABEL_ITEM.replace("job-nolabel", "job-nolabel-2")
        return _write_registry(self.tmp, OK_ITEM, NO_LABEL_ITEM, second)

    def _run(self, extra_args: list) -> str:
        registry_path = self._mixed_registry()
        with (
            mock.patch.object(launchd, "is_loaded", side_effect=lambda c: c == "local.job-ok"),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
        ):
            code, out = _run_doctor(extra_args + ["--registry", str(registry_path)])
        self.assertEqual(code, 0)
        return out

    def test_report_collapses_unjudged_items_into_one_footer_line(self):
        out = self._run(["--report"])
        footer = "not judged (no launchd label, no evidence): job-nolabel, job-nolabel-2"
        self.assertIn(footer, out)
        table = out.split("== jobs ==")[1].split("not judged")[0]
        self.assertIn("job-ok", table)
        self.assertNotIn("job-nolabel", table)

    def test_json_still_carries_every_item(self):
        payload = json.loads(self._run(["--json"]))
        self.assertEqual(
            [j["id"] for j in payload["jobs"]], ["job-ok", "job-nolabel", "job-nolabel-2"]
        )


class CadenceParsingTests(unittest.TestCase):
    def test_monthly_beats_the_manual_keyword(self):
        self.assertEqual(doctor.cadence_interval_hours("manual, expected at least monthly"), 720)

    def test_quarterly_is_staleness_checkable(self):
        self.assertEqual(doctor.cadence_interval_hours("quarterly 1st 10:00"), 2184)

    def test_daily_time_beats_a_manual_kickstart_note(self):
        self.assertEqual(doctor.cadence_interval_hours("daily 08:30 (manual kickstart ok)"), 24)

    def test_weekly_with_a_day_and_time(self):
        self.assertEqual(doctor.cadence_interval_hours("weekly Mon 07:45"), 168)

    def test_cadences_without_an_expected_interval(self):
        for cadence in ("on-demand", "event", "always-on"):
            with self.subTest(cadence=cadence):
                self.assertIsNone(doctor.cadence_interval_hours(cadence))


class CustomCheckTests(unittest.TestCase):
    def test_exit_code_maps_to_warn(self):
        result = doctor.run_custom_check({
            "name": "t", "command": f"{sys.executable} -c 'import sys; sys.exit(3)'",
            "warn_exit": 3, "fail_exit": 9, "timeout_s": 5,
        })
        self.assertEqual(result["status"], "warn")

    def test_exit_code_maps_to_fail(self):
        result = doctor.run_custom_check({
            "name": "t", "command": f"{sys.executable} -c 'import sys; sys.exit(9)'",
            "warn_exit": 3, "fail_exit": 9, "timeout_s": 5,
        })
        self.assertEqual(result["status"], "fail")

    def test_zero_exit_is_ok(self):
        result = doctor.run_custom_check({
            "name": "t", "command": f"{sys.executable} -c 'pass'", "timeout_s": 5,
        })
        self.assertEqual(result["status"], "ok")

    def test_realm_defaults_to_none(self):
        result = doctor.run_custom_check({
            "name": "t", "command": f"{sys.executable} -c 'pass'", "timeout_s": 5,
        })
        self.assertIsNone(result["realm"])

    def test_realm_passes_through_from_config(self):
        result = doctor.run_custom_check({
            "name": "t", "command": f"{sys.executable} -c 'import sys; sys.exit(9)'",
            "fail_exit": 9, "timeout_s": 5, "realm": "client",
        })
        self.assertEqual(result["realm"], "client")

    def test_fail_detail_is_first_nonempty_stdout_line(self):
        cmd = (f"{sys.executable} -c 'import sys; print(); print(\"first line\"); "
               "print(\"second\"); sys.exit(9)'")
        result = doctor.run_custom_check({
            "name": "t", "command": cmd, "fail_exit": 9, "timeout_s": 5,
        })
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["detail"], "first line")

    def test_fail_detail_falls_back_to_exit_text_with_no_stdout(self):
        result = doctor.run_custom_check({
            "name": "t", "command": f"{sys.executable} -c 'import sys; sys.exit(9)'",
            "fail_exit": 9, "timeout_s": 5,
        })
        self.assertEqual(result["detail"], "exit 9")

    def test_warn_detail_uses_stdout_line_too(self):
        cmd = f"{sys.executable} -c 'print(\"low disk\"); import sys; sys.exit(3)'"
        result = doctor.run_custom_check({
            "name": "t", "command": cmd, "warn_exit": 3, "timeout_s": 5,
        })
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["detail"], "low disk")

    def test_fail_detail_truncated_to_200_chars(self):
        cmd = f"{sys.executable} -c 'print(\"x\" * 300); import sys; sys.exit(9)'"
        result = doctor.run_custom_check({
            "name": "t", "command": cmd, "fail_exit": 9, "timeout_s": 5,
        })
        self.assertEqual(len(result["detail"]), 200)


class DiskFreeCheckTests(unittest.TestCase):
    def test_below_fail_threshold(self):
        result = doctor.run_disk_free_check({"path": "/", "warn_gb": 10**9, "fail_gb": 10**9})
        self.assertEqual(result["status"], "fail")

    def test_below_warn_threshold_only(self):
        result = doctor.run_disk_free_check({"path": "/", "warn_gb": 10**9, "fail_gb": -1})
        self.assertEqual(result["status"], "warn")

    def test_above_both_thresholds(self):
        result = doctor.run_disk_free_check({"path": "/", "warn_gb": -1, "fail_gb": -1})
        self.assertEqual(result["status"], "ok")

    def test_tilde_path_expands_but_detail_keeps_configured_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                result = doctor.run_disk_free_check({"path": "~", "warn_gb": -1, "fail_gb": -1})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["detail"].endswith("free on ~"))

    def test_missing_path_detail_keeps_configured_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                result = doctor.run_disk_free_check(
                    {"path": "~/rigops-test-missing-dir", "warn_gb": -1, "fail_gb": -1}
                )
        self.assertEqual(result["status"], "fail")
        self.assertIn("~/rigops-test-missing-dir", result["detail"])
        self.assertNotIn(tmp + "/", result["detail"])


class GatesRecordTests(EnvIsolatedTestCase):
    def _gates_record(self) -> dict:
        path = core.local_state_path("doctor/gates.json")
        return json.loads(path.read_text())

    def test_written_even_when_everything_is_ok(self):
        registry_path = _write_registry(self.tmp, OK_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
        ):
            code, _ = _run_doctor(["--registry", str(registry_path)])
        self.assertEqual(code, 0)
        record = self._gates_record()
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["gates"], [])
        self.assertRegex(record["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_failing_job_is_recorded_with_null_realm_and_empty_detail(self):
        registry_path = _write_registry(self.tmp, FAILING_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 1)),
        ):
            _run_doctor(["--registry", str(registry_path)])
        self.assertEqual(self._gates_record()["gates"], [
            {"kind": "job", "name": "job-failing", "status": "failing",
             "realm": None, "detail": ""},
        ])

    def test_stale_job_is_recorded(self):
        evidence = Path(self.tmp) / "evidence.log"
        evidence.write_text("ran\n")
        mtime = time.time() - 40 * 3600
        os.utime(evidence, (mtime, mtime))
        stale_item = NO_LABEL_ITEM.replace("health: -", f"health: {evidence}")
        registry_path = _write_registry(self.tmp, stale_item)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=False),
        ):
            _run_doctor(["--registry", str(registry_path)])
        self.assertEqual(self._gates_record()["gates"], [
            {"kind": "job", "name": "job-nolabel", "status": "stale",
             "realm": None, "detail": ""},
        ])

    def test_custom_check_gate_carries_its_configured_realm_and_detail(self):
        registry_path = _write_registry(self.tmp, OK_ITEM)
        Path(os.environ["RIGOPS_CONFIG"]).write_text(json.dumps({"doctor": {"checks": {"custom": [
            {"name": "probe", "command": f"{sys.executable} -c 'import sys; sys.exit(1)'",
             "fail_exit": 1, "realm": "client"},
        ]}}}))
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
        ):
            _run_doctor(["--registry", str(registry_path)])
        self.assertEqual(self._gates_record()["gates"], [
            {"kind": "check", "name": "probe", "status": "fail",
             "realm": "client", "detail": "exit 1"},
        ])

    def test_ok_custom_check_is_not_a_gate(self):
        registry_path = _write_registry(self.tmp, OK_ITEM)
        Path(os.environ["RIGOPS_CONFIG"]).write_text(json.dumps({"doctor": {"checks": {"custom": [
            {"name": "probe", "command": f"{sys.executable} -c 'pass'"},
        ]}}}))
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
        ):
            _run_doctor(["--registry", str(registry_path)])
        self.assertEqual(self._gates_record()["gates"], [])

    def _run_with_config_findings(self, registry_path, findings) -> None:
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
            mock.patch.object(doctor.config_checks, "run_checks", return_value=findings),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                doctor.main(["--registry", str(registry_path)])

    def test_config_findings_for_one_check_collapse_into_one_failing_gate(self):
        registry_path = _write_registry(self.tmp, OK_ITEM)
        findings = [
            core.Finding(check="pointers", area="rigops", symptom="s1", evidence="e1", fix="f1"),
            core.Finding(check="pointers", area="rigops", symptom="s2", evidence="e2", fix="f2",
                         severity="warn"),
        ]
        self._run_with_config_findings(registry_path, findings)
        self.assertEqual(self._gates_record()["gates"], [
            {"kind": "config", "name": "pointers", "status": "fail",
             "realm": None, "detail": "2 findings"},
        ])

    def test_config_gate_is_warn_when_every_finding_is_a_warning(self):
        registry_path = _write_registry(self.tmp, OK_ITEM)
        findings = [core.Finding(check="levers", area="rigops", symptom="s", evidence="e",
                                  fix="f", severity="warn")]
        self._run_with_config_findings(registry_path, findings)
        self.assertEqual(self._gates_record()["gates"], [
            {"kind": "config", "name": "levers", "status": "warn",
             "realm": None, "detail": "1 findings"},
        ])


class EventsJsonlTests(EnvIsolatedTestCase):
    def test_event_appended_per_run(self):
        registry_path = _write_registry(self.tmp, OK_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
        ):
            _run_doctor(["--json", "--registry", str(registry_path)])
        events_path = Path(self.tmp) / "state" / "doctor" / "events.jsonl"
        lines = events_path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(
            set(event), {"ts", "jobs_checked", "fail_count", "stale_count", "healed"}
        )
        self.assertEqual(event["jobs_checked"], 1)
        self.assertEqual(event["fail_count"], 0)


class StaleCountTests(EnvIsolatedTestCase):
    def _stale_registry(self) -> Path:
        evidence = Path(self.tmp) / "evidence.log"
        evidence.write_text("ran\n")
        mtime = time.time() - 40 * 3600
        os.utime(evidence, (mtime, mtime))
        stale_item = NO_LABEL_ITEM.replace("health: -", f"health: {evidence}")
        return _write_registry(self.tmp, OK_ITEM, stale_item)

    def _run(self) -> dict:
        registry_path = self._stale_registry()
        with (
            mock.patch.object(launchd, "is_loaded", side_effect=lambda c: c == "local.job-ok"),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
        ):
            code, out = _run_doctor(["--json", "--registry", str(registry_path)])
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_json_payload_counts_the_stale_row(self):
        payload = self._run()
        self.assertEqual(payload["stale_count"], 1)
        self.assertEqual(payload["fail_count"], 0)

    def test_event_records_the_stale_count(self):
        self._run()
        events_path = Path(self.tmp) / "state" / "doctor" / "events.jsonl"
        event = json.loads(events_path.read_text().splitlines()[0])
        self.assertEqual(event["stale_count"], 1)
        self.assertEqual(event["fail_count"], 0)


class HealCooldownTests(EnvIsolatedTestCase):
    def _seed_heal_stamp(self, job_id: str, age_s: float) -> None:
        stamps_path = Path(self.tmp) / "state" / "doctor" / "heal-stamps.json"
        stamps_path.parent.mkdir(parents=True, exist_ok=True)
        stamps_path.write_text(json.dumps({job_id: time.time() - age_s}))

    def test_recent_heal_stamp_suppresses_kickstart(self):
        self._seed_heal_stamp("job-failing", 60)  # 1 minute ago, well under 12h default
        registry_path = _write_registry(self.tmp, FAILING_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 1)),
            mock.patch.object(launchd, "kickstart") as m_kick,
        ):
            code, out = _run_doctor(["--heal", "--json", "--registry", str(registry_path)])
        m_kick.assert_not_called()
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["action"], "notify")
        self.assertGreaterEqual(payload["fail_count"], 1)
        self.assertEqual(code, 0)

    def test_expired_heal_stamp_allows_kickstart(self):
        self._seed_heal_stamp("job-failing", 13 * 3600)  # past the 12h default cooldown
        registry_path = _write_registry(self.tmp, FAILING_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 1)),
            mock.patch.object(launchd, "kickstart", return_value=True) as m_kick,
        ):
            code, out = _run_doctor(["--heal", "--json", "--registry", str(registry_path)])
        m_kick.assert_called_once_with("local.job-failing")
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["action"], "kickstart")
        self.assertGreaterEqual(payload["fail_count"], 1)
        self.assertEqual(code, 0)


class ListFlagTests(EnvIsolatedTestCase):
    def test_list_renders_items_without_touching_launchd(self):
        registry_path = _write_registry(self.tmp, HUNG_ITEM, FAILING_ITEM, OK_ITEM)

        def _fail(*a, **k):
            raise AssertionError("--list must not judge job health")

        with mock.patch.object(launchd, "job_pid", side_effect=_fail):
            code, out = _run_doctor(["--list", "--json", "--registry", str(registry_path)])
        self.assertEqual(code, 0)
        items = json.loads(out)
        self.assertEqual([it["id"] for it in items], ["job-hung", "job-failing", "job-ok"])


class LabelPrefixGateTests(EnvIsolatedTestCase):
    def test_plist_label_not_prefix_matched_is_never_signalled(self):
        registry_path = _write_registry(self.tmp, UNRELATED_PLIST_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot") as m_snap,
            mock.patch.object(launchd, "terminate_process_group") as m_term,
            mock.patch.object(launchd, "kickstart") as m_kick,
        ):
            code, out = _run_doctor(["--heal", "--json", "--registry", str(registry_path)])
        m_snap.assert_not_called()
        m_term.assert_not_called()
        m_kick.assert_not_called()
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["status"], "unknown")
        self.assertEqual(payload["jobs"][0]["action"], "none")
        self.assertEqual(code, 0)


class SelfPidExclusionTests(EnvIsolatedTestCase):
    def test_resolved_pid_matching_self_is_reported_ok_and_not_signalled(self):
        registry_path = _write_registry(self.tmp, HUNG_ITEM)
        own_pid = os.getpid()
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, own_pid, None)),
            mock.patch.object(launchd, "runtime_hours", return_value=999.0),
            mock.patch.object(launchd, "terminate_process_group") as m_term,
        ):
            code, out = _run_doctor(["--heal", "--json", "--registry", str(registry_path)])
        m_term.assert_not_called()
        payload = json.loads(out)
        self.assertEqual(payload["jobs"][0]["status"], "ok")
        self.assertEqual(payload["jobs"][0]["action"], "none")
        self.assertEqual(code, 0)


class PidChangedRaceTests(EnvIsolatedTestCase):
    def test_pid_changed_before_kill_skips_and_records_event(self):
        registry_path = _write_registry(self.tmp, HUNG_ITEM)
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, 4242, None)),
            mock.patch.object(launchd, "runtime_hours", return_value=2.5),
            mock.patch.object(launchd, "job_pid", return_value=9999),
            mock.patch.object(launchd, "terminate_process_group") as m_term,
        ):
            code, out = _run_doctor(["--heal", "--json", "--registry", str(registry_path)])
        m_term.assert_not_called()
        payload = json.loads(out)
        self.assertIn("pid changed", " ".join(payload["healed"]))
        self.assertGreaterEqual(payload["fail_count"], 1)
        self.assertEqual(code, 0)


class CheckFailReportingTests(EnvIsolatedTestCase):
    def test_failing_custom_check_is_reported_without_failing_the_run(self):
        registry_path = _write_registry(self.tmp, OK_ITEM)
        config_path = Path(self.tmp) / "config.json"
        config_path.write_text(json.dumps({
            "doctor": {"checks": {"custom": [{
                "name": "disk", "command": f"{sys.executable} -c 'import sys; sys.exit(1)'",
                "fail_exit": 1,
            }]}},
        }))
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True),
            mock.patch.object(launchd, "job_snapshot", return_value=(True, None, 0)),
        ):
            code, out = _run_doctor(["--json", "--registry", str(registry_path)])
        payload = json.loads(out)
        self.assertEqual(payload["fail_count"], 0)
        self.assertEqual(payload["checks"][0]["status"], "fail")
        self.assertEqual(code, 0)


class WarningSeverityTests(unittest.TestCase):
    """A warning is reported alongside failures but never decides the exit code."""

    def _finding(self, severity):
        return doctor.rigops_core.Finding(
            check="levers", area="metrics", symptom=f"{severity} symptom", evidence="e",
            fix="f", key=severity, severity=severity,
        )

    def _run(self, findings, argv):
        buf = io.StringIO()
        with mock.patch.object(doctor.rigops_config, "load", return_value={}), \
             mock.patch.object(doctor.config_checks, "run_checks", return_value=findings), \
             contextlib.redirect_stdout(buf):
            code = doctor.main(argv)
        return code, buf.getvalue()

    def test_warning_alone_is_reported_and_exits_zero(self):
        code, out = self._run([self._finding("warn")], ["--config-only"])
        self.assertEqual(code, 0)
        self.assertIn("config checks: 0 findings", out)
        self.assertIn("warnings: 1", out)
        self.assertIn("warn symptom", out)

    def test_failure_beside_a_warning_still_fails(self):
        code, out = self._run([self._finding("warn"), self._finding("fail")], ["--config-only"])
        self.assertEqual(code, 1)
        self.assertIn("config checks: 1 findings", out)

    def test_json_carries_the_severity(self):
        code, out = self._run([self._finding("warn")], ["--config-only", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual([f["severity"] for f in json.loads(out)], ["warn"])


class ConfigOnlyStagedTests(unittest.TestCase):
    """--staged asserts the commit is sound, not that the whole tree is quiet."""

    GOOD = "---\nstatus: active\ndate: 2026-01-01\ntopic: something\n---\n\nbody\n"
    BROKEN = "prose with no frontmatter block\n"

    def _repo(self, tmp):
        root = Path(tmp) / "plans"
        root.mkdir()
        for argv in (
            ("init", "-q", "-b", "main"),
            ("config", "user.email", "t@example.com"),
            ("config", "user.name", "t"),
        ):
            subprocess.run(
                ["git", "-C", str(root), *argv], check=True, capture_output=True,
            )
        return root

    def _git(self, root, *argv):
        subprocess.run(["git", "-C", str(root), *argv], check=True, capture_output=True)

    def _run(self, root, argv):
        cfg = {"doctor": {"plans": {"dir": str(root)}}}
        buf = io.StringIO()
        cwd = os.getcwd()
        os.chdir(root)
        try:
            with mock.patch.object(doctor.rigops_config, "load", return_value=cfg), \
                 contextlib.redirect_stdout(buf):
                code = doctor.main(argv)
        finally:
            os.chdir(cwd)
        return code, buf.getvalue()

    def test_unstaged_breakage_elsewhere_does_not_block_the_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "in-flight.md").write_text(self.BROKEN)
            code, out = self._run(root, ["--config-only", "--staged", "--check", "plans"])
        self.assertEqual(code, 0)
        self.assertIn("0 findings", out)

    def test_staged_fix_is_judged_on_the_index_not_the_working_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            plan = root / "plan.md"
            plan.write_text(self.BROKEN)
            self._git(root, "add", "plan.md")
            self._git(root, "commit", "-q", "-m", "broken plan", "--no-verify")
            plan.write_text(self.GOOD)
            self._git(root, "add", "plan.md")
            plan.write_text(self.BROKEN)
            staged_code, staged_out = self._run(
                root, ["--config-only", "--staged", "--check", "plans"],
            )
            live_code, _ = self._run(root, ["--config-only", "--check", "plans"])
        self.assertEqual((staged_code, live_code), (0, 1))
        self.assertIn("0 findings", staged_out)

    def test_staged_defect_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "plan.md").write_text(self.BROKEN)
            self._git(root, "add", "plan.md")
            code, out = self._run(root, ["--config-only", "--staged", "--check", "plans"])
        self.assertEqual(code, 1)
        self.assertIn("missing frontmatter", out)

    def test_staged_outside_config_only_is_refused(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                doctor.main(["--report", "--staged"])


if __name__ == "__main__":
    unittest.main()
