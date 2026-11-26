from __future__ import annotations

import contextlib
import io
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import unittest
from importlib import resources
from pathlib import Path
from unittest import mock

from rigops import jobs
from rigops.cli import jobs as jobs_cli
from rigops.errors import RigopsError

from tests.support import wrapper

REPO = Path(__file__).resolve().parent.parent
LABELS = [f"com.rigops.{name}" for name in ("doctor", "ledger", "janitor", "reap")]


def script(path: Path, body: str = "exit 0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


@contextlib.contextmanager
def home_dir():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp).resolve() / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            yield home


def write_plist(home: Path, name: str, program) -> Path:
    path = home / "Library" / "LaunchAgents" / f"com.rigops.{name}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps({"Label": f"com.rigops.{name}", "ProgramArguments": program}))
    return path


class ResolveExecutableTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()

    def resolve(self, found, argv0):
        with mock.patch("shutil.which", return_value=found), \
                mock.patch.object(sys, "argv", [argv0]):
            return jobs.resolve_executable()

    def test_none_on_path_exits_2_with_a_reason(self):
        with self.assertRaisesRegex(RigopsError, "no `rigops` on PATH"):
            self.resolve(None, "x")

    def test_realpath_mismatch_names_both_paths(self):
        found = script(self.tmp / "a" / "rigops")
        other = script(self.tmp / "b" / "rigops")

        with self.assertRaises(RigopsError) as ctx:
            self.resolve(str(found), str(other))

        self.assertIn(str(found), str(ctx.exception))
        self.assertIn(str(other), str(ctx.exception))
        self.assertIn("python -m rigops", str(ctx.exception))

    def test_cache_and_caches_components_are_refused(self):
        for part in (".cache", "Caches"):
            with self.subTest(part=part):
                found = script(self.tmp / part / "env" / "bin" / "rigops")

                with self.assertRaisesRegex(RigopsError, "ephemeral"):
                    self.resolve(str(found), str(found))

    def test_a_cache_component_in_the_symlink_target_is_refused(self):
        target = script(self.tmp / ".cache" / "env" / "bin" / "rigops")
        link = self.tmp / "bin" / "rigops"
        link.parent.mkdir()
        link.symlink_to(target)

        with self.assertRaisesRegex(RigopsError, "ephemeral"):
            self.resolve(str(link), str(link))

    def test_symlink_on_path_returns_the_link_not_the_target(self):
        target = script(self.tmp / "venv" / "bin" / "rigops")
        link = self.tmp / "bin" / "rigops"
        link.parent.mkdir()
        link.symlink_to(target)

        self.assertEqual(self.resolve(str(link), str(target)), str(link))

    def test_relative_argv0_is_resolved_against_the_cwd(self):
        target = script(self.tmp / "bin" / "rigops")
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.tmp / "bin")

        self.assertEqual(self.resolve(str(target), "rigops"), str(target))


class RenderPlistTests(unittest.TestCase):
    def render(self, name="doctor", path="/home/alice/.local/bin/rigops", heal=False):
        return jobs.render_plist(name, path, heal)

    def test_every_placeholder_is_replaced_and_the_plist_parses(self):
        for name in jobs.JOBS:
            with self.subTest(job=name):
                data = self.render(name)

                self.assertNotRegex(data.decode(), r"@[A-Z_]+@")
                parsed = plistlib.loads(data)
                self.assertEqual(parsed["Label"], f"com.rigops.{name}")
                self.assertEqual(parsed["ProgramArguments"][0], "/home/alice/.local/bin/rigops")

    def test_program_arguments_are_the_command_and_its_args(self):
        expected = {
            "doctor": ["doctor", "--exit-zero"],
            "ledger": ["ledger", "write"],
            "janitor": ["janitor", "--apply"],
            "reap": ["reap", "--apply"],
        }
        for name, args in expected.items():
            with self.subTest(job=name):
                self.assertEqual(plistlib.loads(self.render(name))["ProgramArguments"][1:], args)

    def test_heal_adds_the_flag_to_doctor_only(self):
        self.assertEqual(
            plistlib.loads(self.render("doctor", heal=True))["ProgramArguments"][1:],
            ["doctor", "--exit-zero", "--heal"],
        )
        ledger = plistlib.loads(self.render("ledger", heal=True))
        self.assertNotIn("--heal", ledger["ProgramArguments"])

    def test_xml_special_characters_in_a_value_are_escaped(self):
        with mock.patch.dict(os.environ, {"RIGOPS_CONFIG": "/home/alice/a&b<c>.json"}):
            data = self.render()

        self.assertIn(b"a&amp;b&lt;c&gt;.json", data)
        env = plistlib.loads(data)["EnvironmentVariables"]
        self.assertEqual(env["RIGOPS_CONFIG"], "/home/alice/a&b<c>.json")

    def test_extra_env_pairs_appear_only_when_set(self):
        names = ("RIGOPS_CONFIG", "RIGOPS_STATE_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME")
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in names:
                os.environ.pop(name, None)
            os.environ["XDG_STATE_HOME"] = "/home/alice/state"
            env = plistlib.loads(self.render())["EnvironmentVariables"]

        self.assertEqual(env["XDG_STATE_HOME"], "/home/alice/state")
        for name in names[:3]:
            self.assertNotIn(name, env)

    def test_two_renders_are_byte_identical_and_carry_no_version_or_interpreter(self):
        first, second = self.render(), self.render()

        self.assertEqual(first, second)
        text = first.decode()
        import rigops

        self.assertNotIn(rigops.__version__, text)
        self.assertNotIn(sys.executable, text)

    def test_a_leftover_placeholder_raises(self):
        template = resources.files("rigops") / "templates" / "launchd" / "ledger.plist.in"
        broken = template.read_text(encoding="utf-8").replace("@RIGOPS@", "@RIGOPS@@BOGUS@")

        with mock.patch.object(jobs, "_template", return_value=broken):
            with self.assertRaisesRegex(RigopsError, "BOGUS"):
                self.render("ledger")


class ProgramProblemTests(unittest.TestCase):
    def test_missing_program_file(self):
        with home_dir() as home:
            plist = write_plist(home, "doctor", [str(home / "gone"), "doctor"])

            self.assertIn(str(home / "gone"), jobs.program_problem(plist))

    def test_non_executable_program(self):
        with home_dir() as home:
            prog = home / "rigops"
            prog.write_text("#!/bin/sh\nexit 0\n")
            plist = write_plist(home, "doctor", [str(prog), "doctor"])

            self.assertIn("not executable", jobs.program_problem(plist))

    def test_version_exit_is_a_problem_naming_the_path_and_status(self):
        with home_dir() as home:
            prog = script(home / "rigops", "exit 3")
            plist = write_plist(home, "doctor", [str(prog), "doctor"])

            problem = jobs.program_problem(plist)

        self.assertIn(str(prog), problem)
        self.assertIn("exited 3", problem)

    def test_version_timeout_is_a_problem(self):
        with home_dir() as home:
            prog = script(home / "rigops", "sleep 5")
            plist = write_plist(home, "doctor", [str(prog), "doctor"])

            with mock.patch.object(jobs, "VERSION_TIMEOUT_S", 0.2):
                problem = jobs.program_problem(plist)

        self.assertIn("timed out", problem)
        self.assertIn(str(prog), problem)

    def test_a_working_program_has_no_problem(self):
        with home_dir() as home:
            prog = script(home / "rigops", '[ "$1" = version ] || exit 9')
            plist = write_plist(home, "doctor", [str(prog), "doctor"])

            self.assertIsNone(jobs.program_problem(plist))

    def test_the_version_probe_does_not_run_a_file_from_the_callers_cwd(self):
        with home_dir() as home:
            work = home / "work"
            marker = work / "ran"
            script(work / "version", f"touch {marker}")
            plist = write_plist(home, "doctor", ["/bin/sh", "doctor"])
            cwd = os.getcwd()
            os.chdir(work)
            try:
                problem = jobs.program_problem(plist)
            finally:
                os.chdir(cwd)

            self.assertFalse(marker.exists())
            self.assertIsNotNone(problem)

    def test_legacy_interpreter_and_script_plist_is_a_problem(self):
        with home_dir() as home:
            legacy = script(home / "libexec" / "rigops-doctor")
            plist = write_plist(home, "doctor", [sys.executable, str(legacy), "--exit-zero"])

            self.assertIsNotNone(jobs.program_problem(plist))

    def test_missing_or_empty_program_arguments_and_unreadable_plist(self):
        with home_dir() as home:
            empty = write_plist(home, "doctor", [])
            missing = home / "Library" / "LaunchAgents" / "com.rigops.ledger.plist"
            missing.write_bytes(plistlib.dumps({"Label": "com.rigops.ledger"}))
            broken = home / "Library" / "LaunchAgents" / "com.rigops.reap.plist"
            broken.write_text("not a plist")
            absent = home / "Library" / "LaunchAgents" / "com.rigops.janitor.plist"

            for plist in (empty, missing):
                with self.subTest(plist=plist.name):
                    self.assertIsNotNone(jobs.program_problem(plist))
            with self.assertRaisesRegex(RigopsError, "cannot parse"):
                jobs.program_problem(broken)
            with self.assertRaisesRegex(RigopsError, "cannot read"):
                jobs.program_problem(absent)


class CronTests(unittest.TestCase):
    def test_lines_match_each_schedule_and_command(self):
        with home_dir() as home, mock.patch.dict(os.environ, {"XDG_STATE_HOME": ""}):
            lines = jobs.cron_lines("/home/alice/.local/bin/rigops", list(jobs.JOBS))
            log = home / ".local" / "state" / "rigops"

        self.assertEqual(lines, [
            "*/30 * * * * /home/alice/.local/bin/rigops doctor --supervisor none --exit-zero"
            f" >> {log}/doctor.cron.log 2>&1",
            f"5 9 * * 1 /home/alice/.local/bin/rigops ledger write >> {log}/ledger.cron.log 2>&1",
            "0 10 * * 0 /home/alice/.local/bin/rigops janitor --apply"
            f" >> {log}/janitor.cron.log 2>&1",
            f"30 8 * * * /home/alice/.local/bin/rigops reap --apply >> {log}/reap.cron.log 2>&1",
        ])

    def test_logs_follow_xdg_state_home(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/home/alice/st"}):
            lines = jobs.cron_lines("/r", ["reap"])

        self.assertIn(">> /home/alice/st/rigops/reap.cron.log", lines[0])

    def test_cron_schedules_agree_with_the_plist_templates(self):
        for name, job in jobs.JOBS.items():
            with self.subTest(job=name):
                plist = plistlib.loads(jobs.render_plist(name, "/r", False))
                minute, hour, _dom, _mon, weekday = job.schedule.split()
                if "StartInterval" in plist:
                    self.assertEqual(job.schedule.split()[0], f"*/{plist['StartInterval'] // 60}")
                else:
                    cal = plist["StartCalendarInterval"]
                    self.assertEqual(str(cal["Minute"]), minute)
                    self.assertEqual(str(cal["Hour"]), hour)
                    self.assertEqual(str(cal.get("Weekday", "*")), weekday)


def run_cli(argv, platform="darwin", **patches):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "platform", platform), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = jobs_cli.main(argv)
        except RigopsError as exc:
            code = 2
            print(f"error: {exc}", file=err)
    return code, out.getvalue(), err.getvalue()


class InstallPlanTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            jobs, "resolve_executable", return_value="/home/alice/bin/rigops"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dry_run_prints_plans_and_touches_nothing(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=False), \
                mock.patch("rigops.launchd.subprocess.run") as launchctl:
            code, out, _ = run_cli(["install"])

            self.assertFalse((home / "Library").exists())

        self.assertEqual(code, 0)
        launchctl.assert_not_called()
        plist = home / "Library" / "LaunchAgents"
        self.assertIn(f"plan: installed {plist}/com.rigops.doctor.plist (com.rigops.doctor)", out)
        self.assertIn(f"plan: installed {plist}/com.rigops.ledger.plist (com.rigops.ledger)", out)
        self.assertNotIn("janitor", out)
        self.assertIn("--apply", out.splitlines()[-1])

    def test_apply_writes_atomically_bootstraps_and_reports_the_domain(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=False), \
                mock.patch("rigops.launchd.bootstrap", return_value="gui/501") as boot:
            code, out, _ = run_cli(["install", "--apply"])
            plist = home / "Library" / "LaunchAgents" / "com.rigops.doctor.plist"

            self.assertEqual(code, 0)
            self.assertEqual(oct(plist.stat().st_mode & 0o777), "0o644")
            self.assertTrue((home / "Library" / "Logs" / "rigops").is_dir())
            self.assertEqual(list(plist.parent.glob("*.tmp")), [])
            self.assertEqual(
                [c.args[0] for c in boot.call_args_list], ["com.rigops.doctor", "com.rigops.ledger"]
            )
            self.assertIn(f"installed {plist} (com.rigops.doctor) gui/501", out)

    def test_a_failing_write_is_exit_2_with_an_error_line(self):
        with home_dir(), mock.patch("rigops.launchd.is_loaded", return_value=False), \
                mock.patch.object(jobs, "write_atomic", side_effect=OSError("disk full")), \
                mock.patch("rigops.launchd.bootstrap") as boot:
            code, _, err = run_cli(["install", "--apply"])

        self.assertEqual(code, 2)
        self.assertEqual(err, "error: disk full\n")
        boot.assert_not_called()

    def test_actions_updated_loaded_and_unchanged(self):
        with home_dir() as home:
            agents = home / "Library" / "LaunchAgents"
            agents.mkdir(parents=True)
            (agents / "com.rigops.doctor.plist").write_bytes(b"old")
            (agents / "com.rigops.ledger.plist").write_bytes(
                jobs.render_plist("ledger", "/home/alice/bin/rigops", False)
            )
            for loaded, expected in ((False, "loaded"), (True, "unchanged")):
                with self.subTest(loaded=loaded), \
                        mock.patch("rigops.launchd.is_loaded", return_value=loaded):
                    _, out, _ = run_cli(["install"])

                    self.assertIn("plan: updated", out)
                    self.assertIn(f"plan: {expected} {agents}/com.rigops.ledger.plist", out)

    def test_dry_run_with_every_job_unchanged_asks_for_no_apply(self):
        with home_dir(), mock.patch("rigops.launchd.is_loaded", return_value=True), \
                mock.patch("rigops.launchd.bootstrap", return_value="gui/501"):
            run_cli(["install", "--apply"])
            code, out, _ = run_cli(["install"])

        self.assertEqual(code, 0)
        self.assertNotIn("re-run with --apply", out)
        self.assertEqual(out.splitlines()[-1], "nothing to change")

    def test_dry_run_with_one_changed_job_still_asks_for_apply(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=True):
            agents = home / "Library" / "LaunchAgents"
            agents.mkdir(parents=True)
            (agents / "com.rigops.ledger.plist").write_bytes(
                jobs.render_plist("ledger", "/home/alice/bin/rigops", False)
            )
            _, out, _ = run_cli(["install"])

        self.assertIn("plan: unchanged", out)
        self.assertEqual(out.splitlines()[-1], "re-run with --apply to make these changes")

    def test_unchanged_job_is_not_bootstrapped(self):
        with home_dir(), mock.patch("rigops.launchd.is_loaded", return_value=True), \
                mock.patch("rigops.launchd.bootstrap", return_value="gui/501") as boot:
            run_cli(["install", "--apply"])
            boot.reset_mock()
            _, out, _ = run_cli(["install", "--apply"])

        boot.assert_not_called()
        self.assertIn("unchanged", out)

    def test_jobs_option_selects_and_validates(self):
        with home_dir(), mock.patch("rigops.launchd.is_loaded", return_value=False):
            _, out, _ = run_cli(["install", "--jobs", "reap"])
            self.assertIn("com.rigops.reap", out)
            self.assertNotIn("com.rigops.doctor", out)
            for bad in ("nope", "", "doctor,nope"):
                with self.subTest(jobs=bad):
                    code, _, err = run_cli(["install", "--jobs", bad])
                    self.assertEqual(code, 2)
                    self.assertIn("doctor, ledger, janitor, reap", err)

    def test_heal_reaches_the_rendered_doctor_plist(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=False), \
                mock.patch("rigops.launchd.bootstrap", return_value="gui/501"):
            run_cli(["install", "--apply", "--heal"])
            written = home / "Library/LaunchAgents/com.rigops.doctor.plist"
            doctor = plistlib.loads(written.read_bytes())

        self.assertEqual(doctor["ProgramArguments"][-1], "--heal")

    def test_a_fifo_at_a_plist_path_is_refused_without_blocking(self):
        def blocked(signum, frame):
            raise AssertionError("jobs install blocked reading the FIFO")

        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=False):
            plist = home / "Library" / "LaunchAgents" / "com.rigops.doctor.plist"
            plist.parent.mkdir(parents=True)
            os.mkfifo(plist)
            previous = signal.signal(signal.SIGALRM, blocked)
            signal.setitimer(signal.ITIMER_REAL, 2)
            try:
                code, _, err = run_cli(["install"])
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)

        self.assertEqual(code, 2)
        self.assertIn(f"cannot read {plist}: not a regular file", err)

    def test_a_refused_executable_writes_nothing_even_in_the_dry_run(self):
        with home_dir() as home, mock.patch.object(
            jobs, "resolve_executable", side_effect=RigopsError("no `rigops` on PATH")
        ):
            for argv in (["install"], ["install", "--apply"]):
                code, _, err = run_cli(argv)
                self.assertEqual(code, 2)
                self.assertIn("no `rigops` on PATH", err)
            self.assertFalse((home / "Library").exists())

    def test_linux_prints_crontab_lines_and_writes_nothing(self):
        with home_dir() as home:
            for argv in (["install"], ["install", "--apply"]):
                with self.subTest(argv=argv), \
                        mock.patch("rigops.launchd.subprocess.run") as launchctl:
                    code, out, _ = run_cli(argv, platform="linux")

                    self.assertEqual(code, 0)
                    launchctl.assert_not_called()
                    self.assertIn("crontab -e", out.splitlines()[0])
                    self.assertEqual(len(out.splitlines()), 3)
                    self.assertIn("doctor --supervisor none --exit-zero", out)
                    self.assertFalse((home / "Library").exists())
                    self.assertFalse((home / ".local").exists())

    def test_linux_heal_prints_one_launchd_only_note(self):
        with home_dir():
            _, out, _ = run_cli(["install", "--heal"], platform="linux")

        self.assertEqual(sum("launchd" in line and "heal" in line for line in out.splitlines()), 1)


class UninstallTests(unittest.TestCase):
    def test_nothing_installed_is_one_line_and_exit_0(self):
        with home_dir(), mock.patch("rigops.launchd.is_loaded", return_value=False):
            code, out, _ = run_cli(["uninstall", "--apply"])

        self.assertEqual((code, len(out.splitlines())), (0, 1))

    def test_dry_run_lists_plans_and_changes_nothing(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=False), \
                mock.patch("rigops.launchd.bootout") as bootout:
            plist = write_plist(home, "doctor", ["/x", "doctor"])
            code, out, _ = run_cli(["uninstall"])

            self.assertTrue(plist.exists())

        bootout.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn(f"plan: remove {plist} (com.rigops.doctor)", out)
        self.assertNotIn("com.rigops.ledger", out)

    def test_apply_boots_out_then_removes_the_plist(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=False), \
                mock.patch("rigops.launchd.bootout") as bootout:
            plist = write_plist(home, "doctor", ["/x", "doctor"])
            code, out, _ = run_cli(["uninstall", "--apply"])

            self.assertFalse(plist.exists())

        bootout.assert_called_once_with("com.rigops.doctor")
        self.assertIn(f"removed {plist} (com.rigops.doctor)", out)
        self.assertEqual(code, 0)

    def test_a_loaded_label_without_a_plist_is_still_booted_out(self):
        with home_dir(), mock.patch(
            "rigops.launchd.is_loaded", side_effect=lambda label: label == "com.rigops.reap"
        ), mock.patch("rigops.launchd.bootout") as bootout:
            run_cli(["uninstall", "--apply"])

        bootout.assert_called_once_with("com.rigops.reap")

    def test_linux_says_to_edit_the_crontab(self):
        with home_dir(), mock.patch("rigops.launchd.subprocess.run") as launchctl:
            code, out, _ = run_cli(["uninstall", "--apply"], platform="linux")

        launchctl.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("crontab -e", out)


class StatusTests(unittest.TestCase):
    def good(self, home: Path, name: str) -> Path:
        prog = script(home / "bin" / "rigops", '[ "$1" = version ] || exit 9')
        return write_plist(home, name, [str(prog), name])

    def test_all_good_exits_0(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=True):
            self.good(home, "doctor")
            code, out, _ = run_cli(["status"])

        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 4)
        self.assertIn("com.rigops.doctor  installed=yes  loaded=yes  program=ok", out)
        self.assertIn("com.rigops.ledger  installed=no", out)

    def test_nothing_installed_exits_0_with_a_row_per_label(self):
        with home_dir(), mock.patch("rigops.launchd.is_loaded", return_value=False):
            code, out, _ = run_cli(["status"])

        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in out.splitlines()], LABELS)

    def test_installed_but_not_loaded_exits_1(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=False):
            self.good(home, "doctor")
            code, out, _ = run_cli(["status"])

        self.assertEqual(code, 1)
        self.assertIn("installed=yes  loaded=no", out)

    def test_a_program_problem_exits_1_and_is_shown(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=True):
            write_plist(home, "doctor", [str(home / "gone"), "doctor"])
            code, out, _ = run_cli(["status"])

        self.assertEqual(code, 1)
        self.assertIn(f"program={home}/gone", out)

    def test_a_launchctl_timeout_exits_2(self):
        timeout = subprocess.TimeoutExpired("launchctl", 10)
        with home_dir() as home, mock.patch("rigops.launchd.subprocess.run", side_effect=timeout):
            self.good(home, "doctor")
            code, _, err = run_cli(["status"])

        self.assertEqual(code, 2)
        self.assertIn("error: cannot ask launchd about com.rigops.doctor", err)

    def test_a_plist_that_cannot_be_read_exits_2(self):
        denied = PermissionError(13, "Permission denied")
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=True):
            self.good(home, "doctor")
            with mock.patch.object(Path, "read_bytes", side_effect=denied):
                code, _, err = run_cli(["status"])

        self.assertEqual(code, 2)
        self.assertIn("error: cannot read", err)

    def test_a_malformed_plist_exits_2(self):
        with home_dir() as home, mock.patch("rigops.launchd.is_loaded", return_value=True):
            plist = self.good(home, "doctor")
            plist.write_text("not a plist")
            code, _, err = run_cli(["status"])

        self.assertEqual(code, 2)
        self.assertIn(f"error: cannot parse {plist}", err)

    def test_linux_does_not_inspect_cron(self):
        with home_dir(), mock.patch("rigops.launchd.subprocess.run") as launchctl:
            code, out, _ = run_cli(["status"], platform="linux")

        launchctl.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("not inspected", out)


MODULE_INSTALL = [sys.executable, "-m", "rigops", "jobs", "install", "--apply"]
FAKE_LAUNCHCTL = """#!/bin/sh
echo "$*" >> "$FAKE_LAUNCHCTL_LOG"
if [ "$1" = list ]; then
  grep -q "^bootstrap .*$2.plist" "$FAKE_LAUNCHCTL_LOG" && exit 0
  exit 1
fi
exit 0
"""


class SubprocessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "launchctl.log"
        self.log.write_text("")

    def env(self, path=None):
        env = {
            "HOME": str(self.home),
            "PYTHONPATH": str(REPO / "lib"),
            "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin" if path is None else path,
            "FAKE_LAUNCHCTL_LOG": str(self.log),
            "RIGOPS_STATE_DIR": str(self.tmp / "state"),
            "XDG_STATE_HOME": str(self.tmp / "xdg-state"),
            "XDG_CONFIG_HOME": str(self.tmp / "xdg-config"),
        }
        return env

    def run_jobs(self, *args, path=None, cmd=None):
        return subprocess.run(
            cmd or ["rigops", "jobs", *args], capture_output=True, text=True, check=False,
            env=self.env(path), timeout=60,
        )

    def calls(self):
        return self.log.read_text().splitlines()

    def test_empty_path_exits_2_and_writes_no_launch_agents(self):
        proc = self.run_jobs(
            "install", "--apply", path="", cmd=MODULE_INSTALL,
        )

        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())
        self.assertEqual(self.calls(), [])

    def test_module_runner_is_refused_by_the_mismatch_rule(self):
        script(self.bin / "rigops")
        proc = self.run_jobs(
            "install", "--apply", cmd=MODULE_INSTALL,
        )

        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("python -m rigops", proc.stderr)
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())


@unittest.skipUnless(sys.platform == "darwin", "launchd round trip needs macOS")
class SubprocessRoundTripTests(SubprocessTests):
    def setUp(self):
        super().setUp()
        self.rigops = wrapper(self.bin)
        launchctl = self.bin / "launchctl"
        launchctl.write_text(FAKE_LAUNCHCTL)
        launchctl.chmod(0o755)
        self.agents = self.home / "Library" / "LaunchAgents"

    def bootstraps(self, since: int = 0):
        return [c for c in self.calls()[since:] if c.startswith("bootstrap ")]

    def args_of(self, name):
        written = self.agents / f"com.rigops.{name}.plist"
        return plistlib.loads(written.read_bytes())["ProgramArguments"]

    def test_install_apply_writes_default_jobs_and_bootstraps_them(self):
        proc = self.run_jobs("install", "--apply")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(p.name for p in self.agents.iterdir()),
                         ["com.rigops.doctor.plist", "com.rigops.ledger.plist"])
        self.assertEqual(self.args_of("doctor"), [str(self.rigops), "doctor", "--exit-zero"])
        self.assertEqual(len(self.bootstraps()), 2)
        for call in self.calls():
            for word in call.split():
                if word.endswith(".plist"):
                    self.assertTrue(word.startswith(str(self.home)), call)

    def test_second_run_is_unchanged_and_calls_no_bootstrap(self):
        self.run_jobs("install", "--apply")
        seen = len(self.calls())

        proc = self.run_jobs("install", "--apply")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("unchanged", proc.stdout)
        self.assertEqual(self.bootstraps(seen), [])

    def test_heal_adds_the_flag_to_doctor_only(self):
        self.run_jobs("install", "--apply", "--heal")

        self.assertEqual(self.args_of("doctor")[-1], "--heal")
        self.assertNotIn("--heal", self.args_of("ledger"))

    def test_jobs_reap_installs_only_reap(self):
        proc = self.run_jobs("install", "--apply", "--jobs", "reap")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([p.name for p in self.agents.iterdir()], ["com.rigops.reap.plist"])

    def test_uninstall_apply_boots_out_and_removes_the_plists(self):
        self.run_jobs("install", "--apply")
        seen = len(self.calls())

        proc = self.run_jobs("uninstall", "--apply")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(list(self.agents.glob("*.plist")), [])
        self.assertTrue(any(c.startswith("bootout ") for c in self.calls()[seen:]))

    def test_status_reports_loaded_jobs_ok(self):
        self.run_jobs("install", "--apply")

        proc = self.run_jobs("status")

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("loaded=yes  program=ok", proc.stdout)


class HelpTests(unittest.TestCase):
    def test_usage_starts_with_the_command_name(self):
        proc = subprocess.run(
            [sys.executable, "-m", "rigops", "jobs", "-h"],
            capture_output=True, text=True, check=False, timeout=30,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("usage: rigops jobs"), proc.stdout)

    def test_the_command_is_listed_in_the_cli_table(self):
        from rigops import cli

        self.assertIn("jobs", cli.COMMANDS)
        self.assertNotIn("jobs", cli.EXPERIMENTAL)


if __name__ == "__main__":
    unittest.main()
