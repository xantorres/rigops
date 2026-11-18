from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import mine_dedupe, mine_group  # noqa: E402
from rigops.retrieval import index, roots, search  # noqa: E402

from tests.test_mine import NOW, _registry  # noqa: E402

LINT = "no, always run the lint step before opening a pull request"


def _c(session, text, days=0):
    return mine_group.Correction(
        session=session, project="-p", ts=NOW + dt.timedelta(days=days), text=text)


class TokensTests(unittest.TestCase):
    def test_lowercases_and_returns_a_set(self):
        self.assertEqual(mine_group.tokens("Kiwi KIWI kiwi Lemon"), frozenset({"kiwi", "lemon"}))

    def test_drops_short_words_and_stopwords_in_both_languages(self):
        self.assertEqual(mine_group.tokens("it is the kiwi and que los mango"),
                         frozenset({"kiwi", "mango"}))

    def test_keeps_accents_digits_and_underscores(self):
        self.assertEqual(mine_group.tokens("canción año_2 v12 ñandú"),
                         frozenset({"canción", "año_2", "v12", "ñandú"}))

    def test_empty_and_punctuation_only_text_has_no_tokens(self):
        for text in ("", "   ", "!!! ... ???", "a b c"):
            with self.subTest(text=text):
                self.assertEqual(mine_group.tokens(text), frozenset())

    def test_punctuation_splits_words(self):
        self.assertEqual(mine_group.tokens("kiwi,lemon;mango-papaya"),
                         frozenset({"kiwi", "lemon", "mango", "papaya"}))


class GroupEdgeTests(unittest.TestCase):
    def test_no_items_and_one_item_make_no_group(self):
        self.assertEqual(mine_group.group([]), [])
        self.assertEqual(mine_group.group([_c("s1", LINT)]), [])

    def test_the_same_text_repeated_inside_one_session_is_not_a_group(self):
        self.assertEqual(mine_group.group([_c("s1", LINT), _c("s1", LINT)]), [])

    def test_identical_text_across_two_sessions_is_one_group(self):
        groups = mine_group.group([_c("s1", LINT), _c("s2", LINT)])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sessions, 2)
        self.assertEqual(len(groups[0].members), 2)
        self.assertEqual(groups[0].realm, "unmapped")
        self.assertEqual(groups[0].realms, ())

    def test_a_correction_under_the_token_floor_never_joins(self):
        short = "no, kiwi lemon"
        self.assertLess(len(mine_group.tokens(short)), mine_group.MIN_TOKENS)
        self.assertEqual(mine_group.group([_c("s1", short), _c("s2", short)]), [])
        groups = mine_group.group([_c("s1", LINT), _c("s2", LINT), _c("s3", short)])
        self.assertEqual({m.session for m in groups[0].members}, {"s1", "s2"})

    def test_a_stopword_only_correction_is_unusable(self):
        text = "please just do that now"
        self.assertEqual(mine_group.group([_c("s1", text), _c("s2", text)]), [])

    def test_threshold_is_inclusive(self):
        a = _c("s1", "kiwi lemon mango")
        b = _c("s2", "kiwi lemon papaya")
        self.assertEqual(len(mine_group.group([a, b], threshold=0.5)), 1)
        self.assertEqual(mine_group.group([a, b], threshold=0.51), [])

    def test_disjoint_texts_do_not_group(self):
        self.assertEqual(mine_group.group([_c("s1", "kiwi lemon mango"),
                                           _c("s2", "papaya guava cherry")]), [])

    def test_min_sessions_one_keeps_a_lone_correction(self):
        groups = mine_group.group([_c("s1", LINT)], min_sessions=1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sessions, 1)

    def test_min_sessions_three_rejects_a_pair(self):
        self.assertEqual(mine_group.group([_c("s1", LINT), _c("s2", LINT)], min_sessions=3), [])

    def test_the_input_is_not_reordered_or_consumed(self):
        items = [_c("s2", LINT), _c("s1", LINT)]
        mine_group.group(items)
        self.assertEqual([i.session for i in items], ["s2", "s1"])

    def test_groups_sort_by_sessions_then_members_then_earliest(self):
        big = [_c(f"b{n}", "kiwi lemon mango papaya") for n in range(3)]
        wide = [_c(s, "guava cherry banana grape", days=9) for s in ("w1", "w1", "w2", "w2")]
        late = [_c("l1", "plum apricot nectarine peach", days=5),
                _c("l2", "plum apricot nectarine peach", days=5)]
        early = [_c("e1", "fig date olive quince", days=1),
                 _c("e2", "fig date olive quince", days=1)]
        groups = mine_group.group(late + wide + early + big)
        self.assertEqual([(g.sessions, len(g.members)) for g in groups],
                         [(3, 3), (2, 4), (2, 2), (2, 2)])
        self.assertEqual([g.representative.ts for g in groups[2:]],
                         [NOW + dt.timedelta(days=1), NOW + dt.timedelta(days=5)])

    def test_representative_is_the_most_central_member(self):
        hub = _c("s1", "kiwi lemon mango papaya", days=5)
        left = _c("s2", "kiwi lemon mango guava")
        right = _c("s3", "kiwi lemon papaya cherry")
        groups = mine_group.group([left, right, hub], threshold=0.3)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].members), 3)
        # hub overlaps both others more than they overlap each other, though it is the latest
        self.assertIs(groups[0].representative, hub)

    def test_a_tie_on_centrality_goes_to_the_earliest(self):
        a = _c("s1", LINT, days=2)
        b = _c("s2", LINT, days=0)
        self.assertIs(mine_group.group([a, b])[0].representative, b)


class InheritsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "repo"
        (self.base / "packages" / "core").mkdir(parents=True)
        (self.base / "notes.txt").write_text("x")

    def test_an_empty_or_hidden_remainder_inherits(self):
        self.assertTrue(mine_dedupe._inherits(self.base, ""))
        self.assertTrue(mine_dedupe._inherits(self.base, "--worktrees-t1"))

    def test_a_remainder_inherits_only_through_an_existing_child_dir(self):
        self.assertTrue(mine_dedupe._inherits(self.base, "-packages"))
        self.assertTrue(mine_dedupe._inherits(self.base, "-packages-core"))
        self.assertFalse(mine_dedupe._inherits(self.base, "-pack"))
        self.assertFalse(mine_dedupe._inherits(self.base, "-sandbox"))

    def test_a_plain_file_is_not_a_child_dir(self):
        self.assertFalse(mine_dedupe._inherits(self.base, "-notes-txt"))

    def test_a_missing_base_inherits_nothing_but_a_hidden_dir(self):
        gone = self.base / "absent"
        self.assertFalse(mine_dedupe._inherits(gone, "-anything"))
        self.assertTrue(mine_dedupe._inherits(gone, "--hidden"))


class RealmForProjectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.outer = self.tmp / "work"
        self.inner = self.outer / "inner"
        self.inner.mkdir(parents=True)
        self.registry = _registry(self.tmp, {"roots": [
            {"path": str(self.outer), "realm": "personal", "scope": "repo"},
            {"path": str(self.inner), "realm": "client", "scope": "repo"},
        ]})

    def test_the_longest_matching_root_wins(self):
        self.assertEqual(
            mine_dedupe.realm_for_project(self.registry, roots.encode_project(self.inner)),
            "client")

    def test_the_outer_root_covers_an_existing_child_the_inner_does_not_own(self):
        (self.outer / "other").mkdir()
        self.assertEqual(mine_dedupe.realm_for_project(
            self.registry, roots.encode_project(self.outer / "other")), "personal")

    def test_an_unrelated_project_has_no_realm(self):
        self.assertIsNone(mine_dedupe.realm_for_project(self.registry, "-elsewhere-repo"))

    def test_a_registry_without_roots_has_no_realm(self):
        empty = _registry(self.tmp, {"roots": []})
        self.assertIsNone(mine_dedupe.realm_for_project(empty, "-any"))

    def test_a_root_that_does_not_exist_maps_nothing(self):
        ghost = _registry(self.tmp, {"roots": [
            {"path": str(self.tmp / "ghost"), "realm": "client", "scope": "repo"}]})
        self.assertIsNone(mine_dedupe.realm_for_project(
            ghost, roots.encode_project(self.tmp / "ghost")))


class RealmCwdsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.parent = self.tmp / "repos"
        for name in ("alpha", "beta"):
            (self.parent / name).mkdir(parents=True)

    def _reg(self, root, memory_root=None):
        raw = {"roots": [root]}
        if memory_root is not None:
            raw["memory_root"] = {"path": str(memory_root)}
        return _registry(self.tmp, raw)

    def test_a_realm_with_no_memory_scoped_root_has_no_cwds(self):
        reg = self._reg({"path": str(self.parent / "alpha"), "realm": "personal",
                         "scope": "repo", "cwd_scopes": ["repo"]})
        self.assertEqual(mine_dedupe.realm_cwds(reg, "personal"), [])
        self.assertEqual(mine_dedupe.realm_cwds(reg, "client"), [])

    def test_a_non_repo_root_answers_with_its_own_base(self):
        reg = self._reg({"path": str(self.parent / "alpha"), "realm": "personal",
                         "scope": "repo", "repo": False, "cwd_scopes": ["memory"]})
        self.assertEqual(mine_dedupe.realm_cwds(reg, "personal"), [self.parent / "alpha"])

    def test_repo_roots_without_any_memory_dir_fall_back_to_the_first_base(self):
        reg = self._reg({"path": f"{self.parent}/*", "realm": "personal", "scope": "repo",
                         "repo": True, "cwd_scopes": ["memory"]}, self.tmp / "no-memory-root")
        self.assertEqual(mine_dedupe.realm_cwds(reg, "personal"), [self.parent / "alpha"])

    def test_repo_roots_narrow_to_the_ones_holding_memory(self):
        memory = self.tmp / "mem" / roots.encode_project(self.parent / "beta") / "memory"
        memory.mkdir(parents=True)
        reg = self._reg({"path": f"{self.parent}/*", "realm": "personal", "scope": "repo",
                         "repo": True, "cwd_scopes": ["memory"]}, self.tmp / "mem")
        self.assertEqual(mine_dedupe.realm_cwds(reg, "personal"), [self.parent / "beta"])

    def test_a_missing_root_has_no_cwds(self):
        reg = self._reg({"path": str(self.tmp / "ghost"), "realm": "personal",
                         "scope": "repo", "repo": True, "cwd_scopes": ["memory"]})
        self.assertEqual(mine_dedupe.realm_cwds(reg, "personal"), [])


class GateTests(unittest.TestCase):
    def test_default_gate_when_the_registry_sets_none(self):
        reg = SimpleNamespace(raw={})
        self.assertEqual(mine_dedupe.dedupe_gate(reg), mine_dedupe.DEDUPE_MAX_RANK)
        self.assertEqual(mine_dedupe.dedupe_gate(SimpleNamespace(raw={"prefetch": {}})),
                         mine_dedupe.DEDUPE_MAX_RANK)

    def test_registry_gate_overrides_and_is_a_float(self):
        reg = SimpleNamespace(raw={"prefetch": {"min_score": "-3"}})
        self.assertEqual(mine_dedupe.dedupe_gate(reg), -3.0)
        self.assertIsInstance(mine_dedupe.dedupe_gate(reg), float)

    def test_gated_copy_changes_only_the_score_and_leaves_the_original(self):
        reg = SimpleNamespace(raw={"prefetch": {"min_score": -9, "min_terms": 2}, "x": 1})
        gated = mine_dedupe._gated(reg, -1.5)
        self.assertEqual(gated.raw, {"prefetch": {"min_score": -1.5, "min_terms": 2}, "x": 1})
        self.assertEqual(reg.raw, {"prefetch": {"min_score": -9, "min_terms": 2}, "x": 1})
        self.assertIsNot(gated, reg)

    def test_gated_copy_adds_the_prefetch_block_when_absent(self):
        reg = SimpleNamespace(raw={})
        self.assertEqual(mine_dedupe._gated(reg, -2.0).raw, {"prefetch": {"min_score": -2.0}})
        self.assertEqual(reg.raw, {})


class NearestAndCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "repos" / "alpha"
        self.repo.mkdir(parents=True)
        memory = self.tmp / "mem" / roots.encode_project(self.repo) / "memory"
        memory.mkdir(parents=True)
        (memory / "lint.md").write_text(
            "## Lint before pull request\nAlways run the lint step before opening a pull "
            "request because the pipeline rejects unlinted branches.\n")
        self.registry = _registry(self.tmp, {
            "realms": ["client", "personal", "mixed"],
            "scopes": ["memory", "repo"],
            "memory_root": {"path": str(self.tmp / "mem")},
            "index": {"db": str(self.tmp / "idx.sqlite")},
            "roots": [{"path": str(self.repo), "realm": "personal", "scope": "repo",
                       "repo": False, "index": False, "cwd_scopes": ["memory", "repo"]}],
        })
        index.build(self.registry)

    def test_no_realms_means_no_hit_and_no_duplicate(self):
        self.assertIsNone(mine_dedupe.nearest_memory(self.registry, LINT, []))
        self.assertEqual(mine_dedupe.check(self.registry, LINT, [], 0.0), (None, False))

    def test_text_sharing_no_term_finds_nothing(self):
        self.assertEqual(
            mine_dedupe.check(self.registry, "kiwi papaya guava", ["personal"], 0.0),
            (None, False))

    def test_an_unknown_realm_finds_nothing(self):
        self.assertIsNone(mine_dedupe.nearest_memory(self.registry, LINT, ["client"]))

    def test_a_near_match_is_a_duplicate_under_a_loose_gate(self):
        hit, duplicate = mine_dedupe.check(self.registry, LINT, ["personal"], 0.0)
        self.assertTrue(duplicate)
        self.assertTrue(hit.path.endswith("lint.md"))

    def test_a_strict_gate_returns_the_nearest_hit_without_calling_it_a_duplicate(self):
        hit, duplicate = mine_dedupe.check(self.registry, LINT, ["personal"], -1000.0)
        self.assertFalse(duplicate)
        self.assertTrue(hit.path.endswith("lint.md"))

    def test_a_database_error_is_skipped_not_raised(self):
        with mock.patch.object(search, "query", side_effect=sqlite3.DatabaseError("locked")):
            self.assertIsNone(mine_dedupe.nearest_memory(self.registry, LINT, ["personal"]))
            self.assertEqual(mine_dedupe.check(self.registry, LINT, ["personal"], 0.0),
                             (None, False))

    def test_archived_notes_are_never_the_nearest(self):
        archived = SimpleNamespace(path=f"/x{search.ARCHIVE_MARKER}old.md", rank=-50.0)
        live = SimpleNamespace(path="/x/memory/live.md", rank=-5.0)
        result = SimpleNamespace(hits=[archived, live])
        with mock.patch.object(search, "query", return_value=result):
            self.assertIs(mine_dedupe.nearest_memory(self.registry, LINT, ["personal"]), live)
        with mock.patch.object(search, "query", return_value=SimpleNamespace(hits=[archived])):
            self.assertIsNone(mine_dedupe.nearest_memory(self.registry, LINT, ["personal"]))

    def test_the_best_ranked_hit_across_cwds_wins(self):
        worse = SimpleNamespace(path="/a/memory/a.md", rank=-2.0)
        better = SimpleNamespace(path="/b/memory/b.md", rank=-9.0)
        results = iter([SimpleNamespace(hits=[worse]), SimpleNamespace(hits=[better])])
        cwds = [self.repo, self.repo / "other"]
        with mock.patch.object(mine_dedupe, "realm_cwds", return_value=cwds), \
                mock.patch.object(search, "query", side_effect=lambda *a, **k: next(results)):
            self.assertIs(mine_dedupe.nearest_memory(self.registry, LINT, ["personal"]), better)


if __name__ == "__main__":
    unittest.main()
