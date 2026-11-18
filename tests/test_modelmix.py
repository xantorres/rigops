from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import levers, modelmix  # noqa: E402
from rigops.doctor import check_levers  # noqa: E402

from tests.support import no_external_sources  # noqa: E402


def turn(mid: str, minutes: int, model: str, created: int) -> dict:
    ts = dt.datetime(2026, 8, 12, 9, minutes, tzinfo=dt.timezone.utc)
    return {
        "type": "assistant", "sessionId": "sess-1",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "message": {"id": mid, "model": model, "content": [],
                    "usage": {"input_tokens": 0, "cache_creation_input_tokens": created,
                              "cache_read_input_tokens": 0}},
    }


class DefaultFamilyTests(unittest.TestCase):
    def _family(self, text: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            if text is not None:
                path.write_text(text)
            return modelmix.default_family(path)

    def test_ids_and_aliases_map_to_a_family(self):
        self.assertEqual(self._family('{"model": "claude-beta-2-1"}'), "beta")
        self.assertEqual(self._family('{"model": "beta[1m]"}'), "beta")

    def test_no_single_default_yields_none(self):
        for text in (None, "{}", '{"model": "default"}', '{"model": "alphaplan"}', "[]", "{"):
            self.assertIsNone(self._family(text), text)


class LedgerFieldsTests(unittest.TestCase):
    def setUp(self):
        no_external_sources(self)

    def test_share_of_main_thread_eit_on_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            session = root / "-Users-alice-app" / "sess-1.jsonl"
            session.parent.mkdir(parents=True)
            rows = [turn("m1", 0, "claude-alpha-1", 3_000), turn("m2", 1, "claude-beta-1", 1_000)]
            session.write_text("".join(json.dumps(r) + "\n" for r in rows))
            agent = root / "-Users-alice-app" / "sess-1" / "subagents" / "agent-1.jsonl"
            agent.parent.mkdir(parents=True)
            agent.write_text(json.dumps(turn("s1", 2, "claude-beta-1", 50_000)) + "\n")
            (Path(tmp) / "settings.json").write_text('{"model": "claude-beta-1"}')
            cfg = {"transcripts_dir": str(root), "ledger": {"watch_projects": {}},
                   "fixed_tax": {"paths": [], "globs": []}}
            row = levers.build_row(dt.date(2026, 8, 15), "", cfg=cfg, transcripts_dir=root)
        self.assertEqual(row["default_model"], "beta")
        self.assertEqual(row["main_default_model_pct"], 25.0)

    def test_without_turns_or_default_the_share_is_empty(self):
        fields = modelmix.ledger_fields([], Path("/nonexistent"))
        self.assertIsNone(fields["main_default_model_pct"])


class FloorRuleTests(unittest.TestCase):
    def test_built_in_floor_fires_once_the_main_thread_leaves_the_default(self):
        rows = [{"date": d, "main_default_model_pct": v} for d, v in
                (("2026-09-21", 97.2), ("2026-09-28", 98.4), ("2026-10-05", 28.0))]
        rules, builtin = check_levers.effective_rules({})
        ledger = Path("/tmp/ledger.jsonl")
        quiet = check_levers.judge(rows[:2], [], rules, dt.date(2026, 9, 29), ledger,
                                   builtin=builtin)
        found = check_levers.judge(rows, [], rules, dt.date(2026, 10, 6), ledger, builtin=builtin)
        self.assertEqual(quiet, [])
        self.assertEqual([f.key for f in found], ["main_default_model_pct:min"])
        self.assertIn("model pick overrides the settings default", found[0].fix)


if __name__ == "__main__":
    unittest.main()
