from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import realm  # noqa: E402
from rigops.retrieval import probe, roots  # noqa: E402


def mount(base: Path, target: Path) -> None:
    (base / ".claude").mkdir(parents=True, exist_ok=True)
    (base / ".claude" / "realm").symlink_to(target)


def registry_of(tmp: Path, entries: list) -> roots.Registry:
    path = tmp / "roots.json"
    path.write_text(json.dumps({"roots": entries}))
    return roots.load(path)


class FindTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.realm_dir = self.tmp / "brain" / "rig"
        self.realm_dir.mkdir(parents=True)
        self.registry_file = self.tmp / "roots.json"
        patcher = mock.patch.dict(os.environ, {"REALM_REGISTRY": str(self.registry_file)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def trust(self, *paths, realm="client"):
        self.registry_file.write_text(json.dumps(
            {"roots": [{"path": str(p), "realm": realm} for p in paths]}
        ))

    def test_finds_the_link_at_the_start_dir(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.trust(self.tmp / "mnt")
        self.assertEqual(realm.find(self.tmp / "mnt"), self.realm_dir)

    def test_walks_up_from_a_nested_path_that_does_not_exist(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.trust(self.tmp / "mnt")
        self.assertEqual(realm.find(self.tmp / "mnt" / "a" / "b" / "gone"), self.realm_dir)

    def test_nearest_trusted_link_wins(self):
        other = self.tmp / "other"
        other.mkdir()
        mount(self.tmp / "mnt", self.realm_dir)
        mount(self.tmp / "mnt" / "inner", other)
        self.trust(self.tmp / "mnt", self.tmp / "mnt" / "inner")
        self.assertEqual(realm.find(self.tmp / "mnt" / "inner" / "x"), other)

    def test_walks_past_an_untrusted_link_to_a_trusted_parent(self):
        other = self.tmp / "other"
        other.mkdir()
        mount(self.tmp / "mnt", self.realm_dir)
        mount(self.tmp / "mnt" / "inner", other)
        self.trust(self.tmp / "mnt")
        self.assertEqual(realm.find(self.tmp / "mnt" / "inner" / "x"), self.realm_dir)

    def test_a_link_outside_every_registry_root_is_not_trusted(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.trust(self.tmp / "elsewhere")
        self.assertIsNone(realm.find(self.tmp / "mnt"))

    def test_a_personal_root_does_not_trust_its_link(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.trust(self.tmp / "mnt", realm="personal")
        self.assertIsNone(realm.find(self.tmp / "mnt"))

    def test_a_root_does_not_trust_a_link_above_or_below_it(self):
        mount(self.tmp, self.realm_dir)
        mount(self.tmp / "mnt" / "repo", self.realm_dir)
        self.trust(self.tmp / "mnt")
        self.assertIsNone(realm.find(self.tmp / "mnt" / "repo"))

    def test_glob_root_trusts_the_dir_before_the_glob(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.trust(f"{self.tmp}/mnt/*")
        self.assertEqual(realm.find(self.tmp / "mnt" / "repo"), self.realm_dir)

    def test_glob_root_does_not_trust_a_link_below_the_cut(self):
        mount(self.tmp / "mnt" / "repo", self.realm_dir)
        self.trust(f"{self.tmp}/mnt/*")
        self.assertIsNone(realm.find(self.tmp / "mnt" / "repo"))

    def test_tilde_expands(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.trust("~/mnt")
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.assertEqual(realm.find(self.tmp / "mnt"), self.realm_dir)

    def test_a_start_dir_reached_through_a_symlink_is_trusted(self):
        mount(self.tmp / "mnt", self.realm_dir)
        (self.tmp / "mnt" / "repo").mkdir()
        (self.tmp / "elsewhere").mkdir()
        (self.tmp / "elsewhere" / "alias").symlink_to(self.tmp / "mnt" / "repo")
        self.trust(self.tmp / "mnt")
        self.assertEqual(realm.find(self.tmp / "elsewhere" / "alias"), self.realm_dir)

    def test_a_registry_root_that_is_a_symlink_trusts_its_real_path(self):
        mount(self.tmp / "real", self.realm_dir)
        (self.tmp / "mnt").symlink_to(self.tmp / "real")
        self.trust(self.tmp / "mnt")
        self.assertEqual(realm.find(self.tmp / "real"), self.realm_dir)

    def test_a_symlink_into_an_untrusted_dir_stays_untrusted(self):
        mount(self.tmp / "mnt", self.realm_dir)
        (self.tmp / "plain").mkdir()
        (self.tmp / "mnt" / "alias").symlink_to(self.tmp / "plain")
        self.trust(self.tmp / "mnt")
        self.assertIsNone(realm.find(self.tmp / "mnt" / "alias"))

    def test_a_relative_registry_path_trusts_nothing(self):
        (self.tmp / "cwd").mkdir()
        mount(self.tmp / "cwd" / "rel", self.realm_dir)
        self.trust("rel")
        before = os.getcwd()
        os.chdir(self.tmp / "cwd")
        try:
            self.assertIsNone(realm.find(self.tmp / "cwd" / "rel"))
        finally:
            os.chdir(before)

    def test_default_registry_is_the_one_under_home(self):
        mount(self.tmp / "mnt", self.realm_dir)
        (self.tmp / "rig" / "registry").mkdir(parents=True)
        (self.tmp / "rig" / "registry" / "roots.json").write_text(json.dumps(
            {"roots": [{"path": str(self.tmp / "mnt"), "realm": "client"}]}
        ))
        env = {k: v for k, v in os.environ.items() if k != "REALM_REGISTRY"}
        env["HOME"] = str(self.tmp)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(realm.find(self.tmp / "mnt"), self.realm_dir)

    def test_explicit_registry_wins_over_the_env(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.trust(self.tmp / "elsewhere")
        other = registry_of(self.tmp, [{"path": str(self.tmp / "mnt"), "realm": "client"}])
        self.assertEqual(realm.find(self.tmp / "mnt", other), self.realm_dir)

    def test_missing_registry_trusts_nothing(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.assertIsNone(realm.find(self.tmp / "mnt"))

    def test_malformed_registry_trusts_nothing(self):
        mount(self.tmp / "mnt", self.realm_dir)
        self.registry_file.write_text("{not json")
        self.assertIsNone(realm.find(self.tmp / "mnt"))

    def test_none_without_a_link(self):
        (self.tmp / "plain").mkdir()
        self.trust(self.tmp / "plain")
        self.assertIsNone(realm.find(self.tmp / "plain"))

    def test_a_link_to_a_file_is_not_a_realm(self):
        target = self.tmp / "file"
        target.write_text("x")
        mount(self.tmp / "mnt", target)
        self.trust(self.tmp / "mnt")
        self.assertIsNone(realm.find(self.tmp / "mnt"))

    def test_returns_the_resolved_dir_behind_a_chained_link(self):
        hop = self.tmp / "hop"
        hop.symlink_to(self.realm_dir)
        mount(self.tmp / "mnt", hop)
        self.trust(self.tmp / "mnt")
        self.assertEqual(realm.find(self.tmp / "mnt"), self.realm_dir)


class FromRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.realm_dir = self.tmp / "brain" / "rig"
        self.realm_dir.mkdir(parents=True)
        self.mnt = self.tmp / "mnt"
        mount(self.mnt, self.realm_dir)
        (self.mnt / "repos").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_personal_roots_are_skipped(self):
        reg = registry_of(self.tmp, [{"path": str(self.mnt), "realm": "personal"}])
        self.assertEqual(realm.from_registry(reg), [])

    def test_root_cut_at_home_or_above_is_not_trusted(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.mnt)}):
            for path in (str(self.mnt), f"{self.mnt}/*", str(self.tmp), f"{self.tmp}/*"):
                with self.subTest(path=path):
                    reg = registry_of(self.tmp, [{"path": path, "realm": "client"}])
                    self.assertEqual(realm.mounts(reg), [])

    def test_root_below_home_with_a_trailing_glob_is_trusted(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            reg = registry_of(self.tmp, [{"path": f"{self.mnt}/*", "realm": "client"}])
            self.assertEqual(realm.mounts(reg), [str(self.mnt)])

    def test_root_with_a_non_trailing_glob_is_not_trusted(self):
        for path in (f"{self.tmp}/*/mnt", f"{self.mnt}/**", f"{self.mnt}/re*"):
            with self.subTest(path=path):
                reg = registry_of(self.tmp, [{"path": path, "realm": "client"}])
                self.assertEqual(realm.mounts(reg), [])

    def test_glob_path_is_cut_at_the_first_glob_char(self):
        reg = registry_of(self.tmp, [{"path": f"{self.mnt}/*", "realm": "client"}])
        self.assertEqual(realm.from_registry(reg), [self.realm_dir])

    def test_root_below_a_link_dir_that_is_not_itself_a_root_adds_nothing(self):
        reg = registry_of(self.tmp, [{"path": f"{self.mnt}/repos/*", "realm": "client"}])
        self.assertEqual(realm.from_registry(reg), [])

    def test_roots_in_one_realm_dedupe_and_keep_order(self):
        second = self.tmp / "brain2"
        second.mkdir()
        other_mnt = self.tmp / "other"
        mount(other_mnt, second)
        reg = registry_of(self.tmp, [
            {"path": str(other_mnt), "realm": "client"},
            {"path": f"{self.mnt}/*", "realm": "client"},
            {"path": str(self.mnt), "realm": "mixed"},
        ])
        self.assertEqual(realm.from_registry(reg), [second, self.realm_dir])

    def test_tilde_expands(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            reg = registry_of(self.tmp, [{"path": "~/mnt/*", "realm": "client"}])
            self.assertEqual(realm.from_registry(reg), [self.realm_dir])

    def test_root_outside_any_realm_adds_nothing(self):
        (self.tmp / "plain").mkdir()
        reg = registry_of(self.tmp, [{"path": str(self.tmp / "plain"), "realm": "client"}])
        self.assertEqual(realm.from_registry(reg), [])


class ProbeLoadMergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.realm_dir = self.tmp / "brain" / "rig"
        (self.realm_dir / "eval").mkdir(parents=True)
        mount(self.tmp / "mnt", self.realm_dir)
        self.default = self.tmp / "default.json"
        self.default.write_text(json.dumps({
            "target_precision": 0.9, "min_probes_per_realm": 2,
            "probes": [{"id": 1, "realm": "personal", "question": "q1", "expect": "e1"}],
            "negatives": [{"id": "N1", "cwd": "~/docs", "query": "q", "forbid_realm": "client"}],
        }))
        (self.realm_dir / "eval" / "retrieval-probes.json").write_text(json.dumps({
            "target_precision": 0.1,
            "probes": [{"id": 2, "realm": "client", "question": "q2", "expect": "e2"}],
            "negatives": [{"id": "N2", "cwd": "~/c", "query": "q", "forbid_realm": "personal"}],
        }))
        self.registry = registry_of(self.tmp, [{"path": str(self.tmp / "mnt"), "realm": "client"}])
        self.env = {k: v for k, v in os.environ.items() if k != "RIGOPS_PROBES"}

    def tearDown(self):
        self._tmp.cleanup()

    def _load(self, path=None, registry="unset", env=None):
        registry = self.registry if registry == "unset" else registry
        with mock.patch.object(probe, "DEFAULT_SPEC", str(self.default)), \
                mock.patch.dict(os.environ, env or self.env, clear=True):
            return probe.load(path, registry)

    def test_realm_probes_merge_after_the_default_ones(self):
        spec = self._load()
        self.assertEqual([p["id"] for p in spec["probes"]], [1, 2])
        self.assertEqual([n["id"] for n in spec["negatives"]], ["N1", "N2"])

    def test_settings_come_from_the_default_spec(self):
        spec = self._load()
        self.assertEqual(spec["target_precision"], 0.9)
        self.assertEqual(spec["min_probes_per_realm"], 2)

    def test_explicit_file_skips_the_merge(self):
        spec = self._load(path=str(self.default))
        self.assertEqual([p["id"] for p in spec["probes"]], [1])

    def test_env_file_skips_the_merge(self):
        spec = self._load(env={**self.env, "RIGOPS_PROBES": str(self.default)})
        self.assertEqual([p["id"] for p in spec["probes"]], [1])

    def test_no_registry_skips_the_merge(self):
        spec = self._load(registry=None)
        self.assertEqual([p["id"] for p in spec["probes"]], [1])

    def test_realm_without_a_suite_adds_nothing(self):
        (self.realm_dir / "eval" / "retrieval-probes.json").unlink()
        spec = self._load()
        self.assertEqual([p["id"] for p in spec["probes"]], [1])

    def _realm_suite(self, text):
        (self.realm_dir / "eval" / "retrieval-probes.json").write_text(text)

    def _exit_message(self, **kwargs):
        with self.assertRaises(SystemExit) as caught:
            self._load(**kwargs)
        return str(caught.exception.code)

    def test_merged_suites_are_recorded_and_reported(self):
        spec = self._load()
        suite = str(self.realm_dir / "eval" / "retrieval-probes.json")
        self.assertEqual(spec["merged"], [{"path": suite, "probes": 1, "negatives": 1}])
        outcome = {"by_realm": {}, "negatives": [], "misses": [], "thin_realms": [],
                   "pass": True, "merged": spec["merged"]}
        self.assertIn(f"merged realm suite {suite}: 1 probes, 1 negatives", probe.report(outcome))

    def test_run_outcome_carries_the_merged_suites(self):
        merged = [{"path": "x", "probes": 0, "negatives": 0}]
        spec = {"probes": [], "negatives": [], "merged": merged}
        self.assertEqual(probe.run(self.registry, spec)["merged"], spec["merged"])

    def test_base_suite_alone_may_repeat_ids(self):
        data = json.loads(self.default.read_text())
        data["probes"].append(dict(data["probes"][0]))
        self.default.write_text(json.dumps(data))
        (self.realm_dir / "eval" / "retrieval-probes.json").unlink()
        spec = self._load()
        self.assertEqual([p["id"] for p in spec["probes"]], [1, 1])

    def test_missing_registry_is_an_error(self):
        message = self._exit_message(registry=roots.load(self.tmp / "absent.json"))
        self.assertIn("roots registry not found", message)

    def test_registry_without_roots_is_an_error(self):
        message = self._exit_message(registry=registry_of(self.tmp, []))
        self.assertIn("lists no roots", message)

    def test_missing_or_empty_registry_does_not_matter_when_the_merge_is_skipped(self):
        spec = self._load(path=str(self.default), registry=registry_of(self.tmp, []))
        self.assertEqual([p["id"] for p in spec["probes"]], [1])

    def test_malformed_realm_suite_is_an_error_not_a_traceback(self):
        self._realm_suite("{not json")
        message = self._exit_message()
        self.assertIn("error: realm probe suite", message)
        self.assertIn("retrieval-probes.json", message)

    def test_realm_suite_with_the_wrong_shape_is_an_error(self):
        for text in ("[]", '{"probes": {"id": 2}}', '{"negatives": [1]}'):
            self._realm_suite(text)
            self.assertIn("error: realm probe suite", self._exit_message())

    def test_malformed_default_suite_is_an_error_not_a_traceback(self):
        self.default.write_text("{not json")
        self.assertIn("error: probe suite", self._exit_message())

    def test_duplicate_probe_id_across_suites_is_an_error(self):
        self._realm_suite(json.dumps({"probes": [{"id": 1, "realm": "client"}]}))
        message = self._exit_message()
        self.assertIn("duplicate probes ids", message)
        self.assertIn("1", message)

    def test_duplicate_negative_id_across_suites_is_an_error(self):
        self._realm_suite(json.dumps({"negatives": [{"id": "N1"}]}))
        self.assertIn("duplicate negatives ids across merged suites: N1", self._exit_message())

    def test_duplicate_id_inside_one_suite_is_an_error(self):
        self._realm_suite(json.dumps({"probes": [{"id": 7}, {"id": 7}]}))
        self.assertIn("duplicate probes ids across merged suites: 7", self._exit_message())

    def test_same_id_in_probes_and_negatives_is_not_a_duplicate(self):
        self._realm_suite(json.dumps({"negatives": [{"id": 1}]}))
        self.assertEqual([n["id"] for n in self._load()["negatives"]], ["N1", 1])


if __name__ == "__main__":
    unittest.main()
