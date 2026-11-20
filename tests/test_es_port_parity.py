"""Removable together with lib/rigops/mine_es.py: proves the es.json overlay matches it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import mine_es  # noqa: E402

from tests import test_es_precision, test_mine_t2  # noqa: E402
from tests.support import es_detector  # noqa: E402

ENGLISH = (
    "no, wrong file", "nope", "stop.", "wait, that broke it", "revert that", "undo the last edit",
    "stop the server", "wait for CI", "now add tests", "no-op change is fine", "hello there",
    "not what I asked", "wrong approach", "thanks, looks good", "",
)
SPANISH_STATEMENT_AND_QUESTION_LINES = (
    "no se como hacerlo", "No hay nada de eso", "no tengo tiempo", "no puedo abrirlo",
    "no hace nada", "no lo se", "no lo puedes bajar tu", "no seria mejor cerrar ese pr?",
    "no hace falta repetir el plan", "no se toca el core", "no es lo que pedi, vale",
    "no, es otra cosa", "no pero usa la otra", "eso no?", "esto no gasta memoria?",
    "\n\nno se que hacer\nno, otra cosa", "para", "para ya", "para mañana", "para qué sirve",
)
# The oracle only reads the first 160 chars; a plain pattern cannot express that window.
LONG_REPROACH = "x" * 170 + ", no has puesto el test"


def _lines(*groups):
    seen = []
    for group in groups:
        for line in group:
            if line not in seen:
                seen.append(line)
    return seen


def _module_strings(module):
    return [v for value in vars(module).values() if isinstance(value, tuple)
            for v in value if isinstance(v, str)]


class EsPortParityTests(unittest.TestCase):
    def test_overlay_matches_the_oracle_line_for_line(self):
        detector = es_detector()
        lines = _lines(
            _module_strings(test_es_precision), ENGLISH, SPANISH_STATEMENT_AND_QUESTION_LINES,
            _module_strings(test_mine_t2),
        )
        self.assertGreater(len(lines), 100)
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(detector.is_correction(line), mine_es.counted(line))

    def test_reproach_past_the_160_char_window_now_counts(self):
        self.assertFalse(mine_es.counted(LONG_REPROACH))
        self.assertTrue(es_detector().is_correction(LONG_REPROACH))


if __name__ == "__main__":
    unittest.main()
