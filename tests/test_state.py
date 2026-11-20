from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import state  # noqa: E402
from rigops.errors import RigopsError  # noqa: E402


class StateDirTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_rigops_state_dir_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom-state"
            os.environ["RIGOPS_STATE_DIR"] = str(target)
            os.environ.pop("XDG_STATE_HOME", None)
            result = state.state_dir()
            self.assertEqual(result, target)
            self.assertTrue(result.is_dir())

    def test_xdg_state_home_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("RIGOPS_STATE_DIR", None)
            os.environ["XDG_STATE_HOME"] = tmp
            result = state.state_dir()
            self.assertEqual(result, Path(tmp) / "rigops")
            self.assertTrue(result.is_dir())


def _second_writer_lands_mid_write(first, second):
    """Run `first`, letting `second` write the same file between first's
    temp write and its swap into place, as a second concurrent run would."""
    real_replace = os.replace
    landed = []

    def replace(src, dst):
        if not landed:
            landed.append(True)
            second()
        return real_replace(src, dst)

    with mock.patch("os.replace", side_effect=replace):
        first()


class AtomicWriteTests(unittest.TestCase):
    def test_two_concurrent_writers_both_finish_and_leave_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"
            _second_writer_lands_mid_write(
                lambda: state.atomic_write(path, "first\n"),
                lambda: state.atomic_write(path, "second\n"),
            )
            self.assertEqual(path.read_text(), "first\n")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["out.txt"])

    def test_existing_mode_survives_and_a_new_file_follows_the_umask(self):
        previous = os.umask(0o027)
        self.addCleanup(os.umask, previous)
        with tempfile.TemporaryDirectory() as tmp:
            kept, fresh = Path(tmp) / "kept.txt", Path(tmp) / "sub" / "fresh.txt"
            kept.write_text("old")
            os.chmod(kept, 0o604)
            state.atomic_write(kept, "new")
            state.atomic_write(fresh, "new")
            self.assertEqual(oct(kept.stat().st_mode & 0o777), "0o604")
            self.assertEqual(oct(fresh.stat().st_mode & 0o777), "0o640")
            self.assertEqual(kept.read_text(), "new")

    def test_a_failed_write_leaves_the_file_and_no_temp_file(self):
        for step in ("os.chmod", "os.replace"):
            with self.subTest(step), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "out.txt"
                path.write_text("old")
                with mock.patch(step, side_effect=OSError("disk full")), \
                        self.assertRaises(OSError):
                    state.atomic_write(path, "new")
                self.assertEqual(path.read_text(), "old")
                self.assertEqual([p.name for p in Path(tmp).iterdir()], ["out.txt"])


class JsonlTests(unittest.TestCase):
    def test_append_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "ledger.jsonl"
            state.append_jsonl(path, {"a": 1})
            state.append_jsonl(path, {"b": 2})
            rows = state.read_jsonl(path)
            self.assertEqual(rows, [{"version": 1, "a": 1}, {"version": 1, "b": 2}])

    def test_missing_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.jsonl"
            self.assertEqual(state.read_jsonl(path), [])

    def test_blank_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text('{"a": 1}\n\n   \n{"b": 2}\n')
            self.assertEqual(state.read_jsonl(path), [{"a": 1}, {"b": 2}])

    def test_malformed_line_raises_value_error_with_lineno(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text('{"a": 1}\nnot json\n')
            with self.assertRaises(ValueError) as ctx:
                state.read_jsonl(path)
            message = str(ctx.exception)
            self.assertIn(f"malformed JSON line in {path}:2", message)


class VersionedRowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "rows.jsonl"

    def test_append_stamps_a_version_and_keeps_an_explicit_one(self):
        state.append_jsonl(self.path, {"a": 1})
        state.append_jsonl(self.path, {"a": 2, "version": 1})
        self.assertEqual([r["version"] for r in state.read_jsonl(self.path)], [1, 1])

    def test_a_row_without_a_version_reads_as_the_old_shape(self):
        self.path.write_text('{"a": 1}\n')
        self.assertEqual(state.read_jsonl(self.path), [{"a": 1}])

    def test_strict_read_raises_on_a_newer_version(self):
        self.path.write_text('{"a": 1}\n{"version": 99, "a": 2}\n')
        with self.assertRaises(state.MalformedJsonl) as ctx:
            state.read_jsonl(self.path)
        self.assertIn(f"{self.path}:2", str(ctx.exception))
        self.assertIn("newer", str(ctx.exception))

    def test_read_rows_skips_and_counts_torn_and_newer_rows_and_sorts(self):
        self.path.write_text(
            '{"date": "2026-02-01"}\n{torn\n{"version": 99, "date": "2026-03-01"}\n'
            '{"date": "2026-01-01"}\n')
        rows, skipped = state.read_rows(self.path)
        self.assertEqual([r["date"] for r in rows], ["2026-01-01", "2026-02-01"])
        self.assertEqual(skipped, 2)

    def test_read_rows_counts_rows_the_caller_rejects(self):
        self.path.write_text('{"date": "x"}\n{"date": "2026-01-01"}\n')
        rows, skipped = state.read_rows(self.path, valid=lambda r: r["date"] != "x")
        self.assertEqual((len(rows), skipped), (1, 1))

    def test_read_rows_on_a_missing_file_is_empty(self):
        self.assertEqual(state.read_rows(self.path), ([], 0))

    def test_an_unreadable_path_raises_in_both_readers(self):
        self.path.mkdir()
        for reader in (state.read_jsonl, state.read_rows):
            with self.subTest(reader.__name__), self.assertRaises(RigopsError):
                reader(self.path)


class AppendTests(unittest.TestCase):
    def test_a_row_over_the_cap_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            with self.assertRaises(RigopsError):
                state.append_jsonl(path, {"blob": "x" * state.MAX_ROW_BYTES})
            self.assertFalse(path.exists() and path.read_text())

    def test_a_row_is_one_write_on_an_append_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            real_write = os.write
            calls = []

            def spy(fd, data):
                calls.append(data)
                return real_write(fd, data)

            with mock.patch("os.write", side_effect=spy):
                state.append_jsonl(path, {"a": 1})
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0].endswith(b"\n"))


class LockTests(unittest.TestCase):
    def test_locked_serialises_two_holders(self):
        import threading
        import time

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.jsonl"
            order = []

            def hold():
                with state.locked(target):
                    order.append("first-in")
                    time.sleep(0.3)
                    order.append("first-out")

            thread = threading.Thread(target=hold)
            thread.start()
            time.sleep(0.1)
            with state.locked(target):
                order.append("second-in")
            thread.join()
            self.assertEqual(order, ["first-in", "first-out", "second-in"])


class PrivateDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patch = mock.patch.dict(os.environ, {"RIGOPS_STATE_DIR": str(self.root / "state")})
        patch.start()
        self.addCleanup(patch.stop)

    def mode(self, path) -> int:
        return path.stat().st_mode & 0o777

    def test_a_new_state_dir_is_0700(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        self.assertEqual(self.mode(state.state_dir()), 0o700)

    def test_writing_into_a_wider_state_dir_tightens_it(self):
        target = self.root / "state"
        target.mkdir(mode=0o755)
        os.chmod(target, 0o755)
        state.append_jsonl(target / "rows.jsonl", {"a": 1})
        self.assertEqual(self.mode(target), 0o700)

    def test_a_subdir_made_by_a_write_is_0700(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        state.atomic_write(state.state_dir() / "doctor" / "x.json", "{}")
        self.assertEqual(self.mode(self.root / "state" / "doctor"), 0o700)

    def test_a_directory_outside_the_state_dir_keeps_its_mode(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        os.chmod(outside, 0o755)
        state.atomic_write(outside / "x.txt", "x")
        self.assertEqual(self.mode(outside), 0o755)

    def test_a_directory_made_outside_the_state_dir_follows_the_umask(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        state.atomic_write(self.root / "reports" / "x.txt", "x")
        self.assertEqual(self.mode(self.root / "reports"), 0o755)

    def test_the_xdg_state_dir_is_tightened_when_the_override_points_elsewhere(self):
        xdg = self.root / "xdg"
        (xdg / "rigops").mkdir(parents=True)
        os.chmod(xdg / "rigops", 0o755)
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
            state.atomic_write(xdg / "rigops" / "doctor" / "gates.json", "{}")
        self.assertEqual(self.mode(xdg / "rigops"), 0o700)
        self.assertEqual(self.mode(xdg / "rigops" / "doctor"), 0o700)
        self.assertEqual(self.mode(xdg), 0o755)


if __name__ == "__main__":
    unittest.main()
