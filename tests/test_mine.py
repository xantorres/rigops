from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from rigops import mine

from tests.support import RIGOPS, es_detector

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)


def _stamp(days_ago: int) -> str:
    return (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _user(session: str, text, days_ago: int = 1, as_list: bool = False) -> dict:
    content = [{"type": "text", "text": text}] if as_list else text
    return {
        "type": "user",
        "timestamp": _stamp(days_ago),
        "sessionId": session,
        "message": {"role": "user", "content": content},
    }


def _write(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


_ES = es_detector()


def _excluded(text):
    return _ES.classify(text) == "excluded"


class ExcludePatternTests(unittest.TestCase):
    def test_statements_are_not_corrections(self):
        for text in ("no se como hacerlo", "No hay nada de eso", "no tengo tiempo",
                     "no puedo abrirlo"):
            with self.subTest(text=text):
                self.assertTrue(_excluded(text))

    def test_real_corrections_survive(self):
        for text in ("no, es otra cosa", "no pero usa la otra", "no digas eso", "nope, wrong file",
                     "no mucho mas breve", "no es lo que pedi, vale"):
            with self.subTest(text=text):
                self.assertFalse(_excluded(text))

    def test_only_first_nonblank_line_counts(self):
        self.assertTrue(_excluded("\n\nno se que hacer\nno, otra cosa"))


class CollectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "projects"

    def tearDown(self):
        self._tmp.cleanup()

    def test_counts_detected_filtered_and_kept_inside_the_window(self):
        f = self.root / "-work-alpha" / "s1.jsonl"
        _write(
            f,
            _user("s1", "no, use the staging flag"),
            _user("s1", "no se como hacerlo"),
            _user("s1", "looks good, continue"),
            _user("s1", "<system-reminder>no, ignore</system-reminder>"),
            _user("s1", "no, too old", days_ago=200),
            _user("s1", "no, list form works", as_list=True),
        )
        _write(self.root / "-work-alpha" / "s1" / "subagents" / "a.jsonl",
               _user("s1", "no, subagent noise"))
        harvest = mine.collect(NOW - dt.timedelta(days=90), self.root, detector=_ES)

        self.assertEqual(harvest.files, 1)
        self.assertEqual(harvest.prompts, 4)
        self.assertEqual(harvest.detected, 3)
        self.assertEqual(len(harvest.filtered), 1)
        self.assertEqual(sorted(c.text for c in harvest.kept),
                         ["no, list form works", "no, use the staging flag"])
        self.assertEqual({c.session for c in harvest.kept}, {"s1"})

    def test_text_is_first_line_capped(self):
        f = self.root / "-work-alpha" / "s1.jsonl"
        _write(f, _user("s1", "no, " + "x" * 1200 + "\nsecond line"))
        kept = mine.collect(None, self.root).kept
        self.assertEqual(len(kept[0].text), mine.KEEP_TEXT)
        self.assertNotIn("second", kept[0].text)

    def test_unreadable_lines_are_skipped(self):
        f = self.root / "-work-alpha" / "s1.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text("not json\n" + json.dumps(_user("s1", "no, real one")) + "\n")
        self.assertEqual(len(mine.collect(None, self.root).kept), 1)


class GroupTests(unittest.TestCase):
    def _c(self, session: str, text: str, project: str = "-work-alpha") -> mine.Correction:
        return mine.Correction(session=session, project=project, ts=NOW, text=text)

    def test_similar_text_across_two_sessions_forms_a_group(self):
        items = [
            self._c("s1", "no, always run the lint step before opening the pull request"),
            self._c("s2", "no, run the lint step before you open a pull request"),
            self._c("s3", "no, rename the database column to created_at"),
        ]
        groups = mine.group(items)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sessions, 2)

    def test_same_session_repeated_does_not_qualify(self):
        items = [
            self._c("s1", "no, always run the lint step before opening the pull request"),
            self._c("s1", "no, run the lint step before you open a pull request"),
        ]
        self.assertEqual(mine.group(items), [])

    def test_too_short_to_compare_is_ignored(self):
        items = [self._c("s1", "no tio"), self._c("s2", "no tio")]
        self.assertEqual(mine.group(items), [])

    def test_representative_is_the_most_central_member(self):
        items = [
            self._c("s1", "no, run the lint step before opening the pull request today"),
            self._c("s2", "no, run the lint step before you open a pull request"),
            self._c("s3", "no, run the lint step before opening a pull request"),
        ]
        rep = mine.group(items)[0].representative
        self.assertEqual(rep.session, "s3")


class RedactTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_generic_secrets_and_identifiers_are_masked(self):
        text = ("mail dev@example.com see https://example.com/x?t=1 in /Users/jane "
                "ticket ABC-1234 key sk-abcdefghijklmnopqrstuv end")
        out = mine.redact(text)
        for leaked in ("dev@example.com", "https://example.com", "jane", "ABC-1234",
                       "sk-abcdefghijklmnopqrstuv"):
            self.assertNotIn(leaked, out)
        self.assertIn("mail", out)

    def test_pattern_file_rules_apply_with_flags(self):
        rules = self.tmp / "rules.txt"
        rules.write_text("# c\n\ni acme[a-z]*\ns CaseOnly\ni (broken\n")
        out = mine.redact("Acmecorp and CaseOnly and caseonly", pattern_files=[rules])
        self.assertNotIn("Acmecorp", out)
        self.assertNotIn("CaseOnly", out)
        self.assertIn("caseonly", out)


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_naive_timestamp_and_bad_session_id_do_not_crash(self):
        row = _user("s1", "no, use the other flag")
        row["timestamp"] = "2026-11-01T10:00:00"
        row["sessionId"] = ["x"]
        _write(self.tmp / "tr" / "-p" / "a.jsonl", row)
        self.assertEqual(len(mine.collect(None, self.tmp / "tr").kept), 1)


class CliGuardTests(unittest.TestCase):
    def test_registry_flag_is_rejected(self):
        proc = subprocess.run(
            [*RIGOPS, "mine", "--dry-run", "--registry", "/nonexistent/roots.json"],
            capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unrecognized arguments", proc.stderr)

    def test_absurd_since_is_an_error(self):
        proc = subprocess.run([*RIGOPS, "mine", "--dry-run", "--since", "99999999999d"],
                              capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(proc.returncode, 2)


class SinceTests(unittest.TestCase):
    def test_days(self):
        self.assertEqual(mine.parse_since("90d", NOW), NOW - dt.timedelta(days=90))
        self.assertEqual(mine.parse_since("-7d", NOW), NOW - dt.timedelta(days=7))

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            mine.parse_since("soon", NOW)


class CliTests(unittest.TestCase):
    def _run(self, *args, env=None):
        return subprocess.run([*RIGOPS, "mine", *args], capture_output=True, text=True,
                              check=False, timeout=60, env=env)

    def test_without_dry_run_is_a_clear_error(self):
        proc = self._run("--since", "90d")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--dry-run", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_dry_run_needs_no_registry_index_or_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "tr").mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("RIGOPS_")}
            env["HOME"] = str(tmp / "home")
            proc = self._run("--dry-run", "--transcripts-dir", str(tmp / "tr"), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("# rigops mine --dry-run --since 90d"))

    def test_dry_run_prints_header_and_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tr = tmp / "tr"
            today = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            for sid, text in (("s1", "no, always pin the dependency version in the manifest"),
                              ("s2", "no, pin the dependency version in the manifest always")):
                row = _user(sid, text)
                row["timestamp"] = today
                _write(tr / "-proj" / f"{sid}.jsonl", row)
            env = dict(os.environ, HOME=str(tmp / "home"), RIGOPS_CONFIG=str(tmp / "c.json"))
            proc = self._run("--dry-run", "--since", "90d", "--transcripts-dir", str(tr),
                             env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("transcripts walked: 2", proc.stdout)
        self.assertIn("groups: 1", proc.stdout)
        self.assertIn("sessions: 2", proc.stdout)
        self.assertIn("transcripts walked: 2", proc.stdout.splitlines()[2])
        self.assertTrue(proc.stdout.startswith("# rigops mine --dry-run --since 90d"))
        for gone in ("realm:", "memory dedupe", "nearest memory", "already in memory"):
            self.assertNotIn(gone, proc.stdout)


if __name__ == "__main__":
    unittest.main()
