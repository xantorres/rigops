from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import transcripts  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "transcripts"


def _write_line(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


def _turn_obj(msg_id: str, ts: str, session: str, input_tokens: int) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": session,
        "message": {
            "id": msg_id,
            "model": "claude-sonnet-5-20260101",
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 1,
            },
            "content": [],
        },
    }


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _isolate_env(self, tmp: str) -> None:
        os.environ["RIGOPS_STATE_DIR"] = str(Path(tmp) / "state")
        os.environ["RIGOPS_CONFIG"] = str(Path(tmp) / "config.json")


class DedupeTests(EnvIsolatedTestCase):
    def test_streamed_lines_dedupe_by_message_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            turns = transcripts.collect_turns(None, None, transcripts_dir=FIXTURE_DIR)
            matches = [t for t in turns if t["input"] == 1000 and t["cache_creation"] == 200]
            self.assertEqual(len(matches), 1)
            turn = matches[0]
            self.assertEqual(turn["tools"], ["Read", "Write"])
            self.assertEqual(turn["output"], 250)
            self.assertEqual(turn["ts"], transcripts.parse_ts("2026-08-11T12:00:05Z"))


class CollectTurnsBranchTests(EnvIsolatedTestCase):
    def test_missing_message_id_keys_each_line_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            root = Path(tmp) / "transcripts"
            obj1 = _turn_obj("ignored", "2026-08-12T00:00:00Z", "sess", 111)
            del obj1["message"]["id"]
            obj2 = _turn_obj("ignored", "2026-08-12T00:00:01Z", "sess", 222)
            del obj2["message"]["id"]
            _write_line(root / "proj" / "s.jsonl", obj1)
            _write_line(root / "proj" / "s.jsonl", obj2)
            turns = transcripts.collect_turns(None, None, transcripts_dir=root)
            self.assertEqual(sorted(t["input"] for t in turns), [111, 222])

    def test_non_assistant_type_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            root = Path(tmp) / "transcripts"
            user_obj = _turn_obj("msg-u", "2026-08-12T00:00:00Z", "sess", 999)
            user_obj["type"] = "user"
            _write_line(root / "proj" / "s.jsonl", user_obj)
            turns = transcripts.collect_turns(None, None, transcripts_dir=root)
            self.assertEqual(turns, [])

    def test_assistant_without_usage_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            root = Path(tmp) / "transcripts"
            obj = _turn_obj("msg-no-usage", "2026-08-12T00:00:00Z", "sess", 999)
            del obj["message"]["usage"]
            _write_line(root / "proj" / "s.jsonl", obj)
            turns = transcripts.collect_turns(None, None, transcripts_dir=root)
            self.assertEqual(turns, [])

    def test_malformed_json_line_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            root = Path(tmp) / "transcripts"
            path = root / "proj" / "s.jsonl"
            _write_line(path, _turn_obj("msg-ok", "2026-08-12T00:00:00Z", "sess", 42))
            with path.open("a", encoding="utf-8") as fh:
                fh.write("not valid json\n")
            turns = transcripts.collect_turns(None, None, transcripts_dir=root)
            self.assertEqual([t["input"] for t in turns], [42])


class EitCtxTests(unittest.TestCase):
    def test_eit_known_value(self):
        t = {"input": 1000, "cache_creation": 200, "cache_read": 500}
        self.assertEqual(transcripts.eit(t), 1300.0)

    def test_ctx_known_value(self):
        t = {"input": 1000, "cache_creation": 200, "cache_read": 500}
        self.assertEqual(transcripts.ctx(t), 1700.0)


class PercentileTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(transcripts.percentile([], 0.5), 0.0)

    def test_single_value(self):
        self.assertEqual(transcripts.percentile([7], 0.37), 7)

    def test_p50_known_value(self):
        self.assertEqual(transcripts.percentile([10, 20, 30, 40, 50], 0.5), 30.0)

    def test_p90_known_value(self):
        self.assertEqual(transcripts.percentile([10, 20, 30, 40, 50], 0.9), 46.0)


class WindowBoundaryTests(EnvIsolatedTestCase):
    def test_7day_windows_do_not_overlap_at_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            root = Path(tmp) / "transcripts"
            since1 = transcripts.parse_ts("2026-08-10T00:00:00Z")
            until1 = since1 + dt.timedelta(days=7)
            until2 = until1 + dt.timedelta(days=7)

            _write_line(root / "proj" / "s.jsonl", _turn_obj("x", since1.isoformat(), "sess", 111))
            _write_line(root / "proj" / "s.jsonl", _turn_obj("y", until1.isoformat(), "sess", 222))
            before = since1 - dt.timedelta(seconds=1)
            _write_line(root / "proj" / "s.jsonl", _turn_obj("z", before.isoformat(), "sess", 333))

            week1 = transcripts.collect_turns(since1, until1, transcripts_dir=root)
            week2 = transcripts.collect_turns(until1, until2, transcripts_dir=root)

            self.assertEqual(sorted(t["input"] for t in week1), [111])
            self.assertEqual(sorted(t["input"] for t in week2), [222])


if __name__ == "__main__":
    unittest.main()
