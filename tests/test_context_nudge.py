from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core  # noqa: E402
from rigops.context import nudge, util  # noqa: E402


def _write_nudges(tmp: Path, data: dict, name: str = "nudges.json") -> Path:
    path = tmp / name
    path.write_text(json.dumps(data))
    return path


# The host's own doctor record and nudge state must never leak into these tests.
_STATE_HOME = tempfile.TemporaryDirectory()
_STATE_ENV = mock.patch.dict(os.environ, {"XDG_STATE_HOME": _STATE_HOME.name})


def setUpModule():
    _STATE_ENV.start()


def tearDownModule():
    _STATE_ENV.stop()
    _STATE_HOME.cleanup()


class IsReplayTests(unittest.TestCase):
    def test_task_notification_marker(self):
        self.assertTrue(nudge.is_replay("<task-notification>...</task-notification>"))

    def test_system_notification_marker(self):
        self.assertTrue(nudge.is_replay("[SYSTEM NOTIFICATION] something happened"))

    def test_system_reminder_marker(self):
        self.assertTrue(nudge.is_replay("<system-reminder>hi</system-reminder>"))

    def test_ordinary_prompt_is_not_a_replay(self):
        self.assertFalse(nudge.is_replay("please open a PR for this"))

    def test_empty_prompt_is_not_a_replay(self):
        self.assertFalse(nudge.is_replay(""))
        self.assertFalse(nudge.is_replay(None))


class DeclarationsPathTests(unittest.TestCase):
    def test_default_is_beside_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path = tmp / "roots.json"
            self.assertEqual(nudge.declarations_path({}, registry_path),
                              core.expand(tmp / "nudges.json"))

    def test_configured_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry_path = tmp / "roots.json"
            cfg = {"context": {"nudges_path": str(tmp / "elsewhere.json")}}
            self.assertEqual(nudge.declarations_path(cfg, registry_path),
                              core.expand(tmp / "elsewhere.json"))


class LoadDeclarationsTests(unittest.TestCase):
    def test_missing_file_means_no_nudges_default_budget_and_repeat(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            nudges, budget, repeat_after = nudge.load_declarations({}, tmp / "roots.json")
        self.assertEqual(nudges, [])
        self.assertEqual(budget, nudge.DEFAULT_BUDGET)
        self.assertEqual(repeat_after, nudge.DEFAULT_REPEAT_AFTER)

    def test_malformed_json_is_treated_as_no_file(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            (tmp / "nudges.json").write_text("{not json")
            with contextlib.redirect_stderr(io.StringIO()):
                nudges, budget, repeat_after = nudge.load_declarations({}, tmp / "roots.json")
        self.assertEqual(nudges, [])
        self.assertEqual(budget, nudge.DEFAULT_BUDGET)

    def test_entry_missing_a_required_field_is_skipped_with_one_stderr_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            _write_nudges(tmp, {"nudges": [{"name": "no-say", "pattern": "x"}]})
            with contextlib.redirect_stderr(io.StringIO()) as err:
                nudges, _, _ = nudge.load_declarations({}, tmp / "roots.json")
        self.assertEqual(nudges, [])
        self.assertIn("no-say", err.getvalue())

    def test_pattern_that_does_not_compile_is_skipped_with_one_stderr_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            _write_nudges(tmp, {"nudges": [
                {"name": "bad", "pattern": "(unclosed", "say": "hi"},
            ]})
            with contextlib.redirect_stderr(io.StringIO()) as err:
                nudges, _, _ = nudge.load_declarations({}, tmp / "roots.json")
        self.assertEqual(nudges, [])
        self.assertIn("bad", err.getvalue())

    def test_budget_and_repeat_after_come_from_the_file(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            _write_nudges(tmp, {"budget": 150, "repeat_after": 5, "nudges": []})
            _, budget, repeat_after = nudge.load_declarations({}, tmp / "roots.json")
        self.assertEqual(budget, 150)
        self.assertEqual(repeat_after, 5)

    def test_valid_entry_compiles_with_its_flags(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            _write_nudges(tmp, {"nudges": [
                {"name": "pr", "pattern": "open a pr", "flags": "i", "say": "careful with PRs"},
            ]})
            nudges, _, _ = nudge.load_declarations({}, tmp / "roots.json")
        self.assertEqual(len(nudges), 1)
        self.assertTrue(nudges[0]["regex"].search("please OPEN A PR now"))


class MatchTests(unittest.TestCase):
    def _entry(self, name, pattern, say, realms=None, flags=""):
        import re
        rx = re.compile(pattern, re.IGNORECASE if "i" in flags else 0)
        return {"name": name, "regex": rx, "say": say, "realms": realms}

    def test_matching_pattern_fires(self):
        entries = [self._entry("pr", "open a pr", "careful")]
        hits = nudge.match(entries, "please open a pr today", None)
        self.assertEqual([h["name"] for h in hits], ["pr"])

    def test_non_matching_pattern_does_not_fire(self):
        entries = [self._entry("pr", "open a pr", "careful")]
        hits = nudge.match(entries, "just chatting", None)
        self.assertEqual(hits, [])

    def test_realm_scoped_entry_fires_only_in_that_realm(self):
        entries = [self._entry("pr", "pr", "careful", realms=["work"])]
        self.assertEqual(len(nudge.match(entries, "pr", "work")), 1)
        self.assertEqual(len(nudge.match(entries, "pr", "personal")), 0)

    def test_unmapped_cwd_only_fires_entries_without_realms(self):
        scoped = self._entry("scoped", "pr", "a", realms=["work"])
        unscoped = self._entry("unscoped", "pr", "b")
        hits = nudge.match([scoped, unscoped], "pr", None)
        self.assertEqual([h["name"] for h in hits], ["unscoped"])

    def test_dedupes_identical_say_text_keeping_the_first(self):
        entries = [
            self._entry("a", "pr", "same text"),
            self._entry("b", "pr", "same text"),
        ]
        hits = nudge.match(entries, "pr", None)
        self.assertEqual([h["name"] for h in hits], ["a"])

    def test_declaration_order_is_preserved(self):
        entries = [self._entry("second", "pr", "s"), self._entry("first", "pr", "f")]
        hits = nudge.match(entries, "pr", None)
        self.assertEqual([h["name"] for h in hits], ["second", "first"])


class RepeatSuppressionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = dict(os.environ)
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def test_without_a_session_id_everything_fires_and_nothing_is_persisted(self):
        matched = [{"name": "a"}, {"name": "b"}]
        firing, suppressed = nudge.apply_repeat_suppression(None, matched, 20)
        self.assertEqual(firing, matched)
        self.assertEqual(suppressed, [])
        self.assertFalse((self.tmp / "xdg" / "rigops" / "nudge").exists())

    def test_first_fire_is_recorded_at_prompt_index_one(self):
        firing, suppressed = nudge.apply_repeat_suppression("sess-1", [{"name": "a"}], 20)
        self.assertEqual([e["name"] for e in firing], ["a"])
        self.assertEqual(suppressed, [])
        state = json.loads((self.tmp / "xdg" / "rigops" / "nudge" / "sess-1.json").read_text())
        self.assertEqual(state, {"prompts": 1, "fired": {"a": 1}})

    def test_second_call_within_repeat_after_window_is_suppressed(self):
        nudge.apply_repeat_suppression("sess-1", [{"name": "a"}], 20)
        firing, suppressed = nudge.apply_repeat_suppression("sess-1", [{"name": "a"}], 20)
        self.assertEqual(firing, [])
        self.assertEqual(suppressed, ["a"])
        state = json.loads((self.tmp / "xdg" / "rigops" / "nudge" / "sess-1.json").read_text())
        self.assertEqual(state, {"prompts": 2, "fired": {"a": 1}})

    def test_fires_again_once_repeat_after_prompts_have_passed(self):
        nudge.apply_repeat_suppression("sess-1", [{"name": "a"}], 3)
        nudge.apply_repeat_suppression("sess-1", [], 3)
        nudge.apply_repeat_suppression("sess-1", [], 3)
        firing, suppressed = nudge.apply_repeat_suppression("sess-1", [{"name": "a"}], 3)
        self.assertEqual([e["name"] for e in firing], ["a"])
        self.assertEqual(suppressed, [])

    def test_every_call_increments_prompts_even_with_no_match(self):
        nudge.apply_repeat_suppression("sess-1", [], 20)
        nudge.apply_repeat_suppression("sess-1", [], 20)
        state = json.loads((self.tmp / "xdg" / "rigops" / "nudge" / "sess-1.json").read_text())
        self.assertEqual(state["prompts"], 2)

    def test_session_id_is_sanitized_for_the_filename(self):
        nudge.apply_repeat_suppression("weird/../id with spaces", [{"name": "a"}], 20)
        state_dir = self.tmp / "xdg" / "rigops" / "nudge"
        names = [p.name for p in state_dir.iterdir()]
        self.assertEqual(len(names), 1)
        self.assertNotIn("/", names[0])
        self.assertNotIn(" ", names[0])

    def test_prune_removes_state_files_older_than_seven_days(self):
        nudge.apply_repeat_suppression("old-sess", [{"name": "a"}], 20)
        state_dir = self.tmp / "xdg" / "rigops" / "nudge"
        old_file = state_dir / "old-sess.json"
        old_mtime = time.time() - 8 * 86400
        os.utime(old_file, (old_mtime, old_mtime))

        nudge.apply_repeat_suppression("new-sess", [{"name": "a"}], 20)

        names = {p.name for p in state_dir.iterdir()}
        self.assertNotIn("old-sess.json", names)
        self.assertIn("new-sess.json", names)


class SelectWithinBudgetTests(unittest.TestCase):
    def test_keeps_first_entry_even_when_it_alone_exceeds_budget(self):
        entries = [{"name": "a", "say": "x" * 400}]
        kept, tokens = nudge.select_within_budget(entries, 10)
        self.assertEqual(kept, entries)
        self.assertEqual(tokens, 100)

    def test_drops_trailing_entries_that_would_exceed_budget(self):
        entries = [
            {"name": "a", "say": "x" * 40}, {"name": "b", "say": "x" * 40},
            {"name": "c", "say": "x" * 40},
        ]
        kept, tokens = nudge.select_within_budget(entries, 15)
        self.assertEqual([e["name"] for e in kept], ["a"])
        self.assertEqual(tokens, 10)

    def test_everything_fits_when_budget_is_generous(self):
        entries = [{"name": "a", "say": "short"}, {"name": "b", "say": "also short"}]
        kept, _ = nudge.select_within_budget(entries, 400)
        self.assertEqual(kept, entries)

    def test_cost_is_utf8_bytes_over_four_not_characters(self):
        entries = [{"name": "a", "say": "€" * 40}, {"name": "b", "say": "€" * 40}]
        kept, tokens = nudge.select_within_budget(entries, 45)
        self.assertEqual([e["name"] for e in kept], ["a"])
        self.assertEqual(tokens, 30)


class RenderOutputTests(unittest.TestCase):
    def test_say_lines_then_claims_block(self):
        out = nudge.render_output(["say one", "say two"], "claim block", 400)
        self.assertEqual(out, "say one\nsay two\nclaim block")

    def test_nothing_matched_and_no_hits_means_empty_string(self):
        self.assertEqual(nudge.render_output([], "", 400), "")

    def test_say_lines_only_when_no_claims(self):
        self.assertEqual(nudge.render_output(["say one"], "", 400), "say one")

    def test_final_output_is_truncated_to_budget_times_four_chars(self):
        out = nudge.render_output(["x" * 500], "", 10)
        self.assertEqual(len(out), 40)

    def test_truncation_counts_utf8_bytes_and_never_splits_a_character(self):
        out = nudge.render_output(["€" * 500], "", 10)
        self.assertEqual(out, "€" * 13)


class PlanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = dict(os.environ)
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def test_missing_nudges_file_gives_the_full_budget_to_prefetch(self):
        result = nudge.plan({}, self.tmp / "roots.json", "open a pr", "work", None)
        self.assertEqual(result.say, [])
        self.assertEqual(result.budget, nudge.DEFAULT_BUDGET)
        self.assertEqual(result.remaining, nudge.DEFAULT_BUDGET)

    def test_cli_budget_overrides_file_budget(self):
        _write_nudges(self.tmp, {"budget": 300, "nudges": []})
        result = nudge.plan({}, self.tmp / "roots.json", "hi", "work", None, budget_override=50)
        self.assertEqual(result.budget, 50)

    def test_file_budget_wins_over_the_400_fallback(self):
        _write_nudges(self.tmp, {"budget": 300, "nudges": []})
        result = nudge.plan({}, self.tmp / "roots.json", "hi", "work", None)
        self.assertEqual(result.budget, 300)

    def test_end_to_end_fire_then_suppress_then_refire(self):
        _write_nudges(self.tmp, {"repeat_after": 2, "nudges": [
            {"name": "pr", "pattern": "pr", "say": "careful with PRs"},
        ]})
        first = nudge.plan({}, self.tmp / "roots.json", "open a pr", "work", "sess-x")
        self.assertEqual(first.fired, ["pr"])
        second = nudge.plan({}, self.tmp / "roots.json", "open a pr", "work", "sess-x")
        self.assertEqual(second.fired, [])
        self.assertEqual(second.suppressed, ["pr"])
        third = nudge.plan({}, self.tmp / "roots.json", "open a pr", "work", "sess-x")
        self.assertEqual(third.fired, ["pr"])

    def test_nudge_cut_for_budget_is_not_recorded_and_fires_next_prompt(self):
        _write_nudges(self.tmp, {"budget": 15, "repeat_after": 20, "nudges": [
            {"name": "first", "pattern": "pr", "say": "x" * 40},
            {"name": "second", "pattern": "pr", "say": "y" * 40},
        ]})
        shown = nudge.plan({}, self.tmp / "roots.json", "open a pr", "work", "sess-cut")
        self.assertEqual(shown.fired, ["first"])
        again = nudge.plan({}, self.tmp / "roots.json", "open a pr", "work", "sess-cut")
        self.assertEqual(again.fired, ["second"])
        self.assertEqual(again.suppressed, ["first"])

    def test_json_shape_helper_tokens_matches_util(self):
        result = nudge.plan({}, self.tmp / "roots.json", "hi", "work", None)
        self.assertEqual(util.tokens("\n".join(result.say)), result.tokens)


if __name__ == "__main__":
    unittest.main()
