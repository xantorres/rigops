from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import cli_argv
from tests.test_eval_cli import _content_reply, _run_eval, _StubServerMixin, _write_case

FORMS = [
    ("authprobe", "--json"),
    ("authprobe", "--json", "--force"),
    ("backlog", "lint", "--json"),
    ("doctor", "--supervisor", "none", "--json"),
    ("doctor", "--list", "--json"),
    ("doctor", "--config-only", "--json"),
    ("eit", "--json"),
    ("eit", "--rebuilds", "--json"),
    ("eval", "list", "--json"),
    ("janitor", "--json"),
    ("ledger", "write", "--dry-run", "--json"),
    ("ledger", "write", "--show", "--json"),
    ("ledger", "diff", "--json"),
    ("reap", "--json"),
    ("tax", "--json"),
    ("tax", "--history", "--json"),
    ("tax", "--starts", "--json"),
    ("tax", "--dead", "--json"),
]


class EveryJsonPayloadTests(unittest.TestCase):
    def test_each_json_output_is_one_object_with_version_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "transcripts").mkdir()
            (home / "config.json").write_text(
                json.dumps({"transcripts_dir": str(home / "transcripts")}))
            env = {
                **os.environ, "HOME": str(home), "RIGOPS_STATE_DIR": str(home / "state"),
                "RIGOPS_CONFIG": str(home / "config.json"),
                "XDG_CONFIG_HOME": str(home / "xc"), "XDG_STATE_HOME": str(home / "xs"),
            }
            for cmd, *args in FORMS:
                if cmd in ("tax", "eit"):
                    args += ["--dir", str(home / "transcripts")]
                with self.subTest(form=" ".join((cmd, *args))):
                    proc = subprocess.run(
                        [*cli_argv(cmd), *args],
                        capture_output=True, text=True, timeout=60, env=env,
                        stdin=subprocess.DEVNULL, check=False,
                    )
                    self.assertNotIn("Traceback", proc.stderr)
                    payload = json.loads(proc.stdout)
                    self.assertIsInstance(payload, dict)
                    self.assertEqual(payload.get("version"), 1)


class EvalJsonPayloadTests(_StubServerMixin, unittest.TestCase):
    def test_eval_run_and_diff_are_versioned_objects(self):
        endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
        with tempfile.TemporaryDirectory() as tmp:
            cases = Path(tmp) / "cases"
            cases.mkdir()
            _write_case(cases, "greets")
            env = {
                **os.environ, "HOME": tmp, "RIGOPS_STATE_DIR": str(Path(tmp) / "state"),
                "RIGOPS_CONFIG": str(Path(tmp) / "config.json"),
                "XDG_CONFIG_HOME": str(Path(tmp) / "xc"), "XDG_STATE_HOME": str(Path(tmp) / "xs"),
            }
            run = ["run", "--cases", str(cases), "--endpoint", endpoint, "--model", "m", "--json"]
            outputs = [_run_eval([*run, "--label", label], env) for label in ("r1", "r2")]
            outputs.append(_run_eval(["diff", "--json"], env))
        for proc in outputs:
            with self.subTest(args=proc.args[2:4]):
                self.assertEqual(proc.returncode, 0, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertIsInstance(payload, dict)
                self.assertEqual(payload.get("version"), 1)


if __name__ == "__main__":
    unittest.main()
