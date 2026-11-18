from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import state  # noqa: E402


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
            self.assertEqual(rows, [{"a": 1}, {"b": 2}])

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


if __name__ == "__main__":
    unittest.main()
