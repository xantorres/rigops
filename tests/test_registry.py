from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_REGISTRY = REPO_ROOT / "config" / "registry.example.md"


class ParseFrontMatterTests(unittest.TestCase):
    def test_absent_items_returns_empty_dict(self):
        self.assertEqual(registry.parse_front_matter("no items here\njust text\n"), {})

    def test_schema_scalar_captured_before_items(self):
        text = "---\nschema: rigops.v1\nitems:\n  - id: a\n    domain: x\n---\n"
        fm = registry.parse_front_matter(text)
        self.assertEqual(fm["schema"], "rigops.v1")
        self.assertEqual(fm["items"], [{"id": "a", "domain": "x"}])

    def test_comment_lines_inside_items_skipped(self):
        text = (
            "items:\n"
            "  - id: a\n"
            "    # a comment line, ignored\n"
            "    domain: x\n"
            "---\n"
        )
        fm = registry.parse_front_matter(text)
        self.assertEqual(fm["items"], [{"id": "a", "domain": "x"}])

    def test_notes_block_captured_verbatim(self):
        text = (
            "items:\n"
            "  - id: a\n"
            "    domain: x\n"
            "    notes: |\n"
            "      line one\n"
            "      line two\n"
            "---\n"
        )
        fm = registry.parse_front_matter(text)
        self.assertEqual(fm["items"], [{"id": "a", "domain": "x", "notes": "line one\nline two"}])

    def test_notes_block_ends_at_new_item(self):
        text = (
            "items:\n"
            "  - id: a\n"
            "    notes: |\n"
            "      only line\n"
            "  - id: b\n"
            "    domain: y\n"
            "---\n"
        )
        fm = registry.parse_front_matter(text)
        self.assertEqual(fm["items"], [
            {"id": "a", "notes": "only line"},
            {"id": "b", "domain": "y"},
        ])

    def test_notes_block_sibling_field_at_same_indent_survives(self):
        text = (
            "items:\n"
            "  - id: a\n"
            "    notes: |\n"
            "      only line\n"
            "    max_runtime_h: 3\n"
            "---\n"
        )
        fm = registry.parse_front_matter(text)
        self.assertEqual(
            fm["items"], [{"id": "a", "notes": "only line", "max_runtime_h": "3"}]
        )

    def test_no_closing_fence_reads_to_end_of_file(self):
        text = "items:\n  - id: a\n    domain: x\n"
        fm = registry.parse_front_matter(text)
        self.assertEqual(fm["items"], [{"id": "a", "domain": "x"}])

    def test_malformed_line_raises_system_exit_naming_line(self):
        text = "items:\n  - id: a\n    not a field or item\n---\n"
        with self.assertRaises(SystemExit) as ctx:
            registry.parse_front_matter(text)
        self.assertIn("3", str(ctx.exception))

    def test_field_before_any_item_is_malformed(self):
        text = "items:\n  domain: orphan\n  - id: a\n---\n"
        with self.assertRaises(SystemExit):
            registry.parse_front_matter(text)


class LoadRegistryTests(unittest.TestCase):
    def test_example_file_parses_three_items_with_defaults(self):
        items = registry.load_registry(EXAMPLE_REGISTRY)
        self.assertEqual(
            [it["id"] for it in items], ["nightly-backup", "metrics-rollup", "log-prune"]
        )
        by_id = {it["id"]: it for it in items}
        self.assertEqual(by_id["nightly-backup"]["max_runtime_h"], 2.0)
        self.assertEqual(by_id["log-prune"]["max_runtime_h"], registry.DEFAULT_MAX_RUNTIME_H)
        self.assertEqual(by_id["log-prune"]["plist"], "")
        self.assertEqual(by_id["log-prune"]["notes"], "")
        self.assertIn("Rolls up per-minute", by_id["metrics-rollup"]["notes"])

    def test_missing_file_raises_system_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                registry.load_registry(Path(tmp) / "nope.md")

    def test_no_items_key_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.md"
            path.write_text("# just a doc, no registry front matter\n")
            self.assertEqual(registry.load_registry(path), [])

    def test_max_runtime_h_invalid_string_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.md"
            path.write_text(
                "schema: rigops.v1\nitems:\n  - id: a\n    max_runtime_h: not-a-number\n---\n"
            )
            with self.assertRaises(SystemExit):
                registry.load_registry(path)

    def test_max_runtime_h_non_positive_or_non_finite_exits(self):
        for bad in ("0", "-1", "inf", "nan"):
            with self.subTest(bad):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "reg.md"
                    path.write_text(
                        f"schema: rigops.v1\nitems:\n  - id: a\n    max_runtime_h: {bad}\n---\n"
                    )
                    with self.assertRaises(SystemExit):
                        registry.load_registry(path)

    def test_duplicate_item_ids_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.md"
            path.write_text(
                "schema: rigops.v1\nitems:\n"
                "  - id: a\n    domain: x\n"
                "  - id: a\n    domain: y\n"
                "---\n"
            )
            with self.assertRaises(SystemExit):
                registry.load_registry(path)

    def test_wrong_schema_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.md"
            path.write_text("schema: rigops.v2\nitems:\n  - id: a\n---\n")
            with self.assertRaises(SystemExit):
                registry.load_registry(path)

    def test_missing_schema_with_items_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.md"
            path.write_text("items:\n  - id: a\n---\n")
            with self.assertRaises(SystemExit):
                registry.load_registry(path)


if __name__ == "__main__":
    unittest.main()
