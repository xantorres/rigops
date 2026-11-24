from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import types
import unittest
from pathlib import Path

from rigops import config, reap, reap_guards

from tests.support import cli_argv
from tests.test_reap import GitFixtureTestCase, _init_repo, _rev_parse, _run_git

REPO_ROOT = Path(__file__).resolve().parent.parent


class PreciousPatternsTests(unittest.TestCase):
    def test_config_default_is_the_builtin_pattern_list(self):
        self.assertEqual(config.DEFAULTS["reaper"]["precious"],
                         list(reap_guards.PRECIOUS_PATTERNS))

    def test_custom_patterns_replace_the_defaults(self):
        got = reap_guards.precious_path([".env", "notes.secret"], set(), (), ("*.secret",))
        self.assertEqual(got, "notes.secret")

    def test_empty_patterns_make_no_file_name_precious(self):
        self.assertIsNone(reap_guards.precious_path([".env", "id_rsa"], set(), (), ()))

    def test_empty_patterns_keep_the_nested_repository_guard(self):
        self.assertEqual(reap_guards.precious_path(["vendor/x/"], set(), (), ()), "vendor/x/")


class PreciousConfigCliTests(unittest.TestCase):
    def run_reap(self, precious):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "tree").mkdir()
            (home / "config.json").write_text(json.dumps({"reaper": {"precious": precious}}))
            env = {**os.environ, "HOME": str(home), "RIGOPS_STATE_DIR": str(home / "state"),
                   "RIGOPS_CONFIG": str(home / "config.json"),
                   "XDG_CONFIG_HOME": str(home / "xdg-config"),
                   "XDG_STATE_HOME": str(home / "xdg-state")}
            return subprocess.run([*cli_argv("reap"), "--root", str(home / "tree")],
                                  capture_output=True, text=True, env=env, check=False)

    def test_wrong_type_exits_2_naming_the_key(self):
        for bad in ("*.pem", None, 3, [1], [""]):
            with self.subTest(value=bad):
                proc = self.run_reap(bad)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("reaper.precious", proc.stderr)

    def test_empty_list_is_accepted(self):
        self.assertEqual(self.run_reap([]).returncode, 0)


class TreeFixtureTestCase(GitFixtureTestCase):
    def merged_tree(self, name="wt", gitignore=None):
        repo = self.tmp / "repo"
        _init_repo(repo)
        if gitignore:
            (repo / ".gitignore").write_text(gitignore)
            _run_git(["add", ".gitignore"], repo)
            _run_git(["commit", "-m", "ignore"], repo)
        wt_path = self.tmp / name
        _run_git(["worktree", "add", "-b", name, str(wt_path)], repo)
        wt = {"path": str(wt_path), "branch": name, "head": _rev_parse(repo, name),
              "bare": False, "detached": False, "locked": False, "prunable": False}
        return repo, wt_path, wt

    def classify(self, repo, wt, **extra):
        return reap.classify(wt, repo, "main", [], self.tmp / "outside", "main", 14,
                             ignored_dirs=set(), ignored_prefixes=(), **extra)

    def run_reap(self, repo, apply=False, dry_run=False, as_json=True, **extra):
        self.stub_process_observation()
        sessions = self.tmp / "sessions"
        sessions.mkdir(exist_ok=True)
        opts = types.SimpleNamespace(
            selftest=False, apply=apply, dry_run=dry_run, json=as_json,
            repo=[repo], roots=[], no_fetch=True, grace_days=14,
            sessions_dir=sessions, deadline_s=60,
            max_kills_per_tree=20, orphan_min_age_s=172800, kill_grace_s=5,
            ignored_untracked_dirs=set(), ignored_untracked_prefixes=(), **extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(reap.run(opts), 0)
        out = buf.getvalue()
        return json.loads(out) if as_json else out

    def reap_log(self) -> str:
        path = Path(os.environ["RIGOPS_STATE_DIR"]) / "reap.log"
        return path.read_text() if path.exists() else ""


class PreciousClassifyTests(TreeFixtureTestCase):
    def test_empty_precious_list_lets_an_env_file_go(self):
        repo, wt_path, wt = self.merged_tree(gitignore=".env\n")
        (wt_path / ".env").write_text("X=1\n")
        self.assertEqual(self.classify(repo, wt, precious=()), reap.REAP)

    def test_custom_precious_list_protects_its_pattern_only(self):
        repo, wt_path, wt = self.merged_tree(gitignore=".env\n*.secret\n")
        (wt_path / ".env").write_text("X=1\n")
        self.assertEqual(self.classify(repo, wt, precious=("*.secret",)), reap.REAP)
        (wt_path / "a.secret").write_text("X=1\n")
        self.assertEqual(self.classify(repo, wt, precious=("*.secret",)), "skip: precious")

    def test_run_reads_the_precious_option(self):
        repo, wt_path, _wt = self.merged_tree(gitignore=".env\n")
        (wt_path / ".env").write_text("X=1\n")
        kept = self.run_reap(repo)["repos"][0]["worktrees"][0]
        freed = self.run_reap(repo, precious=[])["repos"][0]["worktrees"][0]
        self.assertEqual((kept["verdict"], freed["verdict"]), ("skip: precious", reap.REAP))


class PreciousSkipNamesFileAndKeyTests(TreeFixtureTestCase):
    def setUp(self):
        super().setUp()
        self.repo, self.wt_path, _wt = self.merged_tree(gitignore=".env\n")
        (self.wt_path / ".env").write_text("X=1\n")

    def test_json_names_the_file(self):
        wt = self.run_reap(self.repo)["repos"][0]["worktrees"][0]
        self.assertEqual(wt["verdict"], "skip: precious")
        self.assertEqual(wt["precious_file"], ".env")

    def test_report_names_the_file_and_the_key(self):
        text = self.run_reap(self.repo, as_json=False)
        self.assertIn(".env", text)
        self.assertIn("reaper.precious", text)

    def test_dry_run_output_names_the_file_and_the_key(self):
        text = self.run_reap(self.repo, apply=True, dry_run=True, as_json=False)
        self.assertIn(".env", text)
        self.assertIn("reaper.precious", text)

    def test_log_line_names_the_file_and_the_key(self):
        self.run_reap(self.repo)
        line = next(ln for ln in self.reap_log().splitlines() if "SKIP-PRECIOUS" in ln)
        self.assertIn("file=.env", line)
        self.assertIn("reaper.precious", line)


class IgnoredFilesListingTests(TreeFixtureTestCase):
    def ds_store_and_node_modules(self):
        repo, wt_path, wt = self.merged_tree(gitignore=".DS_Store\nnode_modules/\n")
        (wt_path / ".DS_Store").write_bytes(b"\0")
        pkg = wt_path / "node_modules" / "left-pad"
        pkg.mkdir(parents=True)
        (pkg / "index.js").write_text("module.exports = 1\n")
        (pkg / "package.json").write_text("{}\n")
        return repo, wt_path, wt

    def test_a_tree_holding_only_ds_store_and_node_modules_is_removed(self):
        repo, wt_path, _wt = self.ds_store_and_node_modules()

        dry = self.run_reap(repo, apply=True, dry_run=True, as_json=False)
        self.assertIn(".DS_Store", dry)
        self.assertIn("node_modules/", dry)
        self.assertTrue(wt_path.exists())

        payload = self.run_reap(repo, apply=True)
        target = payload["repos"][0]["worktrees"][0]
        self.assertTrue(target["applied"]["removed"])
        self.assertFalse(wt_path.exists())

    def test_json_lists_top_level_entries_once_each(self):
        repo, _wt_path, _wt = self.ds_store_and_node_modules()
        target = self.run_reap(repo)["repos"][0]["worktrees"][0]
        self.assertEqual(target["ignored_files"], [".DS_Store", "node_modules/"])

    def test_plain_report_lists_what_goes_with_the_tree(self):
        repo, _wt_path, _wt = self.ds_store_and_node_modules()
        text = self.run_reap(repo, as_json=False)
        self.assertIn(".DS_Store", text)
        self.assertIn("node_modules/", text)
        self.assertNotIn("index.js", text)

    def test_real_removal_logs_the_list_before_removing(self):
        repo, _wt_path, _wt = self.ds_store_and_node_modules()
        self.run_reap(repo, apply=True)
        lines = self.reap_log().splitlines()
        listed = next(i for i, ln in enumerate(lines) if "REAP-IGNORED" in ln)
        removed = next(i for i, ln in enumerate(lines) if " REAP repo=" in ln)
        self.assertLess(listed, removed)
        self.assertIn(".DS_Store", lines[listed])
        self.assertIn("node_modules/", lines[listed])

    def test_printed_list_is_capped(self):
        repo, wt_path, _wt = self.merged_tree(gitignore="*.tmp\n")
        for i in range(25):
            (wt_path / f"f{i:02d}.tmp").write_text("x")
        target = self.run_reap(repo)["repos"][0]["worktrees"][0]
        self.assertEqual(len(target["ignored_files"]), 25)
        text = self.run_reap(repo, as_json=False)
        self.assertIn("and 15 more", text)
        self.assertNotIn("f24.tmp", text)

    def test_tree_without_ignored_files_has_an_empty_list(self):
        repo, _wt_path, _wt = self.merged_tree()
        target = self.run_reap(repo)["repos"][0]["worktrees"][0]
        self.assertEqual(target["ignored_files"], [])


if __name__ == "__main__":
    unittest.main()
