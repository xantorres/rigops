from __future__ import annotations

import importlib.util
import sys
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


if __name__ == "__main__":
    unittest.main()
