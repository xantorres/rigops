from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rigops import reap_devservers
from rigops.errors import RigopsError
from rigops.reap_devservers import live_sessions, stale_dev_servers

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
    "pnpm run dev:web",
    "node /w/root/node_modules/.bin/nodemon src/main.ts",
    "node /w/root/app/node_modules/.bin/rsbuild dev",
    "node /w/root/node_modules/@rsbuild/core/bin/rsbuild.js dev",
    "/opt/homebrew/bin/node --max-old-space-size=4096 /w/root/node_modules/.bin/next dev",
    "vite",
    "node /w/root/node_modules/.bin/vite --host",
    "pnpm dev",
    "node /opt/homebrew/bin/pnpm run dev",
    "npm run dev:web",
    "yarn storybook",
    "turbo run dev",
    "node /w/root/node_modules/.bin/turbo dev",
    "node /w/root/node_modules/.bin/nodemon server.js",
    "nodemon",
    "tsx watch src/index.ts",
    "node /w/root/node_modules/.bin/webpack-dev-server",
    "storybook dev -p 6006",
]

NON_MATCHES = [
    "node /w/root/node_modules/.bin/vite build",
    "node /w/root/node_modules/.bin/tsserver",
    "bun /w/root/apps/server/src/main.ts",
    "pnpm install",
    "node /w/root/node_modules/.bin/vitest run",
    "node /w/root/node_modules/.bin/next start",
    "pnpm start",
    "npm run serve",
    "yarn start",
    "node /w/root/node_modules/.bin/vite preview",
    "pnpm run dev-tools",
    "node /w/root/tools/nodemon-notes/build.js",
    "fswatch -o /w/root/oss/vite",
    "tail -F /w/root/logs/vite",
    "code --wait /w/root/vite",
    "python3 -m http.server -d /w/root/vite",
    "tsx watch.ts",
    "tsx watch-config.ts",
    "ssh -f -N host pnpm run dev",
    "vite --mode production build",
    "vite build",
    'grep -r "next dev" /w/root',
    'sh -c "pnpm run dev"',
    "next start",
    "vite preview",
    "node /w/root/server.js",
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
    def sweep(self, snap, cwds, sessions=None, roots=None):
        return stale_dev_servers(snap, cwds, roots or [ROOT], MIN_AGE, PATTERNS, sessions).stale

    def kept(self, snap, cwds, sessions=None):
        return stale_dev_servers(snap, cwds, [ROOT], MIN_AGE, PATTERNS, sessions).kept

    def test_takes_topmost_server_with_its_tree(self):
        snap = {
            900001: _proc("npm exec turbo run dev --filter=web"),
            900002: _proc("node /w/root/node_modules/.bin/turbo run dev", ppid=900001),
            900003: _proc("node /w/root/node_modules/.bin/esbuild --service", ppid=900002),
        }
        cwds = {900001: ROOT + "/wt"}
        self.assertEqual(self.sweep(snap, cwds), {900001: {900001, 900002, 900003}})
        self.assertEqual(self.kept(snap, cwds), {})

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

    def test_server_under_active_session_is_kept(self):
        snap = {
            900020: _proc("/bin/agent-host", ppid=1),
            900021: _proc("pnpm run dev", ppid=900020),
        }
        for idle in (None, MIN_AGE - 1, MIN_AGE):
            with self.subTest(idle=idle):
                sessions = {900020: idle}
                self.assertEqual(self.sweep(snap, {900021: ROOT + "/wt"}, sessions), {})
                self.assertEqual(self.kept(snap, {900021: ROOT + "/wt"}, sessions),
                                 {900021: ("session", 900020)})

    def test_server_under_long_idle_session_is_stale(self):
        snap = {
            900020: _proc("/bin/agent-host", ppid=1),
            900021: _proc("pnpm run dev", ppid=900020),
        }
        sessions = {900020: MIN_AGE + 1}
        self.assertEqual(self.sweep(snap, {900021: ROOT + "/wt"}, sessions), {900021: {900021}})

    def test_any_active_session_ancestor_keeps_the_server(self):
        snap = {
            900100: _proc("outer-host", ppid=1),
            900101: _proc("inner-host", ppid=900100),
            900102: _proc("pnpm run dev", ppid=900101),
        }
        cwds = {900102: ROOT + "/wt"}
        sessions = {900101: MIN_AGE + 1, 900100: None}
        self.assertEqual(self.kept(snap, cwds, sessions), {900102: ("session", 900100)})
        self.assertEqual(self.sweep(snap, cwds, sessions), {})
        sessions = {900101: MIN_AGE + 1, 900100: MIN_AGE + 1}
        self.assertEqual(self.sweep(snap, cwds, sessions), {900102: {900102}})

    def test_session_that_quotes_a_dev_command_is_never_a_candidate(self):
        snap = {
            900030: _proc("/bin/launcher", ppid=1),
            900031: _proc("agent --prompt 'pnpm run dev' /w/root/wt", ppid=900030),
            900032: _proc("pnpm run dev /w/root/wt", ppid=900031),
        }
        result = self.sweep(snap, {}, {900031: MIN_AGE + 1})
        self.assertEqual(result, {900032: {900032}})
        snap[900030]["command"] = "pnpm run dev /w/root/wt"
        self.assertNotIn(900030, self.sweep(snap, {}, {900031: MIN_AGE + 1}))
        self.assertNotIn(900031, self.sweep(snap, {}, {900031: MIN_AGE + 1}))

    def test_detached_server_with_terminal_parent_is_kept(self):
        snap = {
            900040: _proc("-zsh", ppid=1, tty="ttys003"),
            900041: _proc("pnpm run dev", ppid=900040),
        }
        cwds = {900041: ROOT + "/wt"}
        self.assertEqual(self.sweep(snap, cwds), {})
        self.assertEqual(self.kept(snap, cwds), {900041: ("terminal", 900040)})

    def test_server_with_terminal_descendant_is_kept(self):
        snap = {
            900050: _proc("pnpm run dev"),
            900051: _proc("node child", ppid=900050),
            900052: _proc("node tty-child", ppid=900050, tty="ttys004"),
        }
        cwds = {900050: ROOT + "/wt"}
        self.assertEqual(self.sweep(snap, cwds), {})
        self.assertEqual(self.kept(snap, cwds), {900050: ("terminal", 900052)})

    def test_nearest_terminal_ancestor_holds_it(self):
        snap = {
            900060: _proc("-zsh", ppid=1, tty="ttys001"),
            900061: _proc("shim", ppid=900060, tty="ttys002"),
            900062: _proc("pnpm run dev", ppid=900061),
        }
        self.assertEqual(self.kept(snap, {900062: ROOT + "/wt"}), {900062: ("terminal", 900061)})

    def test_terminal_beats_session(self):
        snap = {
            900070: _proc("agent-host", ppid=1),
            900071: _proc("-zsh", ppid=900070, tty="ttys001"),
            900072: _proc("pnpm run dev", ppid=900071),
        }
        kept = self.kept(snap, {900072: ROOT + "/wt"}, {900070: 0})
        self.assertEqual(kept, {900072: ("terminal", 900071)})

    def test_argv_only_rooting_matches(self):
        snap = {900080: _proc("node /w/root/node_modules/vite/bin/vite.js --port 1")}
        self.assertEqual(self.sweep(snap, {900080: "/elsewhere"}), {900080: {900080}})

    def test_root_through_symlink_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp, "real").resolve()
            real.mkdir()
            link = Path(tmp, "link")
            link.symlink_to(real)
            snap = {900090: _proc("pnpm run dev")}
            result = self.sweep(snap, {900090: str(real / "app")}, roots=[str(link)])
            self.assertEqual(result, {900090: {900090}})

KEYS = {"generated", "version", "repos", "orphan_daemons", "process_observation_ok",
        "dev_servers", "skipped_owned", "kills", "deadline_hit"}
KILL_RESULT = {"would": [], "killed": 0, "kill_failed": 0, "overflow": False, "failed_pids": []}


class RunProcsTests(unittest.TestCase):
    def setUp(self):
        self.snap = {
            900001: _proc("pnpm run dev"),
            900002: _proc("node child", ppid=900001),
        }
        self.cwds = {900001: ROOT + "/wt"}
        self.kill = self.patch("kill_pids", return_value=dict(KILL_RESULT))
        self.patch("ps_snapshot", side_effect=lambda: self.snap)
        self.patch("cwd_map", side_effect=lambda: self.cwds)
        self.sessions = self.patch("live_sessions", return_value={})
        self.patch("orphan_sweep", return_value=set())

    def patch(self, name, **kw):
        patcher = mock.patch.object(reap_devservers, name, **kw)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def opts(self, **kw):
        base = dict(apply=True, dry_run=False, json=True, roots=[ROOT], dev_server_sweep=True,
                    dev_server_min_age_s=MIN_AGE, dev_server_patterns=PATTERNS,
                    sessions_dir=Path("/w/sessions"), deadline_s=3600,
                    max_kills_per_tree=20, kill_grace_s=5, orphan_sweep=False,
                    orphan_min_age_s=1)
        return SimpleNamespace(**{**base, **kw})

    def run_it(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = reap_devservers.run_procs(self.opts(**kw))
        return code, out.getvalue(), err.getvalue()

    def test_failed_observation_reports_and_signals_nothing(self):
        self.snap = None
        code, out, err = self.run_it()
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(set(report), KEYS)
        self.assertFalse(report["process_observation_ok"])
        self.assertEqual(report["dev_servers"], [])
        self.assertIn("observation failed", err)
        self.kill.assert_not_called()

    def test_failed_observation_without_json_prints_no_object(self):
        self.cwds = None
        self.assertEqual(self.run_it(json=False)[1], "")

    def test_json_has_every_contract_key(self):
        _, out, _ = self.run_it()
        report = json.loads(out)
        self.assertEqual(set(report), KEYS)
        self.assertTrue(report["process_observation_ok"])
        self.assertFalse(report["deadline_hit"])
        server = report["dev_servers"][0]
        self.assertEqual((server["pid"], server["match"], server["cwd"], server["pids"]),
                         (900001, "dev-server", ROOT + "/wt", [900001, 900002]))

    def test_sweep_disabled_makes_no_dev_server_group(self):
        self.run_it(dev_server_sweep=False)
        labels = [c.args[0] for c in self.kill.call_args_list]
        self.assertEqual(labels, ["orphan-daemon"])
        self.sessions.assert_not_called()

    def test_kept_server_is_reported_and_not_killed(self):
        self.snap[900000] = _proc("agent-host")
        self.snap[900001]["ppid"] = 900000
        self.sessions.return_value = {900000: None}
        _, out, _ = self.run_it()
        report = json.loads(out)
        self.assertEqual(report["skipped_owned"], [
            {"pid": 900001, "comm": "pnpm", "reason": "session", "parent": 900000,
             "tree": f"dev-server={ROOT}/wt"}])
        self.assertEqual(report["dev_servers"], [])
        self.assertNotIn("dev-server", " ".join(str(c.args[0]) for c in self.kill.call_args_list))

    def test_kept_server_prints_skip_owned_line(self):
        self.snap[900000] = _proc("agent-host")
        self.snap[900001]["ppid"] = 900000
        self.sessions.return_value = {900000: None}
        _, out, _ = self.run_it(json=False, apply=False)
        self.assertIn(f"SKIP-OWNED pid=900001 comm=pnpm parent=900000 tree=dev-server={ROOT}/wt",
                      out)

    def orphan_groups(self):
        return {c.args[0]: c.args[1] for c in self.kill.call_args_list}

    def test_kept_tree_never_reaches_orphan_group(self):
        self.snap[900000] = _proc("agent-host")
        self.snap[900001]["ppid"] = 900000
        self.sessions.return_value = {900000: None}
        self.patch("orphan_sweep", return_value={900001, 900002})
        self.run_it(orphan_sweep=True)
        self.assertEqual(self.orphan_groups()["orphan-daemon"], {})

    def test_kept_terminal_tree_never_reaches_orphan_group(self):
        self.snap[900002]["tty"] = "ttys001"
        self.patch("orphan_sweep", return_value={900001, 900002})
        self.run_it(orphan_sweep=True)
        self.assertEqual(self.orphan_groups()["orphan-daemon"], {})

    def test_live_session_never_reaches_orphan_group(self):
        self.snap[900000] = _proc("agent-host")
        self.snap[900001]["ppid"] = 900000
        self.snap[900003] = _proc("agent-child", ppid=900000)
        self.sessions.return_value = {900003: None}
        self.patch("orphan_sweep", return_value={900000, 900003})
        self.run_it(orphan_sweep=True, dev_server_sweep=False)
        self.assertEqual(self.orphan_groups()["orphan-daemon"], {})

    def test_sessions_read_when_either_sweep_is_on(self):
        for dev, orphan, read in ((False, True, True), (True, False, True),
                                  (False, False, False)):
            with self.subTest(dev=dev, orphan=orphan):
                self.sessions.reset_mock()
                self.run_it(dev_server_sweep=dev, orphan_sweep=orphan)
                self.assertEqual(self.sessions.called, read)

    def test_zero_deadline_prints_notice_in_text(self):
        for apply in (True, False):
            with self.subTest(apply=apply):
                _, out, _ = self.run_it(json=False, apply=apply, deadline_s=0)
                self.assertIn("SKIP-DEADLINE run deadline reached; remaining groups wait "
                              "for the next run", out)

    def test_no_deadline_notice_when_not_hit(self):
        self.assertNotIn("SKIP-DEADLINE", self.run_it(json=False)[1])

    def test_zero_deadline_signals_nothing_and_reports_it(self):
        _, out, _ = self.run_it(deadline_s=0)
        self.kill.assert_not_called()
        self.assertTrue(json.loads(out)["deadline_hit"])

    def test_strict_session_read_only_under_real_apply(self):
        for apply, dry_run, strict in ((True, False, True), (False, False, False),
                                       (True, True, False)):
            with self.subTest(apply=apply, dry_run=dry_run):
                self.sessions.reset_mock()
                self.run_it(apply=apply, dry_run=dry_run)
                self.assertEqual(self.sessions.call_args.kwargs["strict"], strict)

    def test_session_read_error_propagates(self):
        self.sessions.side_effect = reap_devservers.RigopsError("unreadable session file x")
        with self.assertRaises(reap_devservers.RigopsError):
            self.run_it()
        self.kill.assert_not_called()


class LiveSessionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, name, payload, mtime=None):
        path = self.dir / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_idle_session_reports_seconds_from_mtime(self):
        import time
        self.write("a.json", {"pid": os.getpid(), "status": "idle"}, mtime=time.time() - 500)
        idle = live_sessions(self.dir)[os.getpid()]
        self.assertTrue(500 <= idle <= 510)

    def test_non_idle_status_is_unknown(self):
        for status in ("busy", "waiting", None):
            with self.subTest(status=status):
                payload = {"pid": os.getpid()}
                if status:
                    payload["status"] = status
                self.write("a.json", payload)
                self.assertEqual(live_sessions(self.dir), {os.getpid(): None})

    def test_dead_pid_is_dropped(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.write("a.json", {"pid": child.pid, "status": "idle"})
        self.assertEqual(live_sessions(self.dir), {})

    def test_unreadable_json_skipped_or_raises(self):
        self.write("bad.json", "{not json")
        self.assertEqual(live_sessions(self.dir), {})
        with self.assertRaises(RigopsError):
            live_sessions(self.dir, strict=True)

    def test_missing_dir_is_empty(self):
        self.assertEqual(live_sessions(self.dir / "nope"), {})
        self.assertEqual(live_sessions(self.dir / "nope", strict=True), {})

    def test_uninterpretable_file_is_skipped_unless_strict(self):
        for payload in ({"status": "idle"}, [1, 2], {"pid": None}):
            with self.subTest(payload=payload):
                self.write("a.json", payload)
                self.assertEqual(live_sessions(self.dir), {})
                with self.assertRaises(RigopsError):
                    live_sessions(self.dir, strict=True)


if __name__ == "__main__":
    unittest.main()
