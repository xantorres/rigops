from __future__ import annotations

import contextlib
import io
import json
import os
import types
import unittest
from pathlib import Path

from rigops import reap, reap_owned, reap_procs

from tests.test_reap import GitFixtureTestCase, _init_repo, _run_git


def proc(ppid, tty="??", command="node server.js"):
    return {"ppid": ppid, "pgid": 1, "uid": os.getuid(), "tty": tty,
            "etimes": 100, "command": command}


OUTSIDE = 4242


class OwnedPidsTests(unittest.TestCase):
    def test_root_reparented_to_init_and_its_child_are_attributable(self):
        snap = {900001: proc(1), 900002: proc(900001)}
        self.assertEqual(reap_owned.owned_pids({900001, 900002}, snap), set())
        self.assertEqual(reap_procs._filter_killable({900001, 900002}, snap), {900001, 900002})

    def test_root_with_a_live_parent_outside_is_owned_with_its_children(self):
        snap = {OUTSIDE: proc(1), 900001: proc(OUTSIDE), 900002: proc(900001),
                900003: proc(900002)}
        self.assertEqual(reap_owned.owned_pids({900001, 900002, 900003}, snap),
                         {900001, 900002, 900003})

    def test_mixed_tree_splits_by_top_most_rooted_ancestor(self):
        snap = {OUTSIDE: proc(1), 900001: proc(1), 900002: proc(900001),
                900003: proc(OUTSIDE), 900004: proc(900003)}
        rooted = {900001, 900002, 900003, 900004}
        self.assertEqual(reap_owned.owned_pids(rooted, snap), {900003, 900004})

    def test_walk_stops_at_the_first_parent_outside_the_rooted_set(self):
        snap = {900001: proc(900002), 900002: proc(1)}
        self.assertEqual(reap_owned.owned_pids({900001}, snap), {900001})

    def test_ppid_cycle_does_not_loop(self):
        snap = {900001: proc(900002), 900002: proc(900001)}
        self.assertEqual(reap_owned.owned_pids({900001, 900002}, snap), {900001, 900002})


class OwnedRunTests(GitFixtureTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)
        self.wt_path = self.tmp / "wt"
        _run_git(["worktree", "add", "-b", "wt", str(self.wt_path)], self.repo)
        self.tree = os.path.realpath(self.wt_path)
        orig_blockers = reap.removal_blockers
        self.addCleanup(setattr, reap, "removal_blockers", orig_blockers)
        # The fake pids do not exist, so the liveness probe would clear them.
        reap.removal_blockers = lambda rooted, attempted, kr: orig_blockers(
            rooted, attempted, kr, alive=lambda pid: True)

    def run_with(self, snap, cwds, apply=True, dry_run=False, stale=None):
        snap = {**snap, os.getpid(): proc(1, tty="ttys000", command="reap")}
        cwds = {**cwds, os.getpid(): "/"}
        for name, value in (("ps_snapshot", snap), ("cwd_map", cwds)):
            self.addCleanup(setattr, reap, name, getattr(reap, name))
            setattr(reap, name, lambda value=value: value)
        calls = []

        def fake_kill(label, pids, snap_, apply_, max_kills, grace_s):
            calls.append((label, dict(pids)))
            return {"would": [{"pid": p, "comm": "node", "match": t, "tree": label}
                              for p, t in sorted(pids.items())],
                    "killed": len(pids), "kill_failed": 0, "overflow": False,
                    "failed_pids": []}

        self.addCleanup(setattr, reap, "kill_pids", reap.kill_pids)
        reap.kill_pids = fake_kill
        sessions = self.tmp / "sessions"
        sessions.mkdir(exist_ok=True)
        opts = types.SimpleNamespace(
            selftest=False, apply=apply, dry_run=dry_run, json=True,
            repo=[self.repo], roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(reap.run(opts), 0)
        return calls, json.loads(buf.getvalue())

    def reap_log(self) -> str:
        path = Path(os.environ["RIGOPS_STATE_DIR"]) / "reap.log"
        return path.read_text() if path.exists() else ""

    def test_orphaned_root_and_child_are_killed_and_the_tree_removed(self):
        snap = {900001: proc(1), 900002: proc(900001)}
        calls, payload = self.run_with(snap, {900001: self.tree})
        self.assertEqual(calls[0], (self.tree, {900001: "cwd", 900002: "child"}))
        target = payload["repos"][0]["worktrees"][0]
        self.assertEqual(target["blockers"], [])
        self.assertTrue(target["applied"]["removed"])

    def test_owned_root_and_children_are_not_signalled_and_block_removal(self):
        snap = {OUTSIDE: proc(1), 900001: proc(OUTSIDE), 900002: proc(900001)}
        calls, payload = self.run_with(snap, {900001: self.tree})
        self.assertEqual(calls[0][1], {})
        target = payload["repos"][0]["worktrees"][0]
        self.assertEqual(sorted(target["blockers"]), [900001, 900002])
        self.assertFalse(target["applied"]["removed"] if target["applied"] else False)
        self.assertTrue(self.wt_path.exists())

    def test_owned_processes_are_in_json_with_their_own_reason(self):
        snap = {OUTSIDE: proc(1), 900001: proc(OUTSIDE), 900002: proc(900001)}
        _calls, payload = self.run_with(snap, {900001: self.tree})
        owned = payload["repos"][0]["kills"][0]["owned"]
        self.assertEqual([(o["pid"], o["reason"]) for o in owned],
                         [(900001, "owned"), (900002, "owned")])

    def test_owned_processes_are_logged(self):
        snap = {OUTSIDE: proc(1), 900001: proc(OUTSIDE), 900002: proc(900001)}
        self.run_with(snap, {900001: self.tree})
        line = next(ln for ln in self.reap_log().splitlines() if "SKIP-OWNED" in ln)
        self.assertIn(f"tree={self.tree}", line)
        self.assertIn("pids=900001,900002", line)

    def test_mixed_tree_kills_only_what_it_can_attribute_and_keeps_the_tree(self):
        snap = {OUTSIDE: proc(1), 900001: proc(1), 900002: proc(900001),
                900003: proc(OUTSIDE), 900004: proc(900003)}
        calls, payload = self.run_with(snap, {900001: self.tree, 900003: self.tree})
        self.assertEqual(calls[0][1], {900001: "cwd", 900002: "child"})
        target = payload["repos"][0]["worktrees"][0]
        self.assertEqual(sorted(target["blockers"]), [900003, 900004])
        self.assertTrue(self.wt_path.exists())

    def test_dry_run_preview_shows_owned_processes(self):
        snap = {OUTSIDE: proc(1), 900001: proc(OUTSIDE)}
        _calls, payload = self.run_with(snap, {900001: self.tree}, dry_run=True)
        self.assertEqual([o["pid"] for o in payload["repos"][0]["kills"][0]["owned"]], [900001])

    def test_plain_preview_prints_the_owned_line(self):
        snap = {OUTSIDE: proc(1), 900001: proc(OUTSIDE)}
        out = self.run_text(snap, {900001: self.tree})
        self.assertIn("SKIP-OWNED pid=900001", out)

    def run_text(self, snap, cwds):
        snap = {**snap, os.getpid(): proc(1, tty="ttys000", command="reap")}
        cwds = {**cwds, os.getpid(): "/"}
        for name, value in (("ps_snapshot", snap), ("cwd_map", cwds)):
            self.addCleanup(setattr, reap, name, getattr(reap, name))
            setattr(reap, name, lambda value=value: value)
        sessions = self.tmp / "sessions"
        sessions.mkdir(exist_ok=True)
        opts = types.SimpleNamespace(
            selftest=False, apply=False, dry_run=False, json=False,
            repo=[self.repo], roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            reap.run(opts)
        return buf.getvalue()

    def test_stale_directory_kill_path_skips_owned_processes(self):
        stale = self.repo / ".claude" / "worktrees" / "stale1"
        stale.mkdir(parents=True)
        tree = os.path.realpath(stale)
        snap = {OUTSIDE: proc(1), 900001: proc(OUTSIDE), 900002: proc(1)}
        calls, payload = self.run_with(snap, {900001: tree, 900002: tree})
        stale_call = next(c for c in calls if c[0] == f"stale={tree}")
        self.assertEqual(stale_call[1], {900002: "cwd"})
        kill = next(k for k in payload["repos"][0]["kills"]
                    if any(o["tree"] == f"stale={tree}" for o in k["owned"]))
        self.assertEqual([o["pid"] for o in kill["owned"]], [900001])


if __name__ == "__main__":
    unittest.main()
