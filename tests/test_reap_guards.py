from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import reap_guards  # noqa: E402

LIST_CASES = [
    ("list_of_strings", ["a", "b"], None),
    ("empty_list", [], None),
    ("string", "~/projects", "bad"),
    ("none", None, "bad"),
    ("dict", {"a": "b"}, "bad"),
    ("number", 3, "bad"),
    ("empty_string_item", ["a", ""], "bad"),
    ("blank_string_item", ["a", "  "], "bad"),
    ("number_item", ["a", 1], "bad"),
    ("nested_list_item", ["a", ["b"]], "bad"),
]

class ListSettingErrorTests(unittest.TestCase):
    def test_table(self):
        for name, value, expected in LIST_CASES:
            with self.subTest(name):
                got = reap_guards.list_setting_error("reaper.roots", value)
                if expected is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual(
                        got, "reaper.roots in config must be a list of non-empty strings")


if __name__ == "__main__":
    unittest.main()
