from __future__ import annotations

import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

TOOL = Path(__file__).resolve().parent.parent / "tools" / "replay-notify.py"
spec = importlib.util.spec_from_file_location("replay_notify", TOOL)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def event(ts, fail=0, healed=(), stale=0, notified=()):
    return {"ts": ts, "fail_count": fail, "stale_count": stale, "healed": list(healed),
            "notified": list(notified)}


class ReplayTests(unittest.TestCase):
    def test_a_stale_kick_is_not_a_failing_job(self):
        e = event("2026-11-01T10:00:00Z", fail=1, stale=1, healed=[
            "kickstarted job-a (local.a)", "kickstarted job-b (local.b)",
        ])
        ids, kicked, _ = replay.failing_of(e)
        self.assertEqual((ids, kicked), (["job-a"], ["job-a", "job-b"]))

    def test_checks_the_event_paged_are_replayed(self):
        events = [event("2026-11-01T10:00:00Z", notified=["check:disk"])]
        per_day, _ = replay.replay(events, "current")
        self.assertEqual(sum(per_day.values()), 1)
        per_day, _ = replay.replay(events, "current", with_checks=False)
        self.assertEqual(sum(per_day.values()), 0)

    def test_the_ceiling_delays_the_third_page_of_a_day(self):
        events = [
            event(f"2026-11-01T1{i}:00:00Z", notified=[f"check:c{i}"]) for i in range(3)
        ]
        capped, held = replay.replay(events, "current")
        uncapped, _ = replay.replay(events, "uncapped")
        self.assertEqual((sum(capped.values()), held), (2, 1))
        self.assertEqual(sum(uncapped.values()), 3)

    def test_the_day_is_the_local_one(self):
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Etc/GMT-7"
        time.tzset()
        try:
            per_day, _ = replay.replay(
                [event("2026-11-01T20:00:00Z", notified=["check:disk"])], "current"
            )
        finally:
            if old_tz is None:
                os.environ.pop("TZ")
            else:
                os.environ["TZ"] = old_tz
            time.tzset()
        self.assertEqual(dict(per_day), {"2026-11-02": 1})

    def test_the_window_counts_its_check_pages(self):
        events = [
            event("2026-11-01T10:00:00Z", notified=["check:disk", "job:a:failing"]),
            event("2026-11-01T11:00:00Z", notified=["check:net"]),
        ]
        self.assertEqual(replay.check_pages(events), 2)
        self.assertEqual(replay.check_pages([event("2026-11-01T10:00:00Z")]), 0)

    def test_a_longer_recovery_window_keeps_a_flapping_failure_silent(self):
        events = [
            event("2026-11-01T10:00:00Z", fail=1), event("2026-11-01T10:30:00Z"),
            event("2026-11-01T13:00:00Z", fail=1),
        ]
        short, _ = replay.replay(events, "uncapped", recovery_h=1)
        long, _ = replay.replay(events, "uncapped", recovery_h=6)
        self.assertEqual((sum(short.values()), sum(long.values())), (2, 1))


if __name__ == "__main__":
    unittest.main()
