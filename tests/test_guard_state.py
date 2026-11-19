from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import guard_state  # noqa: E402


class LoadGuardStateTests(unittest.TestCase):
    def test_table(self):
        cases = [
            ("valid object", b'{"a": 1, "b": 2.5}', True, {"a": 1, "b": 2.5}, False),
            ("empty object", b"{}", True, {}, False),
            ("garbage bytes", b"\xff\xfe\x00garbage", True, {}, True),
            ("json list", b"[1, 2]", True, {}, True),
            ("json string", b'"text"', True, {}, True),
            ("string stamp", b'{"a": "12"}', True, {}, True),
            ("true stamp", b'{"a": true}', True, {}, True),
            ("NaN stamp", b'{"a": NaN}', True, {}, True),
            ("Infinity stamp", b'{"a": Infinity}', True, {}, True),
            ("nested stamp", b'{"a": {"b": 1}}', True, {}, True),
            ("integer too big for a float", b'{"a": 1' + b"0" * 400 + b"}", True, {}, True),
            ("nested value allowed when not numeric", b'{"a": {"b": 1}}', False,
             {"a": {"b": 1}}, False),
        ]
        for name, raw, numeric, want_data, want_bad in cases:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "state.json"
                path.write_bytes(raw)
                data, reason = guard_state.load_guard_state(path, numeric_values=numeric)
                self.assertEqual(data, want_data)
                self.assertEqual(bool(reason), want_bad)

    def test_missing_file_is_usable_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(guard_state.load_guard_state(Path(tmp) / "nope.json", True), ({}, ""))

    def test_unreadable_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, reason = guard_state.load_guard_state(Path(tmp), True)
        self.assertEqual(data, {})
        self.assertTrue(reason)


class MoveAsideTests(unittest.TestCase):
    def test_keeps_name_prefix_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heal-stamps.json"
            path.write_bytes(b"junk")
            target = guard_state.move_aside(path, "2026-01-01T00:00:00Z")
            self.assertFalse(path.exists())
            self.assertTrue(target.name.startswith("heal-stamps.json."))
            self.assertEqual(target.read_bytes(), b"junk")

    def test_never_overwrites_an_earlier_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "holds.json"
            path.write_bytes(b"one")
            first = guard_state.move_aside(path, "S")
            path.write_bytes(b"two")
            second = guard_state.move_aside(path, "S")
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"one")

    def test_failed_move_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(guard_state.move_aside(Path(tmp) / "absent.json", "S"))


if __name__ == "__main__":
    unittest.main()
