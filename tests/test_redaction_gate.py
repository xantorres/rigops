import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tools" / "redaction-gate.sh"

# Built by concatenation so no literal here matches the gate itself.
SHAPES = {
    "dni": "id " + "12345678" + "Z ok",
    "nie": "id " + "X" + "1234567" + "L ok",
    "iban": "iban " + "ES" + "00 1234 5678 9012 3456 7890",
    "iban_compact": "iban " + "ES" + "001234567890123456789012",
    "phone_plus": "call " + "+34" + " 600 111 222",
    "phone_zero": "call " + "0034" + " 600 111 222",
    "phone_compact": "call " + "+34" + "600111222",
    "phone_dash": "call " + "+1" + "-555-010-0199",
    "street_es": "vive en calle " + "Inventada" + " 12, 3B",
    "street_ca": "carrer " + "de la Prova" + " 7",
    "street_en": "ship to 12 " + "Invented" + " Road, London",
    "floor": "Invented " + "Nombre" + " 12, " + "Piso" + " 3",
}

CLEAN = {
    "sha": "index " + "0a1b2c3" + ".." + "d4e5f6a" + " 100644",
    "digest": "sha256 " + "e3b0c44298fc1c149afbf4c8996fb92427ae41e4" + "649b934ca495991b7852b855",
    "uuid": "id " + "123e4567" + "-e89b-12d3-a456-426614174000",
    "version": "bump to v0.3.0 and python 3.11.4",
    "tz": "Date: Wed Nov 4 10:00:00 2026 " + "+0100",
    "diff_date": "+" + "2026-11-04" + "-12",
    "offset_date": "date " + "+2026" + "-11-04",
    "diff_epoch": "+" + "1730000000",
    "plus_prose": "ratio " + "+12" + " and +3",
    "prose": "pay attention to 200k tokens and step 3 of 12",
}


def gate(text):
    with tempfile.TemporaryDirectory() as home:
        msg = Path(home) / "msg.txt"
        msg.write_text(text + "\n", encoding="utf-8")
        env = {**os.environ, "HOME": home}
        env.pop("RIGOPS_REDACTION_PATTERNS", None)
        env.pop("RIGOPS_REDACTION_PATTERNS_B64", None)
        out = subprocess.run(
            ["bash", str(GATE), "--message", str(msg)],
            capture_output=True,
            text=True,
            env=env,
        )
        return out.returncode


class PersonalDataShapeTests(unittest.TestCase):
    def test_each_shape_is_flagged(self):
        for name, text in SHAPES.items():
            with self.subTest(name=name):
                self.assertEqual(gate(text), 1)

    def test_hashes_ids_versions_and_prose_pass(self):
        for name, text in CLEAN.items():
            with self.subTest(name=name):
                self.assertEqual(gate(text), 0)


if __name__ == "__main__":
    unittest.main()
