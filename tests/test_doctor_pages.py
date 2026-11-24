from __future__ import annotations

import json
import os
import plistlib
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from rigops import guard_state, launchd
from rigops import notify as rigops_notify

from tests.test_doctor import (
    FAILING_ITEM,
    HUNG_ITEM,
    REPO_ROOT,
    SHARED_LABEL_ITEMS,
    EnvIsolatedTestCase,
    _run_doctor,
    _write_registry,
    doctor,
)

NOW = 1_800_000_000.0
H = 3600


class PagingBase(EnvIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.sink = Path(self.tmp) / "sink.txt"
        self.state = Path(self.tmp) / "state" / "doctor"
        self.config = {"notify_command": f"sh -c 'echo x >> {self.sink}' notify"}
        self._write_config()

    def _write_config(self, **extra):
        self.config.update(extra)
        Path(os.environ["RIGOPS_CONFIG"]).write_text(json.dumps({"doctor": self.config}))

    def sent(self) -> int:
        return len(self.sink.read_text().splitlines()) if self.sink.exists() else 0

    def events(self) -> list:
        return [json.loads(x) for x in (self.state / "events.jsonl").read_text().splitlines()]

    def run_doctor(self, registry, snapshot=(True, None, 1), heal=True, runtime=None):
        with (
            mock.patch.object(launchd, "is_loaded", return_value=True) as self.is_loaded,
            mock.patch.object(launchd, "job_snapshot", return_value=snapshot),
            mock.patch.object(launchd, "kickstart", return_value=True) as kick,
            mock.patch.object(launchd, "runtime_hours", return_value=runtime),
            mock.patch.object(launchd, "job_pid", return_value=snapshot[1]),
            mock.patch.object(launchd, "terminate_process_group", return_value=True),
        ):
            flags = ["--heal"] if heal else []
            _, out = _run_doctor([*flags, "--json", "--registry", str(registry)])
        return json.loads(out), kick

    def age(self, hours: float) -> None:
        for name in ("notified.json", "heal-stamps.json", "kicked.json"):
            path = self.state / name
            if not path.exists():
                continue
            data = guard_state.unwrap(json.loads(path.read_text()))[0]
            shift = hours * H
            data = {
                k: (v if k == rigops_notify.PAGES_KEY else
                    {f: t - shift for f, t in v.items()} if isinstance(v, dict) else v - shift)
                for k, v in data.items()
            }
            path.write_text(json.dumps(guard_state.wrap(data)))


class KickPagingTests(PagingBase):
    def test_every_id_of_a_kicked_shared_label_is_kicked(self):
        registry = _write_registry(self.tmp, SHARED_LABEL_ITEMS)
        payload, kick = self.run_doctor(registry)
        kick.assert_called_once_with("local.shared")
        self.assertEqual(self.sent(), 0)
        self.assertEqual(self.events()[-1]["notified"], [])

    def test_job_kicked_on_every_pass_pages_after_the_first(self):
        registry = _write_registry(self.tmp, FAILING_ITEM)
        kicks = []
        for _ in range(5):
            _, kick = self.run_doctor(registry)
            kicks.append(kick.call_count)
            self.age(25)
        self.assertEqual(kicks, [1] * 5)
        self.assertGreaterEqual(self.sent(), 2)

    def test_zero_cooldown_kick_pages_on_the_second_pass(self):
        self._write_config(heal_cooldown_h=0)
        registry = _write_registry(self.tmp, FAILING_ITEM)
        self.run_doctor(registry)
        self.assertEqual(self.sent(), 0)
        _, kick = self.run_doctor(registry)
        kick.assert_called_once()
        self.assertEqual(self.sent(), 1)

    def test_a_recovery_does_not_clear_the_kick_mark(self):
        registry = _write_registry(self.tmp, FAILING_ITEM)
        self.run_doctor(registry)
        self.run_doctor(registry, snapshot=(True, None, 0))
        self.age(25)
        self.run_doctor(registry)
        self.assertEqual(self.sent(), 1)

    def test_a_kick_after_seven_days_is_silent_again(self):
        registry = _write_registry(self.tmp, FAILING_ITEM)
        self.run_doctor(registry)
        self.run_doctor(registry, snapshot=(True, None, 0))
        self.age(7 * 24 + 1)
        self.run_doctor(registry)
        self.assertEqual(self.sent(), 0)

    def test_the_old_list_marker_still_loads(self):
        registry = _write_registry(self.tmp, FAILING_ITEM)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "kicked.json").write_text(json.dumps(["job-failing"]))
        self.run_doctor(registry)
        self.assertEqual(self.sent(), 1)

    def test_a_kill_pages_even_when_the_next_pass_is_ok(self):
        registry = _write_registry(self.tmp, HUNG_ITEM)
        payload, _ = self.run_doctor(registry, snapshot=(True, 4242, 0), runtime=5.0)
        self.assertTrue(payload["healed"][0].startswith("terminated hung job"))
        self.assertEqual(self.sent(), 1)
        self.run_doctor(registry, snapshot=(True, 4243, 0), runtime=0.2)
        self.assertEqual(self.sent(), 1)


class CeilingTests(unittest.TestCase):
    def test_third_page_of_a_day_is_held_and_unstamped(self):
        stamps = {}
        pages = []
        for i, key in enumerate(("a", "b", "c")):
            due, stamps, held = rigops_notify.notify_due(stamps, {key}, NOW + i * 60)
            pages.append((due, held))
        self.assertEqual(pages, [(True, []), (True, []), (False, ["c"])])
        self.assertNotIn("c", stamps)

    def test_held_key_pages_the_next_day_if_still_failing(self):
        stamps = {}
        for i, key in enumerate(("a", "b", "c")):
            _, stamps, _ = rigops_notify.notify_due(stamps, {key}, NOW + i * 60)
        due, stamps, held = rigops_notify.notify_due(stamps, {"c"}, NOW + 25 * H)
        self.assertEqual((due, held), (True, []))
        self.assertIn("c", stamps)

    def test_expired_stamp_held_keeps_its_old_sent(self):
        stamps = {}
        _, stamps, _ = rigops_notify.notify_due(stamps, {"x"}, NOW)
        for i, key in enumerate(("a", "b")):
            _, stamps, _ = rigops_notify.notify_due(stamps, {key}, NOW + 25 * H + i * 60)
        due, stamps, held = rigops_notify.notify_due(stamps, {"x"}, NOW + 25 * H + 600)
        self.assertEqual((due, held), (False, ["x"]))


class DoctorCeilingTests(PagingBase):
    def test_event_names_the_held_keys(self):
        items = [
            FAILING_ITEM.replace("job-failing", f"job-{n}").replace(
                "local.job-failing", f"local.job-{n}")
            for n in "abc"
        ]
        for n in range(len(items)):
            registry = _write_registry(self.tmp, *items[: n + 1])
            self.run_doctor(registry, heal=False)
        self.assertEqual(self.sent(), 2)
        self.assertEqual(self.events()[-1]["held"], ["job:job-c:failing"])


class CorruptStampTests(unittest.TestCase):
    def due(self, raw):
        return rigops_notify.notify_due({"k": raw}, {"k"}, NOW)[0]

    def test_untrusted_stamps_count_as_no_stamp(self):
        bad = [
            float("nan"), float("inf"), -float("inf"), NOW + 99 * H, True, "x", None, [1],
            {"sent": float("nan"), "seen": NOW - H}, {"sent": NOW - H, "seen": float("inf")},
            {"sent": NOW + 99 * H, "seen": NOW + 99 * H}, {"sent": NOW - H},
            {"sent": "a", "seen": NOW}, {"sent": True, "seen": True},
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                self.assertTrue(self.due(raw))

    def test_trusted_stamps_still_dedupe(self):
        for raw in (NOW - H, {"sent": NOW - H, "seen": NOW - H}):
            with self.subTest(raw=raw):
                self.assertFalse(self.due(raw))

    def test_bad_pages_ledger_counts_as_zero(self):
        today = time.strftime("%Y-%m-%d", time.localtime(NOW))
        for raw in ({"day": "x", "n": 9}, {"n": 9}, "z", {"day": today, "n": "9"}):
            with self.subTest(raw=raw):
                due, _, _ = rigops_notify.notify_due({rigops_notify.PAGES_KEY: raw}, {"k"}, NOW)
                self.assertTrue(due)


class KillCeilingTests(unittest.TestCase):
    def test_a_kill_pages_past_the_ceiling_and_is_never_held(self):
        stamps = {rigops_notify.PAGES_KEY: {"day": time.strftime(
            "%Y-%m-%d", time.localtime(NOW)), "n": 2}}
        due, out, held = rigops_notify.notify_due(stamps, {"job:j:hung"}, NOW)
        self.assertEqual((due, held), (True, []))
        self.assertIn("job:j:hung", out)
        self.assertEqual(out[rigops_notify.PAGES_KEY]["n"], 2)

    def test_a_kill_does_not_use_up_the_ceiling(self):
        _, stamps, _ = rigops_notify.notify_due({}, {"job:j:hung"}, NOW)
        due, stamps, _ = rigops_notify.notify_due(stamps, {"a"}, NOW + 60)
        due_b, _, _ = rigops_notify.notify_due(stamps, {"b"}, NOW + 120)
        self.assertEqual((due, due_b), (True, True))

    def test_other_keys_over_the_ceiling_are_still_held_beside_a_kill(self):
        stamps = {rigops_notify.PAGES_KEY: {"day": time.strftime(
            "%Y-%m-%d", time.localtime(NOW)), "n": 2}}
        due, out, held = rigops_notify.notify_due(stamps, {"job:j:hung", "check:c"}, NOW)
        self.assertEqual((due, held), (True, ["check:c"]))
        self.assertNotIn("check:c", out)

    def test_a_check_named_like_a_kill_is_not_exempt(self):
        stamps = {rigops_notify.PAGES_KEY: {"day": time.strftime(
            "%Y-%m-%d", time.localtime(NOW)), "n": 2}}
        due, _, held = rigops_notify.notify_due(stamps, {"check:x:hung"}, NOW)
        self.assertEqual((due, held), (False, ["check:x:hung"]))

    def test_a_kill_that_flaps_inside_recovery_stays_silent(self):
        _, stamps, _ = rigops_notify.notify_due({}, {"job:j:hung"}, NOW)
        _, stamps, _ = rigops_notify.notify_due(stamps, set(), NOW + 2 * H)
        due, _, held = rigops_notify.notify_due(stamps, {"job:j:hung"}, NOW + 4 * H)
        self.assertEqual((due, held), (False, []))


class HugeStampTests(unittest.TestCase):
    BIG = int("1" + "0" * 400)

    def test_an_integer_too_big_for_a_float_counts_as_no_stamp(self):
        shapes = (
            self.BIG, {"sent": self.BIG, "seen": NOW - H}, {"sent": NOW - H, "seen": self.BIG},
        )
        for raw in shapes:
            with self.subTest(raw=str(raw)[:20]):
                due, _, _ = rigops_notify.notify_due({"k": raw}, {"k"}, NOW)
                self.assertTrue(due)

    def test_a_pages_ledger_is_never_read_as_a_stamp(self):
        junk = {"sent": NOW - H, "seen": NOW - H}
        _, out, _ = rigops_notify.notify_due({rigops_notify.PAGES_KEY: junk}, set(), NOW)
        self.assertNotIn(rigops_notify.PAGES_KEY, out)


class CorruptStateFileTests(PagingBase):
    def test_a_state_file_with_a_huge_integer_falls_back_to_the_default(self):
        self.state.mkdir(parents=True, exist_ok=True)
        big = "1" + "0" * 5000
        for name in ("notified.json", "kicked.json", "holds.json", "heal-stamps.json"):
            (self.state / name).write_text(big)
        self.run_doctor(_write_registry(self.tmp, FAILING_ITEM))
        events = self.events()
        self.assertEqual(len(events), 2)
        self.assertIn("jobs_checked", events[0])
        self.assertEqual(events[1]["event"].split(" rejected:")[0], "heal-stamps.json")

    def test_a_state_file_holding_the_wrong_json_shape_falls_back_to_the_default(self):
        self.state.mkdir(parents=True, exist_ok=True)
        for shape in ("5", "[1, 2]", '"text"', "null"):
            for name in ("notified.json", "kicked.json", "holds.json", "heal-stamps.json"):
                (self.state / name).write_text(shape)
            with self.subTest(shape=shape):
                self.run_doctor(_write_registry(self.tmp, FAILING_ITEM))
                self.assertGreaterEqual(len(self.events()), 1)

    def test_a_marker_list_of_unhashable_items_is_ignored(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "kicked.json").write_text(json.dumps([["a"], {"b": 1}, "job-failing"]))
        self.run_doctor(_write_registry(self.tmp, FAILING_ITEM))
        self.assertEqual(len(self.events()), 1)


class LabelLookupTests(PagingBase):
    def test_the_cause_policy_looks_a_label_up_once_per_item(self):
        self._write_config(cause_policy="shadow")
        self.run_doctor(_write_registry(self.tmp, FAILING_ITEM))
        self.assertEqual(self.is_loaded.call_count, 1)


class KickMarkTests(unittest.TestCase):
    WEEK = 7 * 24 * H

    def test_a_key_gets_one_silent_kick_a_week(self):
        silenced, marks = rigops_notify.kick_suppressed({}, {"a"}, NOW)
        self.assertEqual(silenced, {"a"})
        silenced, marks = rigops_notify.kick_suppressed(marks, {"a"}, NOW + 25 * H)
        self.assertEqual((silenced, marks), (set(), {"a": NOW}))
        silenced, _ = rigops_notify.kick_suppressed(marks, {"a"}, NOW + self.WEEK + 1)
        self.assertEqual(silenced, {"a"})

    def test_the_mark_outlives_a_run_without_that_kick(self):
        _, marks = rigops_notify.kick_suppressed({}, {"a"}, NOW)
        _, marks = rigops_notify.kick_suppressed(marks, set(), NOW + 2 * H)
        self.assertEqual(marks, {"a": NOW})

    def test_an_expired_or_untrusted_mark_is_dropped(self):
        marks = {"old": NOW - self.WEEK - 1, "nan": float("nan"), "future": NOW + H,
                 "big": int("1" + "0" * 400), "ok": NOW - H}
        _, kept = rigops_notify.kick_suppressed(marks, set(), NOW)
        self.assertEqual(kept, {"ok": NOW - H})


class HoldReleaseTests(PagingBase):
    def setUp(self):
        super().setUp()
        plist = Path(self.tmp) / "local.job-failing.plist"
        self.log = Path(self.tmp) / "job.err.log"
        plist.write_bytes(plistlib.dumps({"StandardErrorPath": str(self.log)}))
        self.log.write_text(
            (REPO_ROOT / "tests" / "fixtures" / "cause" / "network-dns.txt").read_text()
        )
        self.registry = _write_registry(
            self.tmp, FAILING_ITEM.replace("/fake/local.job-failing.plist", str(plist))
        )
        self.marker = Path(self.tmp) / "probes"

    def seed(self, releases_h=(), try_h=2, kicked_h=None):
        self.state.mkdir(parents=True, exist_ok=True)
        now = time.time()
        (self.state / "holds.json").write_text(json.dumps({"job-failing": {
            "cause": "network", "since": now - 5 * H, "last_try": now - try_h * H,
            "releases": [now - h * H for h in releases_h],
        }}))
        if kicked_h is not None:
            (self.state / "heal-stamps.json").write_text(
                json.dumps({"job-failing": now - kicked_h * H})
            )

    def holds(self):
        raw = json.loads((self.state / "holds.json").read_text())
        return guard_state.unwrap(raw)[0]["job-failing"]

    def test_release_kicks_while_the_heal_cooldown_is_active(self):
        self._write_config(cause_policy="on", cause_probe_command="true")
        self.seed(releases_h=(2,), kicked_h=2)
        payload, kick = self.run_doctor(self.registry)
        kick.assert_called_once()
        self.assertEqual(payload["jobs"][0]["action"], "kickstart")
        self.assertEqual(len(self.holds()["releases"]), 2)

    def test_release_is_still_capped_at_three_a_day(self):
        self._write_config(cause_policy="on", cause_probe_command="true")
        self.seed(releases_h=(2, 5, 8), kicked_h=2)
        _, kick = self.run_doctor(self.registry)
        kick.assert_not_called()

    def test_release_is_still_spaced_by_one_hour(self):
        self._write_config(cause_policy="on", cause_probe_command="true")
        self.seed(releases_h=(0.5,), try_h=0.5, kicked_h=0.5)
        _, kick = self.run_doctor(self.registry)
        kick.assert_not_called()

    def test_whitespace_only_probe_means_unset(self):
        self._write_config(cause_policy="on", cause_probe_command="   ")
        self.seed()
        payload, kick = self.run_doctor(self.registry)
        kick.assert_called_once()
        self.assertEqual(payload["jobs"][0]["cause"], "network")
        self.assertFalse(any("probe" in e.get("event", "") for e in self.events()))

    def test_probe_that_cannot_run_keeps_the_hold_and_leaves_a_warning(self):
        for command in ("/no/such/binary arg", "probe 'unclosed"):
            with self.subTest(command=command):
                self._write_config(cause_policy="on", cause_probe_command=command)
                self.seed()
                _, kick = self.run_doctor(self.registry)
                kick.assert_not_called()
                self.assertTrue(
                    any("cause probe could not run" in e.get("event", "") for e in self.events())
                )

    def test_probe_runs_once_for_a_fleet(self):
        items = ""
        holds = {}
        for n in "abc":
            plist = Path(self.tmp) / f"local.job-{n}.plist"
            plist.write_bytes(plistlib.dumps({"StandardErrorPath": str(self.log)}))
            items += FAILING_ITEM.replace(
                "/fake/local.job-failing.plist", str(plist)).replace("job-failing", f"job-{n}")
            holds[f"job-{n}"] = {
                "cause": "network", "since": time.time() - 5 * H,
                "last_try": time.time() - 2 * H, "releases": [],
            }
        registry = _write_registry(self.tmp, items)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "holds.json").write_text(json.dumps(holds))
        self._write_config(
            cause_policy="on", cause_probe_command=f"sh -c 'echo x >> {self.marker}; exit 1' p",
        )
        self.run_doctor(registry)
        self.assertEqual(len(self.marker.read_text().splitlines()), 1)


class ProbeRunnerTests(unittest.TestCase):
    def test_every_failure_is_closed_and_recorded(self):
        for exc in (
            subprocess.TimeoutExpired("p", 120), FileNotFoundError(), ValueError("x"),
            RuntimeError("boom"),
        ):
            with self.subTest(exc=type(exc).__name__):
                warnings = []
                with mock.patch.object(doctor.procgroup, "run_group", side_effect=exc):
                    self.assertFalse(doctor._run_probe("p arg", warnings)())
                self.assertEqual(len(warnings), 1)

    def test_a_nonzero_exit_is_not_a_warning(self):
        warnings = []
        done = subprocess.CompletedProcess([], 1)
        with mock.patch.object(doctor.procgroup, "run_group", return_value=done):
            self.assertFalse(doctor._run_probe("p", warnings)())
        self.assertEqual(warnings, [])

    def test_result_is_memoized(self):
        done = subprocess.CompletedProcess([], 0)
        with mock.patch.object(doctor.procgroup, "run_group", return_value=done) as run:
            probe = doctor._run_probe("p", [])
            self.assertTrue(probe())
            self.assertTrue(probe())
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
