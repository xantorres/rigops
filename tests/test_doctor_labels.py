from __future__ import annotations

import hashlib
import json
import plistlib
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import cause as rigops_cause  # noqa: E402

from tests.test_doctor import FAILING_ITEM, REPO_ROOT, _write_registry  # noqa: E402
from tests.test_doctor_pages import H, PagingBase  # noqa: E402


def _item(job_id: str, plist: Path, extra: str = "") -> str:
    return FAILING_ITEM.replace("/fake/local.job-failing.plist", str(plist)).replace(
        "job-failing", job_id
    ) + extra


class LabelBase(PagingBase):
    def setUp(self):
        super().setUp()
        self.plist = Path(self.tmp) / "local.shared.plist"
        self.log = Path(self.tmp) / "job.err.log"
        self.plist.write_bytes(plistlib.dumps({"StandardErrorPath": str(self.log)}))
        self.log.write_text(
            (REPO_ROOT / "tests" / "fixtures" / "cause" / "network-dns.txt").read_text()
        )

    def registry(self, *ids, extra=""):
        return _write_registry(self.tmp, *[_item(i, self.plist, extra) for i in ids])

    def seed_json(self, name, data):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / name).write_text(json.dumps(data))

    def hold(self, since_h=5, try_h=2, releases_h=()):
        now = time.time()
        return {
            "cause": "network", "since": now - since_h * H, "last_try": now - try_h * H,
            "releases": [now - h * H for h in releases_h],
        }


class KickedLabelSilenceTests(LabelBase):
    def test_every_id_of_a_kicked_label_is_silent_whatever_its_action(self):
        now = time.time()
        self.seed_json("heal-stamps.json", {"job-c": now - 60, "job-d": now - 60})
        registry = self.registry("job-a", "job-b", "job-c", "job-d")
        _, kick = self.run_doctor(registry)
        kick.assert_called_once()
        self.assertEqual(self.sent(), 0)
        self.assertEqual(self.events()[-1]["notified"], [])

    def test_a_sibling_in_cooldown_ahead_of_the_kicked_one_is_silent_too(self):
        self.seed_json("heal-stamps.json", {"job-a": time.time() - 60})
        registry = self.registry("job-a", "job-b")
        _, kick = self.run_doctor(registry)
        kick.assert_called_once()
        self.assertEqual(self.sent(), 0)


class KickMarkSurvivesFailedSendTests(PagingBase):
    def test_a_failed_send_for_another_key_keeps_the_kick_marker(self):
        check = {"name": "c", "command": "exit 2", "fail_exit": 2}
        self._write_config(
            heal_cooldown_h=0, notify_command="sh -c 'exit 3' notify", checks={"custom": [check]},
        )
        registry = _write_registry(self.tmp, FAILING_ITEM)
        self.run_doctor(registry)
        self.assertIn("job-failing", json.loads((self.state / "kicked.json").read_text()))
        self._write_config(notify_command=f"sh -c 'echo x >> {self.sink}' notify")
        self.run_doctor(registry)
        self.assertIn("job:job-failing:failing", self.events()[-1]["notified"])


class KillCooldownTests(LabelBase):
    def test_a_released_hold_does_not_kick_through_a_kills_cooldown(self):
        self._write_config(cause_policy="on", cause_probe_command="true")
        registry = self.registry("job-failing", extra="    max_runtime_h: 1\n")
        self.run_doctor(registry, snapshot=(True, 4242, 0), runtime=5.0)
        self.assertIn("kill:job-failing", json.loads(
            (self.state / "heal-stamps.json").read_text()))
        self.seed_json("holds.json", {"job-failing": self.hold()})
        payload, kick = self.run_doctor(registry)
        kick.assert_not_called()
        self.assertEqual(payload["jobs"][0]["action"], "hold")
        self.assertEqual(json.loads((self.state / "holds.json").read_text())[
            "job-failing"]["releases"], [])

    def test_a_kill_stamp_alone_blocks_a_release_the_label_pacing_would_allow(self):
        self._write_config(cause_policy="on", cause_probe_command="true")
        registry = self.registry("job-failing")
        self.seed_json("heal-stamps.json", {"kill:job-failing": time.time() - 1.5 * H})
        self.seed_json("holds.json", {"job-failing": self.hold()})
        _, kick = self.run_doctor(registry)
        kick.assert_not_called()

    def test_the_release_kicks_once_the_kills_cooldown_is_over(self):
        self._write_config(cause_policy="on", cause_probe_command="true")
        registry = self.registry("job-failing", extra="    max_runtime_h: 1\n")
        self.run_doctor(registry, snapshot=(True, 4242, 0), runtime=5.0)
        self.age(13)
        self.seed_json("holds.json", {"job-failing": self.hold()})
        _, kick = self.run_doctor(registry)
        kick.assert_called_once()


class LabelPacingTests(LabelBase):
    def setUp(self):
        super().setUp()
        self._write_config(cause_policy="on", cause_probe_command="true")
        self.registry_ab = self.registry("job-a", "job-b")

    def test_a_sibling_released_half_an_hour_ago_holds_the_label(self):
        self.seed_json("holds.json", {
            "job-a": self.hold(), "job-b": self.hold(try_h=0.5, releases_h=(0.5,)),
        })
        payload, kick = self.run_doctor(self.registry_ab)
        kick.assert_not_called()
        self.assertEqual({j["action"] for j in payload["jobs"]}, {"hold"})

    def test_a_sibling_kicked_half_an_hour_ago_by_heal_stamp_holds_the_label(self):
        self.seed_json("holds.json", {"job-a": self.hold(), "job-b": self.hold()})
        self.seed_json("heal-stamps.json", {"job-b": time.time() - 0.5 * H})
        _, kick = self.run_doctor(self.registry_ab)
        kick.assert_not_called()

    def test_three_label_kicks_in_a_day_cap_every_sibling(self):
        self.seed_json("holds.json", {
            "job-a": self.hold(), "job-b": self.hold(releases_h=(2, 5, 8)),
        })
        _, kick = self.run_doctor(self.registry_ab)
        kick.assert_not_called()

    def test_one_release_serves_the_label_and_stamps_every_hold(self):
        self.seed_json("holds.json", {"job-a": self.hold(), "job-b": self.hold(try_h=0.5)})
        payload, kick = self.run_doctor(self.registry_ab)
        kick.assert_called_once()
        holds = json.loads((self.state / "holds.json").read_text())
        self.assertEqual(len(holds["job-a"]["releases"]), 1)
        self.assertEqual(holds["job-b"]["releases"], holds["job-a"]["releases"])

    def test_two_released_siblings_make_one_kick_and_one_stays_held(self):
        self.seed_json("holds.json", {"job-a": self.hold(), "job-b": self.hold()})
        payload, kick = self.run_doctor(self.registry_ab)
        kick.assert_called_once()
        self.assertEqual(sorted(j["action"] for j in payload["jobs"]), ["hold", "kickstart"])


class WouldEvidenceTests(LabelBase):
    def test_would_records_exit_log_and_a_hash_of_the_tail_never_the_tail(self):
        self._write_config(cause_policy="shadow")
        self.run_doctor(self.registry("job-failing"))
        tail = rigops_cause.tail_of(self.log, now=time.time(), max_age_h=24)
        [entry] = self.events()[0]["would"]
        self.assertEqual(entry["exit"], 1)
        self.assertEqual(entry["tail_sha"], hashlib.sha256(tail.encode()).hexdigest()[:12])
        self.assertTrue(entry["log"].endswith("job.err.log"))
        self.assertNotIn("nodename nor servname", (self.state / "events.jsonl").read_text())


class ProbeCommandTypeTests(LabelBase):
    def test_a_probe_command_that_is_not_a_string_warns(self):
        for value in (5, ["true"], None):
            with self.subTest(value=value):
                self._write_config(cause_policy="shadow", cause_probe_command=value)
                self.run_doctor(self.registry("job-failing"))
                self.assertTrue(any(
                    "cause_probe_command" in e.get("event", "") for e in self.events()
                ))
                (self.state / "events.jsonl").unlink()


if __name__ == "__main__":
    unittest.main()
