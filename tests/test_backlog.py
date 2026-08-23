from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import backlog, config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# (name, line, kind) kind in {"item", "malformed", "ignored"}
CASES = [
    ("valid_open", "- [ ] 2026-08-01 hooks: a hook misfires", "item"),
    ("valid_done", "- [x] 2026-08-01 hooks: a hook misfires. Done 2026-08-02: fixed", "item"),
    ("invalid_checkbox_y", "- [y] 2026-08-01 hooks: text", "malformed"),
    ("missing_date", "- [ ] hooks: text", "malformed"),
    ("impossible_date", "- [ ] 2026-13-40 hooks: text", "malformed"),
    ("uppercase_area", "- [ ] 2026-08-01 Hooks: text", "malformed"),
    ("digit_area", "- [ ] 2026-08-01 h00ks: text", "malformed"),
    ("missing_colon", "- [ ] 2026-08-01 hooks text", "malformed"),
    ("missing_space_after_colon", "- [ ] 2026-08-01 hooks:text", "malformed"),
    ("indented_line_ignored", "  - [ ] 2026-08-01 hooks: text", "ignored"),
    ("non_list_prose_ignored", "just a prose sentence, no checkbox", "ignored"),
    ("empty_rest", "- [ ] 2026-08-01 hooks: ", "malformed"),
]


class ParseGrammarTests(unittest.TestCase):
    def test_cases(self):
        for name, line, kind in CASES:
            with self.subTest(name):
                result = backlog.parse(line)
                if kind == "item":
                    self.assertEqual(len(result["items"]), 1)
                    self.assertEqual(result["malformed"], [])
                elif kind == "malformed":
                    self.assertEqual(result["items"], [])
                    self.assertEqual(len(result["malformed"]), 1)
                else:
                    self.assertEqual(result["items"], [])
                    self.assertEqual(result["malformed"], [])

    def test_impossible_date_reason(self):
        result = backlog.parse("- [ ] 2026-13-40 hooks: text")
        self.assertEqual(result["malformed"][0]["reason"], "invalid date")

    def test_item_fields(self):
        result = backlog.parse("- [ ] 2026-08-01 hooks: a hook misfires")
        item = result["items"][0]
        self.assertEqual(item["line_no"], 1)
        self.assertFalse(item["done"])
        self.assertEqual(item["date"], "2026-08-01")
        self.assertEqual(item["area"], "hooks")
        self.assertEqual(item["rest"], "a hook misfires")

    def test_done_item_fields(self):
        result = backlog.parse("- [x] 2026-08-01 hooks: text")
        item = result["items"][0]
        self.assertTrue(item["done"])

    def test_line_numbers_1_based(self):
        text = "prose\n- [ ] 2026-08-01 hooks: text\n"
        result = backlog.parse(text)
        self.assertEqual(result["items"][0]["line_no"], 2)


class LintTests(unittest.TestCase):
    def test_counts(self):
        text = (
            "- [ ] 2026-08-01 hooks: open one\n"
            "- [ ] 2026-08-02 hooks: open two\n"
            "- [x] 2026-08-03 hooks: done one\n"
        )
        result = backlog.lint(text, ["hooks"])
        self.assertEqual(result["open"], 2)
        self.assertEqual(result["done"], 1)
        self.assertTrue(result["ok"])

    def test_unknown_area(self):
        text = "- [ ] 2026-08-01 mystery: text\n"
        result = backlog.lint(text, ["hooks"])
        self.assertEqual(result["unknown_area"], [{"line_no": 1, "area": "mystery"}])
        self.assertFalse(result["ok"])

    def test_malformed_makes_not_ok(self):
        text = "- [y] 2026-08-01 hooks: text\n"
        result = backlog.lint(text, ["hooks"])
        self.assertEqual(len(result["malformed"]), 1)
        self.assertFalse(result["ok"])

    def test_empty_text_is_ok(self):
        result = backlog.lint("", ["hooks"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["open"], 0)
        self.assertEqual(result["done"], 0)


class FormatLineTests(unittest.TestCase):
    def test_valid(self):
        line = backlog.format_line("2026-08-01", "hooks", "a hook misfires")
        self.assertEqual(line, "- [ ] 2026-08-01 hooks: a hook misfires")

    def test_control_char_rejected(self):
        with self.assertRaises(ValueError):
            backlog.format_line("2026-08-01", "hooks", "bad\x01char")

    def test_multiline_rejected(self):
        with self.assertRaises(ValueError):
            backlog.format_line("2026-08-01", "hooks", "line one\nline two")

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            backlog.format_line("2026-08-01", "hooks", "")

    def test_invalid_date_rejected(self):
        with self.assertRaises(ValueError):
            backlog.format_line("2026-13-40", "hooks", "text")


class AddLineTests(unittest.TestCase):
    def test_scaffold_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "backlog.md"
            line = backlog.add_line(path, "hooks", "a hook misfires", "2026-08-01", ["hooks"])
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertEqual(line, "- [ ] 2026-08-01 hooks: a hook misfires")
            self.assertIn("# Backlog", content)
            self.assertIn("## Open", content)
            self.assertIn("## Done", content)
            self.assertIn(line, content)

    def test_insert_newest_first_under_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backlog.md"
            path.write_text(
                "# Backlog\n\n## Open\n\n- [ ] 2026-08-01 hooks: existing item\n\n## Done\n",
                encoding="utf-8",
            )
            backlog.add_line(path, "memory", "new item", "2026-08-02", ["hooks", "memory"])
            lines = path.read_text(encoding="utf-8").splitlines()
            open_idx = lines.index("## Open")
            self.assertEqual(lines[open_idx + 2], "- [ ] 2026-08-02 memory: new item")
            self.assertEqual(lines[open_idx + 3], "- [ ] 2026-08-01 hooks: existing item")

    def test_no_open_heading_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backlog.md"
            path.write_text("# Backlog\n\nno heading here\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                backlog.add_line(path, "hooks", "text", "2026-08-01", ["hooks"])
            self.assertIn("no '## Open' heading", str(ctx.exception))

    def test_unknown_area_raises_listing_areas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backlog.md"
            with self.assertRaises(ValueError) as ctx:
                backlog.add_line(path, "bogus", "text", "2026-08-01", ["hooks", "memory"])
            msg = str(ctx.exception)
            self.assertIn("hooks", msg)
            self.assertIn("memory", msg)

    def test_atomicity_smoke_content_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backlog.md"
            backlog.add_line(path, "hooks", "first item", "2026-08-01", ["hooks"])
            backlog.add_line(path, "hooks", "second item", "2026-08-02", ["hooks"])
            content = path.read_text(encoding="utf-8")
            self.assertIn("first item", content)
            self.assertIn("second item", content)
            self.assertIn("## Done", content)
            leftover_tmp = list(Path(tmp).glob("*.tmp"))
            self.assertEqual(leftover_tmp, [])


class HasOpenLineTests(unittest.TestCase):
    def test_open_hit(self):
        text = "- [ ] 2026-08-01 memory: watermark auto-memory tripped\n"
        self.assertTrue(backlog.has_open_line(text, "watermark auto-memory"))

    def test_done_only_is_false(self):
        text = "- [x] 2026-08-01 memory: watermark auto-memory tripped. Done 2026-08-02: fixed\n"
        self.assertFalse(backlog.has_open_line(text, "watermark auto-memory"))

    def test_no_match(self):
        text = "- [ ] 2026-08-01 memory: unrelated text\n"
        self.assertFalse(backlog.has_open_line(text, "watermark auto-memory"))


class AddLinePreservesModeTests(unittest.TestCase):
    def test_existing_file_mode_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backlog.md"
            path.write_text("# Backlog\n\n## Open\n\n## Done\n", encoding="utf-8")
            os.chmod(path, 0o664)
            backlog.add_line(path, "hooks", "text", "2026-08-01", ["hooks"])
            self.assertEqual(oct(os.stat(path).st_mode)[-3:], "664")


class ExampleFileLintsCleanTests(unittest.TestCase):
    def test_example_file_lints_clean(self):
        path = REPO_ROOT / "config" / "backlog.example.md"
        text = path.read_text(encoding="utf-8")
        result = backlog.lint(text, config.DEFAULTS["backlog"]["areas"])
        self.assertEqual(result["malformed"], [])
        self.assertEqual(result["unknown_area"], [])
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
