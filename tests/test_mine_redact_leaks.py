from __future__ import annotations

import tempfile
import time
import unittest
import warnings
from pathlib import Path

from rigops import mine, mine_redact


def _out(text):
    return mine.redact(text, pattern_files=[])


class IdentifierLeakTests(unittest.TestCase):
    def _masked(self, cases):
        for text, leaked in cases.items():
            with self.subTest(text=text):
                self.assertNotIn(leaked, _out(text))

    def test_ibans_with_a_letter_bank_code(self):
        self._masked({
            "iban " + "GB00" + " WXYZ" + " 0000 0000 0000 00 ok": "WXYZ",
            "iban " + "IE00" + "WXYZ00000000000000" + " ok": "WXYZ",
            "pay to " + "NL00" + " WXYZ " + "0000 0000 00": "WXYZ",
        })

    def test_tax_ids_vat_ssn_and_cards(self):
        self._masked({
            "cif " + "B" + "7654321" + "0 ok": "7654321",
            "nif " + "Q" + "76543210 ok": "7654321",
            "dni " + "12" + "." + "345" + "." + "678" + "-Z ok": "345",
            "vat " + "DE" + "123456789 ok": "123456789",
            "vat " + "GB" + "123456789 ok": "123456789",
            "vat " + "FR" + "12345678901 ok": "12345678901",
            "vat " + "NL" + "123456789" + "B01 ok": "123456789",
            "ssn " + "123" + "-45-" + "6789 ok": "6789",
            "card " + "4000 " + "1234 " + "5678 " + "9010 ok": "5678",
            "card " + "4000-" + "1234-" + "5678-" + "9010 ok": "5678",
        })

    def test_french_uk_and_spanish_landline_phones(self):
        self._masked({
            "tel " + "06 12 34 " + "56 78 ok": "12 34",
            "tel " + "06.12.34." + "56.78 ok": "12.34",
            "tel " + "07700 " + "900123 ok": "900123",
            "tel " + "91 234 " + "56 78 ok": "234 56",
            "tel " + "93-123-" + "45-67 ok": "123-45",
        })

    def test_more_address_shapes(self):
        self._masked({
            "at " + "742 Evergreen" + " Terrace ok": "Evergreen",
            "at " + "10 Maple" + " Court ok": "Maple",
            "at " + "5 Oak" + " Way ok": "Oak",
            "at " + "9 Elm" + " Close ok": "Elm",
            "at " + "742 evergreen" + " street ok": "evergreen",
            "at " + "Maple Lane" + " 12 ok": "Maple",
            "vive en " + "Passeig de" + " Prova 12": "Prova",
            "vive en " + "Rambla" + " Inventada 5": "Inventada",
            "vive en " + "Plaza" + " Mayor s/n": "Mayor",
            "wohnt " + "Haupt" + "strasse 12": "Haupt",
            "wohnt " + "Goethe" + "straße 5": "Goethe",
            "habite " + "12 rue" + " de la Paix": "Paix",
            "send to " + "PO Box" + " 1234": "1234",
        })

    def test_tlds_ips_and_obfuscated_domains(self):
        self._masked({
            "see " + "foo-corp.nl and bar-corp.it": "foo-corp",
            "see " + "baz-corp.ch and x-corp.us": "baz-corp",
            "see " + "y-corp.info and z-corp.biz": "y-corp",
            "see " + "a-corp.pt and b-corp.mx and c-corp.ca": "a-corp",
            "see " + "d-corp.be and e-corp.ie and f-corp.pl": "d-corp",
            "see " + "g-corp.se and h-corp.gov and i-corp.edu": "g-corp",
            "see " + "foo-corp[.]com": "foo-corp",
            "see " + "foo-corp(dot)com": "foo-corp",
            "host " + "203.0.113.45 now": "113",
            "host " + "2001:db8::1 now": "db8",
            "host " + "fe80::1ab2:3c4d now": "1ab2",
        })

    def test_credentials(self):
        self._masked({
            "token " + "npm_" + "a1B2c3D4e5F6g7H8i9J0 ok": "a1B2c3",
            "password: hunter2 ok": "hunter2",
            "api_key=abc123xyz ok": "abc123xyz",
            "\"secret\": \"s3cr3tvalue\" ok": "s3cr3tvalue",
            "DB_PASSWORD = swordfish": "swordfish",
            "url " + "postgres://user:pass" + "@db/app ok": "pass",
        })

    def test_shapes_found_on_the_second_pass(self):
        self._masked({
            "call " + "+33" + " 6 12 34 56 78": "56 78",
            "call " + "020" + " 7946 0958": "7946",
            "vat " + "1234567" + "FA ok": "1234567",
            "vat " + "IE1234567" + "FA ok": "1234567",
            "Gran " + "Vía" + " 28 3º B": "28",
            "card " + "3782" + " 822463 10005": "822463",
        })

    def test_volumes_and_named_home_paths(self):
        self._masked({
            "see " + "/Volumes/" + "invented-disk/notes/a.md": "invented-disk",
            "see " + "~invented" + "/notes/a.md": "invented",
        })
        self.assertEqual(_out("a ~= b and x~y/z"), "a ~= b and x~y/z")

    def test_masks_keep_their_label(self):
        self.assertIn("[iban]", _out("iban " + "GB00" + " WXYZ" + " 0000 0000 0000 00"))
        self.assertIn("[phone]", _out("tel " + "06 12 34 " + "56 78"))
        self.assertIn("[address]", _out("at " + "742 Evergreen" + " Terrace"))
        self.assertIn("[ip]", _out("host " + "203.0.113.45"))
        self.assertIn("password", _out("password: hunter2"))


class PayLeakTests(unittest.TestCase):
    def test_more_pay_words_and_currency_prefixes(self):
        cases = {
            "paying 85000 now": "85000",
            "pays 90k a year": "90k",
            "payroll 250000 total": "250000",
            "compensation of 120.000": "120.000",
            "raise to 20k next year": "20k",
            "fee of 5000 agreed": "5000",
            "rate 12000 gross": "12000",
            "gané 50000 el año": "50000",
            "me aumento 30000": "30000",
            "USD " + "50000 total": "50000",
            "CHF " + "5000 total": "5000",
            "EUR " + "4500 total": "4500",
            "cobran 5000 pesos": "5000",
        }
        for text, leaked in cases.items():
            with self.subTest(text=text):
                self.assertNotIn(leaked, _out(text))

    def test_code_and_measures_near_the_new_pay_words_survive(self):
        for text in ("rate limit of 10000 calls", "raise ValueError on bad input",
                     "rate 5000 per second", "rate 5000 req", "rate of 20k tokens",
                     "fee 200 files", "coffee 12345 lines", "feed 50000 rows",
                     "the pays attribute", "payroll.py:213", "raise 3 errors",
                     "a rate of 20000 qps", "compensation 30k tokens"):
            with self.subTest(text=text):
                self.assertEqual(_out(text), text)


class SurvivorTests(unittest.TestCase):
    def test_code_versions_and_private_hosts_survive(self):
        for text in ("logger.info is noisy", "console.info(x)", "expect(x).to.be.true",
                     "v1.2.3.4 shipped", "versions 1.2.3 and 1.2.3.4.5", "host 127.0.0.1",
                     "host 10.0.0.5 and 172.16.0.9", "Chrome 120.0.6099.109", "at 10:30:15",
                     "std::vector and Foo::bar", "the password policy: strong is a label",
                     "password reset flow", "max_tokens=4096 and tokens: 25k",
                     "api_key rotation", "see 2 hard drive failures", "3 ways to fix it",
                     "12 items on the road", "mine.py:213", "S3-12 and step-3"):
            with self.subTest(text=text):
                self.assertNotIn("[", _out(text))


class GateRuleDialectTests(unittest.TestCase):
    def test_public_rules_compile_without_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            self.assertEqual(mine_redact.broken_rules([mine_redact.PUBLIC_PATTERNS]), 0)

    def test_posix_classes_keep_their_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp) / "rules.txt"
            rules.write_text("s (^|[^[:alnum:]])zz[[:alpha:]]+zz($|[^[:alnum:]])\n")
            masked = mine.redact("a zzéazz b", pattern_files=[rules])
            kept = mine.redact("azzéazz b", pattern_files=[rules])
        self.assertNotIn("zzéazz", masked)
        self.assertIn("zzéazz", kept)


class SecondPassSurvivorTests(unittest.TestCase):
    def test_type_words_slices_and_section_numbers_survive(self):
        for text in ("password: string", "secret: true", "api_key: null", "a[1::2]",
                     "ver el apartado 3", "CAD 3 files"):
            with self.subTest(text=text):
                self.assertEqual(_out(text), text)


class AcceptedOverMaskTests(unittest.TestCase):
    def test_accepted_over_masks_stay_masked(self):
        cases = {
            "a" * 40: "[hex]", "b" * 64: "[hex]", "1234567": "[id]", "20241104": "[id]",
            "Node-18": "[ticket]", "RFC-2119": "[ticket]", "ADR-7": "[ticket]",
            "CVE-2024-1234": "[ticket]", "build-123": "[ticket]", "bug-123": "[ticket]",
            "my.dev": "[domain]", "script.pl": "[domain]", "1.2.3.4": "[ip]",
            "2023 2024 2025 2026": "[card]", "rate: 44100": "[amount]",
        }
        for text, mask in cases.items():
            with self.subTest(text=text[:12]):
                self.assertIn(mask, _out(text))


class PerformanceTests(unittest.TestCase):
    def test_a_capped_input_stays_fast(self):
        pieces = ("a1 " * 340, "1" * 1000, "ab12 " * 200, "1. " * 330, "a" * 1000,
                  "de " + "1234 " * 190, ("Ab " * 330), "0" + " 12" * 330,
                  "password" + ":" * 990, "ES" + "00" + "0000" * 240, "::" * 500,
                  "1.2.3." * 160, "x" + "-" * 990, "Calle " + "Ab " * 300)
        for text in pieces:
            text = text[: mine.KEEP_TEXT]
            start = time.perf_counter()
            _out(text)
            with self.subTest(head=text[:8]):
                self.assertLess(time.perf_counter() - start, 0.5)


if __name__ == "__main__":
    unittest.main()
