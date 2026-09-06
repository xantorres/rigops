from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import launchd  # noqa: E402

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


def _write_registry(tmp: str, *item_texts: str) -> Path:
    path = Path(tmp) / "registry.md"
    path.write_text("schema: rigops.v1\nitems:\n" + "".join(item_texts) + "---\n")
    return path


def _run_doctor(args: list) -> tuple:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor.main(args)
    return code, buf.getvalue()


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name
        os.environ["RIGOPS_STATE_DIR"] = str(Path(self.tmp) / "state")
        os.environ["RIGOPS_CONFIG"] = str(Path(self.tmp) / "config.json")

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
        self.assertEqual(set(event), {"ts", "jobs_checked", "fail_count", "healed"})
        self.assertEqual(event["jobs_checked"], 1)
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


if __name__ == "__main__":
    unittest.main()
