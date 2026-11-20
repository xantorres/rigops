from __future__ import annotations

import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core, hook  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_BIN = REPO_ROOT / "libexec" / "rigops-hook"
BIN = REPO_ROOT / "bin" / "rigops"


def _stamp(hours_ago=0):
    moment = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return moment.strftime(core.UTC_STAMP)


class HookCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ, {
            "XDG_STATE_HOME": str(self.tmp / "xdg-state"),
            "RIGOPS_CONFIG": str(self.tmp / "config.json"),
        })
        env.start()
        self.addCleanup(env.stop)

    def run_hook(self, event, payload=None):
        stdin = io.StringIO("" if payload is None else (
            payload if isinstance(payload, str) else json.dumps(payload)))
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), redirect_stdout(out):
            code = hook.main([event])
        self.assertEqual(code, 0)
        return out.getvalue()


class SessionStartTests(HookCase):
    def write_gates(self, gates, ts=None):
        path = core.local_state_path(core.GATES_STATE_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": ts or _stamp(), "gates": gates}))
        return path

    def test_missing_record_says_so(self):
        self.assertEqual(
            self.run_hook("session-start"),
            "rigops: no doctor gates recorded yet (run rigops doctor)\n")

    def test_wrong_shape_is_unreadable(self):
        path = core.local_state_path(core.GATES_STATE_NAME)
        path.parent.mkdir(parents=True)
        for body in ("{not json", '{"gates": "x"}', '{"gates": [1]}', "[]"):
            with self.subTest(body=body):
                path.write_text(body)
                self.assertEqual(
                    self.run_hook("session-start"),
                    f"rigops: gate state unreadable at {core.short(path)} (run rigops doctor)\n")

    def test_clean_record_prints_nothing(self):
        self.write_gates([{"kind": "job", "name": "j", "status": "ok"}])
        self.assertEqual(self.run_hook("session-start"), "")

    def test_stale_items_join_on_one_line(self):
        self.write_gates([
            {"kind": "job", "name": "a", "status": "failing"},
            {"kind": "job", "name": "b", "status": "hung"},
            {"kind": "check", "name": "paths", "status": "failing", "detail": "secret detail"},
            {"kind": "config", "name": "agents", "status": "drift"},
        ], ts=_stamp(5))
        out = self.run_hook("session-start")
        self.assertEqual(
            out, "stale: doctor gates last recorded 5h ago; jobs: 2 failing; "
                 "paths failing; config agents drift (rigops doctor --report)\n")
        self.assertNotIn("secret detail", out)

    def test_realm_tagged_gate_is_shown_like_any_other(self):
        self.write_gates([{"kind": "check", "name": "plans", "status": "failing",
                           "realm": "acme", "detail": "acme detail"}])
        out = self.run_hook("session-start")
        self.assertEqual(out, "stale: plans failing (rigops doctor --report)\n")

    def test_age_threshold_reads_config(self):
        self.write_gates([], ts=_stamp(3))
        self.assertIn("3h ago", self.run_hook("session-start"))
        (self.tmp / "config.json").write_text(
            json.dumps({"context": {"card": {"doctor_max_age_h": 10}}}))
        self.assertEqual(self.run_hook("session-start"), "")

    def test_unreadable_config_prints_one_line(self):
        self.write_gates([])
        (self.tmp / "config.json").write_text("{bad")
        out = self.run_hook("session-start")
        self.assertTrue(out.startswith("rigops: config unreadable: "), out)
        self.assertEqual(out.count("\n"), 1)

    def test_other_exception_names_its_type(self):
        with mock.patch.object(hook, "_session_start_line", side_effect=RuntimeError("x")):
            self.assertEqual(self.run_hook("session-start"),
                             "rigops: session-start hook failed (RuntimeError)\n")

    def test_output_is_capped_on_a_utf8_boundary(self):
        self.write_gates([{"kind": "check", "name": "é" * 600, "status": "failing"}])
        out = self.run_hook("session-start")
        raw = out.rstrip("\n").encode("utf-8")
        self.assertLessEqual(len(raw), hook.MAX_OUTPUT_BYTES)
        self.assertTrue(out.rstrip("\n").endswith("…"))
        raw.decode("utf-8")

    def test_unknown_or_missing_event_is_silent_exit_0(self):
        self.assertEqual(self.run_hook("bogus"), "")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(hook.main([]), 0)
        self.assertEqual(out.getvalue(), "")

    def test_systemexit_from_a_handler_still_exits_0(self):
        with mock.patch.object(hook, "_user_prompt_submit", side_effect=SystemExit(2)):
            self.assertEqual(self.run_hook("user-prompt-submit", {}), "")


class UserPromptSubmitTests(HookCase):
    SID = "sess-1"

    def transcript(self, *tokens):
        path = self.tmp / "t.jsonl"
        rows = []
        for n in tokens:
            rows.append(json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": n, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0}}}))
        path.write_text("\n".join(rows) + "\n")
        return path

    def prompt(self, path, sid=None):
        return self.run_hook("user-prompt-submit",
                             {"transcript_path": str(path), "session_id": sid or self.SID})

    def state_file(self):
        return self.tmp / "xdg-state" / "rigops" / "ctx-nudge" / self.SID

    def test_below_tier_is_silent(self):
        self.assertEqual(self.prompt(self.transcript(249999)), "")

    def test_first_crossing_prints_tier_one(self):
        out = self.prompt(self.transcript(260000))
        self.assertEqual(
            out, "[ctx-nudge] Context 260k (>250k). Wrap the current task; delegate mechanical "
                 "edits and broad reads to subagents; then suggest a fresh chat.\n")

    def test_rate_limit_rearm_and_drop(self):
        self.assertNotEqual(self.prompt(self.transcript(260000)), "")
        self.assertEqual(self.prompt(self.transcript(300000)), "")
        self.assertNotEqual(self.prompt(self.transcript(310000)), "")
        self.assertEqual(self.state_file().read_text().strip(), "310000")
        self.assertEqual(self.prompt(self.transcript(100000)), "")
        self.assertFalse(self.state_file().exists())
        self.assertNotEqual(self.prompt(self.transcript(260000)), "")

    def test_tier_two_and_three_messages(self):
        self.assertEqual(
            self.prompt(self.transcript(360000)),
            "[ctx-nudge] Context 360k: heavy. Finish and verify the current step ONLY; at the "
            "next natural stopping point, tell the user: fresh chat now. All reads/edits via "
            "subagents.\n")
        self.assertEqual(
            self.prompt(self.transcript(510000)),
            "[ctx-nudge] Context 510k: past the point where this session is worth continuing. "
            "Take no new work. Finish or hand off the step in flight, write down what is left, "
            "and tell the user to start a fresh chat now.\n")

    def test_config_tiers_and_rearm_override_defaults(self):
        (self.tmp / "config.json").write_text(
            json.dumps({"context": {"nudge_tiers": [1000, 2000, 3000], "rearm_tokens": 10}}))
        self.assertIn("Context 1k (>1k)", self.prompt(self.transcript(1500)))

    def test_missing_transcript_or_fields_are_silent(self):
        self.assertEqual(self.prompt(self.tmp / "nope.jsonl"), "")
        self.assertEqual(self.run_hook("user-prompt-submit", {"session_id": "s"}), "")
        self.assertEqual(self.run_hook("user-prompt-submit", {"transcript_path": "x"}), "")
        self.assertEqual(self.run_hook("user-prompt-submit", "not json"), "")
        self.assertEqual(self.run_hook("user-prompt-submit"), "")

    def test_zero_usage_entries_are_ignored(self):
        self.assertNotEqual(self.prompt(self.transcript(260000, 0)), "")

    def test_session_id_is_sanitized(self):
        self.prompt(self.transcript(260000), sid="../a b")
        self.assertTrue((self.tmp / "xdg-state" / "rigops" / "ctx-nudge" / "___a_b").exists())

    def test_old_state_files_are_pruned(self):
        old = self.state_file().parent / "old"
        old.parent.mkdir(parents=True)
        old.write_text("1")
        past = time.time() - 8 * 86400
        os.utime(old, (past, past))
        self.prompt(self.transcript(100))
        self.assertFalse(old.exists())

    def test_only_a_bounded_tail_is_read(self):
        path = self.tmp / "big.jsonl"
        filler = json.dumps({"type": "user", "message": {"content": "x" * 1000}})
        path.write_text(filler + "\n" + "\n".join([filler] * 3000) + "\n")
        with open(path, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": 260000}}}) + "\n")
        self.assertNotEqual(self.prompt(path), "")


class SubprocessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = {**os.environ, "XDG_STATE_HOME": str(self.tmp / "xdg"),
                    "RIGOPS_CONFIG": str(self.tmp / "config.json")}

    def run_bin(self, *args, stdin=""):
        start = time.monotonic()
        proc = subprocess.run([sys.executable, str(HOOK_BIN), *args], input=stdin,
                              capture_output=True, text=True, timeout=30, env=self.env)
        return proc, time.monotonic() - start

    def test_median_wall_time_is_inside_the_budget(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 260000}}}) + "\n")
        payload = json.dumps({"transcript_path": str(transcript), "session_id": "budget"})
        for event in ("session-start", "user-prompt-submit"):
            with self.subTest(event=event):
                times = []
                for _ in range(3):
                    proc, elapsed = self.run_bin(event, stdin=payload)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    times.append(elapsed)
                self.assertLess(statistics.median(times), hook.TIME_BUDGET_S)

    def test_help_exits_0(self):
        proc, _ = self.run_bin("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage", proc.stdout)

    def test_hook_is_hidden_from_help_but_dispatches(self):
        listing = subprocess.run([str(BIN), "help"], capture_output=True, text=True,
                                 timeout=30, env=self.env)
        self.assertEqual(listing.returncode, 0)
        self.assertNotIn("  hook", listing.stdout.splitlines())
        self.assertIn("  mine", listing.stdout.splitlines())
        proc = subprocess.run([str(BIN), "hook", "--help"], capture_output=True, text=True,
                              timeout=30, env=self.env)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
