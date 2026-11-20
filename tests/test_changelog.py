"""Each release's upgrade notes name every config default that changed since the previous one."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from rigops import config  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "config"


def _leaves(tree: dict, prefix: str = ""):
    for key, value in tree.items():
        if isinstance(value, dict) and value:
            yield from _leaves(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _hashed(defaults: dict) -> dict:
    return {key: _hash(value) for key, value in _leaves(defaults)}


def _changed(old: dict, new: dict) -> list:
    return sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))


def _upgrade_notes(heading: str) -> str:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    notes = re.search(rf"^{re.escape(heading)}\n(.*?)(?=^##|\Z)", text, re.S | re.M)
    assert notes, f"no '{heading}' section in CHANGELOG.md"
    return notes.group(1)


class UpgradeNotesTests(unittest.TestCase):
    def assert_named(self, changed: list, heading: str) -> None:
        self.assertTrue(changed, "no changed default found; the comparison is vacuous")
        notes = _upgrade_notes(heading)
        missing = [key for key in changed if f"`{key}`" not in notes]
        self.assertEqual(missing, [], f"changed defaults not named under '{heading}': {missing}")

    def test_defaults_changed_in_0_11_are_named(self):
        changed = _changed(_fixture("defaults-0.10.1.json"), _fixture("defaults-0.11.0.json"))
        self.assert_named(changed, "### Upgrading from 0.10.x")

    def test_defaults_changed_since_0_11_are_named(self):
        changed = _changed(_fixture("defaults-0.11.0.json"), _hashed(config.DEFAULTS))
        self.assert_named(changed, "### Upgrading from 0.11.x")


if __name__ == "__main__":
    unittest.main()
