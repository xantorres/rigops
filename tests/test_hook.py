from __future__ import annotations

import contextlib
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

import rigops
from rigops import core, hook

from tests.support import RIGOPS, cli_argv

REPO_ROOT = Path(__file__).resolve().parent.parent


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


class PluginVersionTests(HookCase):
    MISMATCH = ("rigops: plugin {p} and CLI {c} differ; upgrade the older one "
                "(see docs/INSTALL.md#upgrade)\n")

    def hook_with(self, plugin, cli="0.12.0", event="session-start", payload=None):
        env = {} if plugin is None else {"RIGOPS_PLUGIN_VERSION": plugin}
        with mock.patch.dict(os.environ, env), mock.patch.object(rigops, "__version__", cli):
            if plugin is None:
                os.environ.pop("RIGOPS_PLUGIN_VERSION", None)
            return self.run_hook(event, payload)

    def test_different_major_minor_prints_one_line_after_the_session_line(self):
        self.assertEqual(self.hook_with("1.0.0"),
                         "rigops: no doctor gates recorded yet (run rigops doctor)\n"
                         + self.MISMATCH.format(p="1.0", c="0.12"))

    def test_mismatch_alone_when_the_session_line_is_empty(self):
        path = core.local_state_path(core.GATES_STATE_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": _stamp(), "gates": []}))
        self.assertEqual(self.hook_with("1.0.0"), self.MISMATCH.format(p="1.0", c="0.12"))

    def test_same_major_minor_adds_nothing(self):
        base = self.hook_with(None)
        for plugin in ("0.12.3", "0.12.0rc1", "0.12.0"):
            with self.subTest(plugin=plugin):
                self.assertEqual(self.hook_with(plugin), base)

    def test_unset_empty_or_garbage_adds_nothing(self):
        base = self.hook_with(None)
        for plugin in (None, "", "garbage", "1", "v1.0.0", " "):
            with self.subTest(plugin=plugin):
                self.assertEqual(self.hook_with(plugin), base)

    def test_unparseable_cli_version_adds_nothing(self):
        self.assertEqual(self.hook_with("1.0.0", cli="dev"), self.hook_with(None, cli="dev"))

    def test_user_prompt_submit_never_prints_it(self):
        self.assertEqual(self.hook_with("1.0.0", event="user-prompt-submit",
                                        payload={"session_id": "s"}), "")

    def test_mismatch_line_goes_through_the_cap(self):
        gates = [{"kind": "check", "name": "é" * 600, "status": "failing"}]
        path = core.local_state_path(core.GATES_STATE_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": _stamp(), "gates": gates}))
        out = self.hook_with("1.0.0")
        self.assertLessEqual(len(out.rstrip("\n").encode("utf-8")), hook.MAX_OUTPUT_BYTES)


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


class NoChildProcessTests(HookCase):
    def test_hook_starts_no_child_process(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 260000}}}) + "\n")
        payload = {"transcript_path": str(transcript), "session_id": "nochild"}

        def boom(*args, **kwargs):
            raise AssertionError("hook started a child process")

        for event in (*hook.EVENTS, "bogus", "--help"):
            with self.subTest(event=event), contextlib.ExitStack() as stack:
                for owner, name in ((subprocess, "Popen"), (os, "system"),
                                    (os, "posix_spawn"), (os, "fork")):
                    stack.enter_context(mock.patch.object(owner, name, boom))
                self.run_hook(event, payload)


class ExitZeroTests(HookCase):
    def test_every_failure_path_exits_0_and_prints_nothing_extra(self):
        (self.tmp / "config.json").write_text("{bad")
        for event in hook.EVENTS:
            for stdin in ("", "not json", "[]", "null", '{"transcript_path": 5, "session_id": 7}'):
                with self.subTest(event=event, stdin=stdin):
                    out = self.run_hook(event, stdin)
                    self.assertLessEqual(len(out.encode()), hook.MAX_OUTPUT_BYTES)

    def test_unreadable_config_still_nudges_with_defaults(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 260000}}}) + "\n")
        (self.tmp / "config.json").mkdir()
        out = self.run_hook("user-prompt-submit",
                            {"transcript_path": str(transcript), "session_id": "cfgdir"})
        self.assertIn("[ctx-nudge]", out)

    def test_state_dir_that_cannot_be_created_exits_0(self):
        (self.tmp / "xdg-state").write_text("a file, not a directory")
        transcript = self.tmp / "t.jsonl"
        transcript.write_text("")
        self.assertEqual(self.run_hook(
            "user-prompt-submit", {"transcript_path": str(transcript), "session_id": "s"}), "")

    def test_broken_stdout_exits_0(self):
        class Broken:
            def write(self, _):
                raise BrokenPipeError

            def flush(self):
                raise BrokenPipeError

        with mock.patch.object(sys, "stdout", Broken()), mock.patch.object(os, "dup2"):
            self.assertEqual(hook.main(["session-start"]), 0)

    def test_closed_stdout_exits_0_through_the_real_entry_point(self):
        proc = subprocess.run(
            ["sh", "-c", '"$0" "$1" "$2" "$3" 1>&-', *cli_argv("hook"), "session-start"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "XDG_STATE_HOME": str(self.tmp / "x"),
                 "RIGOPS_CONFIG": str(self.tmp / "config.json")})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_stdout_pipe_closed_early_exits_0(self):
        proc = subprocess.Popen(
            [*cli_argv("hook"), "session-start"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "XDG_STATE_HOME": str(self.tmp / "x"),
                 "RIGOPS_CONFIG": str(self.tmp / "config.json")})
        proc.stdout.close()
        _, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)


class CapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = {**os.environ, "XDG_STATE_HOME": str(self.tmp / "xdg"),
                    "RIGOPS_CONFIG": str(self.tmp / "config.json")}

    def run_entry(self, event, stdin=""):
        return subprocess.run([*cli_argv("hook"), event], input=stdin.encode(),
                              capture_output=True, timeout=30, env=self.env)

    def seed_gates(self, text):
        path = Path(self.env["XDG_STATE_HOME"]) / "rigops" / core.GATES_STATE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": _stamp(), "gates": [
            {"kind": "check", "name": text, "status": text},
            {"kind": "config", "name": text, "status": text}]}))

    def test_output_stays_under_the_cap_on_a_10k_character_input(self):
        for label, unit in (("ascii", "a"), ("multibyte", "é"), ("wide", "日")):
            big = unit * 10000
            transcript = self.tmp / f"{label}.jsonl"
            transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": 260000}}}) + "\n")
            self.seed_gates(big)
            payloads = {
                "session-start": json.dumps({"prompt": big}),
                "user-prompt-submit": json.dumps({
                    "transcript_path": str(transcript), "session_id": label + big, "prompt": big,
                    "cwd": big}),
            }
            for event, stdin in payloads.items():
                with self.subTest(label=label, event=event):
                    proc = self.run_entry(event, stdin)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertTrue(proc.stdout, "event printed nothing")
                    self.assertLessEqual(len(proc.stdout), hook.MAX_OUTPUT_BYTES)
                    proc.stdout.decode("utf-8")
            (Path(self.env["XDG_STATE_HOME"]) / "rigops" / core.GATES_STATE_NAME).unlink()

    def test_a_long_config_error_message_is_capped_including_the_newline(self):
        self.seed_gates("x")
        (self.tmp / "config.json").write_text("{" + "é" * 5000)
        proc = self.run_entry("session-start")
        self.assertLessEqual(len(proc.stdout), hook.MAX_OUTPUT_BYTES)
        self.assertTrue(proc.stdout.endswith(b"\n"))

    def test_usage_goes_through_the_cap(self):
        proc = self.run_entry("--help")
        self.assertLessEqual(len(proc.stdout), hook.MAX_OUTPUT_BYTES)


class SubprocessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = {**os.environ, "XDG_STATE_HOME": str(self.tmp / "xdg"),
                    "RIGOPS_CONFIG": str(self.tmp / "config.json")}

    def run_bin(self, *args, stdin=""):
        start = time.monotonic()
        proc = subprocess.run([*cli_argv("hook"), *args], input=stdin,
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

    def test_hook_p95_is_at_or_under_250_ms_over_20_runs(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text("\n".join(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 260000 + n}}}) for n in range(400)) + "\n")
        gates = Path(self.env["XDG_STATE_HOME"]) / "rigops" / core.GATES_STATE_NAME
        gates.parent.mkdir(parents=True)
        gates.write_text(json.dumps({"ts": _stamp(5), "gates": [
            {"kind": "check", "name": "paths", "status": "failing"}]}))
        for event in ("session-start", "user-prompt-submit"):
            with self.subTest(event=event):
                payload = json.dumps({"transcript_path": str(transcript), "session_id": event})
                cmd = [*RIGOPS, "hook", event]
                subprocess.run(cmd, input=payload, capture_output=True, text=True,
                               timeout=30, env=self.env)
                times = []
                for _ in range(20):
                    start = time.monotonic()
                    proc = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                                          timeout=30, env=self.env)
                    times.append(time.monotonic() - start)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                p95 = sorted(times)[18]
                self.assertLessEqual(p95, 0.250, f"p95 {p95:.3f}s, times {sorted(times)}")

    def test_help_exits_0(self):
        proc, _ = self.run_bin("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage", proc.stdout)

    def test_hook_is_hidden_from_help_but_dispatches(self):
        listing = subprocess.run([*RIGOPS, "help"], capture_output=True, text=True,
                                 timeout=30, env=self.env)
        self.assertEqual(listing.returncode, 0)
        body = listing.stdout.split("commands:\n", 1)[1].split("\n\n", 1)[0]
        names = [line.split()[0] for line in body.splitlines()]
        self.assertNotIn("hook", names)
        self.assertIn("mine", names)
        proc = subprocess.run([*RIGOPS, "hook", "--help"], capture_output=True, text=True,
                              timeout=30, env=self.env)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
