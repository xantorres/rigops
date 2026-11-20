from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import ledger_row, levers, state  # noqa: E402

from tests.test_ledger import (  # noqa: E402
    FIXTURE_FIXED_TAX,
    FIXTURE_TRANSCRIPTS,
    ROW_DATE,
    EnvIsolatedTestCase,
    _run_ledger,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "ledger"


class GoldenRowTests(EnvIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        self.addCleanup(self._restore_tz)

    def _restore_tz(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_full_row_matches_golden_file(self):
        # Changing the expected row for a released version means bumping ROW_VERSION.
        home = Path.home()
        shutil.copytree(FIXTURE_TRANSCRIPTS, home / "transcripts", dirs_exist_ok=True)
        shutil.copytree(FIXTURE_FIXED_TAX, home / "fixed_tax", dirs_exist_ok=True)
        cfg = {
            "transcripts_dir": "~/transcripts",
            "ledger": {"watch_projects": {"beta": "proj-beta"}},
            "fixed_tax": {
                "paths": ["~/fixed_tax/explicit.md"],
                "globs": ["~/fixed_tax/globbed/*.md"],
            },
        }
        row = levers.build_row(ROW_DATE, "golden", cfg=cfg)
        golden = GOLDEN_DIR / f"golden-row-v{ledger_row.ROW_VERSION}.json"
        self.assertEqual(row, json.loads(golden.read_text()))


class ComparabilityTests(unittest.TestCase):
    def test_v0_vs_v1_marks_redefined_metrics_not_comparable(self):
        old = {"date": "2026-08-08", "turns": 10, "corrections": 2, "denials": 1}
        new = {"version": 1, "date": "2026-08-15", "turns": 12, "corrections": 5, "denials": 3}
        deltas = levers.compute_deltas(old, new)
        for key in ("corrections", "denials"):
            self.assertFalse(deltas[key]["comparable"])
            self.assertIsNone(deltas[key]["delta"])
            self.assertIsNone(deltas[key]["pct"])
            self.assertEqual(deltas[key]["new"], new[key])
        self.assertTrue(deltas["turns"]["comparable"])
        self.assertEqual(deltas["turns"]["delta"], 2)
        self.assertNotIn("version", deltas)

    def test_same_version_rows_stay_comparable(self):
        old = {"version": 1, "date": "a", "corrections": 2}
        new = {"version": 1, "date": "b", "corrections": 5}
        self.assertTrue(levers.compute_deltas(old, new)["corrections"]["comparable"])


class FingerprintTests(EnvIsolatedTestCase):
    def _tax_cfg(self, *paths: Path) -> dict:
        return {"fixed_tax": {"paths": [str(p) for p in paths], "globs": []}}

    def _row(self, cfg: dict) -> dict:
        cfg = {"transcripts_dir": str(FIXTURE_TRANSCRIPTS), **cfg}
        return levers.build_row(ROW_DATE, "", cfg=cfg)

    def test_equal_size_content_change_flips_sha_not_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "always.md"
            f.write_text("alpha")
            before = self._row(self._tax_cfg(f))
            f.write_text("omega")
            after = self._row(self._tax_cfg(f))
        self.assertEqual(before["fixed_tax_b"], after["fixed_tax_b"])
        self.assertNotEqual(before["fixed_tax_sha"], after["fixed_tax_sha"])
        self.assertEqual(len(before["fixed_tax_sha"]), 16)

    def test_no_entries_gives_none_sha(self):
        self.assertIsNone(self._row(self._tax_cfg())["fixed_tax_sha"])

    def test_config_sha_tracks_ledger_section_only(self):
        base = {"ledger": {"watch_projects": {"beta": "proj-beta"}}}
        sha = ledger_row.config_sha(base)
        self.assertEqual(len(sha), 16)
        self.assertNotEqual(sha, ledger_row.config_sha({"ledger": {"watch_projects": {}}}))
        self.assertEqual(sha, ledger_row.config_sha({**base, "reaper": {"orphan_sweep": True}}))

    def test_states(self):
        a, b = {"fixed_tax_sha": "x"}, {"fixed_tax_sha": "y"}
        self.assertEqual(ledger_row.fingerprint_state(a, b, "fixed_tax_sha"), "changed")
        self.assertEqual(ledger_row.fingerprint_state(a, a, "fixed_tax_sha"), "unchanged")
        self.assertEqual(ledger_row.fingerprint_state({}, a, "fixed_tax_sha"), "unknown")
        self.assertEqual(
            ledger_row.fingerprint_state({"fixed_tax_sha": None}, a, "fixed_tax_sha"), "unknown"
        )


class DiffOutputTests(EnvIsolatedTestCase):
    def _diff(self, rows: list, *args: str):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "RIGOPS_CONFIG": str(Path(tmp) / "c.json"),
                   "RIGOPS_STATE_DIR": str(Path(tmp) / "state")}
            for row in rows:
                state.append_jsonl(Path(tmp) / "state" / "ledger.jsonl", row)
            return _run_ledger(["diff", *args], env)

    def test_text_reports_changed_files_and_not_comparable(self):
        rows = [
            {"date": "2026-08-08", "turns": 1, "corrections": 2,
             "fixed_tax_b": 5, "fixed_tax_sha": "aaaa", "config_sha": "cccc"},
            {"version": 1, "date": "2026-08-15", "turns": 2, "corrections": 4,
             "fixed_tax_b": 5, "fixed_tax_sha": "bbbb", "config_sha": "cccc"},
        ]
        out = self._diff(rows).stdout
        self.assertIn("always-loaded files: changed", out)
        self.assertIn("measurement config: unchanged", out)
        self.assertIn("not comparable", next(ln for ln in out.splitlines() if "corrections" in ln))

    def test_unknown_when_old_row_lacks_fields(self):
        rows = [{"date": "2026-08-08", "turns": 1},
                {"version": 1, "date": "2026-08-15", "turns": 2, "fixed_tax_sha": "b"}]
        out = self._diff(rows).stdout
        self.assertIn("always-loaded files: unknown", out)
        self.assertIn("measurement config: unknown", out)

    def test_json_payload_adds_fingerprints(self):
        payload = json.loads(self._diff(
            [{"date": "2026-08-08", "turns": 1}, {"date": "2026-08-15", "turns": 2}], "--json"
        ).stdout)
        self.assertEqual(payload["fingerprints"],
                         {"always_loaded_files": "unknown", "measurement_config": "unknown"})
        self.assertIn("comparable", payload["deltas"]["turns"])

    def test_json_early_payload_has_fingerprints(self):
        payload = json.loads(self._diff([{"date": "2026-08-08"}], "--json").stdout)
        self.assertEqual(payload["fingerprints"]["measurement_config"], "unknown")


if __name__ == "__main__":
    unittest.main()
