from __future__ import annotations

import subprocess
import sys
import unittest

import rigops

from tests.support import cli_argv

VISIBLE = [
    "authprobe", "backlog", "config", "doctor", "eit", "eval", "janitor",
    "jobs", "ledger", "mine", "reap", "tax", "version",
]
EXPERIMENTAL = {"authprobe", "backlog", "eit", "mine"}
SUBCOMMAND = {"backlog": "lint", "config": "get", "eval": "run", "ledger": "write"}


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rigops", *args],
        capture_output=True, text=True, check=False, timeout=30,
    )


def _listing(stdout: str) -> dict[str, str]:
    body = stdout.split("commands:\n", 1)[1]
    rows = {}
    for line in body.split("\n\n", 1)[0].splitlines():
        name, _, description = line.strip().partition("  ")
        rows[name] = description.strip()
    return rows


class VersionTests(unittest.TestCase):
    def test_version_subcommand_prints_the_package_version(self):
        proc = _run("version")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), rigops.__version__)

    def test_version_flag_matches_the_subcommand(self):
        proc = _run("--version")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _run("version").stdout)

    def test_version_is_listed_in_usage(self):
        proc = _run("help")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("version", _listing(proc.stdout))


class UsageTests(unittest.TestCase):
    def test_help_forms_list_exactly_the_visible_commands_with_descriptions(self):
        for args in ((), ("help",), ("-h",), ("--help",)):
            with self.subTest(args=args):
                proc = _run(*args)

                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.startswith("usage: rigops <command> [args...]\n"))
                rows = _listing(proc.stdout)
                self.assertEqual(sorted(rows), VISIBLE)
                self.assertEqual(list(rows), VISIBLE)
                self.assertTrue(all(rows.values()), rows)
                self.assertNotIn("hook", rows)

    def test_usage_names_exactly_the_experimental_commands(self):
        from rigops import cli

        self.assertEqual(cli.EXPERIMENTAL, EXPERIMENTAL)
        last = _run("help").stdout.rstrip("\n").splitlines()[-1]
        prefix = "experimental (no compatibility promise): "
        self.assertTrue(last.startswith(prefix), last)
        self.assertEqual(last[len(prefix):].split(", "), sorted(EXPERIMENTAL))

    def test_unknown_command_exits_2_with_usage_on_stderr(self):
        proc = _run("--nope")

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("error: unknown command '--nope'\n\nusage: rigops"))
        self.assertIn("unknown command", proc.stderr)

    def test_every_visible_command_takes_dash_h_with_the_command_usage(self):
        for name in VISIBLE:
            with self.subTest(command=name):
                proc = _run(name, "-h")

                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.startswith(f"usage: rigops {name}"), proc.stdout)

    def test_subcommand_usage_derives_from_the_command_name(self):
        for name, sub in SUBCOMMAND.items():
            with self.subTest(command=name):
                proc = _run(name, sub, "-h")

                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.startswith(f"usage: rigops {name} {sub}"), proc.stdout)


class DispatchTests(unittest.TestCase):
    def test_importing_the_cli_package_imports_no_command_module(self):
        code = (
            "import sys, rigops.cli\n"
            "print([m for m in sys.modules if m.startswith('rigops.cli.')])"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=30,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "[]")

    def test_hook_with_an_unknown_event_exits_0(self):
        proc = subprocess.run(
            [*cli_argv("hook"), "no-such-event"],
            capture_output=True, text=True, check=False, timeout=30, input="",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_hook_is_dispatchable_but_not_listed(self):
        self.assertNotIn("hook", _listing(_run("help").stdout))
        self.assertEqual(_run("hook", "no-such-event").returncode, 0)

    def test_python_dash_m_rigops_exits_with_the_command_status(self):
        self.assertEqual(_run("config", "get", "no.such.key").returncode, 1)


if __name__ == "__main__":
    unittest.main()
