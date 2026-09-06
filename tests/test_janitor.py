from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import backlog, janitor  # noqa: E402


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

            warning = f"warning: watermark memory-index: max_files ignored for file {f}"
            self.assertEqual(buf.getvalue().count(warning), 1)
            self.assertTrue(results[0]["tripped"])
            text = backlog_path.read_text(encoding="utf-8")
            self.assertIn(f"watermark {f} tripped: 9KB (caps 8KB)", text)
            self.assertNotIn("2 files", text)


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


if __name__ == "__main__":
    unittest.main()
