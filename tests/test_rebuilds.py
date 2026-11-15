from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import levers, rebuilds  # noqa: E402

EIT_SCRIPT = Path(__file__).resolve().parent.parent / "libexec" / "rigops-eit"
UTC = dt.timezone.utc
BASE = dt.datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def at(minutes: float) -> str:
    return (BASE + dt.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def turn(mid: str, minutes: float, created: int, read: int, model: str = "model-a",
         version: str = "1.0.0") -> dict:
    return {
        "type": "assistant", "sessionId": "sess-1", "timestamp": at(minutes),
        "version": version,
        "message": {"id": mid, "model": model, "content": [],
                    "usage": {"input_tokens": 10, "cache_creation_input_tokens": created,
                              "cache_read_input_tokens": read}},
    }


def request(minutes: float, shape: str) -> dict:
    return {"type": "api-request", "timestamp": at(minutes), "shapeHash": shape}


COMPACT = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {}}


def write(root: Path, rows: list, name: str = "sess-1", sub: str = "") -> None:
    path = root / "-Users-alice-app" / sub / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def session_rows() -> list:
    return [
        request(0, "h1"), turn("m1", 0, 50_000, 0),
        turn("m2", 1, 30_000, 50_000),
        turn("m2", 1, 30_000, 50_000),
        turn("m3", 2, 82_000, 0, model="model-b"),
        COMPACT, turn("m4", 3, 20_000, 5_000, model="model-b"),
        request(130, "h2"), turn("m5", 130, 25_000, 0, model="model-b", version="1.1.0"),
        turn("m6", 150, 25_000, 0, model="model-b", version="1.1.0"),
    ]


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, session_rows())

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_rebuild_gets_its_class(self):
        found, _ = rebuilds.collect(None, None, transcripts_dir=self.root)
        self.assertEqual([h["class"] for h in found], ["quick", "compaction", "idle", "other"])

    def test_a_large_tool_result_on_a_warm_cache_is_not_a_rebuild(self):
        found, _ = rebuilds.collect(None, None, transcripts_dir=self.root)
        self.assertNotIn(BASE + dt.timedelta(minutes=1), [h["ts"] for h in found])
        self.assertEqual(len(found), 4)

    def test_context_around_each_rebuild(self):
        found, _ = rebuilds.collect(None, None, transcripts_dir=self.root)
        quick, compaction, idle, _ = found
        self.assertTrue(quick["model_change"])
        self.assertFalse(compaction["model_change"])
        self.assertIn("system:compact_boundary", compaction["before"])
        self.assertTrue(idle["version_change"])
        self.assertTrue(idle["shape_change"])
        self.assertEqual(idle["project"], "-Users-alice-app")

    def test_streamed_duplicates_count_once_and_subagents_are_skipped(self):
        write(self.root, [turn("s1", 0, 50_000, 0), turn("s2", 1, 60_000, 0)],
              name="agent-1", sub="sess-1/subagents")
        found, total = rebuilds.collect(None, None, transcripts_dir=self.root)
        self.assertEqual(len(found), 4)
        usage = [(50_000, 0), (30_000, 50_000), (82_000, 0), (20_000, 5_000), (25_000, 0),
                 (25_000, 0)]
        self.assertAlmostEqual(total, sum(10 + 1.25 * c + 0.1 * r for c, r in usage))

    def test_window_counts_only_turns_inside_it(self):
        since = BASE + dt.timedelta(minutes=100)
        found, _ = rebuilds.collect(since, None, transcripts_dir=self.root)
        self.assertEqual([h["class"] for h in found], ["idle", "other"])

    def test_shares_and_ledger_fields(self):
        found, total = rebuilds.collect(None, None, transcripts_dir=self.root)
        fields = rebuilds.ledger_fields(found, total)
        parts = sum(fields[f"rebuild_{c}_eit_pct"] for c in rebuilds.CLASSES)
        self.assertAlmostEqual(parts, fields["rebuild_eit_pct"], delta=0.2)
        self.assertEqual(rebuilds.ledger_fields([], 0)["rebuild_eit_pct"], None)
        summary = rebuilds.summarize(found, total)
        self.assertEqual(summary["classes"]["quick"]["model_change"], 1)

    def test_build_row_records_the_share(self):
        cfg = {"transcripts_dir": str(self.root), "ledger": {"watch_projects": {}},
               "fixed_tax": {"paths": [], "globs": []}}
        row = levers.build_row(dt.date(2026, 8, 15), "", cfg=cfg, transcripts_dir=self.root)
        self.assertGreater(row["rebuild_eit_pct"], 0)
        self.assertIn("rebuild_compaction_eit_pct", row)


class EitRebuildsCliTests(unittest.TestCase):
    def test_json_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, session_rows())
            env = {**os.environ, "RIGOPS_CONFIG": str(root / "absent.json")}
            base = [sys.executable, str(EIT_SCRIPT), "--rebuilds", "--dir", str(root),
                    "--since", "2026-08-01"]
            as_json = subprocess.run([*base, "--json"], env=env, capture_output=True, text=True)
            text = subprocess.run(base, env=env, capture_output=True, text=True)
        self.assertEqual(as_json.returncode, 0, as_json.stderr)
        payload = json.loads(as_json.stdout)
        self.assertEqual(payload["count"], 4)
        self.assertEqual(sorted(payload["classes"]), sorted(rebuilds.CLASSES))
        self.assertIn("cache rebuilds since 2026-08-01: 4 turns", text.stdout)
        self.assertIn("compaction", text.stdout)


if __name__ == "__main__":
    unittest.main()
