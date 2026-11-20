"""The 0.11 upgrade notes name every config default that changed since 0.10.1."""
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

OLD_DEFAULTS = REPO_ROOT / "tests" / "fixtures" / "config" / "defaults-0.10.1.json"
HEADING = "### Upgrading from 0.10.x"


def _leaves(tree: dict, prefix: str = ""):
    for key, value in tree.items():
        if isinstance(value, dict) and value:
            yield from _leaves(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _upgrade_notes() -> str:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## \[)", text, re.S | re.M)
    assert unreleased, "no [Unreleased] section"
    pattern = rf"^{re.escape(HEADING)}\n(.*?)(?=^### |\Z)"
    notes = re.search(pattern, unreleased.group(1), re.S | re.M)
    assert notes, f"no '{HEADING}' subsection under [Unreleased]"
    return notes.group(1)


class UpgradeNotesTests(unittest.TestCase):
    def test_every_changed_default_is_named(self):
        old = json.loads(OLD_DEFAULTS.read_text(encoding="utf-8"))
        new = {key: _hash(value) for key, value in _leaves(config.DEFAULTS)}
        changed = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
        self.assertTrue(changed, "no changed default found; the comparison is vacuous")
        notes = _upgrade_notes()
        missing = [key for key in changed if f"`{key}`" not in notes]
        self.assertEqual(missing, [], f"changed defaults not named in the upgrade notes: {missing}")


if __name__ == "__main__":
    unittest.main()
