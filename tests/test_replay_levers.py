from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "replay-levers.py"


class ReplayLeversTests(unittest.TestCase):
    def _replay(self, tmp, values, *flags):
        state = Path(tmp) / "state"
        state.mkdir()
        start = date(2026, 8, 3)
        rows = [
            {"date": (start + timedelta(days=7 * i)).isoformat(), "turn1_app": v}
            for i, v in enumerate(values)
        ]
        (state / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        config = Path(tmp) / "config.json"
        config.write_text(json.dumps({"ledger": {"watch_projects": {"app": "-Users-alice-app"}}}))
        env = {**os.environ, "RIGOPS_STATE_DIR": str(state), "RIGOPS_CONFIG": str(config)}
        result = subprocess.run(
            [sys.executable, str(TOOL), *flags], env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_each_row_is_judged_as_the_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = self._replay(tmp, [33000, 34000, 32000, 66000, 34000])
        self.assertEqual(
            [line.split("  ", 1)[1] for line in lines],
            ["-", "-", "-", "turn1_app:rise_pct", "-"],
        )

    def test_mask_hides_group_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = self._replay(tmp, [33000, 34000, 66000], "--mask", "-v")
        text = "\n".join(lines)
        self.assertIn("turn1_G0:rise_pct", text)
        self.assertNotIn("app", text)


if __name__ == "__main__":
    unittest.main()
