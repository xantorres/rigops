from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import levers, state  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_SCRIPT = REPO_ROOT / "libexec" / "rigops-ledger"
FIXTURE_TRANSCRIPTS = Path(__file__).resolve().parent / "fixtures" / "transcripts"
FIXTURE_FIXED_TAX = Path(__file__).resolve().parent / "fixtures" / "fixed_tax"

ROW_DATE = dt.date(2026, 8, 15)


def _cfg_dict() -> dict:
    return {
        "transcripts_dir": str(FIXTURE_TRANSCRIPTS),
        "ledger": {"watch_projects": {"beta": "proj-beta"}},
        "fixed_tax": {
            "paths": [str(FIXTURE_FIXED_TAX / "explicit.md")],
            "globs": [str(FIXTURE_FIXED_TAX / "globbed" / "*.md")],
        },
    }


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _isolate_env(self, tmp: str) -> None:
        os.environ["RIGOPS_STATE_DIR"] = str(Path(tmp) / "state")
        os.environ["RIGOPS_CONFIG"] = str(Path(tmp) / "config.json")


class BuildRowTests(EnvIsolatedTestCase):
    def test_row_assembly_against_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            row = levers.build_row(
                ROW_DATE, "test-label", cfg=_cfg_dict(), transcripts_dir=FIXTURE_TRANSCRIPTS
            )

            # msg-1 (deduped) + msg-agent + msg-beta are main turns; the subagents/
            # file adds msg-sub and msg-big (a >300k-ctx turn) to the window total
            # but neither counts toward session/agent stats.
            self.assertEqual(row["turns"], 5)
            self.assertEqual(row["sessions"], 2)
            self.assertEqual(row["eit_per_turn"], 62845)
            self.assertEqual(row["ctx_p50"], 1700)
            self.assertEqual(row["out_per_turn"], 84)
            self.assertEqual(row["cache_hit_pct"], 0.2)
            self.assertEqual(row["over300k_pct"], 98.7)
            self.assertEqual(row["agent_per_100"], 33.33)
            self.assertEqual(row["cheap_model_eit_pct"], 0.7)
            self.assertEqual(row["turn1_p10"], 710)
            self.assertEqual(row["turn1_p50"], 1150)
            self.assertEqual(row["turn1_beta"], 600)
            self.assertEqual(row["turn1_beta_p50"], 600)
            self.assertEqual(row["sessions_beta"], 1)

            explicit_size = (FIXTURE_FIXED_TAX / "explicit.md").stat().st_size
            plain_size = (FIXTURE_FIXED_TAX / "globbed" / "plain.md").stat().st_size
            self.assertEqual(row["fixed_tax_b"], explicit_size + plain_size)

    def test_empty_watch_projects_skips_per_project_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_env(tmp)
            cfg = {
                "transcripts_dir": str(FIXTURE_TRANSCRIPTS),
                "ledger": {"watch_projects": {}},
                "fixed_tax": {"paths": [], "globs": []},
            }
            row = levers.build_row(ROW_DATE, "", cfg=cfg, transcripts_dir=FIXTURE_TRANSCRIPTS)
            self.assertNotIn("turn1_beta", row)
            self.assertIsNone(row["fixed_tax_b"])


def _run_ledger(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LEDGER_SCRIPT), *args],
        env=env, capture_output=True, text=True, check=False,
    )


class LedgerCliTests(EnvIsolatedTestCase):
    def _env_for(self, tmp: str) -> dict:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(_cfg_dict()))
        env = dict(os.environ)
        env["RIGOPS_CONFIG"] = str(config_path)
        env["RIGOPS_STATE_DIR"] = str(Path(tmp) / "state")
        return env

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            result = _run_ledger(["--at", ROW_DATE.isoformat(), "--dry-run", "--json"], env)
            self.assertEqual(result.returncode, 0)
            json.loads(result.stdout)  # valid row JSON
            self.assertFalse((Path(tmp) / "state" / "ledger.jsonl").exists())

    def test_rerun_noop_then_force_replace_and_md_matches_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            jsonl_path = Path(tmp) / "state" / "ledger.jsonl"
            md_path = Path(tmp) / "state" / "ledger.md"

            first = _run_ledger(["--at", ROW_DATE.isoformat(), "--label", "first"], env)
            self.assertEqual(first.returncode, 0)
            rows = state.read_jsonl(jsonl_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "first")

            rerun = _run_ledger(["--at", ROW_DATE.isoformat(), "--label", "again"], env)
            self.assertEqual(rerun.returncode, 0)
            self.assertIn("row exists, nothing appended", rerun.stdout)
            rows = state.read_jsonl(jsonl_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "first")

            forced = _run_ledger(
                ["--at", ROW_DATE.isoformat(), "--label", "second", "--force"], env
            )
            self.assertEqual(forced.returncode, 0)
            rows = state.read_jsonl(jsonl_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "second")

            watch_projects = _cfg_dict()["ledger"]["watch_projects"]
            expected_md = levers.render_markdown(rows, watch_projects)
            self.assertEqual(md_path.read_text(), expected_md)

    def test_force_replace_middle_row_preserves_date_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            jsonl_path = Path(tmp) / "state" / "ledger.jsonl"
            md_path = Path(tmp) / "state" / "ledger.md"
            dates = ["2026-08-01", "2026-08-08", "2026-08-15"]
            for i, d in enumerate(dates):
                state.append_jsonl(
                    jsonl_path, {"date": d, "label": f"seed-{i}", "eit_per_turn": (i + 1) * 100}
                )

            forced = _run_ledger(["--at", "2026-08-08", "--label", "replaced", "--force"], env)
            self.assertEqual(forced.returncode, 0)

            rows = state.read_jsonl(jsonl_path)
            self.assertEqual([r["date"] for r in rows], dates)
            middle = next(r for r in rows if r["date"] == "2026-08-08")
            self.assertEqual(middle["label"], "replaced")

            watch_projects = _cfg_dict()["ledger"]["watch_projects"]
            self.assertEqual(md_path.read_text(), levers.render_markdown(rows, watch_projects))

            nxt = _run_ledger(["--at", "2026-08-22", "--label", "next"], env)
            self.assertEqual(nxt.returncode, 0)
            self.assertIn("vs 2026-08-15", nxt.stdout)

    def test_show_json_reflects_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            _run_ledger(["--at", ROW_DATE.isoformat()], env)
            shown = _run_ledger(["--show", "--json"], env)
            self.assertEqual(shown.returncode, 0)
            rows = json.loads(shown.stdout)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["date"], ROW_DATE.isoformat())


if __name__ == "__main__":
    unittest.main()
