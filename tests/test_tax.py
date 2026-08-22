from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import state  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TAX_SCRIPT = REPO_ROOT / "libexec" / "rigops-tax"
FIXTURE_FIXED_TAX = Path(__file__).resolve().parent / "fixtures" / "fixed_tax"


def _run_tax(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TAX_SCRIPT), *args],
        env=env, capture_output=True, text=True, check=False,
    )


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _env_with(self, tmp: str, config: dict) -> dict:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(config))
        env = dict(os.environ)
        env["RIGOPS_CONFIG"] = str(config_path)
        env["RIGOPS_STATE_DIR"] = str(Path(tmp) / "state")
        return env


class CurrentModeTests(EnvIsolatedTestCase):
    def _configured_env(self, tmp: str) -> dict:
        cfg = {
            "fixed_tax": {
                "paths": [str(FIXTURE_FIXED_TAX / "explicit.md")],
                "globs": [str(FIXTURE_FIXED_TAX / "globbed" / "*.md")],
            }
        }
        return self._env_with(tmp, cfg)

    def test_breakdown_sorted_desc_with_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._configured_env(tmp)
            result = _run_tax([], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("explicit.md", result.stdout)
            self.assertIn("plain.md", result.stdout)
            self.assertNotIn("scoped.md", result.stdout)
            self.assertIn("total:", result.stdout)

    def test_json_excludes_frontmatter_scoped_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._configured_env(tmp)
            result = _run_tax(["--json"], env)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            paths = [f["path"] for f in payload["files"]]
            self.assertTrue(any(p.endswith("explicit.md") for p in paths))
            self.assertTrue(any(p.endswith("plain.md") for p in paths))
            self.assertFalse(any(p.endswith("scoped.md") for p in paths))
            self.assertEqual(payload["total_bytes"], sum(f["bytes"] for f in payload["files"]))

    def test_empty_config_prints_message_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_with(tmp, {"fixed_tax": {"paths": [], "globs": []}})
            result = _run_tax([], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("not configured", result.stdout)


class HistoryModeTests(EnvIsolatedTestCase):
    def test_sparkline_and_first_last_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_with(tmp, {})
            jsonl_path = Path(tmp) / "state" / "ledger.jsonl"
            for date, size in (("2026-08-01", 1000), ("2026-08-08", 1500), ("2026-08-15", 2000)):
                state.append_jsonl(jsonl_path, {"date": date, "fixed_tax_b": size})

            result = _run_tax(["--history"], env)
            self.assertEqual(result.returncode, 0)
            lines = result.stdout.splitlines()
            self.assertTrue(lines[0])  # sparkline
            self.assertIn("first 2026-08-01: 1000 B", result.stdout)
            self.assertIn("last  2026-08-15: 2.0 KB", result.stdout)
            self.assertIn("+1000 B (+100.0%)", result.stdout)

    def test_history_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_with(tmp, {})
            jsonl_path = Path(tmp) / "state" / "ledger.jsonl"
            for date, size in (("2026-08-01", 1000), ("2026-08-08", 1500), ("2026-08-15", 2000)):
                state.append_jsonl(jsonl_path, {"date": date, "fixed_tax_b": size})

            result = _run_tax(["--history", "--json"], env)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual([row["fixed_tax_b"] for row in payload], [1000, 1500, 2000])

    def test_missing_ledger_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_with(tmp, {})
            result = _run_tax(["--history"], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("no ledger rows yet", result.stdout)


if __name__ == "__main__":
    unittest.main()
