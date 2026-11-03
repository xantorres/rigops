from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops.doctor import check_memory  # noqa: E402


def _store(root, index, notes):
    mem = Path(root) / ".claude" / "projects" / "-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text(index, encoding="utf-8")
    for name, body in notes.items():
        (mem / name).write_text(body, encoding="utf-8")
    return mem


def _cfg(root, **over):
    memory = {"sources": [str(Path(root) / ".claude/projects/*/memory/*.md")]}
    memory.update(over)
    return {"doctor": {"memory": memory}}


class MemoryLintTests(unittest.TestCase):
    def _run(self, index, notes, **over):
        with tempfile.TemporaryDirectory() as tmp:
            _store(tmp, index, notes)
            return check_memory.run(_cfg(tmp, **over))

    def test_clean_store_has_no_findings(self):
        self.assertEqual(self._run("- [a](a.md) one\n", {"a.md": "body\n"}), [])

    def test_broken_index_pointer_is_a_warning(self):
        findings = self._run("- [a](a.md) one\n- [b](b.md) two\n", {"a.md": "x\n"})
        self.assertEqual([f.key.split(":")[0] for f in findings], ["pointer"])
        self.assertEqual(findings[0].severity, "warn")
        self.assertIn("b.md", findings[0].symptom)

    def test_note_missing_from_index_is_a_warning(self):
        findings = self._run("- [a](a.md) one\n", {"a.md": "x\n", "b.md": "y\n"})
        self.assertEqual([f.key.split(":")[0] for f in findings], ["unindexed"])
        self.assertEqual(findings[0].severity, "warn")

    def test_wikilink_without_a_note_is_not_a_finding(self):
        body = "see [[worth-writing-later]] and [[a]]\n"
        self.assertEqual(self._run("- [a](a.md) one\n", {"a.md": body}), [])

    def test_external_and_anchor_links_are_ignored(self):
        index = "- [x](https://example.org/x.md) y\n- [a](a.md#top) z\n"
        self.assertEqual(self._run(index, {"a.md": "x\n"}), [])

    def test_index_over_line_cap_is_a_warning(self):
        index = "".join(f"- [a](a.md) {i}\n" for i in range(6))
        findings = self._run(index, {"a.md": "x\n"}, index_max_lines=5)
        self.assertEqual([f.key.split(":")[0] for f in findings], ["index-size"])
        self.assertEqual(findings[0].severity, "warn")

    def test_index_over_byte_cap_is_a_warning(self):
        index = "- [a](a.md) " + "x" * 200 + "\n"
        findings = self._run(index, {"a.md": "x\n"}, index_max_bytes=100)
        self.assertEqual([f.key.split(":")[0] for f in findings], ["index-size"])

    def test_directory_without_an_index_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp) / ".claude/projects/-p/memory"
            mem.mkdir(parents=True)
            (mem / "a.md").write_text("x\n", encoding="utf-8")
            self.assertEqual(check_memory.run(_cfg(tmp)), [])

    def test_finding_key_is_stable_across_line_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = _store(tmp, "- [b](b.md) two\n", {})
            before = [f.id for f in check_memory.run(_cfg(tmp))]
            (mem / "MEMORY.md").write_text("\n\n- [b](b.md) two\n", encoding="utf-8")
            after = [f.id for f in check_memory.run(_cfg(tmp))]
        self.assertEqual(before, after)
        self.assertEqual(len(before), 1)


if __name__ == "__main__":
    unittest.main()
