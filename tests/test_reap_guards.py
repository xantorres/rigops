from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import reap_guards  # noqa: E402
from rigops.reap_procs import orphan_daemons  # noqa: E402

LIST_CASES = [
    ("list_of_strings", ["a", "b"], None),
    ("empty_list", [], None),
    ("string", "~/projects", "bad"),
    ("none", None, "bad"),
    ("dict", {"a": "b"}, "bad"),
    ("number", 3, "bad"),
    ("empty_string_item", ["a", ""], "bad"),
    ("blank_string_item", ["a", "  "], "bad"),
    ("number_item", ["a", 1], "bad"),
    ("nested_list_item", ["a", ["b"]], "bad"),
]

BOOL_CASES = [
    ("true", True, None),
    ("false", False, None),
    ("string_false", "false", "bad"),
    ("int", 1, "bad"),
    ("none", None, "bad"),
]



class ListSettingErrorTests(unittest.TestCase):
    def test_table(self):
        for name, value, expected in LIST_CASES:
            with self.subTest(name):
                got = reap_guards.list_setting_error("reaper.roots", value)
                if expected is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual(
                        got, "reaper.roots in config must be a list of non-empty strings")


class BoolSettingErrorTests(unittest.TestCase):
    def test_table(self):
        for name, value, expected in BOOL_CASES:
            with self.subTest(name):
                got = reap_guards.bool_setting_error("reaper.orphan_sweep", value)
                if expected is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual(got, "reaper.orphan_sweep in config must be true or false")



def _proc(ppid=1, uid=None, etimes=200000, command="node /w/root/node_modules/.bin/tsserver"):
    return {"ppid": ppid, "pgid": 1, "uid": os.getuid() if uid is None else uid,
            "tty": "??", "etimes": etimes, "command": command}


ROOT = "/w/root"
MIN_AGE = 172800


class OrphanDaemonsTests(unittest.TestCase):
    def run_one(self, info, cwd=ROOT + "/proj", extra=None):
        snap = {900001: info, **(extra or {})}
        cwds = {900001: cwd} if cwd else {}
        return orphan_daemons(snap, cwds, ROOT, MIN_AGE)

    def test_matching_daemon_by_cwd(self):
        self.assertEqual(self.run_one(_proc()), {900001})

    def test_matching_daemon_by_argument_path(self):
        info = _proc(command="node /elsewhere/node_modules/x " + ROOT + "/proj/a.js")
        self.assertEqual(self.run_one(info, cwd="/elsewhere"), {900001})

    def test_each_exclusion(self):
        cases = {
            "other_uid": _proc(uid=os.getuid() + 1),
            "ppid_not_1": _proc(ppid=4242),
            "no_node_modules": _proc(command="node /w/root/proj/a.js"),
            "exactly_min_age": _proc(etimes=MIN_AGE),
        }
        for name, info in cases.items():
            with self.subTest(name):
                self.assertEqual(self.run_one(info), set())

    def test_outside_root(self):
        info = _proc(command="node /elsewhere/node_modules/x")
        self.assertEqual(self.run_one(info, cwd="/elsewhere/proj"), set())

    def test_protected_pid_excluded(self):
        me = os.getpid()
        snap = {me: _proc()}
        self.assertEqual(orphan_daemons(snap, {me: ROOT + "/p"}, ROOT, MIN_AGE), set())

    def test_child_of_matched_daemon_included(self):
        child = _proc(ppid=900001, command="sleep 1", etimes=5)
        got = self.run_one(_proc(), extra={900002: child})
        self.assertEqual(got, {900001, 900002})


PRECIOUS_CASES = [
    ("env", [".env"], set(), (), ".env"),
    ("env_local", [".env.local"], set(), (), ".env.local"),
    ("envrc", [".envrc"], set(), (), ".envrc"),
    ("nested_env", ["config/.env"], set(), (), "config/.env"),
    ("pem", ["certs/server.pem"], set(), (), "certs/server.pem"),
    ("ed25519", ["id_ed25519"], set(), (), "id_ed25519"),
    ("key", ["a.key"], set(), (), "a.key"),
    ("node_modules", ["node_modules/pkg/.env"], set(), (), None),
    ("site_packages", [".venv/lib/python3.11/site-packages/certifi/cacert.pem"], set(), (), None),
    ("ignored_dir", ["dist/.env"], {"dist"}, (), None),
    ("ignored_prefix", ["coverage-x/a.key"], set(), ("coverage",), None),
    ("env_prefix_name", [".environment-notes"], set(), (), ".environment-notes"),
    ("env_txt", ["env.txt"], set(), (), None),
    ("readme", ["README.md"], set(), (), None),
    ("empty", [], set(), (), None),
    ("first_wins", ["a.txt", "b/.env", "c.key"], set(), (), "b/.env"),
    ("case_sensitive", [".ENV", "A.KEY"], set(), (), None),
]


class PreciousPathTests(unittest.TestCase):
    def test_table(self):
        for name, paths, dirs, prefixes, expected in PRECIOUS_CASES:
            with self.subTest(name):
                self.assertEqual(reap_guards.precious_path(paths, dirs, prefixes), expected)


if __name__ == "__main__":
    unittest.main()
