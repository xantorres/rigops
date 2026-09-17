from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core  # noqa: E402
from rigops.context import card, util  # noqa: E402
from rigops.retrieval import roots  # noqa: E402


def _short_resolved(path) -> str:
    """Oracle for an `add()`-routed component: it dedupes and labels by
    resolved path, so the expected string must resolve too (matters on macOS,
    where the system temp tree sits behind a /var -> /private/var symlink)."""
    return card._short(core.expand(path))


def _registry(tmp: Path, raw: dict) -> roots.Registry:
    path = tmp / "roots.json"
    path.write_text(json.dumps(raw))
    return roots.load(path)


def _mapped(tmp: Path, *, scopes=("repo",), scope_notes=None, realm="work",
            repo=True, cwd_name="acme-app"):
    """A registry with one cwd_scopes root, plus the cwd it maps."""
    cwd = tmp / "repos" / cwd_name
    cwd.mkdir(parents=True)
    raw = {
        "realms": [realm], "scopes": list(scopes),
        "roots": [{"path": str(cwd), "realm": realm, "scope": scopes[0], "repo": repo,
                   "cwd_scopes": list(scopes)}],
    }
    if scope_notes:
        raw["scope_notes"] = scope_notes
    return _registry(tmp, raw), cwd


# The host's own doctor record and nudge state must never leak into these tests.
_STATE_HOME = tempfile.TemporaryDirectory()
_STATE_ENV = mock.patch.dict(os.environ, {"XDG_STATE_HOME": _STATE_HOME.name})


def setUpModule():
    _STATE_ENV.start()


def tearDownModule():
    _STATE_ENV.stop()
    _STATE_HOME.cleanup()


class CardUnmappedTests(unittest.TestCase):
    def test_header_names_the_cwd_and_stops_after_it(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            elsewhere = tmp / "elsewhere"
            elsewhere.mkdir()
            registry = _registry(tmp, {"roots": []})
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp / "xdg")}):
                result = card.build(registry, {}, elsewhere)
        self.assertEqual(len(result["lines"]), 1)
        line = result["lines"][0]
        self.assertIn("is unmapped", line)
        self.assertIn("no realm, no prefetch", line)
        self.assertIn(f"rigops retrieval roots --cwd {elsewhere} explains", line)
        self.assertIsNone(result["realm"])
        self.assertEqual(result["scopes"], [])
        self.assertEqual(result["preload"], {})
        self.assertEqual(result["gates"], [])

    def test_json_tokens_match_the_rendered_text(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry = _registry(tmp, {"roots": []})
            result = card.build(registry, {}, tmp)
        self.assertEqual(result["tokens"], util.tokens("\n".join(result["lines"])))


class CardHeaderTests(unittest.TestCase):
    def test_repo_root_uses_the_repo_name(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, cwd_name="acme-app")
            result = card.build(registry, {}, cwd)
        self.assertEqual(
            result["lines"][0],
            "rig card · acme-app · realm work · prefetch repo",
        )

    def test_non_repo_root_uses_basename_of_profile_root(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, cwd_name="vault", repo=False)
            result = card.build(registry, {}, cwd)
        self.assertIn("· vault · realm work ·", result["lines"][0])

    def test_search_line_is_the_second_line_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            result = card.build(registry, {}, cwd)
        self.assertEqual(result["lines"][1], card.SEARCH_LINE)

    def test_multiple_scopes_join_with_comma_space(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, scopes=("repo", "memory", "vault"))
            result = card.build(registry, {}, cwd)
        self.assertIn("prefetch repo, memory, vault", result["lines"][0])
        self.assertEqual(result["scopes"], ["repo", "memory", "vault"])
        self.assertEqual(result["realm"], "work")


class CardScopeNotesTests(unittest.TestCase):
    def test_scope_with_a_note_gets_a_line_in_scope_order(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(
                tmp, scopes=("repo", "memory"),
                scope_notes={"repo": "repo notes", "memory": "memory notes"},
            )
            result = card.build(registry, {}, cwd)
        self.assertIn("- repo: repo notes", result["lines"])
        self.assertIn("- memory: memory notes", result["lines"])
        self.assertLess(
            result["lines"].index("- repo: repo notes"),
            result["lines"].index("- memory: memory notes"),
        )

    def test_scope_missing_from_scope_notes_gets_no_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, scopes=("repo", "memory"),
                                     scope_notes={"repo": "repo notes"})
            result = card.build(registry, {}, cwd)
        self.assertTrue(any(line.startswith("- repo:") for line in result["lines"]))
        self.assertFalse(any(line.startswith("- memory") for line in result["lines"]))

    def test_no_scope_notes_key_at_all_means_no_scope_lines(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, scopes=("repo",))
            result = card.build(registry, {}, cwd)
        self.assertFalse(any(line.startswith("- ") for line in result["lines"]))


class CardStaleLineTests(unittest.TestCase):
    def _write_gates(self, xdg: Path, gates: list, ts="2020-01-01T00:00:00Z") -> None:
        path = xdg / "rigops" / "doctor" / "gates.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "ts": ts, "gates": gates}))

    def test_missing_gates_file_means_no_stale_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp / "xdg")}):
                result = card.build(registry, {}, cwd)
        self.assertFalse(any(line.startswith("stale:") for line in result["lines"]))
        self.assertEqual(result["gates"], [])

    def test_fresh_ts_and_no_gates_means_no_stale_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_gates(xdg, [], ts=now)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        self.assertFalse(any(line.startswith("stale:") for line in result["lines"]))

    def test_old_ts_reports_hours_since_recorded(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            self._write_gates(xdg, [], ts="2020-01-01T00:00:00Z")
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {"context": {"card": {"doctor_max_age_h": 2}}}, cwd)
        stale = next(line for line in result["lines"] if line.startswith("stale:"))
        self.assertIn("doctor gates last recorded", stale)
        self.assertIn("h ago", stale)
        self.assertIn("(rigops doctor --report)", stale)

    def test_other_realm_gate_is_invisible_not_even_named(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, realm="work")
            xdg = tmp / "xdg"
            self._write_gates(xdg, [
                {"kind": "check", "name": "secret-check", "status": "fail",
                 "realm": "personal", "detail": "personal detail leak"},
            ])
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        text = "\n".join(result["lines"])
        self.assertNotIn("secret-check", text)
        self.assertNotIn("personal detail leak", text)
        self.assertEqual(result["gates"], [])

    def test_untagged_check_shows_status_never_detail(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            self._write_gates(xdg, [
                {"kind": "check", "name": "disk-free", "status": "warn",
                 "realm": None, "detail": "should never appear"},
            ])
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        stale = next(line for line in result["lines"] if line.startswith("stale:"))
        self.assertIn("disk-free warn", stale)
        self.assertNotIn("should never appear", stale)

    def test_realm_matched_check_shows_its_detail(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, realm="work")
            xdg = tmp / "xdg"
            self._write_gates(xdg, [
                {"kind": "check", "name": "authprobe", "status": "fail",
                 "realm": "work", "detail": "token expired"},
            ])
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        stale = next(line for line in result["lines"] if line.startswith("stale:"))
        self.assertIn("authprobe: token expired", stale)

    def test_job_gates_are_aggregated_hung_folds_into_failing(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            self._write_gates(xdg, [
                {"kind": "job", "name": "a", "status": "failing", "realm": None, "detail": ""},
                {"kind": "job", "name": "b", "status": "hung", "realm": None, "detail": ""},
                {"kind": "job", "name": "c", "status": "stale", "realm": None, "detail": ""},
            ])
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        stale = next(line for line in result["lines"] if line.startswith("stale:"))
        self.assertIn("jobs: 2 failing", stale)
        self.assertNotIn("stale)", stale.replace("(rigops doctor --report)", ""))

    def test_only_overdue_jobs_leave_no_stale_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._write_gates(xdg, [
                {"kind": "job", "name": "c", "status": "stale", "realm": None, "detail": ""},
            ], ts=fresh)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        self.assertFalse(any(line.startswith("stale:") for line in result["lines"]))

    def test_config_gate_renders_name_and_status_only(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            self._write_gates(xdg, [
                {"kind": "config", "name": "pointers", "status": "fail",
                 "realm": None, "detail": "3 findings"},
            ])
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        stale = next(line for line in result["lines"] if line.startswith("stale:"))
        self.assertIn("config pointers fail", stale)

    def test_unmapped_cwd_only_sees_realm_null_gates(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            elsewhere = tmp / "elsewhere"
            elsewhere.mkdir()
            registry = _registry(tmp, {"roots": []})
            xdg = tmp / "xdg"
            self._write_gates(xdg, [
                {"kind": "check", "name": "global-check", "status": "warn",
                 "realm": None, "detail": "x"},
                {"kind": "check", "name": "scoped-check", "status": "fail",
                 "realm": "work", "detail": "y"},
            ])
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, elsewhere)
        text = "\n".join(result["lines"])
        self.assertIn("global-check warn", text)
        self.assertNotIn("scoped-check", text)

    def test_stale_line_truncates_to_60_tokens_with_ellipsis(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            gates = [
                {"kind": "check", "name": f"check-{i}", "status": "warn", "realm": None,
                 "detail": "x"}
                for i in range(40)
            ]
            self._write_gates(xdg, gates)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        stale = next(line for line in result["lines"] if line.startswith("stale:"))
        self.assertLessEqual(len(stale), 60 * 4)
        self.assertTrue(stale.endswith("…"))

    def test_stale_line_truncation_counts_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            xdg = tmp / "xdg"
            gates = [
                {"kind": "check", "name": f"€-{i}", "status": "warn", "realm": None}
                for i in range(40)
            ]
            self._write_gates(xdg, gates)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                result = card.build(registry, {}, cwd)
        stale = next(line for line in result["lines"] if line.startswith("stale:"))
        self.assertLessEqual(len(stale.encode()) // 4, 60)
        self.assertTrue(stale.endswith("…"))


class CardBudgetLineTests(unittest.TestCase):
    def _cfg(self, law_path, budget_tokens=None):
        preload = {"law": str(law_path)}
        cfg = {"context": {"preload": preload}}
        if budget_tokens is not None:
            cfg["context"]["preload"]["budget_tokens"] = budget_tokens
        return cfg

    def test_absent_budget_tokens_means_no_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            law = tmp / "law.md"
            law.write_text("x" * 4000)
            result = card.build(registry, self._cfg(law), cwd)
        self.assertFalse(any(line.startswith("budget:") for line in result["lines"]))

    def test_scalar_budget_over_limit_names_the_largest_component(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            law = tmp / "law.md"
            law.write_text("x" * 4000)
            result = card.build(registry, self._cfg(law, 500), cwd)
        budget_line = next(line for line in result["lines"] if line.startswith("budget:"))
        self.assertIn("over 500", budget_line)
        self.assertIn("largest: law 1000", budget_line)
        card_tokens = result["preload"]["components"][-1]["tokens"]
        self.assertEqual(result["preload"]["total"], 1000 + card_tokens)

    def test_scalar_budget_under_limit_means_no_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            law = tmp / "law.md"
            law.write_text("x" * 40)
            result = card.build(registry, self._cfg(law, 500), cwd)
        self.assertFalse(any(line.startswith("budget:") for line in result["lines"]))

    def test_dict_budget_uses_the_cwd_realm_entry(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, realm="work")
            law = tmp / "law.md"
            law.write_text("x" * 4000)
            result = card.build(registry, self._cfg(law, {"work": 500, "personal": 999999}), cwd)
        self.assertTrue(any(line.startswith("budget:") for line in result["lines"]))

    def test_dict_budget_missing_the_cwd_realm_means_no_line(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, realm="work")
            law = tmp / "law.md"
            law.write_text("x" * 4000)
            result = card.build(registry, self._cfg(law, {"personal": 10}), cwd)
        self.assertFalse(any(line.startswith("budget:") for line in result["lines"]))


class CardPreloadComponentsTests(unittest.TestCase):
    def test_law_default_reads_context_preload_law(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            law = tmp / "law.md"
            law.write_text("x" * 400)
            result = card.build(registry, {"context": {"preload": {"law": str(law)}}}, cwd)
        labels = {c["label"]: c for c in result["preload"]["components"]}
        self.assertEqual(labels["law"]["tokens"], 100)

    def test_scoped_rule_excluded_unscoped_rule_included(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            claude_dir = tmp / "claude"
            (claude_dir / "rules").mkdir(parents=True)
            scoped = claude_dir / "rules" / "scoped.md"
            scoped.write_text('---\npaths: ["src/**"]\n---\nscoped body\n')
            unscoped = claude_dir / "rules" / "always.md"
            unscoped.write_text("always-on body, no frontmatter\n")
            cfg = {"context": {"preload": {"law": str(tmp / "missing.md")}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", claude_dir):
                result = card.build(registry, cfg, cwd)
        paths = {c["path"] for c in result["preload"]["components"]}
        self.assertIn(_short_resolved(unscoped), paths)
        self.assertNotIn(_short_resolved(scoped), paths)

    def test_project_instruction_files_found_up_the_ancestor_chain(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            (cwd / "CLAUDE.md").write_text("cwd instructions")
            (cwd.parent / "CLAUDE.local.md").write_text("parent local instructions")
            cfg = {"context": {"preload": {"law": str(tmp / "missing.md")}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                result = card.build(registry, cfg, cwd)
        paths = {c["path"] for c in result["preload"]["components"]}
        self.assertIn(_short_resolved(cwd / "CLAUDE.md"), paths)
        self.assertIn(_short_resolved(cwd.parent / "CLAUDE.local.md"), paths)

    def test_law_file_is_not_double_counted_as_a_project_instruction_file(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            claude_md = cwd / "CLAUDE.md"
            claude_md.write_text("shared text")
            cfg = {"context": {"preload": {"law": str(claude_md)}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                result = card.build(registry, cfg, cwd)
        components = result["preload"]["components"]
        matches = [c for c in components if c["path"] == _short_resolved(claude_md)]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["label"], "law")

    def test_worktree_git_toplevel_is_its_own_root_not_the_main_checkout(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            main_repo = tmp / "main"
            (main_repo / ".git").mkdir(parents=True)
            worktree = tmp / "wt" / "feature-x"
            worktree.mkdir(parents=True)
            (worktree / ".git").write_text(f"gitdir: {main_repo}/.git/worktrees/feature-x\n")
            (worktree / ".claude" / "rules").mkdir(parents=True)
            rule = worktree / ".claude" / "rules" / "local.md"
            rule.write_text("worktree-local rule, no frontmatter\n")

            registry, _ = _mapped(tmp, cwd_name="unused")
            raw = json.loads((tmp / "roots.json").read_text())
            raw["roots"][0]["path"] = str(worktree)
            (tmp / "roots.json").write_text(json.dumps(raw))
            registry = roots.load(tmp / "roots.json")

            cfg = {"context": {"preload": {"law": str(tmp / "missing.md")}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                result = card.build(registry, cfg, worktree)
        paths = {c["path"] for c in result["preload"]["components"]}
        self.assertIn(_short_resolved(rule), paths)

    def test_memory_index_slug_redirects_worktree_to_main_checkout(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            main_repo = tmp / "main"
            (main_repo / ".git").mkdir(parents=True)
            worktree = tmp / "wt" / "feature-x"
            worktree.mkdir(parents=True)
            (worktree / ".git").write_text(f"gitdir: {main_repo}/.git/worktrees/feature-x\n")

            registry, _ = _mapped(tmp, cwd_name="unused")
            raw = json.loads((tmp / "roots.json").read_text())
            raw["roots"][0]["path"] = str(worktree)
            (tmp / "roots.json").write_text(json.dumps(raw))
            registry = roots.load(tmp / "roots.json")

            claude_dir = tmp / "claude"
            slug = card._memory_slug(main_repo)
            memory_index = claude_dir / "projects" / slug / "memory" / "MEMORY.md"
            memory_index.parent.mkdir(parents=True)
            memory_index.write_text("\n".join(f"line {i}" for i in range(250)))

            cfg = {"context": {"preload": {"law": str(tmp / "missing.md")}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", claude_dir):
                result = card.build(registry, cfg, worktree)
        memory = next(c for c in result["preload"]["components"] if c["label"] == "memory")
        self.assertEqual(memory["path"], card._short(memory_index))
        expected_head = "\n".join(f"line {i}" for i in range(200))
        self.assertEqual(memory["tokens"], len(expected_head.encode("utf-8")) // 4)

    def test_no_git_at_all_keys_memory_off_cwd_itself(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            claude_dir = tmp / "claude"
            slug = card._memory_slug(cwd)
            memory_index = claude_dir / "projects" / slug / "memory" / "MEMORY.md"
            memory_index.parent.mkdir(parents=True)
            memory_index.write_text("some memory content")
            cfg = {"context": {"preload": {"law": str(tmp / "missing.md")}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", claude_dir):
                result = card.build(registry, cfg, cwd)
        memory = next(c for c in result["preload"]["components"] if c["label"] == "memory")
        self.assertEqual(memory["path"], card._short(memory_index))

    def test_fixed_tokens_is_a_pathless_component(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            cfg = {"context": {"preload": {"law": str(tmp / "missing.md"), "fixed_tokens": 321}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                result = card.build(registry, cfg, cwd)
        fixed = next(c for c in result["preload"]["components"] if c["label"] == "fixed")
        self.assertIsNone(fixed["path"])
        self.assertEqual(fixed["tokens"], 321)

    def test_card_own_tokens_is_the_last_component(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp)
            cfg = {"context": {"preload": {"law": str(tmp / "missing.md")}}}
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                result = card.build(registry, cfg, cwd)
        self.assertEqual(result["preload"]["components"][-1]["label"], "card")
        self.assertIsNone(result["preload"]["components"][-1]["path"])


class CardCapTests(unittest.TestCase):
    def _cfg(self, tmp, max_tokens=None):
        cfg = {"context": {"preload": {"law": str(tmp / "missing.md")}}}
        if max_tokens is not None:
            cfg["context"]["card"] = {"max_tokens": max_tokens}
        return cfg

    def test_scope_lines_drop_from_the_end_before_header_or_search(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(
                tmp, scopes=("alpha", "beta"),
                scope_notes={"alpha": "short", "beta": "x" * 300},
            )
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                uncapped = card.build(registry, self._cfg(tmp), cwd)
                with_alpha = util.tokens("\n".join(uncapped["lines"][:3]))
                capped = card.build(registry, self._cfg(tmp, max_tokens=with_alpha), cwd)
        self.assertIn("- alpha: short", capped["lines"])
        self.assertFalse(any(line.startswith("- beta") for line in capped["lines"]))
        self.assertIn(capped["lines"][0], uncapped["lines"])
        self.assertIn(capped["lines"][1], uncapped["lines"])

    def test_header_and_search_survive_an_impossibly_small_cap(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(
                tmp, scopes=("alpha",), scope_notes={"alpha": "note"},
            )
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                result = card.build(registry, self._cfg(tmp, max_tokens=1), cwd)
        self.assertEqual(result["lines"][0].split(" · ")[0], "rig card")
        self.assertEqual(result["lines"][1], card.SEARCH_LINE)
        self.assertFalse(any(line.startswith("- alpha") for line in result["lines"]))

    def test_cap_counts_utf8_bytes_not_characters(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            registry, cwd = _mapped(tmp, scopes=("alpha",), scope_notes={"alpha": "€" * 40})
            with mock.patch.object(card.core, "CLAUDE_DIR", tmp / "claude"):
                bare = card.build(registry, self._cfg(tmp, max_tokens=1), cwd)
                cap = len("\n".join(bare["lines"]).encode()) // 4 + 20
                result = card.build(registry, self._cfg(tmp, max_tokens=cap), cwd)
        text = "\n".join(result["lines"])
        self.assertLessEqual(len(text.encode()) // 4, cap)
        self.assertEqual(result["tokens"], len(text.encode()) // 4)


if __name__ == "__main__":
    unittest.main()
