from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import config as rigops_config  # noqa: E402
from rigops import guard_state, hook, state  # noqa: E402
from rigops.doctor import check_levers  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RIGOPS = REPO_ROOT / "bin" / "rigops"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "state-0.10"
NEWER = '{"version": 99, "date": "2026-01-25", "ts": "2026-01-25T00:00:00+00:00"}\n'
TORN = '{"date": "2026-01-2\n'


class Sandbox(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.state = self.home / "xdg-state" / "rigops"
        self.config = self.home / "config.json"
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "RIGOPS_STATE_DIR": str(self.state),
            "RIGOPS_CONFIG": str(self.config),
            "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
            "XDG_STATE_HOME": str(self.home / "xdg-state"),
        }
        self.config.write_text("{}")

    def seed(self) -> None:
        shutil.copytree(FIXTURE, self.state)

    def spawn(self, args) -> subprocess.Popen:
        return subprocess.Popen(
            [str(RIGOPS), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=self.env, stdin=subprocess.DEVNULL,
        )

    def run_cmd(self, args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(RIGOPS), *args], capture_output=True, text=True, check=False, timeout=90,
            env=self.env, stdin=subprocess.DEVNULL,
        )

    def json_of(self, args) -> dict:
        proc = self.run_cmd(args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def append_raw(self, name: str, text: str) -> None:
        with (self.state / name).open("a") as fh:
            fh.write(text)


class ZeroTenFixtureTests(Sandbox):
    def test_a_0_10_state_dir_reads_through_every_reader_with_no_skipped_rows(self):
        self.seed()
        diff = self.json_of(["ledger", "diff", "--json"])
        self.assertEqual(diff["skipped"], 0)
        self.assertFalse(diff["incomplete"])
        shown = self.json_of(["ledger", "write", "--show", "--json"])
        self.assertEqual((len(shown["rows"]), shown["skipped"]), (3, 0))
        history = self.json_of(["tax", "--history", "--json"])
        self.assertEqual((len(history["rows"]), history["skipped"]), (3, 0))
        self.assertEqual(len(self.json_of(["eval", "list", "--json"])["rows"]), 2)
        self.assertEqual(self.run_cmd(["eval", "diff"]).returncode, 0)
        self.assertEqual(self.run_cmd(["hook", "session-start"]).returncode, 0)
        self.assertEqual(
            self.run_cmd(["doctor", "--no-config", "--supervisor", "none"]).returncode, 0)

    def test_the_0_10_levers_and_guard_files_read_clean(self):
        self.seed()
        previous = {k: os.environ.get(k) for k in ("RIGOPS_STATE_DIR", "RIGOPS_CONFIG")}
        os.environ["RIGOPS_STATE_DIR"] = str(self.state)
        os.environ["RIGOPS_CONFIG"] = str(self.config)
        self.addCleanup(lambda: [
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
            for k, v in previous.items()])
        found = check_levers.run(rigops_config.load(), today=date(2026, 1, 19))
        self.assertEqual([f for f in found if "skipped" in f.symptom], [])
        doctor_dir = self.state / "doctor"
        for name, numeric in (("heal-stamps.json", True), ("holds.json", False),
                              ("notified.json", False), ("kicked.json", False)):
            with self.subTest(name):
                data, reason = guard_state.load_guard_state(doctor_dir / name, numeric)
                self.assertEqual((bool(data), reason), (True, ""))


class ConcurrentWriteTests(Sandbox):
    def test_two_processes_writing_50_note_rows_each_yield_100_valid_rows(self):
        def worker(tag: str):
            for i in range(50):
                proc = self.run_cmd(["ledger", "note", f"{tag}-{i}"])
                self.assertEqual(proc.returncode, 0, proc.stderr)

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rows = state.read_jsonl(self.state / "interventions.jsonl")
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({r["text"] for r in rows}), 100)

    def test_concurrent_forced_writes_and_appends_to_one_file_lose_no_row(self):
        self.seed()
        (self.home / ".claude" / "projects").mkdir(parents=True)
        dates = ["2026-01-04", "2026-01-11", "2026-02-01", "2026-02-08", "2026-02-15"]
        procs = [self.spawn(["ledger", "write", "--at", d, "--force", "--json"]) for d in dates]
        for proc in procs:
            _, err = proc.communicate(timeout=90)
            self.assertEqual(proc.returncode, 0, err)
        text = (self.state / "ledger.jsonl").read_text()
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(sorted(r["date"] for r in rows), sorted({*dates, "2026-01-18"}))
        self.assertIn("2026-02-15", (self.state / "ledger.md").read_text())

    def test_a_forced_ledger_write_racing_notes_loses_nothing(self):
        self.seed()
        (self.home / ".claude" / "projects").mkdir(parents=True)
        notes_before = len(state.read_jsonl(self.state / "interventions.jsonl"))
        dates = ["2026-01-04", "2026-01-11", "2026-02-01"]
        writers = [self.spawn(["ledger", "write", "--at", d, "--force", "--json"]) for d in dates]
        for i in range(20):
            proc = self.run_cmd(["ledger", "note", f"raced-{i}"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
        for proc in writers:
            _, err = proc.communicate(timeout=90)
            self.assertEqual(proc.returncode, 0, err)
        ledger = state.read_jsonl(self.state / "ledger.jsonl")
        self.assertEqual(sorted(r["date"] for r in ledger), sorted({*dates, "2026-01-18"}))
        notes = state.read_jsonl(self.state / "interventions.jsonl")
        self.assertEqual(len(notes), notes_before + 20)
        self.assertEqual(len({r["text"] for r in notes if r["text"].startswith("raced-")}), 20)

    def test_many_processes_appending_to_one_file_leave_every_row_whole(self):
        path = self.state / "rows.jsonl"
        code = (
            "import sys; sys.path.insert(0, sys.argv[1] + '/lib'); from rigops import state;"
            "[state.append_jsonl(sys.argv[2], {'who': sys.argv[3], 'i': i, 'pad': 'x' * 3000})"
            " for i in range(100)]"
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", code, str(REPO_ROOT), str(path), str(n)],
                             env=self.env)
            for n in range(4)
        ]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=60), 0)
        self.assertEqual(len(state.read_jsonl(path)), 400)


class ReportVersusGateTests(Sandbox):
    BAD = {"torn row": TORN, "newer-version row": NEWER}

    def seeded_with(self, bad: str) -> None:
        self.seed()
        for name in ("ledger.jsonl", "eval.jsonl"):
            self.append_raw(name, bad)

    def test_eval_diff_exits_2_on_a_torn_or_newer_row(self):
        for name, bad in self.BAD.items():
            with self.subTest(name):
                shutil.rmtree(self.state, ignore_errors=True)
                self.seeded_with(bad)
                proc = self.run_cmd(["eval", "diff"])
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("error:", proc.stderr)

    def test_ledger_diff_exits_0_with_the_skipped_count_and_an_incomplete_mark(self):
        for name, bad in self.BAD.items():
            with self.subTest(name):
                shutil.rmtree(self.state, ignore_errors=True)
                self.seeded_with(bad)
                payload = self.json_of(["ledger", "diff", "--json"])
                self.assertEqual(payload["skipped"], 1)
                self.assertTrue(payload["incomplete"])
                text = self.run_cmd(["ledger", "diff"])
                self.assertEqual(text.returncode, 0)
                self.assertIn("incomplete", text.stdout)
                self.assertIn("1", text.stderr)
                self.assertIn("skipped", text.stderr)

    def test_the_other_reports_exit_0_and_count_what_they_skipped(self):
        self.seeded_with(TORN)
        shown = self.json_of(["ledger", "write", "--show", "--json"])
        self.assertEqual((len(shown["rows"]), shown["skipped"]), (3, 1))
        history = self.json_of(["tax", "--history", "--json"])
        self.assertEqual((len(history["rows"]), history["skipped"]), (3, 1))
        err = self.run_cmd(["tax", "--history"]).stderr
        self.assertIn("skipped 1", err)

    def test_check_levers_warns_with_the_skipped_count(self):
        for name, bad in self.BAD.items():
            with self.subTest(name):
                shutil.rmtree(self.state, ignore_errors=True)
                self.seeded_with(bad)
                previous = os.environ.get("RIGOPS_STATE_DIR")
                os.environ["RIGOPS_STATE_DIR"] = str(self.state)
                try:
                    found = check_levers.run(
                        rigops_config.load(), today=date(2026, 1, 19))
                finally:
                    if previous is None:
                        os.environ.pop("RIGOPS_STATE_DIR")
                    else:
                        os.environ["RIGOPS_STATE_DIR"] = previous
                (warning,) = [f for f in found if f.key == "skipped"]
                self.assertEqual(warning.severity, "warn")
                self.assertIn("1 ledger row", warning.symptom)

    def test_writers_stay_strict(self):
        self.seeded_with(TORN)
        self.assertEqual(self.run_cmd(["ledger", "write", "--dry-run"]).returncode, 2)

    def test_a_row_that_is_not_an_object_is_malformed_for_a_strict_reader(self):
        for bad in ("5\n", "[1]\n", '"text"\n'):
            with self.subTest(bad):
                shutil.rmtree(self.state, ignore_errors=True)
                self.seeded_with(bad)
                with self.assertRaises(state.MalformedJsonl):
                    state.read_jsonl(self.state / "ledger.jsonl")
                proc = self.run_cmd(["ledger", "write", "--dry-run"])
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_an_unreadable_state_file_exits_2_for_reports_and_gates(self):
        self.seed()
        for name in ("ledger.jsonl", "eval.jsonl"):
            (self.state / name).unlink()
            (self.state / name).mkdir()
        for args in (["ledger", "diff"], ["ledger", "write", "--show", "--json"],
                     ["tax", "--history"], ["eval", "diff"], ["eval", "list"]):
            with self.subTest(args):
                proc = self.run_cmd(args)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertNotIn("Traceback", proc.stderr)


class LockedRunTests(Sandbox):
    def assert_waits_for(self, lock_target: Path, args) -> None:
        with state.locked(lock_target):
            proc = self.spawn(args)
            time.sleep(1.5)
            self.assertIsNone(proc.poll(), "ran while the lock was held")
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err)

    def test_a_doctor_run_waits_for_the_doctor_lock(self):
        self.state.mkdir(parents=True)
        self.assert_waits_for(
            self.state / "doctor" / "doctor",
            ["doctor", "--no-config", "--supervisor", "none"])

    def test_reap_apply_waits_for_the_reap_lock(self):
        self.state.mkdir(parents=True)
        root = self.home / "root"
        root.mkdir()
        self.assert_waits_for(
            self.state / "reap", ["reap", "--apply", "--no-fetch", "--root", str(root)])

    def test_reap_apply_refuses_when_a_session_file_is_corrupt(self):
        sessions = self.home / "sessions"
        sessions.mkdir()
        (sessions / "123.json").write_text("{not json")
        root = self.home / "root"
        root.mkdir()
        self.config.write_text(json.dumps({"reaper": {"sessions_dir": str(sessions)}}))
        proc = self.run_cmd(["reap", "--apply", "--no-fetch", "--root", str(root)])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("123.json", proc.stderr)
        dry = self.run_cmd(["reap", "--no-fetch", "--root", str(root)])
        self.assertEqual(dry.returncode, 0, dry.stderr)


class WrittenStateIsVersionedAndAtomicTests(Sandbox):
    def test_the_nudge_state_goes_through_atomic_write_with_unchanged_content(self):
        transcript = self.home / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 300000}}}) + "\n")
        calls = []
        real = state.atomic_write

        def spy(path, text):
            calls.append((path.name, text))
            real(path, text)

        payload = json.dumps({"transcript_path": str(transcript), "session_id": "s1"})
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(state, "atomic_write", side_effect=spy), \
                mock.patch("sys.stdin", io.StringIO(payload)):
            self.assertIn("ctx-nudge", hook._user_prompt_submit())
        self.assertEqual(calls, [("s1", "300000\n")])

    def test_interventions_and_doctor_events_carry_a_version(self):
        self.assertEqual(self.run_cmd(["ledger", "note", "x"]).returncode, 0)
        (row,) = state.read_jsonl(self.state / "interventions.jsonl")
        self.assertEqual(row["version"], 1)
        self.assertEqual(
            self.run_cmd(["doctor", "--no-config", "--supervisor", "none"]).returncode, 0)
        events = state.read_jsonl(self.state / "doctor" / "events.jsonl")
        self.assertTrue(events and all(e["version"] == 1 for e in events))

    def test_doctor_stamp_files_are_written_wrapped_and_the_old_flat_shape_still_reads(self):
        doctor_dir = self.state / "doctor"
        doctor_dir.mkdir(parents=True)
        (doctor_dir / "kicked.json").write_text(json.dumps({"job-a": 1.0}))
        cmd = ["doctor", "--no-config", "--supervisor", "none"]
        self.config.write_text(json.dumps({"doctor": {"notify_command": "true"}}))
        self.assertEqual(self.run_cmd(cmd).returncode, 0)
        written = json.loads((doctor_dir / "kicked.json").read_text())
        self.assertEqual(set(written), {"version", "entries"})
        self.assertEqual(guard_state.load_guard_state(doctor_dir / "kicked.json"),
                         (written["entries"], ""))

    def test_a_newer_stamp_file_fails_closed_with_exit_2(self):
        doctor_dir = self.state / "doctor"
        doctor_dir.mkdir(parents=True)
        (doctor_dir / "heal-stamps.json").write_text(
            json.dumps({"version": 99, "entries": {}}))
        proc = self.run_cmd(["doctor", "--no-config", "--supervisor", "none"])
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_the_state_dir_is_created_private(self):
        self.assertEqual(self.run_cmd(["ledger", "note", "x"]).returncode, 0)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_a_wider_state_dir_is_tightened_when_files_are_written(self):
        self.state.mkdir(parents=True)
        self.state.chmod(0o755)
        self.assertEqual(self.run_cmd(["ledger", "note", "x"]).returncode, 0)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
