from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_malformed_line_raises_system_exit_with_lineno(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text('{"a": 1}\nnot json\n')
            with self.assertRaises(SystemExit) as ctx:
                state.read_jsonl(path)
            message = str(ctx.exception)
            self.assertIn(str(path), message)
            self.assertIn("2", message)


if __name__ == "__main__":
    unittest.main()
