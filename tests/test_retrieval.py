from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops.doctor import check_realms  # noqa: E402
from rigops.retrieval import chunk, index, log, probe, roots, search  # noqa: E402


def make_registry(tmp: Path, raw: dict) -> roots.Registry:
    path = tmp / "roots.json"
    path.write_text(json.dumps(raw))
    return roots.load(path)


def write_tree(base: Path, files: dict) -> None:
    for rel, text in files.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


class ChunkSplitTests(unittest.TestCase):
    def test_splits_at_h2_and_keeps_heading_line_in_section(self):
        text = "## First\nfirst body\n## Second\nsecond body\n"
        sections = chunk.split(text)
        self.assertEqual([s.heading for s in sections], ["First", "Second"])
        self.assertTrue(sections[0].text.startswith("## First"))
        self.assertTrue(sections[1].text.startswith("## Second"))
        self.assertNotIn("second body", sections[0].text)
        self.assertNotIn("first body", sections[1].text)

    def test_start_lines_are_1_based_and_match_source(self):
        text = "intro line one\nintro line two\n## Alpha\nalpha body\n## Beta\nbeta body\n"
        sections = chunk.split(text)
        self.assertEqual([s.line for s in sections], [1, 3, 5])

    def test_strips_frontmatter(self):
        text = "---\nkey: value\n---\n## Heading\nbody line\n"
        sections = chunk.split(text)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].line, 4)
        self.assertNotIn("key: value", sections[0].text)
        self.assertNotIn("---", sections[0].text)

    def test_verified_stamp_in_section_carries_into_verified_field(self):
        text = "## Heading\n<!-- verified: 2026-01-05 -->\nbody\n"
        sections = chunk.split(text)
        self.assertEqual(sections[0].verified, "2026-01-05")

    def test_falls_back_to_frontmatter_date(self):
        text = "---\nverified: 2025-12-01\n---\n## Heading\nbody without its own stamp\n"
        sections = chunk.split(text)
        self.assertEqual(sections[0].verified, "2025-12-01")

    def test_oversized_section_splits_at_h3(self):
        text = "## Big\n### One\none body text here\n### Two\ntwo body text here\n"
        sections = chunk.split(text, max_chars=30)
        self.assertEqual([s.heading for s in sections], ["Big", "Big / One", "Big / Two"])
        self.assertEqual([s.line for s in sections], [1, 2, 4])
        self.assertEqual(sections[0].text, "## Big")
        self.assertEqual(sections[1].text, "### One\none body text here")
        self.assertEqual(sections[2].text, "### Two\ntwo body text here")

    def test_oversized_section_with_no_h3s_hard_cuts(self):
        text = "## Solo\naaaa\nbbbb\ncccc\n"
        sections = chunk.split(text, max_chars=15)
        self.assertEqual([s.heading for s in sections], ["Solo", "Solo"])
        self.assertEqual([s.line for s in sections], [1, 4])
        self.assertEqual(sections[0].text, "## Solo\naaaa\nbbbb")
        self.assertEqual(sections[1].text, "cccc")


class ChunkAnswersOfTests(unittest.TestCase):
    def test_yaml_frontmatter_syntax_and_quoted_value_is_unquoted(self):
        text = ('---\nanswers: "document code drift mismatch"\n---\n'
                '## Heading\nbody\n')
        self.assertEqual(chunk.answers_of(text), "document code drift mismatch")

    def test_html_comment_syntax(self):
        text = "## Heading\n<!-- answers: document code drift mismatch -->\nbody\n"
        self.assertEqual(chunk.answers_of(text), "document code drift mismatch")

    def test_hash_and_slash_line_comment_syntax(self):
        hash_text = "# answers: document code drift mismatch\nimport os\n"
        slash_text = "// answers: document code drift mismatch\nconst x = 1;\n"
        self.assertEqual(chunk.answers_of(hash_text), "document code drift mismatch")
        self.assertEqual(chunk.answers_of(slash_text), "document code drift mismatch")

    def test_absent_returns_empty_string(self):
        text = "## Heading\nno declared line of any syntax here\n"
        self.assertEqual(chunk.answers_of(text), "")


class RegistryPlaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_most_specific_root_wins(self):
        write_tree(self.tmp, {"personal/notes/a.md": "x\n"})
        registry = make_registry(self.tmp, {
            "roots": [
                {"path": str(self.tmp / "personal"), "realm": "personal", "scope": "vault"},
                {"path": str(self.tmp / "personal/notes"), "realm": "personal",
                 "scope": "special"},
            ],
        })
        placement = registry.place(self.tmp / "personal/notes/a.md")
        self.assertEqual(placement.scope, "special")

    def test_include_and_exclude_globs_with_double_star(self):
        write_tree(self.tmp, {
            "root/keep/a.md": "x\n",
            "root/other/b.md": "x\n",
            "root/secret/c.md": "x\n",
        })
        registry = make_registry(self.tmp, {
            "roots": [{
                "path": str(self.tmp / "root"), "realm": "personal", "scope": "vault",
                "include": ["keep/**"], "exclude": ["**/secret/**"],
            }],
        })
        self.assertIsNotNone(registry.place(self.tmp / "root/keep/a.md"))
        self.assertIsNone(registry.place(self.tmp / "root/other/b.md"))
        self.assertIsNone(registry.place(self.tmp / "root/secret/c.md"))

    def test_per_root_extensions_are_carried_on_placement(self):
        write_tree(self.tmp, {"custom/a.md": "x\n", "default/b.md": "x\n"})
        registry = make_registry(self.tmp, {
            "roots": [
                {"path": str(self.tmp / "custom"), "realm": "personal", "scope": "vault",
                 "extensions": [".mdx"]},
                {"path": str(self.tmp / "default"), "realm": "personal", "scope": "vault"},
            ],
        })
        self.assertEqual(registry.place(self.tmp / "custom/a.md").extensions, (".mdx",))
        self.assertEqual(registry.place(self.tmp / "default/b.md").extensions, (".md", ".txt"))

    def test_sensitive_name_parts_skips_indexing_the_file(self):
        # place() only carries `sensitive` onto the Placement; index.rows_for is what
        # actually skips a matching file, so the skip is proven at that call site.
        write_tree(self.tmp, {"root/secret-key.md": "body\n", "root/plain.md": "body\n"})
        registry = make_registry(self.tmp, {
            "roots": [{"path": str(self.tmp / "root"), "realm": "personal", "scope": "vault",
                       "sensitive_name_parts": ["secret"]}],
        })
        sensitive = self.tmp / "root/secret-key.md"
        placement = registry.place(sensitive)
        self.assertIsNotNone(placement)
        rows = index.rows_for(registry, sensitive, placement, 10_000_000,
                              chunk.DEFAULT_MAX_CHARS)
        self.assertEqual(rows, [])
        plain = self.tmp / "root/plain.md"
        rows_plain = index.rows_for(registry, plain, registry.place(plain), 10_000_000,
                                    chunk.DEFAULT_MAX_CHARS)
        self.assertEqual(len(rows_plain), 1)

    def test_path_under_no_root_returns_none(self):
        registry = make_registry(self.tmp, {"roots": []})
        self.assertIsNone(registry.place(self.tmp / "nowhere/a.md"))


class RegistryRealmForTextTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.registry = make_registry(self.tmp, {"roots": [], "client_tokens": ["widgetcorp"]})

    def tearDown(self):
        self._tmp.cleanup()

    def _placement(self, realm, classify):
        return roots.Placement(realm=realm, scope="vault", repo="", root=None,
                               base=self.tmp, rel="a.md", classify=classify)

    def test_classify_personal_root_with_client_token_becomes_mixed(self):
        placement = self._placement("personal", True)
        text = "notes about the widgetcorp rollout"
        self.assertEqual(self.registry.realm_for_text(placement, text), "mixed")

    def test_classify_personal_root_without_token_stays_personal(self):
        placement = self._placement("personal", True)
        text = "notes about the weekend rollout"
        self.assertEqual(self.registry.realm_for_text(placement, text), "personal")

    def test_client_root_is_never_reclassified(self):
        placement = self._placement("client", True)
        text = "notes about the widgetcorp rollout"
        self.assertEqual(self.registry.realm_for_text(placement, text), "client")


class RegistryMemoryDirsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.memory_root = self.tmp / "claude-projects"
        self.memory_root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_encoded_project_dir_maps_to_root_realm_and_repo(self):
        repo = self.tmp / "repos" / "alpha"
        repo.mkdir(parents=True)
        registry = make_registry(self.tmp, {
            "memory_root": {"path": str(self.memory_root)},
            "roots": [{"path": str(repo), "realm": "personal", "scope": "repo", "repo": True}],
        })
        encoded = roots.encode_project(repo)
        write_tree(self.memory_root, {f"{encoded}/memory/note.md": "note\n"})
        dirs = registry.memory_dirs()
        self.assertEqual(dirs, [(self.memory_root / encoded / "memory", "personal", "alpha")])

    def test_longest_encoding_wins_between_repo_and_parent(self):
        parent = self.tmp / "code"
        child = parent / "widget"
        child.mkdir(parents=True)
        registry = make_registry(self.tmp, {
            "memory_root": {"path": str(self.memory_root)},
            "roots": [
                {"path": str(parent), "realm": "parent-realm", "scope": "vault", "repo": False},
                {"path": str(child), "realm": "client", "scope": "repo", "repo": True},
            ],
        })
        encoded_child = roots.encode_project(child)
        write_tree(self.memory_root, {f"{encoded_child}/memory/note.md": "note\n"})
        dirs = registry.memory_dirs()
        self.assertEqual(dirs, [(self.memory_root / encoded_child / "memory", "client", "widget")])

    def test_unmatched_directory_is_unmapped_and_excluded(self):
        registry = make_registry(self.tmp, {
            "memory_root": {"path": str(self.memory_root)},
            "roots": [],
        })
        write_tree(self.memory_root, {"totally-unrelated/memory/note.md": "note\n"})
        self.assertEqual(registry.memory_dirs(), [])
        unmapped = registry.unmapped_memory_dirs()
        self.assertEqual(unmapped, [self.memory_root / "totally-unrelated" / "memory"])


class RegistryProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repos" / "alpha"
        (self.repo / "sub").mkdir(parents=True)
        self.registry = make_registry(self.tmp, {
            "roots": [{"path": str(self.repo), "realm": "personal", "scope": "repo",
                       "repo": True, "cwd_scopes": ["repo", "memory"]}],
        })

    def tearDown(self):
        self._tmp.cleanup()

    def test_profile_at_exact_root(self):
        profile = self.registry.profile(self.repo)
        self.assertEqual(profile.realm, "personal")
        self.assertEqual(profile.scopes, ("repo", "memory"))
        self.assertEqual(profile.repo, "alpha")

    def test_profile_in_nested_subdirectory(self):
        profile = self.registry.profile(self.repo / "sub")
        self.assertEqual(profile.repo, "alpha")

    def test_profile_for_unmapped_cwd_is_none(self):
        self.assertIsNone(self.registry.profile(self.tmp / "elsewhere"))


class RegistryProblemsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_unknown_realm_is_flagged(self):
        existing = self.tmp / "root"
        existing.mkdir()
        registry = make_registry(self.tmp, {
            "realms": ["client", "personal", "mixed"], "scopes": ["vault"],
            "roots": [{"path": str(existing), "realm": "bogus", "scope": "vault"}],
        })
        keys = [item[0] for item in registry.problems()]
        self.assertTrue(any(k.startswith("realm:") for k in keys))

    def test_unknown_scope_is_flagged(self):
        existing = self.tmp / "root"
        existing.mkdir()
        registry = make_registry(self.tmp, {
            "realms": ["personal"], "scopes": ["vault"],
            "roots": [{"path": str(existing), "realm": "personal", "scope": "bogus"}],
        })
        keys = [item[0] for item in registry.problems()]
        self.assertTrue(any(k.startswith("scope:") for k in keys))

    def test_cwd_scope_grant_not_produced_by_that_realm_is_flagged(self):
        client_root = self.tmp / "client"
        personal_root = self.tmp / "personal"
        client_root.mkdir()
        personal_root.mkdir()
        registry = make_registry(self.tmp, {
            "realms": ["client", "personal"], "scopes": ["vault", "special"],
            "roots": [
                {"path": str(client_root), "realm": "client", "scope": "special"},
                {"path": str(personal_root), "realm": "personal", "scope": "vault",
                 "cwd_scopes": ["special"]},
            ],
        })
        keys = [item[0] for item in registry.problems()]
        self.assertTrue(any(k.startswith("grant:") for k in keys))
        self.assertFalse(any(k == "empty:special" for k in keys))

    def test_root_absent_on_disk_is_flagged(self):
        registry = make_registry(self.tmp, {
            "realms": ["personal"], "scopes": ["vault"],
            "roots": [{"path": str(self.tmp / "does-not-exist"), "realm": "personal",
                       "scope": "vault"}],
        })
        keys = [item[0] for item in registry.problems()]
        self.assertTrue(any(k.startswith("absent:") for k in keys))

    def test_classify_with_empty_token_list_is_flagged(self):
        existing = self.tmp / "root"
        existing.mkdir()
        registry = make_registry(self.tmp, {
            "realms": ["personal"], "scopes": ["vault"], "client_tokens": [],
            "roots": [{"path": str(existing), "realm": "personal", "scope": "vault",
                       "classify": True}],
        })
        keys = [item[0] for item in registry.problems()]
        self.assertIn("tokens", keys)


class IndexSearchIsolationTests(unittest.TestCase):
    """Realm and scope isolation end to end - the point of this file.

    A prefetch is scoped by cwd and can never widen; an explicit query from the
    same cwd legitimately reaches `mixed`. One tiny index, built once per test,
    proves both directions plus the repo and budget cutoffs around them.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        write_tree(self.home, {
            "personal/notes.md":
                "## Notes\npersonal caching strategy notes for redis expiry tuning\n",
            "personal/other.md":
                "## Other\npersonal caching strategy notes about memcache tuning too\n",
            "personal/mixed-notes.md":
                "## Mixed\npersonal infrastructure project notes mentioning widgetcorp "
                "deployment pipeline and caching setup\n",
            "client/brief.md":
                "## Brief\nwidgetcorp engagement brief covering project scope, "
                "deliverables, and caching approach\n",
            "repos/alpha/README.md":
                "## Alpha\nalpha repository project readme covering setup steps\n",
            "repos/beta/README.md":
                "## Beta\nbeta repository project readme covering setup steps\n",
        })
        alpha = self.home / "repos" / "alpha"
        beta = self.home / "repos" / "beta"
        memory_root = self.home / "claude-projects"
        write_tree(memory_root, {
            f"{roots.encode_project(alpha)}/memory/note.md": "alpha project decision log entry\n",
            f"{roots.encode_project(beta)}/memory/note.md": "beta project decision log entry\n",
        })
        self.db_path = self.tmp / "index.sqlite"
        self.registry = make_registry(self.tmp, {
            "realms": ["client", "personal", "mixed"],
            "scopes": ["vault", "repo", "memory"],
            "client_tokens": ["widgetcorp"],
            "memory_root": {"path": str(memory_root)},
            "index": {"db": str(self.db_path)},
            # this fixture is a handful of near-identical tiny docs, so bm25 scores
            # cluster near zero; relax the threshold here, the strict case has its own test.
            "prefetch": {"min_score": 0},
            "roots": [
                {"path": str(self.home / "personal"), "realm": "personal", "scope": "vault",
                 "classify": True, "cwd_scopes": ["vault"]},
                {"path": str(self.home / "client"), "realm": "client", "scope": "vault",
                 "cwd_scopes": ["vault"]},
                {"path": str(self.home / "repos" / "*"), "realm": "personal", "scope": "repo",
                 "repo": True, "cwd_scopes": ["repo", "memory"]},
            ],
        })
        index.build(self.registry)
        self.cwd_personal = self.home / "personal"
        self.cwd_client = self.home / "client"
        self.cwd_alpha = alpha
        self.cwd_beta = beta

    def tearDown(self):
        self._tmp.cleanup()

    def test_prefetch_from_client_cwd_returns_zero_personal_rows(self):
        result = search.query(self.registry, "caching", cwd=self.cwd_client, prefetch=True,
                              top=10, db_path=self.db_path)
        self.assertTrue(result.hits)
        self.assertTrue(all(hit.realm == "client" for hit in result.hits))

    def test_prefetch_from_personal_cwd_returns_zero_client_rows(self):
        result = search.query(self.registry, "caching", cwd=self.cwd_personal, prefetch=True,
                              top=10, db_path=self.db_path)
        self.assertTrue(result.hits)
        self.assertTrue(all(hit.realm == "personal" for hit in result.hits))

    def test_neither_prefetch_ever_returns_mixed(self):
        client_result = search.query(self.registry, "caching", cwd=self.cwd_client,
                                     prefetch=True, top=10, db_path=self.db_path)
        personal_result = search.query(self.registry, "caching", cwd=self.cwd_personal,
                                       prefetch=True, top=10, db_path=self.db_path)
        self.assertFalse(any(hit.realm == "mixed" for hit in client_result.hits))
        self.assertFalse(any(hit.realm == "mixed" for hit in personal_result.hits))

    def test_explicit_query_from_the_same_cwd_does_return_mixed(self):
        result = search.query(self.registry, "caching", cwd=self.cwd_personal, top=10,
                              db_path=self.db_path)
        self.assertTrue(any(hit.realm == "mixed" for hit in result.hits))

    def test_scopes_all_returns_every_realm(self):
        result = search.query(self.registry, "project", cwd=self.cwd_personal,
                              scopes=("all",), top=10, db_path=self.db_path)
        self.assertEqual({hit.realm for hit in result.hits}, {"client", "personal", "mixed"})

    def test_repo_cwd_sees_only_its_own_repo_and_memory_rows(self):
        result = search.query(self.registry, "project", cwd=self.cwd_alpha, top=10,
                              db_path=self.db_path)
        self.assertTrue(result.hits)
        self.assertEqual({hit.repo for hit in result.hits}, {"alpha"})
        self.assertTrue(all(hit.scope in ("repo", "memory") for hit in result.hits))

    def test_budget_caps_returned_tokens_and_still_returns_at_least_one_row(self):
        baseline = search.query(self.registry, "caching", cwd=self.cwd_personal, top=10,
                                db_path=self.db_path)
        capped = search.query(self.registry, "caching", cwd=self.cwd_personal, top=10,
                              budget=1, db_path=self.db_path)
        self.assertGreater(len(baseline.hits), len(capped.hits))
        self.assertGreaterEqual(len(capped.hits), 1)

    def test_prefetch_below_min_score_returns_nothing(self):
        self.registry.raw["prefetch"] = {"min_score": -999, "max_rows": 3, "budget": 400}
        result = search.query(self.registry, "caching", cwd=self.cwd_personal, prefetch=True,
                              top=10, db_path=self.db_path)
        self.assertEqual(result.hits, [])
        self.assertEqual(result.silent_reason, "below score threshold")


class IndexUpdateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        write_tree(self.home, {
            "personal/a.md": "## A\noriginal alpha content\n",
            "personal/b.md": "## B\noriginal beta content\n",
        })
        self.db_path = self.tmp / "index.sqlite"
        self.registry = make_registry(self.tmp, {
            "realms": ["personal"], "scopes": ["vault"],
            "index": {"db": str(self.db_path)},
            "roots": [{"path": str(self.home / "personal"), "realm": "personal",
                       "scope": "vault"}],
        })
        index.build(self.registry, self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _rows_for_path(self, path):
        conn = sqlite3.connect(str(self.db_path))
        try:
            query = "SELECT content FROM docs WHERE path = ?"
            return conn.execute(query, (str(path),)).fetchall()
        finally:
            conn.close()

    def test_changed_file_rows_are_replaced(self):
        path = self.home / "personal" / "a.md"
        path.write_text("## A\nrewritten alpha content\n")
        future = time.time() + 10
        os.utime(path, (future, future))
        out = index.update(self.registry, self.db_path)
        self.assertEqual(out["changed"], 1)
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["removed"], 0)
        rows = self._rows_for_path(path)
        self.assertEqual(len(rows), 1)
        self.assertIn("rewritten", rows[0][0])

    def test_deleted_file_rows_are_removed(self):
        path = self.home / "personal" / "b.md"
        path.unlink()
        out = index.update(self.registry, self.db_path)
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["changed"], 0)
        self.assertEqual(self._rows_for_path(path), [])

    def test_new_file_is_added(self):
        path = self.home / "personal" / "c.md"
        path.write_text("## C\nbrand new gamma content\n")
        out = index.update(self.registry, self.db_path)
        self.assertEqual(out["added"], 1)
        self.assertEqual(out["changed"], 0)
        self.assertEqual(out["removed"], 0)
        self.assertEqual(len(self._rows_for_path(path)), 1)

    def test_database_from_an_older_schema_triggers_a_full_rebuild(self):
        self.db_path.unlink()
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('schema', ?)", (str(index.SCHEMA - 1),))
        conn.commit()
        conn.close()
        out = index.update(self.registry, self.db_path)
        self.assertIn("files", out)
        self.assertEqual(out["files"], 2)


class IndexAnswersColumnTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_answers_value_reaches_every_section_row(self):
        write_tree(self.tmp, {
            "root/doc.md":
                "---\nanswers: document code drift mismatch\n---\n"
                "## First\nfirst body\n## Second\nsecond body\n",
        })
        registry = make_registry(self.tmp, {
            "roots": [{"path": str(self.tmp / "root"), "realm": "personal", "scope": "vault"}],
        })
        path = self.tmp / "root" / "doc.md"
        rows = index.rows_for(registry, path, registry.place(path), 10_000_000,
                              chunk.DEFAULT_MAX_CHARS)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row[-1] == "document code drift mismatch" for row in rows))


class SearchAnswersColumnRankingTests(unittest.TestCase):
    """The point of the whole change: a declared answers: line outranks the same
    words sitting in ordinary prose, for a query that uses only those words."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        write_tree(self.home, {
            "root/declared.md":
                "---\nanswers: document code drift mismatch\n---\n"
                "## Doc A\nbody about something else entirely, unrelated prose here\n",
            "root/plain.md":
                "## Doc B\nbody mentions document code drift mismatch as regular "
                "prose without any declared field\n",
        })
        self.db_path = self.tmp / "index.sqlite"
        self.registry = make_registry(self.tmp, {
            "realms": ["personal"], "scopes": ["vault"],
            "index": {"db": str(self.db_path)},
            "roots": [{"path": str(self.home / "root"), "realm": "personal", "scope": "vault",
                       "cwd_scopes": ["vault"]}],
        })
        index.build(self.registry, self.db_path)
        self.cwd = self.home / "root"

    def tearDown(self):
        self._tmp.cleanup()

    def test_declared_answers_outranks_the_same_words_in_prose(self):
        result = search.query(self.registry, "document code drift mismatch",
                              cwd=self.cwd, top=10, db_path=self.db_path)
        self.assertGreaterEqual(len(result.hits), 2)
        self.assertTrue(result.hits[0].short_path.endswith("declared.md"))


class ProbeRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        write_tree(self.home, {
            "personal/doc.md": "## Doc\npersonal alpha keyword content here\n",
            "client/doc.md": "## Doc\nclient alpha keyword content here\n",
        })
        self.db_path = self.tmp / "index.sqlite"
        self.registry = make_registry(self.tmp, {
            "realms": ["client", "personal"], "scopes": ["vault"],
            "index": {"db": str(self.db_path)},
            # near-identical tiny docs push bm25 scores near zero; the negative probe
            # runs a prefetch, so it needs a threshold this fixture can actually clear.
            "prefetch": {"min_score": 0},
            "roots": [
                {"path": str(self.home / "personal"), "realm": "personal", "scope": "vault",
                 "cwd_scopes": ["vault"]},
                {"path": str(self.home / "client"), "realm": "client", "scope": "vault",
                 "cwd_scopes": ["vault"]},
            ],
        })
        index.build(self.registry, self.db_path)
        self.personal_doc = self.home / "personal" / "doc.md"
        self.cwd_personal = self.home / "personal"

    def tearDown(self):
        self._tmp.cleanup()

    def test_precision_arithmetic_counts_a_hit_and_a_miss(self):
        spec = {
            "target_precision": 0.5, "min_probes_per_realm": 1,
            "probes": [
                {"id": "p1", "realm": "personal", "cwd": str(self.cwd_personal),
                 "question": "alpha keyword", "expect": str(self.personal_doc)},
                {"id": "p2", "realm": "personal", "cwd": str(self.cwd_personal),
                 "question": "alpha keyword",
                 "expect": str(self.home / "personal" / "nope.md")},
            ],
        }
        outcome = probe.run(self.registry, spec, top=3, db_path=self.db_path)
        bucket = outcome["by_realm"]["personal"]
        self.assertEqual(bucket["n"], 2)
        self.assertEqual(bucket["hits"], 1)
        self.assertEqual(bucket["precision_at_3"], 0.5)

    def test_expect_as_a_list_matches_either_path(self):
        spec = {
            "target_precision": 0.5, "min_probes_per_realm": 1,
            "probes": [{
                "id": "p3", "realm": "personal", "cwd": str(self.cwd_personal),
                "question": "alpha keyword",
                "expect": [str(self.home / "personal" / "wrong.md"), str(self.personal_doc)],
            }],
        }
        outcome = probe.run(self.registry, spec, top=3, db_path=self.db_path)
        self.assertEqual(outcome["misses"], [])
        self.assertEqual(outcome["by_realm"]["personal"]["hits"], 1)

    def test_negative_probe_fails_when_forbidden_realm_row_returns(self):
        spec = {
            "target_precision": 0.5, "min_probes_per_realm": 1,
            "probes": [],
            "negatives": [{"id": "neg1", "cwd": str(self.cwd_personal), "query": "alpha",
                           "forbid_realm": "personal"}],
        }
        outcome = probe.run(self.registry, spec, top=3, db_path=self.db_path)
        negative = outcome["negatives"][0]
        self.assertFalse(negative["pass"])
        self.assertTrue(negative["leaked"])


class LogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.log_path = self.tmp / "retrieval.jsonl"
        self.registry = make_registry(self.tmp, {"roots": []})

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_appends_a_row_per_call(self):
        result = search.Result(hits=[], realms=("personal",), scopes=("vault",), repo="",
                               cwd=str(self.tmp), query="q1", latency_ms=10, tokens=0,
                               silent_reason="no match")
        with mock.patch.dict(os.environ, {"RIGOPS_RETRIEVAL_LOG": str(self.log_path)}):
            log.record(result, mode="search")
            log.record(result, mode="search")
        lines = self.log_path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["query"], "q1")

    def test_read_respects_the_day_window(self):
        now = dt.datetime.now(dt.timezone.utc)
        old_row = {"ts": (now - dt.timedelta(days=10)).isoformat(timespec="seconds"),
                  "query": "old"}
        recent_row = {"ts": (now - dt.timedelta(days=1)).isoformat(timespec="seconds"),
                     "query": "recent"}
        with self.log_path.open("w") as fh:
            fh.write(json.dumps(old_row) + "\n")
            fh.write(json.dumps(recent_row) + "\n")
        rows = log.read(since_days=7, path=self.log_path)
        self.assertEqual([row["query"] for row in rows], ["recent"])

    def test_summary_reports_latency_percentiles_and_prefetch_silent_rate(self):
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        rows = [
            {"ts": now, "mode": "search", "rows": 1, "latency_ms": 100, "tokens": 40},
            {"ts": now, "mode": "prefetch", "rows": 1, "latency_ms": 300, "tokens": 80},
            {"ts": now, "mode": "prefetch", "rows": 0, "latency_ms": 20, "tokens": 0},
            {"ts": now, "mode": "prefetch", "rows": 0, "latency_ms": 25, "tokens": 0},
        ]
        with self.log_path.open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        empty_home = self.tmp / "empty-home"
        empty_home.mkdir()
        env = {
            "RIGOPS_RETRIEVAL_LOG": str(self.log_path),
            "HOME": str(empty_home),
            "RIGOPS_CONFIG": str(empty_home / "no-config.json"),
        }
        with mock.patch.dict(os.environ, env):
            out = log.summary(self.registry, since_days=7)
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["retrieval_latency_ms_p50"], 200)
        self.assertEqual(out["retrieved_tokens_p50"], 60)
        self.assertEqual(out["prefetches"], 3)
        self.assertEqual(out["prefetch_silent_rate"], 0.667)


class CheckRealmsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_findings_when_no_registry_exists(self):
        cfg = {"retrieval": {"registry": str(self.tmp / "does-not-exist.json")}}
        self.assertEqual(check_realms.run(cfg), [])

    def test_findings_when_registry_names_an_undefined_realm(self):
        registry_path = self.tmp / "roots.json"
        existing = self.home / "personal"
        existing.mkdir()
        registry_path.write_text(json.dumps({
            "realms": ["client", "personal", "mixed"], "scopes": ["vault"],
            "index": {"db": str(self.tmp / "index.sqlite")},
            "roots": [{"path": str(existing), "realm": "not-a-real-realm", "scope": "vault"}],
        }))
        cfg = {"retrieval": {"registry": str(registry_path)}}
        env = {"HOME": str(self.home), "RIGOPS_ROOTS": str(registry_path)}
        with mock.patch.dict(os.environ, env):
            findings = check_realms.run(cfg)
        self.assertTrue(any(f.key.startswith("realm:") for f in findings))

    def test_finding_when_the_index_is_missing(self):
        registry_path = self.tmp / "roots.json"
        registry_path.write_text(json.dumps({
            "realms": ["personal"], "scopes": ["vault"],
            "index": {"db": str(self.tmp / "missing-index.sqlite")},
            "roots": [],
        }))
        cfg = {"retrieval": {"registry": str(registry_path)}}
        env = {"HOME": str(self.home), "RIGOPS_ROOTS": str(registry_path)}
        with mock.patch.dict(os.environ, env):
            findings = check_realms.run(cfg)
        self.assertEqual([f.key for f in findings], ["index:missing"])


if __name__ == "__main__":
    unittest.main()
