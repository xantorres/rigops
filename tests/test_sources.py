from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops.sources import ccusage, command, file_json, rtk  # noqa: E402


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


# (name, payload as run_json would return it, expected window() result)
RTK_CASES = [
    (
        "real_shape",
        {"summary": {
            "total_commands": 10, "total_input": 1000, "total_output": 200,
            "total_saved": 500, "avg_savings_pct": 33.333, "total_time_ms": 10, "avg_time_ms": 1,
        }},
        {"commands": 10, "saved_tokens": 500, "saved_pct": 33.3, "snapshot": True},
    ),
    (
        "flat_legacy_shape",
        {"total_commands": 5, "total_saved": 20, "avg_savings_pct": 10},
        {"commands": 5, "saved_tokens": 20, "saved_pct": 10, "snapshot": True},
    ),
    ("missing_total_saved", {"summary": {"total_commands": 1, "avg_savings_pct": 1}}, None),
    ("payload_list", [1, 2, 3], None),
    ("payload_none", None, None),
]


class RtkWindowTests(unittest.TestCase):
    def test_extraction_table(self):
        for name, payload, expected in RTK_CASES:
            with self.subTest(name):
                with mock.patch.object(rtk, "run_json", return_value=payload):
                    self.assertEqual(rtk.window(), expected)


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


class ReadJsonTests(unittest.TestCase):
    def test_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.json"
            p.write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(file_json.read_json(p), {"a": 1})

    def test_missing(self):
        self.assertIsNone(file_json.read_json("/no/such/rigops-fixture.json"))

    def test_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.json"
            p.write_text("not json", encoding="utf-8")
            self.assertIsNone(file_json.read_json(p))


if __name__ == "__main__":
    unittest.main()
