from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import rigops

REPO_ROOT = Path(__file__).resolve().parent.parent
PEP440 = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?$")


class VersionTests(unittest.TestCase):
    def test_plugin_manifest_matches_package_version(self):
        manifest = json.loads((REPO_ROOT / "plugin/.claude-plugin/plugin.json").read_text())
        self.assertEqual(manifest["version"], rigops.__version__)

    def test_marketplace_entry_matches_package_version(self):
        market = json.loads((REPO_ROOT / ".claude-plugin/marketplace.json").read_text())
        entry = next(p for p in market["plugins"] if p["name"] == "rigops")
        self.assertEqual(entry["version"], rigops.__version__)

    def test_version_is_canonical_pep440(self):
        cases = {rigops.__version__: True, "1.0.0rc1": True, "1.0.0a2": True, "1.0.0b3": True,
                 "1.0.0-rc.1": False, "v1.0.0": False, "1.0": False}
        for value, ok in cases.items():
            with self.subTest(value=value):
                self.assertEqual(bool(PEP440.match(value)), ok)

    def test_readme_does_not_repeat_the_version(self):
        readme = (REPO_ROOT / "README.md").read_text()
        found = re.search(rf"v?{re.escape(rigops.__version__)}(?![\w.])", readme)
        self.assertIsNone(found, "README.md repeats the package version")


if __name__ == "__main__":
    unittest.main()
