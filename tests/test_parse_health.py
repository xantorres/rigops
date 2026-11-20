from __future__ import annotations

import builtins
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import ledger_row, levers  # noqa: E402
from rigops.doctor import check_levers  # noqa: E402
from rigops.errors import RigopsError  # noqa: E402

from tests.support import no_external_sources, path_with_failing_sources  # noqa: E402
from tests.test_state_contract import Sandbox  # noqa: E402

CFG = {"ledger": {"watch_projects": {}}, "fixed_tax": {"paths": [], "globs": []}}
AT = dt.date(2026, 8, 15)


def _turn(day: int, n: int, session: str = "sess-1") -> str:
    return json.dumps({
        "type": "assistant", "timestamp": f"2026-08-{day:02d}T12:00:00Z", "sessionId": session,
        "message": {"id": f"m{day}-{n}-{session}", "model": "claude-opus-4", "content": [],
                    "usage": {"input_tokens": 10, "output_tokens": 5}},
    })


def make_corpus(root: Path, good: int = 30, bad: int = 0) -> list:
    """One main file with `good` turns plus `bad` torn lines, and one subagent file."""
    main = root / "proj-a" / "sess-1.jsonl"
    sub = root / "proj-a" / "sess-1" / "subagents" / "agent-1.jsonl"
    sub.parent.mkdir(parents=True)
    main.write_text("\n".join([_turn(10, i) for i in range(good)] + ['{"torn": '] * bad) + "\n")
    sub.write_text(_turn(11, 0, "sess-sub") + "\n")
    return [main, sub]


class RowCountsTests(unittest.TestCase):
    def setUp(self):
        no_external_sources(self)

    def test_row_records_lines_seen_and_parsed_without_a_version_bump(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_corpus(Path(tmp), good=30, bad=2)
            row = levers.build_row(AT, "", cfg=CFG, transcripts_dir=Path(tmp))
        self.assertEqual((row["lines_seen"], row["lines_parsed"]), (33, 31))
        self.assertEqual(row["version"], ledger_row.ROW_VERSION)
        self.assertEqual(ledger_row.ROW_VERSION, 1)

    def test_a_line_that_is_json_but_not_an_object_counts_as_unparsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            main, _ = make_corpus(Path(tmp), good=30)
            with main.open("a") as fh:
                fh.write('[1, 2]\n"text"\n3\nnull\n')
            row = levers.build_row(AT, "", cfg=CFG, transcripts_dir=Path(tmp))
        self.assertEqual((row["lines_seen"], row["lines_parsed"]), (35, 31))
        self.assertEqual(row["turns"], 31)

    def test_each_transcript_file_is_opened_once_per_row(self):
        opened: Counter = Counter()
        real_open = builtins.open

        def counting(file, *args, **kwargs):
            if str(file).endswith(".jsonl"):
                opened[str(file)] += 1
            return real_open(file, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            files = make_corpus(Path(tmp))
            with mock.patch.object(builtins, "open", counting):
                levers.build_row(AT, "", cfg=CFG, transcripts_dir=Path(tmp))
        self.assertEqual(dict(opened), {str(p): 1 for p in files})

    def test_a_missing_transcripts_dir_is_an_error_not_an_empty_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RigopsError) as ctx:
                levers.build_row(AT, "", cfg=CFG, transcripts_dir=Path(tmp) / "nope")
        self.assertIn("nope", str(ctx.exception))

    def test_an_empty_week_is_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = levers.build_row(AT, "", cfg=CFG, transcripts_dir=Path(tmp))
            make_corpus(Path(tmp))
            full = levers.build_row(AT, "", cfg=CFG, transcripts_dir=Path(tmp))
        self.assertTrue(empty["window_empty"])
        self.assertEqual((empty["turns"], empty["lines_seen"]), (0, 0))
        self.assertFalse(full["window_empty"])

    def test_the_counts_are_not_diff_columns(self):
        old = {"date": "2026-08-01", "turns": 50, "lines_seen": 100, "lines_parsed": 100}
        new = {"date": "2026-08-08", "turns": 60, "lines_seen": 200, "lines_parsed": 190}
        self.assertEqual(sorted(levers.compute_deltas(old, new)), ["turns"])


class LedgerCommandTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.env["PATH"] = path_with_failing_sources(os.environ["PATH"])
        self.transcripts = self.home / "transcripts"
        self.config.write_text(json.dumps({
            "transcripts_dir": str(self.transcripts), "fixed_tax": {"paths": [], "globs": []},
        }))

    def write(self, *extra):
        return self.run_cmd(["ledger", "write", "--at", AT.isoformat(), *extra])

    def test_more_than_one_percent_unparsed_warns_once_and_still_writes(self):
        make_corpus(self.transcripts, good=30, bad=2)
        proc = self.write()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stderr.strip(),
            "warning: 2 of 33 transcript lines (6.1%) did not parse; this row may undercount",
        )
        self.assertEqual(len((self.state / "ledger.jsonl").read_text().splitlines()), 1)

    def test_one_percent_or_less_unparsed_is_quiet(self):
        make_corpus(self.transcripts, good=199, bad=1)
        proc = self.write()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("did not parse", proc.stderr)

    def test_a_missing_transcripts_dir_exits_2_with_one_error_line_and_writes_nothing(self):
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra):
                proc = self.write(*extra)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(len(proc.stderr.strip().splitlines()), 1, proc.stderr)
                self.assertTrue(proc.stderr.startswith("error:"), proc.stderr)
                self.assertFalse((self.state / "ledger.jsonl").exists())
                self.assertFalse((self.state / "ledger.md").exists())

    def test_show_does_not_need_the_transcripts_dir(self):
        proc = self.run_cmd(["ledger", "write", "--show"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.run_cmd(["ledger", "write", "--show", "--json"])
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_an_empty_week_is_written_and_flagged_on_stderr(self):
        self.transcripts.mkdir()
        proc = self.write()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stderr.strip(),
            f"warning: no transcript turns in the 7 days before {AT}; row written, marked empty",
        )
        row = json.loads((self.state / "ledger.jsonl").read_text().splitlines()[0])
        self.assertTrue(row["window_empty"])

    def seed(self, *rows) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "ledger.jsonl").write_text(
            "".join(json.dumps({"version": 1, **r}) + "\n" for r in rows))

    def test_diff_under_the_minimum_prints_insufficient_data_and_exits_0(self):
        self.seed({"date": "2026-08-01", "turns": 5, "eit_per_turn": 100},
                  {"date": "2026-08-08", "turns": 400, "eit_per_turn": 200})
        proc = self.run_cmd(["ledger", "diff"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("insufficient data", proc.stdout)
        self.assertNotIn("+100.0%", proc.stdout)

    def test_diff_json_flags_insufficient_data_and_withholds_percentages(self):
        self.seed({"date": "2026-08-01", "turns": 400, "eit_per_turn": 100},
                  {"date": "2026-08-08", "turns": 3, "eit_per_turn": 200})
        payload = self.json_of(["ledger", "diff", "--json"])
        self.assertIs(payload["insufficient_data"], True)
        self.assertIsNone(payload["deltas"]["eit_per_turn"]["pct"])

    def test_diff_over_the_minimum_keeps_its_percentages(self):
        self.seed({"date": "2026-08-01", "turns": 400, "eit_per_turn": 100},
                  {"date": "2026-08-08", "turns": 300, "eit_per_turn": 200})
        payload = self.json_of(["ledger", "diff", "--json"])
        self.assertIs(payload["insufficient_data"], False)
        self.assertEqual(payload["deltas"]["eit_per_turn"]["pct"], 100.0)
        proc = self.run_cmd(["ledger", "diff"])
        self.assertNotIn("insufficient data", proc.stdout)

    def test_diff_treats_an_empty_window_as_insufficient(self):
        self.seed({"date": "2026-08-01", "turns": 400, "eit_per_turn": 100},
                  {"date": "2026-08-08", "turns": 0, "window_empty": True})
        self.assertIs(self.json_of(["ledger", "diff", "--json"])["insufficient_data"], True)

    def test_diff_with_fewer_than_two_rows_says_sufficient_in_json(self):
        self.seed({"date": "2026-08-01", "turns": 5})
        self.assertIs(self.json_of(["ledger", "diff", "--json"])["insufficient_data"], False)


class DoctorParseHealthTests(unittest.TestCase):
    LEDGER = Path("/tmp/ledger.jsonl")

    def judge(self, *rows):
        return check_levers.judge(list(rows), [], {}, dt.date(2026, 8, 20), self.LEDGER)

    def row(self, **extra) -> dict:
        return {"date": "2026-08-15", "turns": 500, **extra}

    def test_more_than_one_percent_unparsed_is_a_warning(self):
        found = self.judge(self.row(lines_seen=1000, lines_parsed=980))
        self.assertEqual([(f.key, f.severity) for f in found], [("parse", "warn")])
        self.assertIn("20 of 1000", found[0].symptom)

    def test_exactly_one_percent_is_quiet(self):
        self.assertEqual(self.judge(self.row(lines_seen=1000, lines_parsed=990)), [])

    def test_a_row_that_predates_the_counts_is_quiet(self):
        self.assertEqual(self.judge(self.row()), [])

    def test_an_empty_window_is_a_warning(self):
        found = self.judge(self.row(turns=0, window_empty=True, lines_seen=0, lines_parsed=0))
        self.assertEqual([(f.key, f.severity) for f in found], [("no-turns", "warn")])

    def test_only_the_latest_row_is_judged(self):
        old = dict(self.row(lines_seen=1000, lines_parsed=500), date="2026-07-01")
        self.assertEqual(self.judge(old, self.row(lines_seen=1000, lines_parsed=1000)), [])
