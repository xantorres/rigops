from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from rigops import levers, transcripts

from tests.support import no_external_sources

UTC = dt.timezone.utc
CFG = {"ledger": {"watch_projects": {}}, "fixed_tax": {"paths": [], "globs": []}}


def _turn(ts: str, mid: str) -> str:
    return json.dumps({
        "type": "assistant", "timestamp": ts, "sessionId": "sess-1",
        "message": {"id": mid, "model": "claude-opus-4", "content": [],
                    "usage": {"input_tokens": 10, "output_tokens": 5}},
    })


@unittest.skipUnless(hasattr(time, "tzset"), "needs time.tzset")
class LocalDayTests(unittest.TestCase):
    def setUp(self):
        no_external_sources(self)
        old = os.environ.get("TZ")

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

        self.addCleanup(restore)
        os.environ["TZ"] = "Europe/Madrid"
        time.tzset()

    def _window(self, at: dt.date) -> tuple:
        with tempfile.TemporaryDirectory() as tmp:
            row = levers.build_row(at, "", cfg=CFG, transcripts_dir=Path(tmp))
        win = row["window"]
        return (dt.datetime.fromisoformat(win["since"]), dt.datetime.fromisoformat(win["until"]),
                win)

    def _assert_local_midnight(self, edge: dt.datetime) -> None:
        local = edge.astimezone()
        self.assertEqual((local.hour, local.minute, local.second), (0, 0, 0))

    def test_a_window_over_the_spring_change_is_167_hours_between_local_midnights(self):
        since, until, _ = self._window(dt.date(2026, 3, 31))
        self._assert_local_midnight(since)
        self._assert_local_midnight(until)
        self.assertEqual((until - since).total_seconds() / 3600, 167)

    def test_a_window_over_the_autumn_change_is_169_hours_between_local_midnights(self):
        since, until, _ = self._window(dt.date(2026, 10, 28))
        self._assert_local_midnight(since)
        self._assert_local_midnight(until)
        self.assertEqual((until - since).total_seconds() / 3600, 169)

    def test_stored_window_strings_stay_utc_iso(self):
        since, until, win = self._window(dt.date(2026, 10, 28))
        self.assertEqual(win["since"], since.astimezone(UTC).isoformat())
        self.assertTrue(win["since"].endswith("+00:00"))
        self.assertTrue(win["until"].endswith("+00:00"))
        self.assertEqual(win["days"], 7)

    def test_turns_either_side_of_the_first_local_midnight_are_split_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "proj-a" / "sess-1.jsonl"
            session.parent.mkdir()
            session.write_text("\n".join([
                _turn("2026-10-20T21:30:00Z", "before"),
                _turn("2026-10-20T22:30:00Z", "after"),
            ]) + "\n")
            row = levers.build_row(dt.date(2026, 10, 28), "", cfg=CFG, transcripts_dir=Path(tmp))
        self.assertEqual(row["turns"], 1)

    def test_start_of_day_reads_a_naive_value_with_the_offset_of_that_day(self):
        summer = transcripts.start_of_day(dt.date(2026, 7, 1))
        winter = transcripts.start_of_day(dt.date(2026, 12, 1))
        self.assertEqual(summer.utcoffset(), dt.timedelta(hours=2))
        self.assertEqual(winter.utcoffset(), dt.timedelta(hours=1))

    def test_a_naive_bound_is_read_with_the_offset_that_holds_on_its_day(self):
        now = dt.datetime(2026, 11, 21, tzinfo=UTC)
        bound = transcripts.parse_bound("2026-07-01T00:00:00", now)
        self.assertEqual(bound, dt.datetime(2026, 6, 30, 22, 0, tzinfo=UTC))
