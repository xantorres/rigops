from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import backlog, janitor, watermarks  # noqa: E402
from rigops.retrieval import roots  # noqa: E402


def _age(path: Path, days_old: float, now: float) -> None:
    ts = now - days_old * 86400
    os.utime(path, (ts, ts))


class DeleteRuleTests(unittest.TestCase):
    def test_old_deleted_young_survives_with_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            old_file = base / "old.txt"
            young_file = base / "young.txt"
            old_file.write_text("x")
            young_file.write_text("y")
            now = time.time()
            _age(old_file, 10, now)
            _age(young_file, 1, now)

            rule = {
                "name": "stale", "path": str(base), "glob": "*.txt",
                "older_than_days": 7, "action": "delete",
            }
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=now)
            self.assertFalse(old_file.exists())
            self.assertTrue(young_file.exists())
            self.assertEqual(result["rules"][0]["acted"], 1)
            self.assertEqual(result["total_deleted"], 1)

    def test_without_apply_nothing_deleted_but_matched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            old_file = base / "old.txt"
            old_file.write_text("x")
            now = time.time()
            _age(old_file, 10, now)

            rule = {
                "name": "stale", "path": str(base), "glob": "*.txt",
                "older_than_days": 7, "action": "delete",
            }
            result = janitor.run_rules([rule], apply=False, max_delete=200, now=now)
            self.assertTrue(old_file.exists())
            self.assertEqual(result["rules"][0]["acted"], 0)
            self.assertIn(str(old_file), result["rules"][0]["matched"])
            self.assertEqual(result["total_deleted"], 0)


class MaxDepthTests(unittest.TestCase):
    def _tree(self, base: Path) -> None:
        base.mkdir()
        (base / "direct.txt").write_text("x")
        child = base / "child"
        child.mkdir()
        (child / "nested.txt").write_text("y")

    def test_max_depth_1_skips_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            self._tree(base)
            rule = {
                "name": "r", "path": str(base), "glob": "*.txt",
                "max_depth": 1, "action": "report",
            }
            result = janitor.run_rules([rule], apply=False, max_delete=200, now=time.time())
            matched = result["rules"][0]["matched"]
            self.assertIn(str(base / "direct.txt"), matched)
            self.assertNotIn(str(base / "child" / "nested.txt"), matched)

    def test_no_max_depth_catches_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            self._tree(base)
            rule = {"name": "r", "path": str(base), "glob": "*.txt", "action": "report"}
            result = janitor.run_rules([rule], apply=False, max_delete=200, now=time.time())
            matched = result["rules"][0]["matched"]
            self.assertIn(str(base / "direct.txt"), matched)
            self.assertIn(str(base / "child" / "nested.txt"), matched)


class DirsOnlyEmptyTests(unittest.TestCase):
    def test_empty_dir_deleted_nonempty_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            empty_dir = base / "empty_dir"
            empty_dir.mkdir()
            nonempty_dir = base / "nonempty_dir"
            nonempty_dir.mkdir()
            (nonempty_dir / "keep.txt").write_text("x")

            rule = {
                "name": "r", "path": str(base), "glob": "*", "dirs": True,
                "only_empty": True, "action": "delete",
            }
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertFalse(empty_dir.exists())
            self.assertTrue(nonempty_dir.exists())
            self.assertEqual(result["rules"][0]["acted"], 1)


class TruncateTests(unittest.TestCase):
    def test_truncate_keeps_last_500_mode_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.log"
            f.write_text("".join(f"line {i}\n" for i in range(1, 601)))
            os.chmod(f, 0o600)

            rule = {"name": "r", "path": str(f), "action": "truncate", "keep_lines": 500}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())

            lines = f.read_text().splitlines()
            self.assertEqual(len(lines), 500)
            self.assertEqual(lines[0], "line 101")
            self.assertEqual(oct(os.stat(f).st_mode)[-3:], "600")
            self.assertEqual(result["rules"][0]["acted"], 1)

    def test_short_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.log"
            content = "".join(f"line {i}\n" for i in range(1, 11))
            f.write_text(content)

            rule = {"name": "r", "path": str(f), "action": "truncate", "keep_lines": 500}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())

            self.assertEqual(f.read_text(), content)
            self.assertEqual(result["rules"][0]["acted"], 0)
            self.assertEqual(result["rules"][0]["matched"], [])


class TruncateArchiveTests(unittest.TestCase):
    def _rule(self, f, **over):
        rule = {"name": "r", "path": str(f), "action": "truncate", "keep_lines": 3, "archive": True}
        rule.update(over)
        return rule

    def test_dropped_head_is_kept_in_a_dated_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.jsonl"
            f.write_text("".join(f"line {i}\n" for i in range(1, 8)))
            now = time.mktime((2026, 11, 3, 12, 0, 0, 0, 0, -1))

            result = janitor.run_rules([self._rule(f)], apply=True, max_delete=200, now=now)

            self.assertEqual(f.read_text().splitlines(), ["line 5", "line 6", "line 7"])
            kept = Path(tmp) / "run.jsonl.2026-11-03"
            self.assertEqual(kept.read_text().splitlines(), [f"line {i}" for i in range(1, 5)])
            self.assertEqual(result["rules"][0]["acted"], 1)
            self.assertEqual(result["rules"][0]["errors"], [])

    def test_second_cut_the_same_day_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.jsonl"
            now = time.mktime((2026, 11, 3, 12, 0, 0, 0, 0, -1))
            f.write_text("".join(f"a{i}\n" for i in range(5)))
            janitor.run_rules([self._rule(f)], apply=True, max_delete=200, now=now)
            f.write_text(f.read_text() + "".join(f"b{i}\n" for i in range(3)))
            janitor.run_rules([self._rule(f)], apply=True, max_delete=200, now=now)

            kept = (Path(tmp) / "run.jsonl.2026-11-03").read_text().splitlines()
            self.assertEqual(kept, ["a0", "a1", "a2", "a3", "a4"])
            self.assertEqual(f.read_text().splitlines(), ["b0", "b1", "b2"])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.jsonl"
            content = "".join(f"line {i}\n" for i in range(1, 8))
            f.write_text(content)

            rules = [self._rule(f)]
            result = janitor.run_rules(rules, apply=False, max_delete=200, now=time.time())

            self.assertEqual(f.read_text(), content)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["run.jsonl"])
            self.assertEqual(result["rules"][0]["matched"], [str(f)])

    def test_failed_archive_leaves_the_log_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.jsonl"
            content = "".join(f"line {i}\n" for i in range(1, 8))
            f.write_text(content)
            now = time.mktime((2026, 11, 3, 12, 0, 0, 0, 0, -1))
            (Path(tmp) / "run.jsonl.2026-11-03").mkdir()

            result = janitor.run_rules([self._rule(f)], apply=True, max_delete=200, now=now)

            self.assertEqual(f.read_text(), content)
            self.assertEqual(result["rules"][0]["acted"], 0)
            self.assertTrue(result["rules"][0]["errors"])

    def test_without_archive_the_head_is_dropped_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.jsonl"
            f.write_text("".join(f"line {i}\n" for i in range(1, 8)))
            rule = self._rule(f)
            del rule["archive"]
            janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["run.jsonl"])


class KeepNewestNTests(unittest.TestCase):
    def test_keeps_newest_3_of_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            now = time.time()
            files = []
            for i in range(5):
                f = base / f"backup.{i}"
                f.write_text("x")
                _age(f, 5 - i, now)  # backup.4 newest, backup.0 oldest
                files.append(f)

            rule = {
                "name": "r", "path": str(base), "glob": "backup.*",
                "action": "keep_newest_n", "keep": 3,
            }
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=now)

            for f in files[2:]:
                self.assertTrue(f.exists(), f"{f} should survive")
            for f in files[:2]:
                self.assertFalse(f.exists(), f"{f} should be gone")
            self.assertEqual(result["rules"][0]["acted"], 2)


class ReportTests(unittest.TestCase):
    def test_apply_true_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            f = base / "a.bak"
            f.write_text("x")

            rule = {"name": "r", "path": str(base), "glob": "*.bak", "action": "report"}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertTrue(f.exists())
            self.assertEqual(result["rules"][0]["acted"], 0)
            self.assertIn(str(f), result["rules"][0]["matched"])


class CommandTests(unittest.TestCase):
    def test_apply_false_marker_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker"
            rule = {"name": "r", "action": "command", "command": f"touch {marker}"}
            result = janitor.run_rules([rule], apply=False, max_delete=200, now=time.time())
            self.assertFalse(marker.exists())
            self.assertIsNone(result["rules"][0]["output"])

    def test_apply_true_marker_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker"
            rule = {"name": "r", "action": "command", "command": f"touch {marker}"}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertTrue(marker.exists())
            self.assertEqual(result["rules"][0]["errors"], [])

    def test_nonexistent_binary_records_error_no_exception(self):
        rule = {"name": "r", "action": "command", "command": "nonexistent-binary-xyz"}
        result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
        self.assertTrue(result["rules"][0]["errors"])


class OverflowTests(unittest.TestCase):
    def test_single_rule_overflow_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            for i in range(3):
                (base / f"f{i}.txt").write_text("x")

            rule = {"name": "r", "path": str(base), "glob": "*.txt", "action": "delete"}
            result = janitor.run_rules([rule], apply=True, max_delete=2, now=time.time())
            self.assertTrue(result["rules"][0]["overflow"])
            self.assertEqual(result["rules"][0]["acted"], 0)
            self.assertEqual(len(list(base.glob("*.txt"))), 3)

    def test_cumulative_second_rule_overflows(self):
        with tempfile.TemporaryDirectory() as tmp:
            base1 = Path(tmp) / "base1"
            base2 = Path(tmp) / "base2"
            base1.mkdir()
            base2.mkdir()
            for i in range(2):
                (base1 / f"a{i}.txt").write_text("x")
                (base2 / f"b{i}.txt").write_text("x")

            rule1 = {"name": "r1", "path": str(base1), "glob": "*.txt", "action": "delete"}
            rule2 = {"name": "r2", "path": str(base2), "glob": "*.txt", "action": "delete"}
            result = janitor.run_rules([rule1, rule2], apply=True, max_delete=3, now=time.time())
            self.assertFalse(result["rules"][0]["overflow"])
            self.assertEqual(result["rules"][0]["acted"], 2)
            self.assertTrue(result["rules"][1]["overflow"])
            self.assertEqual(result["rules"][1]["acted"], 0)
            self.assertEqual(result["total_deleted"], 2)


class SafetyTests(unittest.TestCase):
    def test_root_base_skipped(self):
        safe, reason = janitor._safe_base(Path("/"))
        self.assertFalse(safe)
        self.assertIsNotNone(reason)

    def test_home_base_skipped(self):
        safe, reason = janitor._safe_base(Path.home())
        self.assertFalse(safe)
        self.assertIsNotNone(reason)

    def test_symlink_base_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_dir = Path(tmp) / "real"
            real_dir.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real_dir)
            safe, reason = janitor._safe_base(link)
            self.assertFalse(safe)
            self.assertIsNotNone(reason)

    def test_symlink_inside_base_removes_link_not_victim(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            victim = Path(tmp) / "victim.txt"
            victim.write_text("keep me")
            link = base / "link_to_victim"
            link.symlink_to(victim)

            rule = {"name": "r", "path": str(base), "glob": "*", "action": "delete"}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())

            self.assertFalse(link.exists())
            self.assertFalse(link.is_symlink())
            self.assertTrue(victim.exists())
            self.assertEqual(victim.read_text(), "keep me")
            self.assertEqual(result["rules"][0]["acted"], 1)


class WatermarkTests(unittest.TestCase):
    def test_files_count_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"
            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=False)
            self.assertTrue(results[0]["tripped"])
            self.assertEqual(results[0]["status"], "would-append")

    def test_kb_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            (d / "big.json").write_bytes(b"x" * 5000)
            wm = {"name": "auto-memory", "path": str(d), "max_kb": 1, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"
            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=False)
            self.assertTrue(results[0]["tripped"])
            self.assertGreater(results[0]["kb"], 1)

    def test_no_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            (d / "small.json").write_text("x")
            wm = {
                "name": "auto-memory", "path": str(d),
                "max_files": 10, "max_kb": 1000, "area": "memory",
            }
            backlog_path = Path(tmp) / "backlog.md"
            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=False)
            self.assertFalse(results[0]["tripped"])
            self.assertEqual(results[0]["status"], "ok")

    def test_apply_appends_line_and_lints_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"

            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            self.assertEqual(results[0]["status"], "appended")
            text = backlog_path.read_text(encoding="utf-8")
            self.assertIn(f"watermark {d}", text)
            self.assertTrue(backlog.lint(text, ["memory"])["ok"])

    def test_second_run_deduped_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"

            janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            before = backlog_path.read_text(encoding="utf-8")
            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            after = backlog_path.read_text(encoding="utf-8")
            self.assertEqual(results[0]["status"], "deduped")
            self.assertEqual(before, after)

    def test_apply_false_never_touches_backlog_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"

            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=False)
            self.assertEqual(results[0]["status"], "would-append")
            self.assertFalse(backlog_path.exists())


class WatermarkFullPathNeedleTests(unittest.TestCase):
    def test_same_basename_different_parents_both_appended_and_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent_a = Path(tmp) / "a"
            parent_b = Path(tmp) / "b"
            (parent_a / "memory").mkdir(parents=True)
            (parent_b / "memory").mkdir(parents=True)
            for i in range(3):
                (parent_a / "memory" / f"f{i}.json").write_text("x")
                (parent_b / "memory" / f"f{i}.json").write_text("x")
            wm = {
                "name": "auto-memory", "path": str(Path(tmp) / "*" / "memory"),
                "max_files": 2, "area": "memory",
            }
            backlog_path = Path(tmp) / "backlog.md"

            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            self.assertEqual([r["status"] for r in results], ["appended", "appended"])
            text = backlog_path.read_text(encoding="utf-8")
            self.assertIn(f"watermark {parent_a / 'memory'}", text)
            self.assertIn(f"watermark {parent_b / 'memory'}", text)

            results2 = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            self.assertEqual([r["status"] for r in results2], ["deduped", "deduped"])


class WatermarkNestedPathNeedleTests(unittest.TestCase):
    def _tripping_pair(self, tmp: str) -> tuple:
        d = Path(tmp) / "memory"
        d.mkdir()
        index = d / "MEMORY.md"
        index.write_bytes(b"x" * 9000)
        dir_wm = {"name": "auto-memory", "path": str(d), "max_kb": 1, "area": "memory"}
        file_wm = {"name": "memory-index", "path": str(index), "max_kb": 8, "area": "memory"}
        return d, index, dir_wm, file_wm

    def test_open_dir_line_does_not_dedupe_a_file_under_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, index, dir_wm, file_wm = self._tripping_pair(tmp)
            backlog_path = Path(tmp) / "backlog.md"
            janitor.check_watermarks([dir_wm], backlog_path, ["memory"], apply=True)

            results = janitor.check_watermarks([file_wm], backlog_path, ["memory"], apply=True)

            self.assertEqual(results[0]["status"], "appended")
            text = backlog_path.read_text(encoding="utf-8")
            self.assertIn(f"watermark {d} tripped:", text)
            self.assertIn(f"watermark {index} tripped:", text)

    def test_open_file_line_does_not_dedupe_the_directory_holding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, index, dir_wm, file_wm = self._tripping_pair(tmp)
            backlog_path = Path(tmp) / "backlog.md"
            janitor.check_watermarks([file_wm], backlog_path, ["memory"], apply=True)

            results = janitor.check_watermarks([dir_wm], backlog_path, ["memory"], apply=True)

            self.assertEqual(results[0]["status"], "appended")
            text = backlog_path.read_text(encoding="utf-8")
            self.assertIn(f"watermark {index} tripped:", text)
            self.assertIn(f"watermark {d} tripped:", text)


class WatermarkFileStatErrorTests(unittest.TestCase):
    def test_file_that_vanishes_before_the_stat_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            gone = Path(tmp) / "GONE.md"
            gone.write_bytes(b"x" * 9000)
            stays = Path(tmp) / "STAYS.md"
            stays.write_bytes(b"x" * 9000)
            wms = [
                {"name": "gone", "path": str(gone), "max_kb": 8, "area": "memory"},
                {"name": "stays", "path": str(stays), "max_kb": 8, "area": "memory"},
            ]
            backlog_path = Path(tmp) / "backlog.md"
            real_file_watermark = watermarks._file_watermark

            def unlink_then_measure(f, max_kb):
                if f == gone:
                    f.unlink()
                return real_file_watermark(f, max_kb)

            watermarks._file_watermark = unlink_then_measure
            self.addCleanup(setattr, watermarks, "_file_watermark", real_file_watermark)

            results = janitor.check_watermarks(wms, backlog_path, ["memory"], apply=True)

            self.assertEqual([r["name"] for r in results], ["stays"])
            self.assertEqual(results[0]["status"], "appended")


class WatermarkAppendErrorTests(unittest.TestCase):
    def test_missing_open_heading_records_error_status_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"
            backlog_path.write_text("# Backlog\n\nno heading here\n", encoding="utf-8")

            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            self.assertTrue(results[0]["status"].startswith("error:"))


class WatermarkCapsTextTests(unittest.TestCase):
    def test_only_max_kb_set_no_none_in_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            (d / "big.json").write_bytes(b"x" * 5000)
            wm = {"name": "auto-memory", "path": str(d), "max_kb": 1, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"
            janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            text = backlog_path.read_text(encoding="utf-8")
            self.assertNotIn("None", text)
            self.assertIn("caps 1KB", text)

    def test_only_max_files_set_no_none_in_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"
            janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            text = backlog_path.read_text(encoding="utf-8")
            self.assertNotIn("None", text)
            self.assertIn("caps 2 files", text)


class WatermarkFileMatchTests(unittest.TestCase):
    def test_file_over_max_kb_trips_and_reports_size_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "MEMORY.md"
            f.write_bytes(b"x" * 9000)
            wm = {"name": "memory-index", "path": str(f), "max_kb": 8, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"

            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)

            self.assertTrue(results[0]["tripped"])
            self.assertEqual(results[0]["status"], "appended")
            self.assertEqual(results[0]["files"], 1)
            text = backlog_path.read_text(encoding="utf-8")
            self.assertIn(f"watermark {f} tripped: 9KB (caps 8KB)", text)
            self.assertNotIn("files", text)
            self.assertTrue(backlog.lint(text, ["memory"])["ok"])

    def test_file_under_max_kb_does_not_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "MEMORY.md"
            f.write_bytes(b"x" * 200)
            wm = {"name": "memory-index", "path": str(f), "max_kb": 8, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"

            results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)

            self.assertFalse(results[0]["tripped"])
            self.assertEqual(results[0]["status"], "ok")
            self.assertFalse(backlog_path.exists())

    def test_max_files_on_a_file_warns_once_and_still_judges_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "MEMORY.md"
            f.write_bytes(b"x" * 9000)
            wm = {
                "name": "memory-index", "path": str(f),
                "max_files": 2, "max_kb": 8, "area": "memory",
            }
            backlog_path = Path(tmp) / "backlog.md"

            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)

            warning = "warning: watermark memory-index: max_files ignored for file matches"
            self.assertEqual(buf.getvalue().count(warning), 1)
            self.assertTrue(results[0]["tripped"])
            text = backlog_path.read_text(encoding="utf-8")
            self.assertIn(f"watermark {f} tripped: 9KB (caps 8KB)", text)
            self.assertNotIn("2 files", text)

    def test_max_files_warns_once_per_watermark_not_once_per_matched_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a", "b", "c"):
                (Path(tmp) / f"{name}.md").write_bytes(b"x" * 9000)
            wm = {
                "name": "memory-index", "path": str(Path(tmp) / "*.md"),
                "max_files": 2, "max_kb": 8, "area": "memory",
            }
            backlog_path = Path(tmp) / "backlog.md"

            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)

            self.assertEqual(len(results), 3)
            self.assertTrue(all(r["tripped"] for r in results))
            self.assertEqual(buf.getvalue().count("max_files ignored"), 1)


class DirWeightCapTests(unittest.TestCase):
    def _make_dir_with_files(self, base: Path, n: int) -> Path:
        base.mkdir()
        d = base / "bigdir"
        d.mkdir()
        for i in range(n):
            (d / f"f{i}.txt").write_text("x")
        return d

    def test_dir_with_20_files_overflows_at_cap_10(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            d = self._make_dir_with_files(base, 20)
            rule = {"name": "r", "path": str(base), "glob": "*", "dirs": True, "action": "delete"}
            result = janitor.run_rules([rule], apply=True, max_delete=10, now=time.time())
            self.assertTrue(result["rules"][0]["overflow"])
            self.assertEqual(result["rules"][0]["acted"], 0)
            self.assertTrue(d.exists())

    def test_dir_with_20_files_deletes_at_cap_25(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            d = self._make_dir_with_files(base, 20)
            rule = {"name": "r", "path": str(base), "glob": "*", "dirs": True, "action": "delete"}
            result = janitor.run_rules([rule], apply=True, max_delete=25, now=time.time())
            self.assertFalse(result["rules"][0]["overflow"])
            self.assertEqual(result["rules"][0]["acted"], 1)
            self.assertFalse(d.exists())


class WeightCreditCapTests(unittest.TestCase):
    def test_two_190_file_dirs_second_rule_overflows_by_weight(self):
        with tempfile.TemporaryDirectory() as tmp:
            base1 = Path(tmp) / "base1"
            base2 = Path(tmp) / "base2"
            d1 = base1 / "bigdir"
            d2 = base2 / "bigdir"
            base1.mkdir()
            base2.mkdir()
            d1.mkdir()
            d2.mkdir()
            for i in range(190):
                (d1 / f"f{i}.txt").write_text("x")
                (d2 / f"f{i}.txt").write_text("x")

            rule1 = {
                "name": "r1", "path": str(base1), "glob": "*", "dirs": True, "action": "delete",
            }
            rule2 = {
                "name": "r2", "path": str(base2), "glob": "*", "dirs": True, "action": "delete",
            }
            result = janitor.run_rules([rule1, rule2], apply=True, max_delete=200, now=time.time())

            self.assertFalse(result["rules"][0]["overflow"])
            self.assertEqual(result["rules"][0]["acted"], 1)
            self.assertFalse(d1.exists())

            self.assertTrue(result["rules"][1]["overflow"])
            self.assertEqual(result["rules"][1]["acted"], 0)
            self.assertTrue(d2.exists())


class DirWeightIncludesEmptySubdirsTests(unittest.TestCase):
    def test_dir_with_500_empty_subdirs_overflows_cap_200(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            d = base / "bigdir"
            d.mkdir()
            for i in range(500):
                (d / f"sub{i}").mkdir()

            rule = {"name": "r", "path": str(base), "glob": "*", "dirs": True, "action": "delete"}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertTrue(result["rules"][0]["overflow"])
            self.assertEqual(result["rules"][0]["acted"], 0)
            self.assertTrue(d.exists())


class WatermarkAppendOSErrorTests(unittest.TestCase):
    def test_chmod_oserror_records_error_status_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"

            orig_chmod = os.chmod

            def raising_chmod(*args, **kwargs):
                raise OSError("no chmod for you")

            backlog.os.chmod = raising_chmod
            try:
                results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            finally:
                backlog.os.chmod = orig_chmod
            self.assertTrue(results[0]["status"].startswith("error:"))


class WatermarkUnreadableBacklogTests(unittest.TestCase):
    def test_unreadable_backlog_records_error_status_no_raise(self):
        if os.geteuid() == 0:
            self.skipTest("mode bits do not restrict root")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "memory"
            d.mkdir()
            for i in range(3):
                (d / f"f{i}.json").write_text("x")
            wm = {"name": "auto-memory", "path": str(d), "max_files": 2, "area": "memory"}
            backlog_path = Path(tmp) / "backlog.md"
            backlog_path.write_text("# Backlog\n\n## Open\n\n## Done\n")
            os.chmod(backlog_path, 0)
            try:
                results = janitor.check_watermarks([wm], backlog_path, ["memory"], apply=True)
            finally:
                os.chmod(backlog_path, 0o644)
            self.assertTrue(results[0]["status"].startswith("error:"))


class DeleteDirsParentChildTests(unittest.TestCase):
    def test_parent_deleted_first_child_skipped_without_bogus_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            (base / "keep-2026" / "keep-child").mkdir(parents=True)
            rule = {
                "name": "r", "path": str(base), "glob": "keep-*",
                "action": "delete", "dirs": True,
            }
            report = janitor.run_rules([rule], apply=True, max_delete=200)
            result = report["rules"][0]
            self.assertEqual(result["errors"], [])
            self.assertFalse((base / "keep-2026").exists())


class MaxDeleteNoneGuardTests(unittest.TestCase):
    def test_none_max_delete_behaves_as_200(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            for i in range(5):
                (base / f"f{i}.txt").write_text("x")
            rule = {"name": "r", "path": str(base), "glob": "*.txt", "action": "delete"}
            result = janitor.run_rules([rule], apply=True, max_delete=None, now=time.time())
            self.assertFalse(result["rules"][0]["overflow"])
            self.assertEqual(result["rules"][0]["acted"], 5)


class BaseIsFileSkipTests(unittest.TestCase):
    def test_delete_rule_base_file_skipped_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "notadir.txt"
            f.write_text("x")
            rule = {"name": "r", "path": str(f), "action": "delete"}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertEqual(result["rules"][0]["skipped"], "base is a file (needs a directory)")
            self.assertTrue(f.exists())

    def test_keep_newest_n_rule_base_file_skipped_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "notadir.txt"
            f.write_text("x")
            rule = {"name": "r", "path": str(f), "action": "keep_newest_n", "keep": 1}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertEqual(result["rules"][0]["skipped"], "base is a file (needs a directory)")


class TruncateKeepLinesGuardTests(unittest.TestCase):
    def test_keep_lines_zero_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.log"
            f.write_text("line\n" * 10)
            rule = {"name": "r", "path": str(f), "action": "truncate", "keep_lines": 0}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertEqual(result["rules"][0]["skipped"], "keep_lines must be positive")
            self.assertEqual(f.read_text(), "line\n" * 10)

    def test_keep_lines_negative_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.log"
            f.write_text("line\n" * 10)
            rule = {"name": "r", "path": str(f), "action": "truncate", "keep_lines": -1}
            result = janitor.run_rules([rule], apply=True, max_delete=200, now=time.time())
            self.assertEqual(result["rules"][0]["skipped"], "keep_lines must be positive")


class WatermarkRealmRoutingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.transcripts = self.tmp / "transcripts"
        self.default_backlog = self.tmp / "backlog.md"
        self.realm_dir = self.tmp / "brain" / "rig"
        (self.realm_dir / "plans").mkdir(parents=True)
        self.realm_backlog = self.realm_dir / "plans" / "backlog.md"
        self.realm_backlog.write_text("# Backlog\n\n## Open\n\n## Done\n")
        (self.tmp / "mnt" / ".claude").mkdir(parents=True)
        (self.tmp / "mnt" / ".claude" / "realm").symlink_to(self.realm_dir)
        (self.tmp / "rogue" / ".claude").mkdir(parents=True)
        (self.tmp / "rogue" / ".claude" / "realm").symlink_to(self.realm_dir)
        (self.tmp / "nolink").mkdir()
        self.registry_file = self.tmp / "roots.json"
        self.set_roots([
            {"path": str(self.tmp / "mnt"), "realm": "client"},
            {"path": f"{self.tmp}/nolink/*", "realm": "client"},
            {"path": str(self.tmp / "plain"), "realm": "personal"},
        ])
        patcher = mock.patch.dict(os.environ, {"REALM_REGISTRY": str(self.registry_file)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def set_roots(self, entries) -> None:
        self.registry_file.write_text(json.dumps({"roots": entries}))

    def slug(self, *parts) -> str:
        return roots.encode_project(self.tmp.joinpath(*parts)) + "-repo"

    def _project(self, name: str, cwd, lines=None) -> Path:
        proj = self.transcripts / name
        memory = proj / "memory"
        memory.mkdir(parents=True)
        for i in range(3):
            (memory / f"f{i}.md").write_text("x")
        records = lines if lines is not None else [{"type": "user", "cwd": str(cwd)}]
        (proj / "s.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return memory

    def _check(self, apply=True, transcripts_dir="unset"):
        wm = {"name": "auto-memory", "path": str(self.transcripts / "*" / "memory"),
              "max_files": 2, "area": "memory"}
        tdir = self.transcripts if transcripts_dir == "unset" else transcripts_dir
        return janitor.check_watermarks(
            [wm], self.default_backlog, ["memory"], apply=apply, transcripts_dir=tdir,
        )

    def test_personal_session_goes_to_the_configured_backlog(self):
        memory = self._project("p-personal", self.tmp / "plain" / "repo")
        results = self._check()
        self.assertEqual(results[0]["status"], "appended")
        self.assertEqual(results[0]["backlog"], str(self.default_backlog))
        self.assertIn(f"watermark {memory} tripped:", self.default_backlog.read_text())
        self.assertNotIn("watermark", self.realm_backlog.read_text())

    def test_client_session_goes_to_the_realm_backlog(self):
        memory = self._project("p-client", self.tmp / "mnt" / "repo")
        results = self._check()
        self.assertEqual(results[0]["status"], "appended")
        self.assertEqual(results[0]["backlog"], str(self.realm_backlog))
        self.assertIn(f"watermark {memory} tripped:", self.realm_backlog.read_text())
        self.assertFalse(self.default_backlog.exists())

    def test_each_target_dedupes_on_its_own_file(self):
        self._project("p-client", self.tmp / "mnt" / "repo")
        self._project("p-personal", self.tmp / "plain")
        self.assertEqual([r["status"] for r in self._check()], ["appended", "appended"])
        self.assertEqual([r["status"] for r in self._check()], ["deduped", "deduped"])
        self.assertEqual(self.realm_backlog.read_text().count("tripped:"), 1)
        self.assertEqual(self.default_backlog.read_text().count("tripped:"), 1)

    def test_dry_run_reports_the_target_without_writing(self):
        self._project("p-client", self.tmp / "mnt" / "repo")
        results = self._check(apply=False)
        self.assertEqual(results[0]["status"], "would-append")
        self.assertEqual(results[0]["backlog"], str(self.realm_backlog))
        self.assertNotIn("tripped:", self.realm_backlog.read_text())

    def test_client_session_without_a_realm_backlog_is_unrouted_not_personal(self):
        self._project("p-client", self.tmp / "mnt" / "repo")
        self.realm_backlog.unlink()
        results = self._check()
        self.assertEqual(results[0]["status"], "unrouted")
        self.assertIsNone(results[0]["backlog"])
        self.assertFalse(self.default_backlog.exists())
        self.assertFalse(self.realm_backlog.exists())

    def test_dry_run_reports_a_client_dir_without_a_backlog_as_unrouted(self):
        self._project("p-client", self.tmp / "mnt" / "repo")
        self.realm_backlog.unlink()
        results = self._check(apply=False)
        self.assertEqual(results[0]["status"], "unrouted")
        self.assertIsNone(results[0]["backlog"])

    def test_unreadable_registry_leaves_a_personal_dir_unrouted(self):
        self._project("p-personal", self.tmp / "plain" / "repo")
        self.registry_file.write_text("{not json")
        for apply in (True, False):
            results = self._check(apply=apply)
            self.assertEqual(results[0]["status"], "unrouted")
            self.assertIsNone(results[0]["backlog"])
        self.assertFalse(self.default_backlog.exists())

    def test_missing_registry_leaves_a_personal_dir_unrouted(self):
        self._project("p-personal", self.tmp / "plain" / "repo")
        self.registry_file.unlink()
        results = self._check()
        self.assertEqual(results[0]["status"], "unrouted")
        self.assertIsNone(results[0]["backlog"])
        self.assertFalse(self.default_backlog.exists())

    def test_missing_registry_does_not_route_a_client_slug_to_the_default_backlog(self):
        self._project(self.slug("mnt"), None)
        self.registry_file.unlink()
        for apply in (True, False):
            self.assertEqual(self._check(apply=apply)[0]["status"], "unrouted")
        self.assertFalse(self.default_backlog.exists())

    def test_slug_prefix_without_a_dash_boundary_is_not_a_client_dir(self):
        slug = roots.encode_project(self.tmp / "mnt")
        self._project(f"{slug}ish-repo", self.tmp / "plain" / "repo")
        results = self._check()
        self.assertEqual(results[0]["backlog"], str(self.default_backlog))

    def test_dry_run_reports_a_backlog_lookup_failure_as_a_status(self):
        self._project("p-personal", self.tmp / "plain" / "repo")
        with mock.patch.object(watermarks, "_backlog_for",
                               side_effect=PermissionError("denied")):
            results = self._check(apply=False)
        self.assertTrue(results[0]["status"].startswith("error:"))

    def test_session_cwd_read_is_bounded_per_line(self):
        proj = self.transcripts / "p-big"
        proj.mkdir(parents=True)
        (proj / "s.jsonl").write_text(
            "x" * (3 << 20) + "\n" + json.dumps({"cwd": "/a"}) + "\n")
        with unittest.mock.patch.object(watermarks, "CWD_SCAN_LINES", 2):
            self.assertIsNone(watermarks._session_cwd(proj))

    def _run_script(self) -> subprocess.CompletedProcess:
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({
            "transcripts_dir": str(self.transcripts),
            "backlog": {"path": str(self.default_backlog)},
            "janitor": {"watermarks": [{
                "name": "auto-memory", "path": str(self.transcripts / "*" / "memory"),
                "max_files": 2, "area": "memory",
            }]},
        }))
        script = Path(__file__).resolve().parent.parent / "libexec" / "rigops-janitor"
        env = {**os.environ, "RIGOPS_CONFIG": str(cfg)}
        return subprocess.run([sys.executable, str(script)], env=env,
                              capture_output=True, text=True, check=False)

    def test_script_fails_on_an_unrouted_watermark_and_keeps_the_line(self):
        self._project("p-client", self.tmp / "mnt" / "repo")
        self.realm_backlog.unlink()
        proc = self._run_script()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("files=3", proc.stdout)
        self.assertIn("unrouted", proc.stdout)

    def test_script_passes_on_a_routed_watermark(self):
        self._project("p-client", self.tmp / "mnt" / "repo")
        proc = self._run_script()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("would-append", proc.stdout)

    def test_project_named_for_a_client_root_goes_to_the_realm_backlog(self):
        memory = self._project(self.slug("mnt"), self.tmp / "plain" / "repo")
        results = self._check()
        self.assertEqual(results[0]["status"], "appended")
        self.assertEqual(results[0]["backlog"], str(self.realm_backlog))
        self.assertIn(f"watermark {memory} tripped:", self.realm_backlog.read_text())
        self.assertFalse(self.default_backlog.exists())

    def test_project_named_for_a_client_root_routes_without_any_transcript(self):
        memory = self._project(self.slug("mnt"), None)
        (memory.parent / "s.jsonl").unlink()
        results = self._check()
        self.assertEqual(results[0]["backlog"], str(self.realm_backlog))

    def test_name_routing_matches_the_harness_slug_of_a_dotted_underscored_path(self):
        odd = self.tmp / "my_shared.d"
        odd.mkdir()
        (odd / ".claude").mkdir()
        (odd / ".claude" / "realm").symlink_to(self.realm_dir)
        self.set_roots([{"path": str(odd), "realm": "client"}])
        name = str(odd).replace("/", "-").replace(".", "-").replace("_", "-") + "-repo"
        self._project(name, None)
        self.assertEqual(self._check()[0]["backlog"], str(self.realm_backlog))

    def test_name_match_without_a_realm_backlog_is_unrouted(self):
        self._project(self.slug("mnt"), self.tmp / "plain")
        self.realm_backlog.unlink()
        results = self._check()
        self.assertEqual(results[0]["status"], "unrouted")
        self.assertFalse(self.default_backlog.exists())

    def test_name_match_on_a_root_without_a_realm_is_unrouted(self):
        self._project(self.slug("nolink"), self.tmp / "plain")
        results = self._check()
        self.assertEqual(results[0]["status"], "unrouted")
        self.assertIsNone(results[0]["backlog"])
        self.assertFalse(self.default_backlog.exists())
        self.assertNotIn("tripped:", self.realm_backlog.read_text())

    def test_name_match_beats_a_personal_cwd(self):
        self._project(self.slug("mnt"), self.tmp / "plain" / "repo")
        self._project(self.slug("nolink"), self.tmp / "plain" / "repo")
        self.assertEqual([r["status"] for r in self._check()], ["appended", "unrouted"])
        self.assertFalse(self.default_backlog.exists())

    def test_name_of_a_personal_root_does_not_route_to_a_realm(self):
        self._project(self.slug("plain"), self.tmp / "plain")
        results = self._check()
        self.assertEqual(results[0]["backlog"], str(self.default_backlog))

    def test_cwd_under_an_untrusted_link_stays_personal(self):
        self._project("p-rogue", self.tmp / "rogue" / "repo")
        results = self._check()
        self.assertEqual(results[0]["backlog"], str(self.default_backlog))
        self.assertNotIn("tripped:", self.realm_backlog.read_text())

    def test_name_match_wins_over_an_untrusted_cwd_link(self):
        self._project(self.slug("mnt"), self.tmp / "rogue" / "repo")
        self.assertEqual(self._check()[0]["backlog"], str(self.realm_backlog))

    def test_cwd_is_read_from_the_newest_transcript(self):
        memory = self._project("p-mixed", self.tmp / "plain")
        old = memory.parent / "s.jsonl"
        new = memory.parent / "t.jsonl"
        new.write_text(json.dumps({"cwd": str(self.tmp / "mnt" / "repo")}) + "\n")
        os.utime(old, (1, 1))
        results = self._check()
        self.assertEqual(results[0]["backlog"], str(self.realm_backlog))

    def test_cwd_scan_skips_junk_and_stops_after_fifty_lines(self):
        junk = [{"type": "x"}] * 48
        reachable = self._project("p-a", None, lines=[*junk, {"cwd": str(self.tmp / "mnt")}])
        unreachable = self._project("p-b", None, lines=[*junk, {"type": "y"}, {"type": "y"},
                                                        {"cwd": str(self.tmp / "mnt")}])
        (reachable.parent / "s.jsonl").write_text(
            "not json\n" + (reachable.parent / "s.jsonl").read_text()
        )
        by_dir = {r["dir"]: r["backlog"] for r in self._check()}
        self.assertEqual(by_dir[str(reachable)], str(self.realm_backlog))
        self.assertEqual(by_dir[str(unreachable)], str(self.default_backlog))

    def test_match_outside_the_transcripts_dir_uses_its_own_path(self):
        elsewhere = self.tmp / "mnt" / "repo" / "memory"
        elsewhere.mkdir(parents=True)
        for i in range(3):
            (elsewhere / f"f{i}.md").write_text("x")
        wm = {"name": "m", "path": str(elsewhere), "max_files": 2, "area": "memory"}
        results = janitor.check_watermarks(
            [wm], self.default_backlog, ["memory"], apply=True, transcripts_dir=self.transcripts,
        )
        self.assertEqual(results[0]["backlog"], str(self.realm_backlog))

    def test_project_without_a_transcript_falls_back_to_the_configured_backlog(self):
        memory = self._project("p-empty", None)
        (memory.parent / "s.jsonl").unlink()
        results = self._check()
        self.assertEqual(results[0]["backlog"], str(self.default_backlog))

    def test_no_transcripts_dir_keeps_the_configured_backlog(self):
        self._project("p-client", self.tmp / "mnt" / "repo")
        results = self._check(transcripts_dir=None)
        self.assertEqual(results[0]["backlog"], str(self.default_backlog))

    def test_realm_backlog_failure_is_reported_not_raised(self):
        if os.geteuid() == 0:
            self.skipTest("mode bits do not restrict root")
        self._project("p-client", self.tmp / "mnt" / "repo")
        os.chmod(self.realm_backlog, 0)
        try:
            results = self._check()
        finally:
            os.chmod(self.realm_backlog, 0o644)
        self.assertTrue(results[0]["status"].startswith("error:"))


if __name__ == "__main__":
    unittest.main()
