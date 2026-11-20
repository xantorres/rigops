from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import mine_redact  # noqa: E402

LAYER_ENV = ("RIGOPS_REDACTION_PATTERNS", "RIGOPS_REDACTION_PATTERNS_B64")


class GateLayerParityTests(unittest.TestCase):
    """The default rule layers are the ones tools/redaction-gate.sh loads."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = self.root / "home" / ".config" / "rigops"
        self.config.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k not in LAYER_ENV}
        env["HOME"] = str(self.root / "home")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rules(self, path: Path, word: str, newline: str = "\n") -> Path:
        path.write_bytes(f"# placeholder layer{newline}s {word}{newline}".encode())
        return path

    def _masked(self, word: str, pattern_files=None) -> bool:
        return word not in mine_redact.redact(f"see {word} here", pattern_files=pattern_files)

    def test_a_colon_list_loads_every_named_file(self):
        one = self._rules(self.root / "one.txt", "zzlayeronezz")
        two = self._rules(self.root / "two.txt", "zzlayertwozz")
        os.environ["RIGOPS_REDACTION_PATTERNS"] = f"{one}:{two}"
        self.assertTrue(self._masked("zzlayeronezz"))
        self.assertTrue(self._masked("zzlayertwozz"))
        self.assertEqual(mine_redact.broken_rules(), 0)

    def test_a_named_file_that_is_missing_is_counted(self):
        one = self._rules(self.root / "one.txt", "zzlayeronezz")
        os.environ["RIGOPS_REDACTION_PATTERNS"] = f"{one}:{self.root / 'absent.txt'}"
        self.assertTrue(self._masked("zzlayeronezz"))
        self.assertEqual(mine_redact.broken_rules(), 1)

    def test_both_default_files_load(self):
        self._rules(self.config / "redaction-patterns.txt", "zzlayeronezz")
        self._rules(self.config / "redaction-patterns.rigops.txt", "zzlayertwozz")
        self.assertTrue(self._masked("zzlayeronezz"))
        self.assertTrue(self._masked("zzlayertwozz"))

    def test_absent_default_files_are_not_counted(self):
        self.assertEqual(mine_redact.broken_rules(), 0)

    def test_the_b64_layer_replaces_every_file_layer(self):
        self._rules(self.config / "redaction-patterns.txt", "zzlayeronezz")
        named = self._rules(self.root / "named.txt", "zzlayertwozz")
        os.environ["RIGOPS_REDACTION_PATTERNS"] = str(named)
        os.environ["RIGOPS_REDACTION_PATTERNS_B64"] = base64.b64encode(
            b"s zzpackedzz\n").decode()
        self.assertTrue(self._masked("zzpackedzz"))
        self.assertFalse(self._masked("zzlayeronezz"))
        self.assertFalse(self._masked("zzlayertwozz"))
        self.assertEqual(mine_redact.broken_rules(), 0)

    def test_explicit_pattern_files_leave_out_the_b64_layer(self):
        os.environ["RIGOPS_REDACTION_PATTERNS_B64"] = base64.b64encode(
            b"s zzpackedzz\n").decode()
        self.assertFalse(self._masked("zzpackedzz", pattern_files=[]))

    def test_crlf_rule_files_match(self):
        crlf = self._rules(self.root / "crlf.txt", "zzcrlfzz$", newline="\r\n")
        os.environ["RIGOPS_REDACTION_PATTERNS"] = str(crlf)
        self.assertEqual(mine_redact.redact("see zzcrlfzz"), "see [redacted]")
        os.environ["RIGOPS_REDACTION_PATTERNS_B64"] = base64.b64encode(
            b"s zzpackedzz$\r\n").decode()
        self.assertEqual(mine_redact.redact("see zzpackedzz"), "see [redacted]")


class PublicPatternsShipWithTheModuleTests(unittest.TestCase):
    def test_public_file_sits_beside_the_module(self):
        beside = Path(mine_redact.__file__).resolve().parent
        self.assertEqual(mine_redact.PUBLIC_PATTERNS.parent, beside)
        self.assertTrue(mine_redact.PUBLIC_PATTERNS.is_file())

    def test_public_rules_load_without_error(self):
        self.assertEqual(mine_redact.broken_rules([mine_redact.PUBLIC_PATTERNS]), 0)


if __name__ == "__main__":
    unittest.main()
