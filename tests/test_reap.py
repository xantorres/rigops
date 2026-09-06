from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import reap, state  # noqa: E402

# Resolved before any test rewrites the environment: the log the operator's own
# reaper runs append to, which no test may touch.
OPERATOR_LOG = state.state_dir() / "reap.log"
_operator_log_stat = None


def _stat_operator_log():
    if not OPERATOR_LOG.exists():
        return None
    st = OPERATOR_LOG.stat()
    return st.st_mtime_ns, st.st_size


def setUpModule():
    global _operator_log_stat
    _operator_log_stat = _stat_operator_log()


def tearDownModule():
    assert _stat_operator_log() == _operator_log_stat, (
        f"a test in this module wrote to {OPERATOR_LOG}"
    )


class EnvIsolatedTestCase(unittest.TestCase):
    """Base for every case here: reap.log() appends to state.state_dir()."""

    def setUp(self):
        self._env_backup = dict(os.environ)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        os.environ["RIGOPS_STATE_DIR"] = str(self.tmp / "state")
        os.environ["RIGOPS_CONFIG"] = str(self.tmp / "config.json")

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self._env_backup)


# --- section 1: pure tables --------------------------------------------------

ETIME_CASES = [
    ("dd_hh_mm_ss", "1-02:03:04", ((1 * 24 + 2) * 60 + 3) * 60 + 4),
    ("hh_mm_ss", "02:03:04", ((0 * 24 + 2) * 60 + 3) * 60 + 4),
    ("mm_ss", "03:04", ((0 * 24 + 0) * 60 + 3) * 60 + 4),
    ("garbage", "not-a-time", None),
]


class EtimeToSecondsTests(EnvIsolatedTestCase):
    def test_table(self):
        for name, text, expected in ETIME_CASES:
            with self.subTest(name):
                self.assertEqual(reap._etime_to_seconds(text), expected)


CLAMP_TIMEOUT_CASES = [
    ("no_deadline_passes_base_through", 180, None, 180),
    ("ample_remaining_uses_base", 180, 3600.0, 180),
    ("remaining_below_base_wins", 180, 60.0, 60),
    ("remaining_at_floor_is_none", 180, 5.0, None),
    ("remaining_zero_is_none", 180, 0.0, None),
    ("remaining_negative_is_none", 180, -12.0, None),
    ("fractional_remaining_truncates", 120, 119.4, 119),
]


class ClampTimeoutTests(EnvIsolatedTestCase):
    def test_table(self):
        for name, base_s, remaining_s, expected in CLAMP_TIMEOUT_CASES:
            with self.subTest(name):
                self.assertEqual(reap.clamp_timeout(base_s, remaining_s), expected)


class RunCapturedKillsProcessGroupTests(EnvIsolatedTestCase):
    def test_sigterm_ignoring_child_group_killed(self):
        with tempfile.TemporaryDirectory() as tmp:
            pgid_file = Path(tmp) / "pgid"
            argv = ["sh", "-c", f"echo $$ > {pgid_file}; trap '' TERM; sleep 30"]
            start = time.time()
            with self.assertRaises(subprocess.TimeoutExpired):
                reap._run_captured(argv, timeout=1, env=dict(os.environ))
            self.assertLess(time.time() - start, 5)

            pgid = int(pgid_file.read_text().strip())
            deadline = time.time() + 2
            gone = False
            while time.time() < deadline:
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    gone = True
                    break
                time.sleep(0.05)
            self.assertTrue(gone, "process group still alive after _run_captured timeout")


PARSE_WORKTREES_CASES = [
    (
        "primary_with_branch",
        "worktree /repo/main\nHEAD abc1234\nbranch refs/heads/main\n",
        [{"path": "/repo/main", "branch": "main", "head": "abc1234",
          "bare": False, "detached": False, "locked": False, "prunable": False}],
    ),
    (
        "locked",
        "worktree /repo/wt\nHEAD abc1\nbranch refs/heads/feat\nlocked\n",
        [{"path": "/repo/wt", "branch": "feat", "head": "abc1",
          "bare": False, "detached": False, "locked": True, "prunable": False}],
    ),
    (
        "detached",
        "worktree /repo/wt2\nHEAD abc2\ndetached\n",
        [{"path": "/repo/wt2", "branch": None, "head": "abc2",
          "bare": False, "detached": True, "locked": False, "prunable": False}],
    ),
    (
        "prunable",
        "worktree /repo/wt3\nHEAD abc3\nbranch refs/heads/old\n"
        "prunable gitdir file points to non-existent location\n",
        [{"path": "/repo/wt3", "branch": "old", "head": "abc3",
          "bare": False, "detached": False, "locked": False, "prunable": True}],
    ),
    (
        "bare",
        "worktree /repo/.bare\nbare\n",
        [{"path": "/repo/.bare", "branch": None, "head": None,
          "bare": True, "detached": False, "locked": False, "prunable": False}],
    ),
    (
        "blank_line_separated_multi_no_trailing_blank",
        "worktree /repo/main\nHEAD abc1\nbranch refs/heads/main\n"
        "\n"
        "worktree /repo/wt\nHEAD abc2\nbranch refs/heads/feat\n",
        [
            {"path": "/repo/main", "branch": "main", "head": "abc1",
             "bare": False, "detached": False, "locked": False, "prunable": False},
            {"path": "/repo/wt", "branch": "feat", "head": "abc2",
             "bare": False, "detached": False, "locked": False, "prunable": False},
        ],
    ),
]


class ParseWorktreesTests(EnvIsolatedTestCase):
    def test_table(self):
        for name, porcelain, expected in PARSE_WORKTREES_CASES:
            with self.subTest(name):
                self.assertEqual(reap.parse_worktrees(porcelain), expected)


PATH_CLAIMS_CASES = [
    ("relative_token_false", "worktrees/foo", "/repo/worktrees", False),
    ("exact_match_true", "/repo/worktrees/foo", "/repo/worktrees/foo", True),
    ("nested_true", "/repo/worktrees/foo/bar", "/repo/worktrees/foo", True),
    ("sibling_prefix_not_nested_false", "/repo/worktrees/foobar", "/repo/worktrees/foo", False),
    ("none_path_false", None, "/repo/worktrees/foo", False),
]


class PathClaimsTests(EnvIsolatedTestCase):
    def test_table(self):
        for name, path, target, expected in PATH_CLAIMS_CASES:
            with self.subTest(name):
                self.assertEqual(reap.path_claims(path, target), expected)


IGNORED_DIRS = {"node_modules", "dist", ".turbo", "@mf-types"}
IGNORED_PREFIXES = ("coverage",)

IGNORED_UNTRACKED_CASES = [
    ("node_modules_nested_true", "packages/app/node_modules/foo/index.js", True),
    ("coverage_prefix_true", "coverage-html/index.html", True),
    ("src_false", "src/real.py", False),
]


class IsIgnoredUntrackedTests(EnvIsolatedTestCase):
    def test_table(self):
        for name, path, expected in IGNORED_UNTRACKED_CASES:
            with self.subTest(name):
                self.assertEqual(
                    reap.is_ignored_untracked(path, IGNORED_DIRS, IGNORED_PREFIXES), expected
                )


GIT_ERROR_CASES = [
    (
        "fatal_line_over_continuation",
        "fatal: not a git repository\nand the repository exists.\n",
        "fatal: not a git repository",
    ),
    ("empty_stderr_unknown", "", "unknown"),
]


class GitErrorTests(EnvIsolatedTestCase):
    def test_table(self):
        for name, stderr, expected in GIT_ERROR_CASES:
            with self.subTest(name):
                self.assertEqual(reap.git_error(stderr), expected)


class DiscoverReposUnreadableDirTests(EnvIsolatedTestCase):
    def test_unreadable_group_dir_skipped_others_found(self):
        if os.geteuid() == 0:
            self.skipTest("root can read mode 000 dirs")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good_group = root / "good"
            good_repo = good_group / "repo"
            good_repo.mkdir(parents=True)
            (good_repo / ".git").mkdir()

            blocked_group = root / "blocked"
            blocked_group.mkdir()
            os.chmod(blocked_group, 0o000)
            try:
                repos = reap.discover_repos(root)
            finally:
                os.chmod(blocked_group, 0o755)

            self.assertEqual(repos, [good_repo])


class LiveWorktreeCwdsNullPidTests(EnvIsolatedTestCase):
    def test_null_pid_ignored_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp)
            (sessions_dir / "bad.json").write_text('{"pid": null, "cwd": "/tmp/x"}')
            live = reap.live_worktree_cwds(sessions_dir)
            self.assertEqual(live, [])


# --- section 2: kill machinery, synthetic snapshots, no real signals --------


class DescendantsTests(EnvIsolatedTestCase):
    def test_grandchild_reachable_through_closure(self):
        snap = {
            100: {"ppid": 1, "pgid": 100, "uid": 0, "tty": "??",
                 "etimes": 10, "command": "root"},
            101: {"ppid": 100, "pgid": 100, "uid": 0, "tty": "??",
                 "etimes": 10, "command": "child"},
            102: {"ppid": 101, "pgid": 100, "uid": 0, "tty": "??",
                 "etimes": 10, "command": "grandchild"},
            999: {"ppid": 1, "pgid": 999, "uid": 0, "tty": "??",
                 "etimes": 10, "command": "unrelated"},
        }
        self.assertEqual(reap._descendants({100}, snap), {100, 101, 102})


class FilterKillableTests(EnvIsolatedTestCase):
    def test_uid_tty_and_protected_filtering(self):
        my_uid = os.getuid()
        other_uid = my_uid + 1
        my_pid = os.getpid()
        snap = {
            900001: {"ppid": 1, "pgid": 900001, "uid": my_uid, "tty": "??",
                     "etimes": 10, "command": "sleep 300"},
            900002: {"ppid": 1, "pgid": 900002, "uid": other_uid, "tty": "??",
                     "etimes": 10, "command": "sleep 300"},
            900003: {"ppid": 1, "pgid": 900003, "uid": my_uid, "tty": "ttys000",
                     "etimes": 10, "command": "sleep 300"},
            900004: {"ppid": 1, "pgid": 900004, "uid": my_uid, "tty": "?",
                     "etimes": 10, "command": "sleep 300"},
            900005: {"ppid": 1, "pgid": 900005, "uid": my_uid, "tty": "pts/0",
                     "etimes": 10, "command": "sleep 300"},
            my_pid: {"ppid": 1, "pgid": my_pid, "uid": my_uid, "tty": "??",
                    "etimes": 10, "command": "self"},
        }
        result = reap._filter_killable({900001, 900002, 900003, 900004, 900005, my_pid}, snap)
        self.assertEqual(result, {900001, 900004})


class KillPidsTests(EnvIsolatedTestCase):
    def test_apply_false_returns_would_list_no_kill(self):
        snap = {900001: {"ppid": 1, "pgid": 900001, "uid": os.getuid(), "tty": "??",
                         "etimes": 10, "command": "sleep 300"}}
        result = reap.kill_pids("tree-label", {900001: "cwd"}, snap, apply=False,
                                max_kills=20, grace_s=5)
        self.assertEqual(result["killed"], 0)
        self.assertFalse(result["overflow"])
        self.assertEqual([w["pid"] for w in result["would"]], [900001])

    def test_overflow_blocks_kill_even_with_apply_true(self):
        pids = {999900 + i: "cwd" for i in range(25)}
        snap = {pid: {"ppid": 1, "pgid": pid, "uid": os.getuid(), "tty": "??",
                     "etimes": 10, "command": "sleep 300"} for pid in pids}
        result = reap.kill_pids("tree-label", pids, snap, apply=True, max_kills=20, grace_s=5)
        self.assertTrue(result["overflow"])
        self.assertEqual(result["killed"], 0)


class ProtectedPidsOwnPgidTests(EnvIsolatedTestCase):
    def test_complete_walk_same_pgid_stranger_not_protected(self):
        my_pid = os.getpid()
        my_pgrp = os.getpgrp()
        snap = {
            my_pid: {"ppid": 1, "pgid": my_pgrp, "uid": 0, "tty": "??",
                    "etimes": 10, "command": "self"},
            555555: {"ppid": 1, "pgid": my_pgrp, "uid": 0, "tty": "??",
                    "etimes": 10, "command": "stranger"},
        }
        protected = reap.protected_pids(snap)
        self.assertNotIn(555555, protected)

    def test_truncated_walk_unions_own_pgid_members(self):
        snap = {
            555555: {"ppid": 1, "pgid": os.getpgrp(), "uid": 0, "tty": "??",
                    "etimes": 10, "command": "stranger"},
        }
        orig = reap._ppid_of
        reap._ppid_of = lambda pid: None
        try:
            protected = reap.protected_pids(snap)
        finally:
            reap._ppid_of = orig
        self.assertIn(555555, protected)


ALIVE_FOR_BLOCKING_CASES = [
    ("rc_nonzero_is_gone", 1, "", False),
    ("empty_stat_is_gone", 0, "", False),
    ("zombie_is_gone", 0, "Z\n", False),
    ("running_is_alive", 0, "S\n", True),
]


class AliveForBlockingStatusTests(EnvIsolatedTestCase):
    def test_table(self):
        orig_run = subprocess.run
        try:
            for name, rc, stdout, expected in ALIVE_FOR_BLOCKING_CASES:
                with self.subTest(name):
                    def fake_run(*args, rc=rc, stdout=stdout, **kwargs):
                        return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr="")
                    subprocess.run = fake_run
                    self.assertEqual(reap._alive_for_blocking(123), expected)
        finally:
            subprocess.run = orig_run

    def test_subprocess_exception_is_unknown_alive_true(self):
        def raising_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ps", timeout=10)
        orig_run = subprocess.run
        subprocess.run = raising_run
        try:
            self.assertTrue(reap._alive_for_blocking(123))
        finally:
            subprocess.run = orig_run


REMOVAL_BLOCKERS_CASES = [
    (
        "policy_excluded_alive_is_blocker",
        {1, 2}, {1: "cwd"}, {"failed_pids": []}, lambda pid: True,
        [{"pid": 2, "reason": "excluded-by-policy"}],
    ),
    (
        "policy_excluded_dead_is_not_blocker",
        {1, 2}, {1: "cwd"}, {"failed_pids": []}, lambda pid: False,
        [],
    ),
    (
        "failed_pids_is_blocker",
        {1}, {1: "cwd"}, {"failed_pids": [1]}, lambda pid: True,
        [{"pid": 1, "reason": "kill-failed"}],
    ),
    (
        "attempted_and_killed_is_not_blocker",
        {1}, {1: "cwd"}, {"failed_pids": []}, lambda pid: False,
        [],
    ),
    (
        "unknown_liveness_alive_stub_true_is_blocker",
        {5}, {}, {"failed_pids": []}, lambda pid: True,
        [{"pid": 5, "reason": "excluded-by-policy"}],
    ),
]


class RemovalBlockersTests(EnvIsolatedTestCase):
    def test_table(self):
        for name, rooted, attempted, kill_result, alive, expected in REMOVAL_BLOCKERS_CASES:
            with self.subTest(name):
                result = reap.removal_blockers(rooted, attempted, kill_result, alive=alive)
                self.assertEqual(result, expected)


# --- section 3/4 helpers: real tiny git fixtures ----------------------------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["-c", "init.defaultBranch=main", "init"], path)
    _run_git(["config", "user.email", "t@example.com"], path)
    _run_git(["config", "user.name", "Test"], path)
    (path / "README.md").write_text("hello\n")
    _run_git(["add", "README.md"], path)
    _run_git(["commit", "-m", "init"], path)


def _rev_parse(repo: Path, ref: str) -> str:
    return _run_git(["rev-parse", ref], repo).stdout.strip()


class GitFixtureTestCase(EnvIsolatedTestCase):
    def setUp(self):
        super().setUp()
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        os.environ["GIT_TERMINAL_PROMPT"] = "0"


# --- section 3: classify on real tiny git fixtures --------------------------


class ClassifyTests(GitFixtureTestCase):
    def test_merged_clean_worktree_is_reap(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-merged"
        _run_git(["worktree", "add", "-b", "feature-merged", str(wt_path)], repo)
        wt = {"path": str(wt_path), "branch": "feature-merged",
              "head": _rev_parse(repo, "feature-merged"),
              "bare": False, "detached": False, "locked": False, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )
        self.assertEqual(verdict, reap.REAP)

    def test_worktree_with_untracked_real_file_is_skip_dirty(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-dirty"
        _run_git(["worktree", "add", "-b", "feature-dirty", str(wt_path)], repo)
        (wt_path / "scratch.txt").write_text("wip\n")
        wt = {"path": str(wt_path), "branch": "feature-dirty",
              "head": _rev_parse(repo, "feature-dirty"),
              "bare": False, "detached": False, "locked": False, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )
        self.assertEqual(verdict, "skip: dirty")

    def test_untracked_file_inside_ignored_dir_only_is_reap(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-ignored-untracked"
        _run_git(["worktree", "add", "-b", "feature-ignored", str(wt_path)], repo)
        (wt_path / "node_modules").mkdir()
        (wt_path / "node_modules" / "x.js").write_text("noise\n")
        wt = {"path": str(wt_path), "branch": "feature-ignored",
              "head": _rev_parse(repo, "feature-ignored"),
              "bare": False, "detached": False, "locked": False, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs={"node_modules"}, ignored_prefixes=(),
        )
        self.assertEqual(verdict, reap.REAP)

    def test_unmerged_branch_with_local_commit_no_upstream_is_needs_push(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-unpushed"
        _run_git(["worktree", "add", "-b", "feature-unpushed", str(wt_path)], repo)
        (wt_path / "new.txt").write_text("work\n")
        _run_git(["add", "new.txt"], wt_path)
        _run_git(["commit", "-m", "local work"], wt_path)
        wt = {"path": str(wt_path), "branch": "feature-unpushed",
              "head": _rev_parse(repo, "feature-unpushed"),
              "bare": False, "detached": False, "locked": False, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )
        self.assertEqual(verdict, "needs-push")

    def test_locked_worktree_is_skip_locked(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-locked"
        _run_git(["worktree", "add", "-b", "feature-locked", str(wt_path)], repo)
        _run_git(["worktree", "lock", str(wt_path)], repo)
        wt = {"path": str(wt_path), "branch": "feature-locked", "head": None,
              "bare": False, "detached": False, "locked": True, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )
        self.assertEqual(verdict, "skip: locked")

    def test_detached_worktree_is_skip_detached(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-detached"
        _run_git(["worktree", "add", "--detach", str(wt_path), "main"], repo)
        wt = {"path": str(wt_path), "branch": None, "head": _rev_parse(repo, "main"),
              "bare": False, "detached": True, "locked": False, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )
        self.assertEqual(verdict, "skip: detached")

    def test_registered_but_deleted_dir_is_prune(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-gone"
        _run_git(["worktree", "add", "-b", "feature-gone", str(wt_path)], repo)
        head = _rev_parse(repo, "feature-gone")
        shutil.rmtree(wt_path)
        wt = {"path": str(wt_path), "branch": "feature-gone", "head": head,
              "bare": False, "detached": False, "locked": False, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )
        # either the missing-dir check or a prunable porcelain flag can drive
        # this verdict; only the final classification is asserted here.
        self.assertEqual(verdict, reap.PRUNE)


class ClassifySelfInUseSymlinkTests(EnvIsolatedTestCase):
    def test_symlink_alias_still_matches_self_and_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_wt = Path(tmp) / "real" / "wt"
            real_wt.mkdir(parents=True)
            alias_parent = Path(tmp) / "alias"
            alias_parent.mkdir()
            alias_wt = alias_parent / "wt"
            os.symlink(real_wt, alias_wt)

            wt = {"path": str(real_wt), "branch": "feature", "head": "abc123",
                  "bare": False, "detached": False, "locked": False, "prunable": False}

            verdict = reap.classify(
                wt, Path(tmp) / "repo", "main", [alias_wt], Path(tmp) / "outside", "main", 14,
                ignored_dirs=set(), ignored_prefixes=(),
            )
            self.assertEqual(verdict, "skip: in-use")

            verdict_self = reap.classify(
                wt, Path(tmp) / "repo", "main", [], alias_wt, "main", 14,
                ignored_dirs=set(), ignored_prefixes=(),
            )
            self.assertEqual(verdict_self, "skip: self")


class ScanRepoDeadlineTests(GitFixtureTestCase):
    def test_zero_remaining_skips_fetch(self):
        repo = self.tmp / "repo"
        _init_repo(repo)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("git must not run once the deadline is already spent")

        orig_git = reap.git
        reap.git = fail_if_called
        try:
            result = reap.scan_repo(
                repo, [], self.tmp / "outside", True, 14, set(), (), remaining_s=0,
            )
        finally:
            reap.git = orig_git

        self.assertTrue(result["timed_out"])
        self.assertIn("deadline: fetch skipped", result["note"])


# --- section 4: worktree_has_real_changes -----------------------------------


class WorktreeHasRealChangesPureTests(EnvIsolatedTestCase):
    def test_all_ignored_untracked_returns_false(self):
        status = "?? node_modules/a.js\n"
        result = reap.worktree_has_real_changes(
            Path("/nonexistent"), status, "main", {"node_modules"}, ()
        )
        self.assertFalse(result)

    def test_untracked_real_file_returns_true(self):
        status = "?? src/real.py\n"
        result = reap.worktree_has_real_changes(Path("/nonexistent"), status, "main", set(), ())
        self.assertTrue(result)


class WorktreeHasRealChangesGitTests(GitFixtureTestCase):
    def test_tracked_modified_line_with_no_actual_diff_returns_false(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        status = " M README.md\n"
        result = reap.worktree_has_real_changes(repo, status, "main", set(), ())
        self.assertFalse(result)

    def test_unreadable_diff_ref_fails_dirty(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        status = " M README.md\n"
        result = reap.worktree_has_real_changes(repo, status, "nonexistent-ref-xyz", set(), ())
        self.assertTrue(result)


# --- section 5: run(), apply + json purity, real fixture --------------------


class RunApplyJsonPurityTests(GitFixtureTestCase):
    def test_apply_json_stdout_is_pure_json_worktree_removed(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-merged"
        _run_git(["worktree", "add", "-b", "feature-merged", str(wt_path)], repo)

        sessions_dir = self.tmp / "sessions"
        sessions_dir.mkdir()

        opts = types.SimpleNamespace(
            selftest=False, apply=True, dry_run=False, json=True,
            repo=repo, roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions_dir, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=(),
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = reap.run(opts)

        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        worktrees = payload["repos"][0]["worktrees"]
        wt_real = os.path.realpath(wt_path)
        target = next(w for w in worktrees if w["path"] == wt_real)
        self.assertEqual(target["verdict"], reap.REAP)
        self.assertTrue(target["applied"]["removed"])
        self.assertFalse(wt_path.exists())


# --- section 6: log isolation -----------------------------------------------


class LogIsolationTests(EnvIsolatedTestCase):
    def test_log_writes_under_the_state_dir_of_the_current_environment(self):
        reap.log("marker")

        log_file = Path(os.environ["RIGOPS_STATE_DIR"]) / "reap.log"
        self.assertTrue(log_file.exists())
        self.assertIn("marker", log_file.read_text())


if __name__ == "__main__":
    unittest.main()
