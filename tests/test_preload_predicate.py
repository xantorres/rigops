from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core, levers  # noqa: E402
from rigops.doctor import check_budget  # noqa: E402

RULES = {
    "scoped.md": ("---\npaths:\n  - 'src/**'\n---\nscoped\n", False),
    "globs-only.md": ("---\nglobs: 'src/**'\n---\nglobs only\n", True),
    "bare.md": ("# no frontmatter\n", True),
}


class PreloadPredicateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.claude = Path(tmp.name) / "claude"
        (self.claude / "rules").mkdir(parents=True)
        for name, (text, _) in RULES.items():
            (self.claude / "rules" / name).write_text(text)

    def test_paths_scopes_and_globs_or_nothing_stays_always_on(self):
        for name, (_, always_on) in RULES.items():
            with self.subTest(name):
                self.assertIs(core.is_always_on(self.claude / "rules" / name), always_on)

    def test_fixed_tax_and_doctor_budget_agree_with_the_predicate(self):
        cfg = {"fixed_tax": {"globs": [str(self.claude / "rules" / "*.md")]}}
        taxed = {p.name for p, _ in levers.fixed_tax_entries(cfg)}
        with mock.patch.object(core, "CLAUDE_DIR", self.claude):
            budgeted = {p.name for p in check_budget.members()}
        expected = {name for name, (_, on) in RULES.items() if on}
        self.assertEqual(taxed, expected)
        self.assertEqual(budgeted, expected)


if __name__ == "__main__":
    unittest.main()
