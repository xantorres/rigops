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


class RemainingAfterTests(EnvIsolatedTestCase):
    def test_no_deadline_stays_none(self):
        self.assertIsNone(reap.remaining_after(None, 1000.0))

    def test_budget_shrinks_by_the_time_already_spent(self):
        self.addCleanup(setattr, reap, "time", reap.time)
        reap.time = types.SimpleNamespace(time=lambda: 1030.0)

        self.assertEqual(reap.remaining_after(90.0, 1000.0), 60.0)

    def test_overspent_budget_goes_negative_rather_than_clamping(self):
        self.addCleanup(setattr, reap, "time", reap.time)
        reap.time = types.SimpleNamespace(time=lambda: 1200.0)

        self.assertEqual(reap.remaining_after(90.0, 1000.0), -110.0)


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


class RunCapturedUndecodableOutputTests(EnvIsolatedTestCase):
    def test_non_utf8_bytes_round_trip_instead_of_raising(self):
        argv = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'bad\\xff\\0')"]
        proc = reap._run_captured(argv, timeout=10, env=dict(os.environ))
        name = proc.stdout.split("\0")[0]
        self.assertEqual(os.fsencode(name), b"bad\xff")


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

            # macOS still answers stat() for a path inside a mode-000
            # directory, so the chmod alone leaves this test passing there
            # while Linux raises. Deny it explicitly to get the same walk on
            # both.
            real_is_dir = Path.is_dir

            def deny_under_blocked(self, *args, **kwargs):
                if self != blocked_group and blocked_group in self.parents:
                    raise PermissionError(13, "Permission denied", str(self))
                return real_is_dir(self, *args, **kwargs)

            Path.is_dir = deny_under_blocked
            try:
                repos = reap.discover_repos(root)
            finally:
                Path.is_dir = real_is_dir
                os.chmod(blocked_group, 0o755)

            self.assertEqual(repos, [good_repo])


class DiscoverReposDepthTests(EnvIsolatedTestCase):
    def test_depth_one_and_two_dirs_found_git_files_and_nested_repos_skipped(self):
        root = self.tmp / "root"
        (root / "x" / ".git").mkdir(parents=True)
        (root / "x" / "inner" / ".git").mkdir(parents=True)
        (root / "g" / "y" / ".git").mkdir(parents=True)
        (root / "g" / "z").mkdir(parents=True)
        (root / "g" / "z" / ".git").write_text("gitdir: /elsewhere/.git/modules/z\n")
        (root / "w").mkdir(parents=True)
        (root / "w" / ".git").write_text("gitdir: /elsewhere/.git/worktrees/w\n")

        self.assertEqual(reap.discover_repos(root), [root / "g" / "y", root / "x"])


class LiveWorktreeCwdsNullPidTests(EnvIsolatedTestCase):
    def test_null_pid_ignored_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp)
            (sessions_dir / "bad.json").write_text('{"pid": null, "cwd": "/tmp/x"}')
            live = reap.live_worktree_cwds(sessions_dir)
            self.assertEqual(live, [])


class StaleWorktreeDirsTests(EnvIsolatedTestCase):
    def test_symlink_under_worktrees_is_never_stale(self):
        repo = self.tmp / "repo"
        base = repo / ".claude" / "worktrees"
        base.mkdir(parents=True)
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, base / "link-out")
        leftover = base / "leftover"
        leftover.mkdir()

        stale = reap.stale_worktree_dirs(repo, set(), [])

        self.assertEqual(stale, [os.path.realpath(leftover)])

    def test_symlinked_base_never_yields_a_dir_outside_the_repo(self):
        cases = {
            "worktrees": lambda repo, shared: (
                (repo / ".claude").mkdir(),
                os.symlink(shared, repo / ".claude" / "worktrees")),
            "claude": lambda repo, shared: os.symlink(shared.parent, repo / ".claude"),
        }
        for name, link in cases.items():
            with self.subTest(name):
                repo = self.tmp / name / "repo"
                repo.mkdir(parents=True)
                shared = self.tmp / name / "shared" / "worktrees"
                (shared / "another-repos-tree").mkdir(parents=True)
                link(repo, shared)

                self.assertEqual(reap.stale_worktree_dirs(repo, set(), []), [])


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

    def test_unreadable_liveness_is_a_failed_kill_not_gone(self):
        def raising_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ps", timeout=10)
        orig_run = subprocess.run
        subprocess.run = raising_run
        self.addCleanup(setattr, subprocess, "run", orig_run)
        snap = {900001: {"ppid": 1, "pgid": 900001, "uid": os.getuid(), "tty": "??",
                         "etimes": 10, "command": "sleep 300"}}

        self.assertTrue(reap.process_alive(900001))
        result = reap.kill_pids("tree-label", {900001: "cwd"}, snap, apply=True,
                                max_kills=20, grace_s=0)

        self.assertEqual(result["failed_pids"], [900001])
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


class SnapshotFailureTests(EnvIsolatedTestCase):
    def _with_run(self, fake):
        orig_run = subprocess.run
        subprocess.run = fake
        self.addCleanup(setattr, subprocess, "run", orig_run)

    def _raising(self, exc):
        def fake(*args, **kwargs):
            raise exc
        return fake

    def _returning(self, rc, stdout):
        def fake(*args, **kwargs):
            return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr="")
        return fake

    def test_ps_failure_is_none_not_empty(self):
        cases = [
            ("timeout", self._raising(subprocess.TimeoutExpired(cmd="ps", timeout=30))),
            ("oserror", self._raising(FileNotFoundError("ps"))),
            ("rc1", self._returning(1, "")),
            ("own_pid_missing", self._returning(0, "garbage\n")),
        ]
        for name, fake in cases:
            with self.subTest(name):
                self._with_run(fake)
                self.assertIsNone(reap.ps_snapshot())

    def test_lsof_failure_is_none_not_empty(self):
        cases = [
            ("timeout", self._raising(subprocess.TimeoutExpired(cmd="lsof", timeout=30))),
            ("oserror", self._raising(FileNotFoundError("lsof"))),
            ("rc2", self._returning(2, "")),
            ("rc1_empty", self._returning(1, "")),
            ("own_pid_missing", self._returning(0, "p123\nn/x\n")),
        ]
        for name, fake in cases:
            with self.subTest(name):
                self._with_run(fake)
                self.assertIsNone(reap.cwd_map())

    def test_lsof_partial_rc1_is_still_usable(self):
        me = os.getpid()
        self._with_run(self._returning(1, f"p123\nn/x\np{me}\nn/here\n"))
        self.assertEqual(reap.cwd_map(), {123: "/x", me: "/here"})


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

    def _verdict_after_tracked_edit(self, rel: str) -> str:
        repo = self.tmp / "repo"
        _init_repo(repo)
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("base\n")
        _run_git(["add", "-A"], repo)
        _run_git(["commit", "-m", "add unusual path"], repo)
        wt_path = self.tmp / "wt-quoted"
        _run_git(["worktree", "add", "-b", "feature-quoted", str(wt_path)], repo)
        (wt_path / rel).write_text("base\nlocal edit\n")
        wt = {"path": str(wt_path), "branch": "feature-quoted",
              "head": _rev_parse(repo, "feature-quoted"),
              "bare": False, "detached": False, "locked": False, "prunable": False}
        return reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )

    def test_tracked_edit_in_path_with_space_is_skip_dirty(self):
        self.assertEqual(self._verdict_after_tracked_edit("d sp/f.txt"), "skip: dirty")

    def test_tracked_edit_in_non_ascii_path_is_skip_dirty(self):
        self.assertEqual(self._verdict_after_tracked_edit("café.txt"), "skip: dirty")

    def test_tracked_edit_in_path_with_pathspec_magic_is_skip_dirty(self):
        self.assertEqual(self._verdict_after_tracked_edit(":colon.txt"), "skip: dirty")

    def test_tracked_edit_in_path_with_rename_arrow_is_skip_dirty(self):
        self.assertEqual(self._verdict_after_tracked_edit("a -> b.txt"), "skip: dirty")

    def test_staged_rename_onto_a_path_main_already_has_is_skip_dirty(self):
        # The new path matches main, so only the deleted source shows the work.
        repo = self.tmp / "repo"
        _init_repo(repo)
        (repo / "a.txt").write_text("base\n")
        _run_git(["add", "a.txt"], repo)
        _run_git(["commit", "-m", "add a"], repo)
        wt_path = self.tmp / "wt-rename"
        _run_git(["worktree", "add", "-b", "feature-rename", str(wt_path)], repo)
        (repo / "b.txt").write_text("base\n")
        _run_git(["add", "b.txt"], repo)
        _run_git(["commit", "-m", "add b"], repo)
        _run_git(["mv", "a.txt", "b.txt"], wt_path)
        wt = {"path": str(wt_path), "branch": "feature-rename",
              "head": _rev_parse(repo, "feature-rename"),
              "bare": False, "detached": False, "locked": False, "prunable": False}
        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(),
        )
        self.assertEqual(verdict, "skip: dirty")

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

    def test_spent_budget_is_skip_deadline_and_runs_no_git(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-merged"
        _run_git(["worktree", "add", "-b", "feature-merged", str(wt_path)], repo)
        wt = {"path": str(wt_path), "branch": "feature-merged",
              "head": _rev_parse(repo, "feature-merged"),
              "bare": False, "detached": False, "locked": False, "prunable": False}

        def fail_if_called(*args, **kwargs):
            raise AssertionError("git must not run once the deadline is already spent")

        for name in ("git", "git_ok", "git_lines"):
            self.addCleanup(setattr, reap, name, getattr(reap, name))
            setattr(reap, name, fail_if_called)

        verdict = reap.classify(
            wt, repo, "main", [], self.tmp / "outside", "main", 14,
            ignored_dirs=set(), ignored_prefixes=(), remaining_s=0,
        )
        self.assertEqual(verdict, "skip: deadline")


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

    def test_budget_spent_mid_repo_skips_the_remaining_worktrees(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        for name in ("wt-one", "wt-two"):
            _run_git(["worktree", "add", "-b", f"feature-{name}", str(self.tmp / name)], repo)

        clock = {"now": 1000.0}
        real_age_days = reap.age_days

        def burn_the_budget(path):
            clock["now"] += 100.0
            return real_age_days(path)

        self.addCleanup(setattr, reap, "time", reap.time)
        self.addCleanup(setattr, reap, "age_days", reap.age_days)
        reap.time = types.SimpleNamespace(time=lambda: clock["now"])
        reap.age_days = burn_the_budget

        result = reap.scan_repo(
            repo, [], self.tmp / "outside", False, 14, set(), (), remaining_s=60,
        )

        verdicts = [w["verdict"] for w in result["worktrees"]]
        self.assertEqual(len(verdicts), 2)
        self.assertNotEqual(verdicts[0], "skip: deadline")
        self.assertEqual(verdicts[1], "skip: deadline")
        self.assertTrue(result["timed_out"])

    def test_listing_deadline_marks_the_repository_timed_out(self):
        repo = self.tmp / "repo"
        _init_repo(repo)

        result = reap.scan_repo(
            repo, [], self.tmp / "outside", False, 14, set(), (), remaining_s=0,
        )

        self.assertTrue(result["timed_out"])
        self.assertFalse(result["listing_ok"])
        self.assertIn("worktree listing skipped", result["note"])

    def test_target_resolution_deadline_skips_every_worktree(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        _run_git(["worktree", "add", "-b", "feature-one", str(self.tmp / "wt-one")], repo)

        clock = {"now": 1000.0}
        real_parse = reap.parse_worktrees

        def burn_the_budget(porcelain):
            clock["now"] += 100.0
            return real_parse(porcelain)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("git must not run once the deadline is already spent")

        self.addCleanup(setattr, reap, "time", reap.time)
        self.addCleanup(setattr, reap, "parse_worktrees", reap.parse_worktrees)
        self.addCleanup(setattr, reap, "resolve_target", reap.resolve_target)
        self.addCleanup(setattr, reap, "default_branch", reap.default_branch)
        reap.time = types.SimpleNamespace(time=lambda: clock["now"])
        reap.parse_worktrees = burn_the_budget
        reap.resolve_target = fail_if_called
        reap.default_branch = fail_if_called

        result = reap.scan_repo(
            repo, [], self.tmp / "outside", False, 14, set(), (), remaining_s=60,
        )

        self.assertTrue(result["timed_out"])
        self.assertTrue(result["listing_ok"])
        self.assertEqual(result["note"], "deadline: target unresolved")
        self.assertEqual([w["verdict"] for w in result["worktrees"]], ["skip: deadline"])

class ResolveTargetBudgetTests(EnvIsolatedTestCase):
    def _fake_clock_and_probes(self, spend_s: float) -> list:
        clock = {"now": 1000.0}
        probes = []

        def probe(repo, *args, timeout=None, **kwargs):
            probes.append(timeout)
            clock["now"] += spend_s
            return None

        self.addCleanup(setattr, reap, "time", reap.time)
        self.addCleanup(setattr, reap, "git_ok", reap.git_ok)
        reap.time = types.SimpleNamespace(time=lambda: clock["now"])
        reap.git_ok = probe
        return probes

    def test_each_candidate_is_clamped_against_what_is_left(self):
        probes = self._fake_clock_and_probes(spend_s=20.0)

        target = reap.resolve_target(Path("/nonexistent"), timeout=120, remaining_s=50)

        self.assertIsNone(target)
        self.assertEqual(probes, [50, 30, 10])

    def test_spent_budget_stops_the_loop_instead_of_probing_on(self):
        probes = self._fake_clock_and_probes(spend_s=60.0)

        target = reap.resolve_target(Path("/nonexistent"), timeout=120, remaining_s=60)

        self.assertIsNone(target)
        self.assertEqual(probes, [60])

    def test_no_deadline_probes_every_candidate_at_the_base_timeout(self):
        probes = self._fake_clock_and_probes(spend_s=1000.0)

        target = reap.resolve_target(Path("/nonexistent"), timeout=120)

        self.assertIsNone(target)
        self.assertEqual(probes, [120] * len(reap.MERGE_TARGETS))


class ScanRepoTargetBudgetTests(GitFixtureTestCase):
    def test_scan_hands_resolve_target_the_remaining_budget(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        _run_git(["worktree", "add", "-b", "feature-one", str(self.tmp / "wt-one")], repo)
        seen = {}

        def record(repo_arg, timeout=None, remaining_s=None):
            seen["timeout"] = timeout
            seen["remaining_s"] = remaining_s
            return None

        self.addCleanup(setattr, reap, "resolve_target", reap.resolve_target)
        reap.resolve_target = record

        reap.scan_repo(repo, [], self.tmp / "outside", False, 14, set(), (), remaining_s=90)

        self.assertIsNotNone(seen["remaining_s"])
        self.assertLessEqual(seen["remaining_s"], 90)


class DirSizeBudgetTests(EnvIsolatedTestCase):
    def test_timeout_is_passed_through_to_du(self):
        seen = {}

        def record(argv, **kwargs):
            seen["argv"] = argv
            seen["timeout"] = kwargs.get("timeout")
            return types.SimpleNamespace(returncode=0, stdout="8\t/x\n")

        self.addCleanup(setattr, reap, "subprocess", reap.subprocess)
        reap.subprocess = types.SimpleNamespace(
            run=record, TimeoutExpired=subprocess.TimeoutExpired,
        )
        size = reap.dir_size(Path("/x"), timeout=17)

        self.assertEqual(size, 8 * 1024)
        self.assertEqual(seen["timeout"], 17)


class ScanRepoDirSizeBudgetTests(GitFixtureTestCase):
    def _repo_with_one_reapable_worktree(self) -> Path:
        repo = self.tmp / "repo"
        _init_repo(repo)
        _run_git(["worktree", "add", "-b", "feature-one", str(self.tmp / "wt-one")], repo)
        return repo

    def test_scan_hands_dir_size_the_remaining_budget(self):
        repo = self._repo_with_one_reapable_worktree()
        seen = {}

        def record(path, timeout=None):
            seen["timeout"] = timeout
            return 0

        self.addCleanup(setattr, reap, "classify", reap.classify)
        self.addCleanup(setattr, reap, "dir_size", reap.dir_size)
        reap.classify = lambda *a, **k: reap.REAP
        reap.dir_size = record

        reap.scan_repo(repo, [], self.tmp / "outside", False, 14, set(), (), remaining_s=90)

        self.assertIsNotNone(seen["timeout"])
        self.assertLessEqual(seen["timeout"], 90)

    def test_spent_budget_skips_the_size_probe(self):
        repo = self._repo_with_one_reapable_worktree()
        clock = {"now": 1000.0}

        def burn_then_reap(*args, **kwargs):
            clock["now"] += 100.0
            return reap.REAP

        def fail_if_called(*args, **kwargs):
            raise AssertionError("du must not run once the deadline is already spent")

        self.addCleanup(setattr, reap, "time", reap.time)
        self.addCleanup(setattr, reap, "classify", reap.classify)
        self.addCleanup(setattr, reap, "dir_size", reap.dir_size)
        reap.time = types.SimpleNamespace(time=lambda: clock["now"])
        reap.classify = burn_then_reap
        reap.dir_size = fail_if_called

        result = reap.scan_repo(
            repo, [], self.tmp / "outside", False, 14, set(), (), remaining_s=60,
        )

        self.assertEqual([w["size"] for w in result["worktrees"]], [0])



# --- section 4: worktree_has_real_changes -----------------------------------


class WorktreeHasRealChangesPureTests(EnvIsolatedTestCase):
    def test_all_ignored_untracked_returns_false(self):
        status = "?? node_modules/a.js\0"
        result = reap.worktree_has_real_changes(
            Path("/nonexistent"), status, "main", {"node_modules"}, ()
        )
        self.assertFalse(result)

    def test_untracked_real_file_returns_true(self):
        status = "?? src/real.py\0"
        result = reap.worktree_has_real_changes(Path("/nonexistent"), status, "main", set(), ())
        self.assertTrue(result)


class WorktreeHasRealChangesGitTests(GitFixtureTestCase):
    def test_tracked_modified_line_with_no_actual_diff_returns_false(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        status = " M README.md\0"
        result = reap.worktree_has_real_changes(repo, status, "main", set(), ())
        self.assertFalse(result)

    def test_unreadable_diff_ref_fails_dirty(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        status = " M README.md\0"
        result = reap.worktree_has_real_changes(repo, status, "nonexistent-ref-xyz", set(), ())
        self.assertTrue(result)

    def test_rename_source_path_is_not_read_as_an_entry(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        status = "R  README.md\0?? scratch.txt\0"
        result = reap.worktree_has_real_changes(repo, status, "main", set(), ())
        self.assertFalse(result)


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
            repo=[repo], roots=[], no_fetch=True, grace_days=14,
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


class RunRecheckBeforeRemovalTests(GitFixtureTestCase):
    def _apply_with_change_after_scan(self, change, dry_run=False) -> tuple[Path, dict]:
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-merged"
        _run_git(["worktree", "add", "-b", "feature-merged", str(wt_path)], repo)
        sessions_dir = self.tmp / "sessions"
        sessions_dir.mkdir()
        opts = types.SimpleNamespace(
            selftest=False, apply=True, dry_run=dry_run, json=True,
            repo=[repo], roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions_dir, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=(),
        )

        # ps_snapshot is the first call after the scan loop, so this lands
        # between the verdict and anything acting on it.
        orig = reap.ps_snapshot
        self.addCleanup(setattr, reap, "ps_snapshot", orig)
        reap.ps_snapshot = lambda: (change(wt_path, sessions_dir, opts), orig())[1]

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            reap.run(opts)
        worktrees = json.loads(buf.getvalue())["repos"][0]["worktrees"]
        wt_real = os.path.realpath(wt_path)
        return wt_path, next(w for w in worktrees if w["path"] == wt_real)

    def test_tree_dirtied_after_scan_survives_apply(self):
        def dirty(wt_path, _sessions, _opts):
            (wt_path / "notes.txt").write_text("wip\n")

        wt_path, target = self._apply_with_change_after_scan(dirty)

        self.assertTrue(wt_path.exists())
        self.assertEqual(target["verdict"], "skip: dirty")
        self.assertFalse(target["applied"]["removed"])
        self.assertIn("verdict changed", target["applied"]["detail"])

    def test_dry_run_reports_the_changed_verdict(self):
        def dirty(wt_path, _sessions, _opts):
            (wt_path / "notes.txt").write_text("wip\n")

        _wt_path, target = self._apply_with_change_after_scan(dirty, dry_run=True)

        self.assertEqual(target["verdict"], "skip: dirty")
        self.assertIn("verdict changed", target["applied"]["detail"])

    def test_session_started_after_scan_blocks_removal(self):
        def occupy(wt_path, sessions, _opts):
            (sessions / "x.json").write_text(json.dumps(
                {"pid": os.getpid(), "cwd": os.path.realpath(wt_path)}))

        wt_path, target = self._apply_with_change_after_scan(occupy)

        self.assertTrue(wt_path.exists())
        self.assertEqual(target["verdict"], "skip: in-use")
        self.assertFalse(target["applied"]["removed"])

    def test_session_process_started_after_scan_is_not_signalled(self):
        procs = []

        def occupy(wt_path, sessions, _opts):
            # Own session, no controlling tty: the kind of pid reap may signal.
            proc = subprocess.Popen(["sleep", "60"], cwd=wt_path, start_new_session=True)
            self.addCleanup(proc.wait)
            self.addCleanup(proc.kill)
            procs.append(proc)
            (sessions / "x.json").write_text(json.dumps(
                {"pid": proc.pid, "cwd": os.path.realpath(wt_path)}))

        wt_path, target = self._apply_with_change_after_scan(occupy)

        self.assertIsNone(procs[0].poll())
        self.assertTrue(wt_path.exists())
        self.assertEqual(target["verdict"], "skip: in-use")

    def test_branch_detached_after_scan_is_kept(self):
        def detach(wt_path, _sessions, _opts):
            _run_git(["checkout", "--detach"], wt_path)
            _run_git(["commit", "--allow-empty", "-m", "wip"], wt_path)

        wt_path, target = self._apply_with_change_after_scan(detach)

        self.assertTrue(wt_path.exists())
        self.assertEqual(target["verdict"], "skip: detached")

    def test_deadline_spent_before_recheck_keeps_tree(self):
        def spend(_wt_path, _sessions, opts):
            opts.deadline_s = 0

        wt_path, target = self._apply_with_change_after_scan(spend)

        self.assertTrue(wt_path.exists())
        self.assertEqual(target["verdict"], reap.DEADLINE_SKIP)


class RunObservationFailureTests(GitFixtureTestCase):
    def _apply_with_failing(self, failing, setup=None) -> tuple[Path, dict, str, dict]:
        repo = self.tmp / "repo"
        _init_repo(repo)
        wt_path = self.tmp / "wt-merged"
        _run_git(["worktree", "add", "-b", "feature-merged", str(wt_path)], repo)
        sessions_dir = self.tmp / "sessions"
        sessions_dir.mkdir()
        if setup:
            setup(repo)

        # git runs through Popen, so only ps, lsof and du see this.
        orig_run = subprocess.run

        def fake_run(argv, *args, **kwargs):
            if failing(argv):
                raise subprocess.TimeoutExpired(cmd=argv, timeout=30)
            return orig_run(argv, *args, **kwargs)
        subprocess.run = fake_run
        self.addCleanup(setattr, subprocess, "run", orig_run)

        opts = types.SimpleNamespace(
            selftest=False, apply=True, dry_run=False, json=True,
            repo=[repo], roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions_dir, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=(),
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            reap.run(opts)
        payload = json.loads(out.getvalue())
        wt_real = os.path.realpath(wt_path)
        target = next(w for w in payload["repos"][0]["worktrees"] if w["path"] == wt_real)
        return wt_path, target, err.getvalue(), payload

    def test_lsof_failure_keeps_tree(self):
        wt_path, target, err, payload = self._apply_with_failing(
            lambda argv: argv[0] == "lsof")
        self.assertTrue(wt_path.exists())
        self.assertFalse(target["applied"]["removed"])
        self.assertIn("observation", target["applied"]["detail"])
        self.assertIn("lsof", err)
        self.assertFalse(payload["process_observation_ok"])

    def test_ps_failure_keeps_tree(self):
        wt_path, target, err, payload = self._apply_with_failing(
            lambda argv: argv[:2] == ["ps", "-axo"])
        self.assertTrue(wt_path.exists())
        self.assertFalse(target["applied"]["removed"])
        self.assertIn("ps", err)
        self.assertFalse(payload["process_observation_ok"])

    def test_lsof_failure_signals_nothing_in_a_stale_dir(self):
        procs = []

        def plant(repo):
            stale = repo / ".claude" / "worktrees" / "stale1"
            stale.mkdir(parents=True)
            # Claimed by argv, the one match left with lsof down.
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)", os.path.realpath(stale)],
                start_new_session=True)
            self.addCleanup(proc.wait)
            self.addCleanup(proc.kill)
            procs.append(proc)

        self._apply_with_failing(lambda argv: argv[0] == "lsof", plant)

        self.assertIsNone(procs[0].poll())


class RunRepeatedRepoTests(GitFixtureTestCase):
    def _scanned(self, repos):
        sessions_dir = self.tmp / "sessions"
        sessions_dir.mkdir(exist_ok=True)
        opts = types.SimpleNamespace(
            selftest=False, apply=False, dry_run=False, json=True,
            repo=repos, roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions_dir, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=(),
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(reap.run(opts), 0)
        return [r["repo"] for r in json.loads(buf.getvalue())["repos"]]

    def test_every_repo_flag_is_scanned(self):
        first = self.tmp / "first"
        second = self.tmp / "second"
        _init_repo(first)
        _init_repo(second)

        self.assertEqual(
            self._scanned([first, second]),
            [str(first.resolve()), str(second.resolve())],
        )

    def test_duplicate_paths_are_scanned_once(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        alias = self.tmp / "alias"
        os.symlink(repo, alias)

        self.assertEqual(self._scanned([repo, alias, repo]), [str(repo.resolve())])


class RunSummaryTests(GitFixtureTestCase):
    def test_summary_line_reports_the_deadline_skip_count(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        _run_git(["worktree", "add", "-b", "feature-merged", str(self.tmp / "wt-merged")], repo)
        sessions_dir = self.tmp / "sessions"
        sessions_dir.mkdir()

        opts = types.SimpleNamespace(
            selftest=False, apply=False, dry_run=False, json=True,
            repo=[repo], roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions_dir, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=(),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reap.run(opts), 0)

        log_lines = (Path(os.environ["RIGOPS_STATE_DIR"]) / "reap.log").read_text().splitlines()
        summary = [line for line in log_lines if "reaper:" in line][-1]
        self.assertIn("deadline_skipped=0", summary)
        self.assertIn("listing_skipped=0", summary)

    def test_summary_line_counts_a_repository_whose_listing_was_skipped(self):
        repo = self.tmp / "repo"
        _init_repo(repo)
        _run_git(["worktree", "add", "-b", "feature-merged", str(self.tmp / "wt-merged")], repo)
        sessions_dir = self.tmp / "sessions"
        sessions_dir.mkdir()

        opts = types.SimpleNamespace(
            selftest=False, apply=False, dry_run=False, json=True,
            repo=[repo], roots=[], no_fetch=True, grace_days=14,
            # At clamp_timeout's floor, so the run loop still enters the scan
            # but every call inside it is judged too thin to risk.
            sessions_dir=sessions_dir, deadline_s=5,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=(),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reap.run(opts), 0)

        log_lines = (Path(os.environ["RIGOPS_STATE_DIR"]) / "reap.log").read_text().splitlines()
        summary = [line for line in log_lines if "reaper:" in line][-1]
        self.assertIn("deadline_skipped=0", summary)
        self.assertIn("listing_skipped=1", summary)


class ScanRepoListingSkippedFlagTests(GitFixtureTestCase):
    def test_deadline_on_the_listing_is_distinct_from_an_unreadable_one(self):
        repo = self.tmp / "repo"
        _init_repo(repo)

        deadline = reap.scan_repo(
            repo, [], self.tmp / "outside", False, 14, set(), (), remaining_s=0,
        )
        self.assertTrue(deadline["listing_skipped"])

        self.addCleanup(setattr, reap, "git_ok", reap.git_ok)
        reap.git_ok = lambda *a, **k: None
        unreadable = reap.scan_repo(repo, [], self.tmp / "outside", False, 14, set(), ())

        self.assertFalse(unreadable["listing_ok"])
        self.assertFalse(unreadable["listing_skipped"])


# --- section 6: log isolation -----------------------------------------------


class LogIsolationTests(EnvIsolatedTestCase):
    def test_log_writes_under_the_state_dir_of_the_current_environment(self):
        reap.log("marker")

        log_file = Path(os.environ["RIGOPS_STATE_DIR"]) / "reap.log"
        self.assertTrue(log_file.exists())
        self.assertIn("marker", log_file.read_text())


if __name__ == "__main__":
    unittest.main()
