from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import config  # noqa: E402
from rigops.detector import BUILTIN, Detector  # noqa: E402


def _cfg(**ledger) -> dict:
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["ledger"].update(ledger)
    return cfg


class DefaultsTests(unittest.TestCase):
    def test_keys_default_to_empty_lists(self):
        for key in ("extra_correction_patterns", "correction_exclude_patterns",
                    "extra_denial_patterns"):
            self.assertEqual(config.DEFAULTS["ledger"][key], [])

    def test_default_config_builds_the_builtin_detector(self):
        self.assertEqual(Detector.from_config(config.DEFAULTS), BUILTIN)

    def test_missing_ledger_section_builds_the_builtin_detector(self):
        self.assertEqual(Detector.from_config({}), BUILTIN)


class BuiltinTests(unittest.TestCase):
    def test_english_corrections(self):
        for text in ("stop.", "wait, that broke it", "no, wrong file", "nope", "revert that"):
            with self.subTest(text=text):
                self.assertEqual(BUILTIN.classify(text), "builtin")
        for text in ("stop the server", "wait for CI", "no-op change", "now add tests", "eso no"):
            with self.subTest(text=text):
                self.assertEqual(BUILTIN.classify(text), "none")

    def test_first_nonblank_line_only(self):
        self.assertTrue(BUILTIN.is_correction("\n\n  no, other\nthanks"))
        self.assertFalse(BUILTIN.is_correction("thanks\nno, other"))

    def test_author_denials_are_not_builtin(self):
        self.assertFalse(BUILTIN.is_denial("STALE BACKEND CHECKOUT: acme-web 7 commits behind"))
        self.assertFalse(BUILTIN.is_denial("checkout behind origin"))
        self.assertTrue(BUILTIN.is_denial("requires approval"))


class ExtraPatternTests(unittest.TestCase):
    def test_extra_pattern_searches_the_first_line_ignoring_case(self):
        det = Detector.from_config(_cfg(extra_correction_patterns=[r"\bnarp\b"]))
        self.assertEqual(det.classify("please NARP that"), "extra")
        self.assertEqual(det.classify("fine\nnarp"), "none")

    def test_builtin_match_reports_builtin_not_extra(self):
        det = Detector.from_config(_cfg(extra_correction_patterns=[r"^no"]))
        self.assertEqual(det.classify("no, x"), "builtin")

    def test_exclude_beats_builtin_and_extra(self):
        det = Detector.from_config(_cfg(
            extra_correction_patterns=[r"^ola"], correction_exclude_patterns=[r"^no se\b", r"^ola"]
        ))
        self.assertEqual(det.classify("no se nada"), "excluded")
        self.assertFalse(det.is_correction("no se nada"))
        self.assertEqual(det.classify("ola mundo"), "excluded")
        self.assertTrue(det.is_correction("no, x"))

    def test_extra_denial_searches_flattened_text(self):
        det = Detector.from_config(_cfg(extra_denial_patterns=["checkout behind origin"]))
        self.assertTrue(det.is_denial("STALE: Checkout Behind Origin/main"))
        self.assertFalse(det.is_denial("npm ERR! code 1"))


class ValidationTests(unittest.TestCase):
    def _error(self, **ledger) -> str:
        with self.assertRaises(SystemExit) as ctx:
            Detector.from_config(_cfg(**ledger))
        return str(ctx.exception)

    def test_non_list(self):
        self.assertEqual(
            self._error(extra_correction_patterns="x"),
            "error: ledger.extra_correction_patterns: must be a list of regex strings",
        )

    def test_non_string_entry(self):
        self.assertEqual(
            self._error(correction_exclude_patterns=["ok", 3]),
            "error: ledger.correction_exclude_patterns[1]: must be a string",
        )

    def test_entry_that_does_not_compile(self):
        self.assertEqual(
            self._error(extra_denial_patterns=["ok", "ok", "(bad"]),
            "error: ledger.extra_denial_patterns[2]: missing ), unterminated subpattern",
        )


if __name__ == "__main__":
    unittest.main()
