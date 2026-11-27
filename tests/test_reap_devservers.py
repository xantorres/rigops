from __future__ import annotations

import os
import unittest

from rigops import reap_devservers
from rigops.reap_devservers import stale_dev_servers

ROOT = "/w/root"
MIN_AGE = 10800
PATTERNS = reap_devservers.compile_patterns(reap_devservers.DEFAULT_PATTERNS)


def _proc(command, ppid=1, etimes=20000, tty="??", uid=None):
    return {"ppid": ppid, "pgid": 1, "uid": os.getuid() if uid is None else uid,
            "tty": tty, "etimes": etimes, "command": command}


MATCHES = [
    "npm exec turbo run dev --filter=patients",
    "node /w/root/node_modules/.bin/../turbo/bin/turbo run dev --filter=web",
    "/bin/sh -c cd /w/root/apps/playground && node_modules/.bin/rsbuild dev --port 3001",
    "node /w/root/node_modules/.bin/../@rslib/core/bin/rslib.js mf-dev",
    "node /w/root/node_modules/.bin/next dev",
    "node /w/root/node_modules/vite/bin/vite.js",
    "node /w/root/node_modules/vite/bin/vite.js --port 5173",
    "pnpm run dev",
    "bun run dev",
    "rsbuild-node",
    "node /w/root/node_modules/.bin/tsx watch src/main.ts",
]

NON_MATCHES = [
    "node /w/root/node_modules/.bin/vite build",
    "node /w/root/node_modules/.bin/tsserver",
    "bun /w/root/apps/server/src/main.ts",
    "pnpm install",
    "node /w/root/node_modules/.bin/vitest run",
]


class PatternTests(unittest.TestCase):
    def test_matches(self):
        for command in MATCHES:
            with self.subTest(command):
                self.assertTrue(any(p.search(command) for p in PATTERNS))

    def test_non_matches(self):
        for command in NON_MATCHES:
            with self.subTest(command):
                self.assertFalse(any(p.search(command) for p in PATTERNS))

    def test_bad_regex_is_reported(self):
        self.assertIn("not a valid regex", reap_devservers.compile_patterns(["("]))


class StaleDevServersTests(unittest.TestCase):
    def sweep(self, snap, cwds):
        return stale_dev_servers(snap, cwds, [ROOT], MIN_AGE, PATTERNS)

    def test_takes_topmost_server_with_its_tree(self):
        snap = {
            900001: _proc("npm exec turbo run dev --filter=web"),
            900002: _proc("node /w/root/node_modules/.bin/turbo run dev", ppid=900001),
            900003: _proc("node /w/root/node_modules/.bin/esbuild --service", ppid=900002),
        }
        cwds = {900001: ROOT + "/wt"}
        self.assertEqual(self.sweep(snap, cwds), {900001: {900001, 900002, 900003}})

    def test_parent_other_than_init_still_matches(self):
        snap = {
            900010: _proc("/Applications/App.app/Contents/MacOS/App", ppid=1),
            900011: _proc("/bin/sh -c cd /w/root/apps/x && rsbuild dev", ppid=900010),
        }
        self.assertEqual(self.sweep(snap, {900011: ROOT + "/apps/x"}), {900011: {900011}})

    def test_young_server_is_kept(self):
        snap = {900001: _proc("pnpm run dev", etimes=MIN_AGE - 1)}
        self.assertEqual(self.sweep(snap, {900001: ROOT + "/wt"}), {})

    def test_terminal_server_is_kept(self):
        snap = {900001: _proc("pnpm run dev", tty="ttys003")}
        self.assertEqual(self.sweep(snap, {900001: ROOT + "/wt"}), {})

    def test_outside_roots_is_kept(self):
        snap = {900001: _proc("pnpm run dev")}
        self.assertEqual(self.sweep(snap, {900001: "/elsewhere/app"}), {})

    def test_other_user_is_kept(self):
        snap = {900001: _proc("pnpm run dev", uid=os.getuid() + 1)}
        self.assertEqual(self.sweep(snap, {900001: ROOT + "/wt"}), {})

    def test_non_server_is_kept(self):
        snap = {900001: _proc("bun /w/root/apps/server/src/main.ts")}
        self.assertEqual(self.sweep(snap, {900001: ROOT + "/apps/server"}), {})


if __name__ == "__main__":
    unittest.main()
