from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops.sources import ccusage, command  # noqa: E402


class RunJsonTests(unittest.TestCase):
    def test_missing_binary_returns_none(self):
        self.assertIsNone(command.run_json(["rigops-no-such-tool-xyz"]))

    def test_nonzero_exit_returns_none(self):
        self.assertIsNone(command.run_json(["sh", "-c", "exit 3"]))

    def test_invalid_json_returns_none(self):
        self.assertIsNone(command.run_json(["sh", "-c", "echo notjson"]))

    def test_valid_json_parsed(self):
        self.assertEqual(command.run_json(["sh", "-c", 'echo \'{"a": 1}\'']), {"a": 1})

    def test_timeout_returns_none(self):
        self.assertIsNone(command.run_json(["sh", "-c", "sleep 5"], timeout_s=0.2))

    def test_string_timeout_coerced_and_parses(self):
        self.assertEqual(command.run_json(["echo", "{}"], timeout_s="20"), {})

    def test_none_timeout_bounded_no_raise(self):
        try:
            command.run_json(["echo", "{}"], timeout_s=None)
        except Exception as exc:
            self.fail(f"run_json raised with timeout_s=None: {exc}")

    def test_infinite_timeout_bounded_no_raise(self):
        # json.loads('{"timeout_s": 1e999}') yields inf; it must not reach
        # subprocess, where it would raise OverflowError.
        self.assertEqual(command.run_json(["echo", "{}"], timeout_s=float("inf")), {})

    def test_nan_timeout_bounded_no_raise(self):
        self.assertEqual(command.run_json(["echo", "{}"], timeout_s=float("nan")), {})

    def test_argv_first_element_not_string_returns_none(self):
        self.assertIsNone(command.run_json([1, "x"]))

    def test_argv_element_not_string_returns_none(self):
        self.assertIsNone(command.run_json(["echo", 1]))


CCUSAGE_CASES = [
    (
        "camelcase_totals",
        {"totals": {"totalCost": 12.5, "totalTokens": 1000}, "daily": [1, 2, 3]},
        {"cost_usd": 12.5, "tokens": 1000, "days": 3},
    ),
    ("totals_missing", {"daily": []}, None),
    ("totals_not_dict", {"totals": "oops"}, None),
    (
        "only_tokens_present",
        {"totals": {"totalTokens": 500}},
        {"cost_usd": None, "tokens": 500, "days": None},
    ),
]


class CcusageWindowTests(unittest.TestCase):
    def test_extraction_table(self):
        for name, payload, expected in CCUSAGE_CASES:
            with self.subTest(name):
                with mock.patch.object(ccusage, "run_json", return_value=payload):
                    self.assertEqual(ccusage.window(), expected)

    def test_since_until_appended_as_yyyymmdd(self):
        captured = {}

        def fake_run_json(argv, timeout_s=60):
            captured["argv"] = argv
            return {"totals": {"totalCost": 1}}

        with mock.patch.object(ccusage, "run_json", side_effect=fake_run_json):
            ccusage.window(dt.datetime(2026, 8, 1), dt.datetime(2026, 8, 8))
        self.assertEqual(captured["argv"][-4:], ["--since", "20260801", "--until", "20260808"])


class CustomAnnotationsTests(unittest.TestCase):
    def test_valid_spec_runs(self):
        specs = [{"name": "t", "argv": ["sh", "-c", 'echo \'{"x": 1}\'']}]
        self.assertEqual(command.custom_annotations(specs), {"t": {"x": 1}})

    def test_nameless_spec_skipped(self):
        specs = [{"argv": ["sh", "-c", "echo {}"]}]
        self.assertEqual(command.custom_annotations(specs), {})

    def test_non_list_argv_skipped(self):
        specs = [{"name": "t", "argv": "sh -c echo"}]
        self.assertEqual(command.custom_annotations(specs), {})


if __name__ == "__main__":
    unittest.main()
