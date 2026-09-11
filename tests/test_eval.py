from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops.eval import cases, checks, diff, score  # noqa: E402

SHIPPED_CASES_DIR = Path(__file__).resolve().parent.parent / "examples" / "eval" / "cases"

PRINTABLE_CASES = [
    ("escape_hex1b", "\x1b", "\\x1b"),
    ("escape_hex9b", "\x9b", "\\x9b"),
    ("escape_hex7f_del", "\x7f", "\\x7f"),
    ("lone_surrogate", "\ud800", "\\ud800"),
    ("accented_letter_kept", "é", "é"),
    ("plain_text_unchanged", "plain text", "plain text"),
    ("nbsp_kept", "10 000", "10 000"),
    ("zwj_kept", "‍", "‍"),
    ("zwnj_kept", "‌", "‌"),
]


class PrintableTests(unittest.TestCase):
    def test_table(self):
        for name, text, expected in PRINTABLE_CASES:
            with self.subTest(name):
                self.assertEqual(checks.printable(text), expected)

    def test_idempotent_on_its_own_output(self):
        for name, text, _ in PRINTABLE_CASES:
            with self.subTest(name):
                once = checks.printable(text)
                self.assertEqual(checks.printable(once), once)

    def test_escapes_line_separator_and_bidi_overrides(self):
        result = checks.printable(" ‮⁦")
        self.assertIn("\\u2028", result)
        self.assertIn("\\u202e", result)
        self.assertIn("\\u2066", result)


EVALUATE_CASES = [
    (
        "contains_all_required_pass",
        {"contains": ("alpha", "beta")},
        "Alpha and Beta are both here.",
        (True, ""),
    ),
    (
        "contains_missing_one",
        {"contains": ("alpha", "gamma")},
        "alpha only, no third letter",
        (False, 'missing "gamma"'),
    ),
    (
        "not_contains_pass",
        {"not_contains": ("banned",)},
        "this text is clean",
        (True, ""),
    ),
    (
        "not_contains_fail",
        {"not_contains": ("banned",)},
        "this text has a BANNED word",
        (False, 'forbidden "banned"'),
    ),
    (
        "regex_case_sensitive_fails_without_inline_flag",
        {"regex": ("FOO",)},
        "foo",
        (False, "no match for /FOO/"),
    ),
    (
        "regex_inline_case_flag_passes",
        {"regex": ("(?i)FOO",)},
        "foo",
        (True, ""),
    ),
    (
        "regex_no_match",
        {"regex": (r"foo\d+",)},
        "bar",
        (False, r"no match for /foo\d+/"),
    ),
    (
        "not_regex_forbidden_match",
        {"not_regex": (r"bad\d+",)},
        "bad123",
        (False, r"forbidden match /bad\d+/"),
    ),
    (
        "not_regex_pass",
        {"not_regex": (r"bad\d+",)},
        "all good",
        (True, ""),
    ),
    (
        "regex_matches_mid_string_via_search",
        {"regex": (r"\d+",)},
        "see 42 here",
        (True, ""),
    ),
    (
        "not_regex_matches_mid_string_via_search",
        {"not_regex": (r"\d+",)},
        "see 42 here",
        (False, r"forbidden match /\d+/"),
    ),
    (
        "json_bool_vs_int_mismatch",
        {"json": {"ok": True}},
        '{"ok": 1}',
        (False, "field ok expected true, got 1"),
    ),
    (
        "json_int_equals_float",
        {"json": {"total": 1}},
        '{"total": 1.0}',
        (True, ""),
    ),
    (
        "json_nested_recursive_pass",
        {"json": {"items": [{"a": 1}, {"a": 2}]}},
        '{"items": [{"a": 1}, {"a": 2}]}',
        (True, ""),
    ),
    (
        "json_nested_recursive_mismatch",
        {"json": {"items": [{"a": 1}]}},
        '{"items": [{"a": 2}]}',
        (False, 'field items expected [{"a": 1}], got [{"a": 2}]'),
    ),
    (
        "json_missing_field",
        {"json": {"missing_field": "x"}},
        '{"other": 1}',
        (False, "field missing_field missing"),
    ),
    (
        "json_keys_exact_match_pass",
        {"json_keys": ("a", "b")},
        '{"a": 1, "b": 2}',
        (True, ""),
    ),
    (
        "json_keys_missing",
        {"json_keys": ("a", "b", "c")},
        '{"a": 1}',
        (False, "json keys missing b, c"),
    ),
    (
        "json_keys_extra",
        {"json_keys": ("a",)},
        '{"a": 1, "b": 2}',
        (False, "json keys extra b"),
    ),
    (
        "json_check_with_no_json_in_reply",
        {"json_keys": ("a",)},
        "plain text, no braces at all",
        (False, "no JSON object in reply"),
    ),
    (
        "several_failures_joined_by_semicolon",
        {"contains": ("hello",), "regex": (r"\d+",)},
        "goodbye",
        (False, r'missing "hello"; no match for /\d+/'),
    ),
    (
        "pass_returns_empty_reason",
        {"contains": ("ok",)},
        "everything is ok",
        (True, ""),
    ),
]


class EvaluateTests(unittest.TestCase):
    def test_table(self):
        for name, expect, text, expected in EVALUATE_CASES:
            with self.subTest(name):
                self.assertEqual(checks.evaluate(expect, text), expected)


class EvaluateEdgeCaseTests(unittest.TestCase):
    def test_json_keys_extra_key_with_control_char_escaped_in_reason(self):
        text = json.dumps({"a": 1, "b\x1bad": 2})
        passed, reason = checks.evaluate({"json_keys": ("a",)}, text)
        self.assertFalse(passed)
        self.assertNotIn("\x1b", reason)
        self.assertIn("\\x1b", reason)

    def test_json_field_value_with_control_char_escaped_in_reason(self):
        text = json.dumps({"secret": "leak\x9bed"})
        passed, reason = checks.evaluate({"json": {"secret": "clean"}}, text)
        self.assertFalse(passed)
        self.assertNotIn("\x9b", reason)
        self.assertIn("\\x9b", reason)

    def test_deeply_nested_unbalanced_array_does_not_raise(self):
        text = '{"a":' + "[" * 100000
        result = checks.evaluate({"json_keys": ("a",)}, text)
        self.assertEqual(result, (False, "no JSON object in reply"))


EXTRACT_JSON_CASES = [
    ("bare_object", '{"a": 1}', {"a": 1}),
    ("fenced_json_block", '```json\n{"a": 1}\n```', {"a": 1}),
    ("wrapped_in_prose", 'Sure, here you go: {"a": 1} let me know if that helps.', {"a": 1}),
    ("leading_non_json_brace_then_real_object", '{placeholder} then real {"a": 1}', {"a": 1}),
    ("two_valid_objects_returns_first", '{"a": 1} and also {"b": 2}', {"a": 1}),
    ("list_only_reply_returns_none", "[1, 2, 3]", None),
    ("no_braces_returns_none", "no json here at all", None),
    ("nan_alone_returns_none", '{"total": NaN}', None),
    ("nan_then_valid_object_returns_valid", '{"total": NaN} {"ok": 1}', {"ok": 1}),
    ("many_junk_braces_before_valid_object_bounded", "{" * 60 + '{"ok": 1}', None),
]


class ExtractJsonTests(unittest.TestCase):
    def test_table(self):
        for name, text, expected in EXTRACT_JSON_CASES:
            with self.subTest(name):
                self.assertEqual(checks.extract_json(text), expected)


SAME_CASES = [
    ("bool_vs_int_true_one", True, 1, False),
    ("int_equals_int", 1, 1, True),
    ("int_equals_float", 1, 1.0, True),
    ("bool_equal_true", True, True, True),
    ("bool_unequal", True, False, False),
    ("nested_dict_equal", {"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 2}}, True),
    ("nested_dict_value_mismatch", {"a": 1}, {"a": 2}, False),
    ("dict_keys_must_match_exactly", {"a": 1, "b": 2}, {"a": 1}, False),
    ("nested_list_equal", [1, {"a": 1}], [1, {"a": 1}], True),
    ("list_length_mismatch", [1, 2], [1], False),
    ("list_order_matters", [1, 2], [2, 1], False),
    ("string_equal", "a", "a", True),
    ("string_unequal", "a", "b", False),
    ("none_equal", None, None, True),
]


class SameTests(unittest.TestCase):
    def test_table(self):
        for name, actual, expected, result in SAME_CASES:
            with self.subTest(name):
                self.assertEqual(checks.same(actual, expected), result)


def _case(**overrides) -> dict:
    data = {"group": "g", "prompt": "hello", "expect": {"contains": ["x"]}}
    data.update(overrides)
    return data


def _missing(key: str) -> dict:
    data = _case()
    del data[key]
    return data


PARSE_ERROR_CASES = [
    ("non_object", ["not", "a", "dict"], "one JSON object"),
    ("unknown_top_level_key", _case(bogus=1), "bogus"),
    ("unknown_assertion_key", _case(expect={"nope": ["x"]}), "nope"),
    ("missing_group", _missing("group"), "group"),
    ("bad_group_blank", _case(group="   "), "group"),
    ("bad_group_type", _case(group=7), "group"),
    ("missing_prompt", _missing("prompt"), "prompt"),
    ("missing_expect", _missing("expect"), "expect"),
    ("empty_expect", _case(expect={}), "expect"),
    ("blank_prompt", _case(prompt="   "), "prompt"),
    ("bad_regex", _case(expect={"regex": ["(unclosed"]}), "does not compile"),
    ("max_tokens_zero", _case(max_tokens=0), "max_tokens"),
    ("max_tokens_bool", _case(max_tokens=True), "max_tokens"),
    ("max_tokens_float", _case(max_tokens=1.5), "max_tokens"),
    ("timeout_zero", _case(timeout_s=0), "timeout_s"),
    ("timeout_string", _case(timeout_s="5"), "timeout_s"),
    ("note_not_string", _case(note=123), "note"),
    ("timeout_over_max", _case(timeout_s=3601), "no greater than 3600"),
    ("timeout_non_finite", _case(timeout_s=1e400), "positive number"),
    ("max_tokens_huge_int", _case(max_tokens=10**400), "positive integer"),
]


class ParseCaseErrorTests(unittest.TestCase):
    def test_table(self):
        for name, data, needle in PARSE_ERROR_CASES:
            with self.subTest(name):
                with self.assertRaises(cases.CaseError) as ctx:
                    cases.parse_case("case", data)
                self.assertIn(needle, str(ctx.exception))


class ParseCaseSuccessTests(unittest.TestCase):
    def test_prompt_list_joined_with_newline(self):
        case = cases.parse_case("c", _case(prompt=["line one", "line two"]))
        self.assertEqual(case.prompt, "line one\nline two")

    def test_single_string_check_normalized_to_tuple(self):
        case = cases.parse_case("c", _case(expect={"contains": "solo"}))
        self.assertEqual(case.expect["contains"], ("solo",))


class LoadSuiteTests(unittest.TestCase):
    def test_missing_dir(self):
        with self.assertRaises(cases.CaseError) as ctx:
            cases.load_suite("/no/such/rigops-eval-cases-dir")
        self.assertIn("cases directory not found", str(ctx.exception))

    def test_dir_without_json_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            self.assertIn("no case files", str(ctx.exception))

    def test_two_broken_files_report_both_in_one_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad-a.json").write_text("not json at all")
            (root / "bad-b.json").write_text(json.dumps(_missing("group")))
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            message = str(ctx.exception)
            self.assertIn("bad-a.json", message)
            self.assertIn("bad-b.json", message)

    def test_invalid_json_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "broken.json").write_text("{not valid json")
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            self.assertIn("broken.json", str(ctx.exception))

    def test_case_id_is_file_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-case.json").write_text(json.dumps(_case()))
            suite = cases.load_suite(tmp)
            self.assertEqual(suite.cases[0].id, "my-case")

    def test_literal_nan_in_case_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nan.json").write_text(
                '{"group":"g","prompt":"x","expect":{"contains":["x"]},"max_tokens":NaN}'
            )
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            self.assertIn("NaN is not valid JSON", str(ctx.exception))

    def test_regex_overflow_reported_alongside_other_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad-regex.json").write_text(
                json.dumps(_case(expect={"regex": ["a{9999999999}"]}))
            )
            (root / "bad-other.json").write_text("not json")
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            message = str(ctx.exception)
            self.assertIn("does not compile", message)
            self.assertIn("bad-other.json", message)

    def test_dotfile_sidecar_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "._x.json").write_text("garbage, not json")
            (root / "good.json").write_text(json.dumps(_case()))
            suite = cases.load_suite(tmp)
            self.assertEqual([c.id for c in suite.cases], ["good"])

    def test_symlinked_case_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.json"
            real.write_text(json.dumps(_case()))
            link = root / "link.json"
            os.symlink(real, link)
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            self.assertIn("symlink", str(ctx.exception))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFO support")
    def test_fifo_case_file_rejected_promptly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.mkfifo(root / "fifo.json")
            (root / "good.json").write_text(json.dumps(_case()))
            started = time.monotonic()
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            self.assertLess(time.monotonic() - started, 2)
            self.assertIn("is not a regular file", str(ctx.exception))

    def test_case_file_over_size_limit_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "big.json").write_text("x" * (cases.MAX_FILE_BYTES + 10))
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            self.assertIn("is larger than", str(ctx.exception))

    def test_control_char_in_file_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                (root / "bad\x1bname.json").write_text(json.dumps(_case()))
            except OSError:
                self.skipTest("filesystem refuses control characters in file names")
            with self.assertRaises(cases.CaseError) as ctx:
                cases.load_suite(tmp)
            self.assertIn("control characters", str(ctx.exception))

    def test_symlinked_cases_directory_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_dir = root / "real_dir"
            real_dir.mkdir()
            (real_dir / "a.json").write_text(json.dumps(_case()))
            link_dir = root / "link_dir"
            os.symlink(real_dir, link_dir, target_is_directory=True)
            suite = cases.load_suite(link_dir)
            self.assertEqual([c.id for c in suite.cases], ["a"])


class CaseHashTests(unittest.TestCase):
    def test_ignores_note(self):
        base = _case()
        with_note = {**base, "note": "just a comment"}
        self.assertEqual(cases.case_hash(base), cases.case_hash(with_note))

    def test_ignores_key_order(self):
        base = _case()
        reordered = dict(reversed(list(base.items())))
        self.assertEqual(cases.case_hash(base), cases.case_hash(reordered))

    def test_ignores_source_file_whitespace(self):
        data = _case()
        loaded_compact = json.loads(json.dumps(data))
        loaded_pretty = json.loads(json.dumps(data, indent=4))
        self.assertEqual(cases.case_hash(loaded_compact), cases.case_hash(loaded_pretty))

    def test_prompt_change_yields_different_hash(self):
        base = _case()
        changed = {**base, "prompt": "different prompt text"}
        self.assertNotEqual(cases.case_hash(base), cases.case_hash(changed))

    def test_suite_hash_changes_when_one_case_changes_and_stable_across_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(json.dumps(_case(prompt="prompt a")))
            (root / "b.json").write_text(json.dumps(_case(prompt="prompt b")))
            suite1 = cases.load_suite(tmp)
            suite_reloaded = cases.load_suite(tmp)
            self.assertEqual(suite1.hash, suite_reloaded.hash)

            (root / "b.json").write_text(json.dumps(_case(prompt="prompt b changed")))
            suite2 = cases.load_suite(tmp)
            self.assertNotEqual(suite1.hash, suite2.hash)

    def test_suite_hash_orders_by_id_not_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a-b.json").write_text(json.dumps(_case(prompt="prompt a-b")))
            (root / "a.json").write_text(json.dumps(_case(prompt="prompt a")))
            suite = cases.load_suite(tmp)
            by_id = {c.id: c for c in suite.cases}
            expected = cases.digest(f"a {by_id['a'].hash}\na-b {by_id['a-b'].hash}")
            self.assertEqual(suite.hash, expected)


class ShippedSuiteTests(unittest.TestCase):
    def test_loads_clean_with_at_least_one_assertion_each(self):
        suite = cases.load_suite(SHIPPED_CASES_DIR)
        self.assertGreater(len(suite.cases), 0)
        for case in suite.cases:
            with self.subTest(case.id):
                self.assertGreaterEqual(len(case.expect), 1)


def _base_row() -> dict:
    return {
        "run_id": "r1",
        "totals": {
            "passed": 1, "total": 1, "errors": 0, "pass_rate": 1.0, "p50_ms": 10, "p95_ms": 10,
        },
        "cases": {"c1": {"group": "g", "status": "pass", "ms": 10, "hash": "h1"}},
    }


def _row_with_case_field(key, value) -> dict:
    row = _base_row()
    row["cases"]["c1"][key] = value
    return row


ROW_PROBLEM_CASES = [
    ("valid_row", _base_row(), ""),
    ("empty_run_id", {**_base_row(), "run_id": ""}, "run_id"),
    ("totals_missing_key", {**_base_row(), "totals": {
        k: v for k, v in _base_row()["totals"].items() if k != "passed"
    }}, "totals needs"),
    ("cases_not_a_dict", {**_base_row(), "cases": []}, "cases must be an object"),
    ("case_status_upper", _row_with_case_field("status", "PASS"),
     "needs group, status, ms and hash"),
    ("case_ms_negative", _row_with_case_field("ms", -1), "needs group, status, ms and hash"),
    ("case_ms_bool", _row_with_case_field("ms", True), "needs group, status, ms and hash"),
    ("case_hash_empty", _row_with_case_field("hash", ""), "needs group, status, ms and hash"),
    ("case_group_blank", _row_with_case_field("group", "  "), "needs group, status, ms and hash"),
]


class RowProblemTests(unittest.TestCase):
    def test_missing_run_id(self):
        row = _base_row()
        del row["run_id"]
        self.assertIn("run_id", score.row_problem(row))

    def test_table(self):
        for name, row, needle in ROW_PROBLEM_CASES:
            with self.subTest(name):
                problem = score.row_problem(row)
                if needle:
                    self.assertIn(needle, problem)
                else:
                    self.assertEqual(problem, "")


class StatsTests(unittest.TestCase):
    def test_counts_and_pass_rate(self):
        results = [
            {"status": "pass", "ms": 100},
            {"status": "pass", "ms": 200},
            {"status": "fail", "ms": 150},
            {"status": "error", "ms": 5},
        ]
        s = score.stats(results)
        self.assertEqual(s["passed"], 2)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["errors"], 1)
        self.assertEqual(s["pass_rate"], 0.5)

    def test_pass_rate_rounds_to_four_places(self):
        results = [
            {"status": "pass", "ms": 1},
            {"status": "fail", "ms": 1},
            {"status": "fail", "ms": 1},
        ]
        self.assertEqual(score.stats(results)["pass_rate"], 0.3333)

    def test_empty_results(self):
        s = score.stats([])
        self.assertEqual(s["total"], 0)
        self.assertIsNone(s["pass_rate"])
        self.assertIsNone(s["p50_ms"])
        self.assertIsNone(s["p95_ms"])

    def test_percentiles_linear_interpolation(self):
        results = [{"status": "pass", "ms": ms} for ms in (100, 200, 300, 400)]
        s = score.stats(results)
        self.assertEqual(s["p50_ms"], 250)
        self.assertEqual(s["p95_ms"], 385)

    def test_errors_count_in_total_not_latency(self):
        results = [
            {"status": "pass", "ms": 100},
            {"status": "pass", "ms": 300},
            {"status": "error", "ms": 999999},
        ]
        s = score.stats(results)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["errors"], 1)
        self.assertEqual(s["p50_ms"], 200)

    def test_all_errored_gives_null_percentiles(self):
        results = [{"status": "error", "ms": 10}, {"status": "error", "ms": 20}]
        s = score.stats(results)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["errors"], 2)
        self.assertIsNone(s["p50_ms"])
        self.assertIsNone(s["p95_ms"])


class SummarizeTests(unittest.TestCase):
    def test_groups_sorted_and_totals_aggregate_all(self):
        cases_map = {
            "c1": {"group": "zeta", "status": "pass", "ms": 100},
            "c2": {"group": "alpha", "status": "fail", "ms": 200},
            "c3": {"group": "alpha", "status": "pass", "ms": 300},
        }
        groups, totals = score.summarize(cases_map)
        self.assertEqual(list(groups), ["alpha", "zeta"])
        self.assertEqual(groups["alpha"]["total"], 2)
        self.assertEqual(groups["zeta"]["total"], 1)
        self.assertEqual(totals["total"], 3)
        self.assertEqual(totals["passed"], 2)


def _row(run_id: str, label: str, cases_map: dict | None = None, **extra) -> dict:
    row = {
        "run_id": run_id,
        "ts": "2026-01-01T00:00:00+00:00",
        "label": label,
        "model": "m",
        "suite": "suite-hash",
        "system": None,
        "git_sha": "abc123",
        "cases": cases_map or {},
    }
    row.update(extra)
    return row


def _result(group: str, status: str, ms: int = 100, reason: str = "", hash_: str = "h1") -> dict:
    return {"group": group, "status": status, "ms": ms, "reason": reason, "hash": hash_}


class CompareTests(unittest.TestCase):
    def test_shared_added_removed_changed(self):
        base = _row("r1", "main", {
            "a": _result("g1", "pass", hash_="h1"),
            "b": _result("g1", "pass", hash_="h1"),
            "c": _result("g1", "pass", hash_="h1"),
        })
        head = _row("r2", "proposal", {
            "a": _result("g1", "pass", hash_="h1"),
            "b": _result("g1", "pass", hash_="h2"),
            "d": _result("g1", "pass", hash_="h1"),
        })
        report = diff.compare(base, head)
        self.assertEqual(report["shared"], 1)
        self.assertEqual(report["added"], ["d"])
        self.assertEqual(report["removed"], ["c"])
        self.assertEqual(report["changed"], ["b"])

    def test_regression_when_group_passed_count_drops(self):
        base = _row("r1", "main", {"a": _result("g1", "pass"), "b": _result("g1", "pass")})
        head = _row("r2", "proposal", {"a": _result("g1", "pass"), "b": _result("g1", "fail")})
        report = diff.compare(base, head)
        self.assertEqual(
            report["regressions"],
            [{
                "group": "g1", "metric": "newly_failing", "cases": ["b"],
                "total": 2, "base": 2, "head": 1,
            }],
        )

    def test_compensating_flips_still_regress(self):
        base = _row("r1", "main", {"a": _result("g1", "pass"), "b": _result("g1", "fail")})
        head = _row("r2", "proposal", {"a": _result("g1", "fail"), "b": _result("g1", "pass")})
        report = diff.compare(base, head)
        self.assertEqual(
            report["regressions"],
            [{
                "group": "g1", "metric": "newly_failing", "cases": ["a"],
                "total": 2, "base": 1, "head": 1,
            }],
        )
        self.assertEqual([f["case"] for f in report["newly_failing"]], ["a"])
        self.assertEqual([p["case"] for p in report["newly_passing"]], ["b"])

    def test_pass_to_error_is_newly_failing_and_regression(self):
        base = _row("r1", "main", {"a": _result("g1", "pass")})
        head = _row("r2", "proposal", {"a": _result("g1", "error", reason="boom")})
        report = diff.compare(base, head)
        self.assertEqual(len(report["newly_failing"]), 1)
        self.assertEqual(report["newly_failing"][0]["status"], "error")
        self.assertEqual(
            report["regressions"],
            [{
                "group": "g1", "metric": "newly_failing", "cases": ["a"],
                "total": 1, "base": 1, "head": 0,
            }],
        )

    def test_changed_hash_case_excluded_even_if_flipped(self):
        base = _row("r1", "main", {"a": _result("g1", "pass", hash_="h1")})
        head = _row("r2", "proposal", {"a": _result("g1", "fail", hash_="h2")})
        report = diff.compare(base, head)
        self.assertEqual(report["shared"], 0)
        self.assertEqual(report["changed"], ["a"])
        self.assertEqual(report["regressions"], [])
        self.assertEqual(report["newly_failing"], [])

    def test_latency_tolerance_none_never_regresses(self):
        base = _row("r1", "main", {
            "a": _result("g1", "pass", ms=100), "b": _result("g1", "pass", ms=100),
        })
        head = _row("r2", "proposal", {
            "a": _result("g1", "pass", ms=100000), "b": _result("g1", "pass", ms=100000),
        })
        report = diff.compare(base, head, latency_tolerance=None)
        self.assertEqual(report["regressions"], [])

    def test_latency_regression_when_p95_grows_beyond_tolerance(self):
        base = _row("r1", "main", {"a": _result("g1", "pass", ms=100)})
        head = _row("r2", "proposal", {"a": _result("g1", "pass", ms=111)})
        report = diff.compare(base, head, latency_tolerance=10)
        self.assertEqual(
            report["regressions"],
            [{"group": "g1", "metric": "p95_ms", "base": 100, "head": 111, "pct": 11.0}],
        )

    def test_latency_exactly_at_bound_is_not_a_regression(self):
        base = _row("r1", "main", {"a": _result("g1", "pass", ms=100)})
        head = _row("r2", "proposal", {"a": _result("g1", "pass", ms=110)})
        report = diff.compare(base, head, latency_tolerance=10)
        self.assertEqual(report["regressions"], [])

    def test_latency_regression_from_zero_base_has_none_pct(self):
        base = _row("r1", "main", {"a": _result("g1", "pass", ms=0)})
        head = _row("r2", "proposal", {"a": _result("g1", "pass", ms=50)})
        report = diff.compare(base, head, latency_tolerance=10)
        self.assertEqual(
            report["regressions"],
            [{"group": "g1", "metric": "p95_ms", "base": 0, "head": 50, "pct": None}],
        )
        self.assertEqual(report["groups"]["g1"]["base"]["p95_ms"], 0)
        self.assertEqual(report["groups"]["g1"]["head"]["p95_ms"], 50)

    def test_hash_missing_both_sides_lands_in_changed_not_shared(self):
        base = _row("r1", "main", {"a": {"group": "g1", "status": "pass", "ms": 10, "reason": ""}})
        head = _row(
            "r2", "proposal", {"a": {"group": "g1", "status": "pass", "ms": 10, "reason": ""}}
        )
        report = diff.compare(base, head)
        self.assertEqual(report["shared"], 0)
        self.assertEqual(report["changed"], ["a"])

    def test_base_error_head_fail_is_unverified_not_regression(self):
        base = _row("r1", "main", {"a": _result("g1", "error", reason="boom")})
        head = _row("r2", "proposal", {"a": _result("g1", "fail", reason="nope")})
        report = diff.compare(base, head)
        self.assertEqual(report["unverified"], ["a"])
        self.assertEqual(report["regressions"], [])

    def test_base_error_head_pass_is_newly_passing_not_unverified(self):
        base = _row("r1", "main", {"a": _result("g1", "error", reason="boom")})
        head = _row("r2", "proposal", {"a": _result("g1", "pass")})
        report = diff.compare(base, head)
        self.assertEqual([p["case"] for p in report["newly_passing"]], ["a"])
        self.assertEqual(report["unverified"], [])

    def test_latency_exact_bound_at_tolerance_15_not_a_regression(self):
        base = _row("r1", "main", {"a": _result("g1", "pass", ms=100)})
        head = _row("r2", "proposal", {"a": _result("g1", "pass", ms=115)})
        report = diff.compare(base, head, latency_tolerance=15)
        self.assertEqual(report["regressions"], [])

    def test_latency_one_ms_past_bound_at_tolerance_15_regresses(self):
        base = _row("r1", "main", {"a": _result("g1", "pass", ms=100)})
        head = _row("r2", "proposal", {"a": _result("g1", "pass", ms=116)})
        report = diff.compare(base, head, latency_tolerance=15)
        self.assertEqual(
            report["regressions"],
            [{"group": "g1", "metric": "p95_ms", "base": 100, "head": 116, "pct": 16.0}],
        )

    def test_removed_passing_case_is_dropped(self):
        base = _row("r1", "main", {"a": _result("g1", "pass")})
        head = _row("r2", "proposal", {})
        report = diff.compare(base, head)
        self.assertEqual(report["dropped"], ["a"])

    def test_edited_failing_case_is_not_dropped(self):
        base = _row("r1", "main", {"a": _result("g1", "fail", hash_="h1")})
        head = _row("r2", "proposal", {"a": _result("g1", "fail", hash_="h2")})
        report = diff.compare(base, head)
        self.assertEqual(report["dropped"], [])


class ResolveTests(unittest.TestCase):
    def test_run_id_match_wins_over_label(self):
        rows = [_row("r1", "main"), _row("r2", "other"), _row("main", "not-main-label")]
        found = diff.resolve(rows, "main")
        self.assertEqual(found["run_id"], "main")

    def test_label_returns_latest_with_that_label(self):
        rows = [_row("r1", "proposal"), _row("r2", "proposal"), _row("r3", "other")]
        found = diff.resolve(rows, "proposal")
        self.assertEqual(found["run_id"], "r2")

    def test_unknown_ref_returns_none(self):
        rows = [_row("r1", "main")]
        self.assertIsNone(diff.resolve(rows, "nope"))


if __name__ == "__main__":
    unittest.main()
