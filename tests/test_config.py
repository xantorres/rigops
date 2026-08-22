from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import config  # noqa: E402


class DeepMergeTests(unittest.TestCase):
    def test_nested_override(self):
        merged = config._deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 99}})
        self.assertEqual(merged, {"a": {"b": 99, "c": 2}})

    def test_type_replace(self):
        merged = config._deep_merge({"a": {"b": 1}}, {"a": [1, 2]})
        self.assertEqual(merged, {"a": [1, 2]})

    def test_new_top_level_key_kept(self):
        merged = config._deep_merge({"a": 1}, {"z": 9})
        self.assertEqual(merged, {"a": 1, "z": 9})


class GetTests(unittest.TestCase):
    def test_hit(self):
        cfg = {"ledger": {"watch_projects": {"x": "y"}}}
        self.assertEqual(config.get(cfg, "ledger.watch_projects"), {"x": "y"})

    def test_miss_returns_default(self):
        cfg = {"ledger": {}}
        self.assertEqual(config.get(cfg, "ledger.nope", "fallback"), "fallback")

    def test_miss_no_default_is_none(self):
        self.assertIsNone(config.get({}, "missing.path"))


class ConfigPathEnvTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_rigops_config_env_wins(self):
        os.environ["RIGOPS_CONFIG"] = "/tmp/example/config.json"
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.assertEqual(config.config_path(), Path("/tmp/example/config.json"))

    def test_xdg_config_home_env(self):
        os.environ.pop("RIGOPS_CONFIG", None)
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdgconf"
        self.assertEqual(config.config_path(), Path("/tmp/xdgconf/rigops/config.json"))

    def test_default_falls_back_to_home_config(self):
        os.environ.pop("RIGOPS_CONFIG", None)
        os.environ.pop("XDG_CONFIG_HOME", None)
        with tempfile.TemporaryDirectory() as tmp_home:
            os.environ["HOME"] = tmp_home
            expected = Path(tmp_home) / ".config" / "rigops" / "config.json"
            self.assertEqual(config.config_path(), expected)


class LoadTests(unittest.TestCase):
    def test_missing_file_returns_defaults_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            loaded = config.load(missing)
            self.assertEqual(loaded, config.DEFAULTS)
            loaded["ledger"]["watch_projects"]["x"] = "y"
            self.assertEqual(config.DEFAULTS["ledger"]["watch_projects"], {})

    def test_overlay_merges_over_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"ledger": {"watch_projects": {"a": "b"}}}))
            loaded = config.load(cfg_path)
            self.assertEqual(loaded["ledger"]["watch_projects"], {"a": "b"})
            self.assertEqual(loaded["transcripts_dir"], config.DEFAULTS["transcripts_dir"])

    def test_invalid_json_raises_system_exit_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text("{not json")
            with self.assertRaises(SystemExit) as ctx:
                config.load(cfg_path)
            self.assertIn(str(cfg_path), str(ctx.exception))


class ExpandTests(unittest.TestCase):
    def test_expanduser_and_expandvars(self):
        os.environ["RIGOPS_TEST_VAR"] = "value"
        result = config.expand("$RIGOPS_TEST_VAR/sub")
        self.assertEqual(result, Path("value/sub"))


if __name__ == "__main__":
    unittest.main()
