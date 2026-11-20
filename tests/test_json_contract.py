from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIBEXEC = Path(__file__).resolve().parent.parent / "libexec"

FORMS = [
    ("authprobe", "--json"),
    ("authprobe", "--json", "--force"),
    ("backlog", "lint", "--json"),
    ("doctor", "--supervisor", "none", "--json"),
    ("doctor", "--list", "--json"),
    ("doctor", "--config-only", "--json"),
    ("eit", "--json"),
    ("eit", "--rebuilds", "--json"),
    ("eval", "list", "--json"),
    ("janitor", "--json"),
    ("ledger", "write", "--dry-run", "--json"),
    ("ledger", "write", "--show", "--json"),
    ("ledger", "diff", "--json"),
    ("reap", "--json"),
    ("tax", "--json"),
    ("tax", "--history", "--json"),
    ("tax", "--starts", "--json"),
    ("tax", "--dead", "--json"),
]


class EveryJsonPayloadTests(unittest.TestCase):
    def test_each_json_output_is_one_object_with_version_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "transcripts").mkdir()
            env = {
                **os.environ, "HOME": str(home), "RIGOPS_STATE_DIR": str(home / "state"),
                "RIGOPS_CONFIG": str(home / "config.json"),
                "XDG_CONFIG_HOME": str(home / "xc"), "XDG_STATE_HOME": str(home / "xs"),
            }
            for cmd, *args in FORMS:
                if cmd in ("tax", "eit"):
                    args += ["--dir", str(home / "transcripts")]
                with self.subTest(form=" ".join((cmd, *args))):
                    proc = subprocess.run(
                        [sys.executable, str(LIBEXEC / f"rigops-{cmd}"), *args],
                        capture_output=True, text=True, timeout=60, env=env,
                        stdin=subprocess.DEVNULL, check=False,
                    )
                    self.assertNotIn("Traceback", proc.stderr)
                    payload = json.loads(proc.stdout)
                    self.assertIsInstance(payload, dict)
                    self.assertEqual(payload.get("version"), 1)


if __name__ == "__main__":
    unittest.main()
