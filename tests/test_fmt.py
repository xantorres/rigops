from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import fmt  # noqa: E402


class SparklineTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(fmt.sparkline([]), "")

    def test_flat_series(self):
        self.assertEqual(fmt.sparkline([5, 5, 5]), "▁▁▁")

    def test_none_holes(self):
        self.assertEqual(fmt.sparkline([1, None, 3]), "▁·█")

    def test_monotonic(self):
        result = fmt.sparkline([0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(result, "▁▂▃▄▅▆▇█")


class HumanBytesTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(fmt.human_bytes(0), "0 B")
        self.assertEqual(fmt.human_bytes(512), "512 B")

    def test_kb_boundary(self):
        self.assertEqual(fmt.human_bytes(1024), "1.0 KB")
        self.assertEqual(fmt.human_bytes(1536), "1.5 KB")

    def test_mb_boundary(self):
        self.assertEqual(fmt.human_bytes(1024 * 1024), "1.0 MB")
        self.assertEqual(fmt.human_bytes(int(1.5 * 1024 * 1024)), "1.5 MB")


class TableTests(unittest.TestCase):
    def test_alignment(self):
        rows = [["a", "1"], ["bbbb", "22"]]
        headers = ["name", "count"]
        result = fmt.table(rows, headers)
        lines = result.splitlines()
        self.assertEqual(lines[0], "name  count")
        self.assertEqual(lines[1], "----  -----")
        self.assertEqual(lines[2], "a".ljust(4) + "  " + "1")
        self.assertEqual(lines[3], "bbbb  22")


if __name__ == "__main__":
    unittest.main()
