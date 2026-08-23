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


class ComputeDeltasTests(unittest.TestCase):
    def test_delta_and_pct_math_including_negative(self):
        old = {
            "date": "2026-08-01", "label": "old", "window": {"since": "x"},
            "eit_per_turn": 1000, "turns": 50,
        }
        new = {
            "date": "2026-08-08", "label": "new", "window": {"since": "y"},
            "eit_per_turn": 1250, "turns": 40,
        }
        deltas = levers.compute_deltas(old, new)
        self.assertNotIn("date", deltas)
        self.assertNotIn("label", deltas)
        self.assertNotIn("window", deltas)
        self.assertEqual(
            deltas["eit_per_turn"], {"old": 1000, "new": 1250, "delta": 250, "pct": 25.0}
        )
        self.assertEqual(deltas["turns"], {"old": 50, "new": 40, "delta": -10, "pct": -20.0})

    def test_none_on_one_side_renders_null_delta_and_pct(self):
        old = {"date": "2026-08-01", "ctx_p50": None}
        new = {"date": "2026-08-08", "ctx_p50": 1500}
        deltas = levers.compute_deltas(old, new)
        self.assertEqual(deltas["ctx_p50"], {"old": None, "new": 1500, "delta": None, "pct": None})

    def test_zero_baseline_skips_pct_but_keeps_delta(self):
        old = {"date": "2026-08-01", "over300k_pct": 0}
        new = {"date": "2026-08-08", "over300k_pct": 5}
        deltas = levers.compute_deltas(old, new)
        self.assertEqual(deltas["over300k_pct"], {"old": 0, "new": 5, "delta": 5, "pct": None})

    def test_float_delta_rounds_to_one_decimal(self):
        old = {"date": "2026-08-01", "cache_hit_pct": 0.2}
        new = {"date": "2026-08-08", "cache_hit_pct": 0.7}
        self.assertEqual(repr(new["cache_hit_pct"] - old["cache_hit_pct"]), "0.49999999999999994")
        deltas = levers.compute_deltas(old, new)
        self.assertEqual(deltas["cache_hit_pct"]["delta"], 0.5)

    def test_key_only_in_new_row_is_union_none(self):
        old = {"date": "2026-08-01", "eit_per_turn": 1000}
        new = {"date": "2026-08-08", "eit_per_turn": 1250, "fixed_tax_b": 2048}
        deltas = levers.compute_deltas(old, new)
        self.assertEqual(
            deltas["fixed_tax_b"], {"old": None, "new": 2048, "delta": None, "pct": None}
        )


class LedgerNoteTests(EnvIsolatedTestCase):
    def _env_for(self, tmp: str) -> dict:
        env = dict(os.environ)
        env["RIGOPS_CONFIG"] = str(Path(tmp) / "config.json")
        env["RIGOPS_STATE_DIR"] = str(Path(tmp) / "state")
        return env

    def test_note_appends_expected_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            result = _run_ledger(["note", "shipped delegation nudge", "--at", "2026-08-10"], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("2026-08-10", result.stdout)

            entries = state.read_jsonl(Path(tmp) / "state" / "interventions.jsonl")
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertEqual(entry["date"], "2026-08-10")
            self.assertEqual(entry["text"], "shipped delegation nudge")
            dt.datetime.fromisoformat(entry["ts"])  # ISO-8601, raises on malformed

    def test_note_rejects_empty_or_whitespace_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            for bad_text in ["", "   "]:
                result = _run_ledger(["note", bad_text], env)
                self.assertEqual(result.returncode, 2)
            self.assertFalse((Path(tmp) / "state" / "interventions.jsonl").exists())

    def test_top_level_at_before_note_no_longer_shadows(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            result = _run_ledger(["--at", "2026-08-10", "note", "shadow-check"], env)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((Path(tmp) / "state" / "interventions.jsonl").exists())


class LedgerDiffTests(EnvIsolatedTestCase):
    def _env_for(self, tmp: str) -> dict:
        env = dict(os.environ)
        env["RIGOPS_CONFIG"] = str(Path(tmp) / "config.json")
        env["RIGOPS_STATE_DIR"] = str(Path(tmp) / "state")
        return env

    def _seed_rows(self, tmp: str) -> Path:
        jsonl_path = Path(tmp) / "state" / "ledger.jsonl"
        state.append_jsonl(
            jsonl_path,
            {"date": "2026-08-01", "label": "base", "eit_per_turn": 1000, "ctx_p50": None},
        )
        state.append_jsonl(
            jsonl_path,
            {"date": "2026-08-08", "label": "mid", "eit_per_turn": 1200, "ctx_p50": 1600},
        )
        state.append_jsonl(
            jsonl_path,
            {"date": "2026-08-15", "label": "latest", "eit_per_turn": 900, "ctx_p50": 1400},
        )
        return jsonl_path

    def test_single_row_prints_message_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state.append_jsonl(
                Path(tmp) / "state" / "ledger.jsonl", {"date": "2026-08-15", "eit_per_turn": 900}
            )
            result = _run_ledger(["diff"], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("not enough ledger rows", result.stdout)

    def test_default_diff_pins_negative_delta_and_interventions_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_rows(tmp)
            # baseline == 2026-08-08 (excluded), inside window, latest == 2026-08-15 (included),
            # after latest (excluded).
            _run_ledger(["note", "before-window-note", "--at", "2026-08-08"], env)
            _run_ledger(["note", "mid-window-note", "--at", "2026-08-10"], env)
            _run_ledger(["note", "latest-day-note", "--at", "2026-08-15"], env)
            _run_ledger(["note", "after-window-note", "--at", "2026-08-16"], env)

            result = _run_ledger(["diff", "--json"], env)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["baseline"], "2026-08-08")
            self.assertEqual(payload["latest"], "2026-08-15")
            self.assertEqual(
                payload["deltas"]["eit_per_turn"],
                {"old": 1200, "new": 900, "delta": -300, "pct": -25.0},
            )
            self.assertEqual(
                payload["deltas"]["ctx_p50"],
                {"old": 1600, "new": 1400, "delta": -200, "pct": -12.5},
            )
            texts = [e["text"] for e in payload["interventions"]]
            self.assertEqual(texts, ["mid-window-note", "latest-day-note"])

            text_result = _run_ledger(["diff"], env)
            self.assertIn("mid-window-note", text_result.stdout)
            self.assertIn("latest-day-note", text_result.stdout)
            self.assertNotIn("before-window-note", text_result.stdout)
            self.assertNotIn("after-window-note", text_result.stdout)
            self.assertIn("Interventions in this window:", text_result.stdout)

    def test_none_on_one_side_renders_dash_in_text_and_null_in_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_rows(tmp)
            result = _run_ledger(["diff", "--since", "2026-08-01", "--json"], env)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["baseline"], "2026-08-01")
            self.assertIsNone(payload["deltas"]["ctx_p50"]["old"])
            self.assertIsNone(payload["deltas"]["ctx_p50"]["delta"])
            self.assertIsNone(payload["deltas"]["ctx_p50"]["pct"])

            text_result = _run_ledger(["diff", "--since", "2026-08-01"], env)
            self.assertEqual(text_result.returncode, 0)
            self.assertNotIn("\u2014", text_result.stdout)
            ctx_line = next(
                ln for ln in text_result.stdout.splitlines() if ln.startswith("ctx_p50")
            )
            self.assertEqual(ctx_line.split(), ["ctx_p50", "-", "1.4k", "-", "-"])

    def test_since_equals_latest_date_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_rows(tmp)
            result = _run_ledger(["diff", "--since", "2026-08-15"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must select a baseline older than the latest row", result.stderr)

    def test_key_only_in_new_row_renders_dash_in_table_and_null_in_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            jsonl_path = Path(tmp) / "state" / "ledger.jsonl"
            state.append_jsonl(jsonl_path, {"date": "2026-08-01", "eit_per_turn": 1000})
            state.append_jsonl(
                jsonl_path, {"date": "2026-08-08", "eit_per_turn": 1200, "fixed_tax_b": 2048}
            )

            result = _run_ledger(["diff", "--json"], env)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["deltas"]["fixed_tax_b"],
                {"old": None, "new": 2048, "delta": None, "pct": None},
            )

            text_result = _run_ledger(["diff"], env)
            tax_line = next(
                ln for ln in text_result.stdout.splitlines() if ln.startswith("fixed_tax_b")
            )
            self.assertEqual(tax_line.replace("2.0 KB", "2.0KB").split(), [
                "fixed_tax_b", "-", "2.0KB", "-", "-",
            ])

    def test_text_mode_pins_negative_pct_and_human_bytes_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            jsonl_path = Path(tmp) / "state" / "ledger.jsonl"
            state.append_jsonl(
                jsonl_path,
                {"date": "2026-08-01", "eit_per_turn": 1200, "fixed_tax_b": 3 * 1024 * 1024},
            )
            state.append_jsonl(
                jsonl_path,
                {"date": "2026-08-08", "eit_per_turn": 900, "fixed_tax_b": 1 * 1024 * 1024},
            )

            result = _run_ledger(["diff"], env)
            self.assertEqual(result.returncode, 0)
            lines = result.stdout.splitlines()
            eit_line = next(ln for ln in lines if ln.startswith("eit_per_turn"))
            tax_line = next(ln for ln in lines if ln.startswith("fixed_tax_b"))
            self.assertEqual(
                eit_line.split(), ["eit_per_turn", "1.2k", "0.9k", "-0.3k", "-25.0%"]
            )
            self.assertEqual(
                tax_line.replace(" MB", "MB").split(),
                ["fixed_tax_b", "3.0MB", "1.0MB", "-2.0MB", "-66.7%"],
            )

    def test_top_level_json_before_diff_no_longer_shadows(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_rows(tmp)
            result = _run_ledger(["--json", "diff"], env)
            self.assertEqual(result.returncode, 2)

    def test_since_picks_correct_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_rows(tmp)
            result = _run_ledger(["diff", "--since", "2026-08-05", "--json"], env)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["baseline"], "2026-08-01")
            self.assertEqual(payload["latest"], "2026-08-15")

    def test_since_missing_baseline_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_rows(tmp)
            result = _run_ledger(["diff", "--since", "2025-01-01"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("no ledger row on or before", result.stderr)

    def test_json_shape_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_rows(tmp)
            result = _run_ledger(["diff", "--json"], env)
            payload = json.loads(result.stdout)
            self.assertEqual(
                set(payload), {"baseline", "latest", "deltas", "interventions"}
            )
            for delta in payload["deltas"].values():
                self.assertEqual(set(delta), {"old", "new", "delta", "pct"})


if __name__ == "__main__":
    unittest.main()
