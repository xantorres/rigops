from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core, doctor  # noqa: E402
from rigops.doctor import check_budget, check_imports, check_plans, check_pointers  # noqa: E402


class FindingIdTests(unittest.TestCase):
    def test_id_stable_for_same_check_and_key(self):
        a = core.Finding(check="c", area="docs", symptom="s1", evidence="e1", fix="f1", key="k")
        b = core.Finding(check="c", area="docs", symptom="s2", evidence="e2", fix="f2", key="k")
        self.assertEqual(a.id, b.id)

    def test_id_differs_when_key_differs(self):
        a = core.Finding(check="c", area="docs", symptom="s", evidence="e", fix="f", key="k1")
        b = core.Finding(check="c", area="docs", symptom="s", evidence="e", fix="f", key="k2")
        self.assertNotEqual(a.id, b.id)


class FindingLineTests(unittest.TestCase):
    def test_line_matches_backlog_grammar(self):
        finding = core.Finding(
            check="pointers", area="docs", symptom="thing is broken",
            evidence="proof here", fix="fix it", key="k",
        )
        rendered = finding.line(today=date(2026, 1, 2))
        expected = (
            "- [ ] 2026-01-02 docs: thing is broken. "
            f"Evidence: proof here. Fix: fix it. [doctor:{finding.id}]"
        )
        self.assertEqual(rendered, expected)


class DiscoverNamesTests(unittest.TestCase):
    def test_names_contains_expected_checks(self):
        found = set(doctor.names())
        self.assertTrue({"budget", "imports", "plans", "pointers"}.issubset(found))

    def test_discover_modules_expose_run_and_area(self):
        for name, module in doctor.discover():
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(module, "run", None)))
                self.assertIsInstance(getattr(module, "AREA", None), str)


class CheckPlansTests(unittest.TestCase):
    def _cfg(self, plans_dir):
        return {"doctor": {"plans": {"dir": str(plans_dir)}}}

    def test_well_formed_active_plan_no_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-plan.md").write_text(
                "---\nstatus: active\ndate: 2026-01-01\ntopic: something\n---\n\nbody\n"
            )
            findings = check_plans.run(self._cfg(root))
            self.assertEqual(findings, [])

    def test_missing_frontmatter_names_status_date_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "no-frontmatter.md").write_text("just prose, no frontmatter block\n")
            findings = check_plans.run(self._cfg(root))
            self.assertEqual(len(findings), 1)
            self.assertIn("status, date, topic", findings[0].symptom)

    def test_done_outside_archive_is_unarchived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "done-plan.md").write_text(
                "---\nstatus: done\ndate: 2026-01-01\ntopic: something\n---\n\nbody\n"
            )
            findings = check_plans.run(self._cfg(root))
            self.assertEqual([f.key for f in findings], ["unarchived:done-plan.md"])

    def test_done_inside_archive_has_no_unarchived_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            (archive / "done-plan.md").write_text(
                "---\nstatus: done\ndate: 2026-01-01\ntopic: something\n---\n\nbody\n"
            )
            findings = check_plans.run(self._cfg(root))
            self.assertEqual(findings, [])

    def test_active_inside_archive_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            (archive / "active-plan.md").write_text(
                "---\nstatus: active\ndate: 2026-01-01\ntopic: something\n---\n\nbody\n"
            )
            findings = check_plans.run(self._cfg(root))
            self.assertEqual([f.key for f in findings], ["active-in-archive:active-plan.md"])

    def test_uppercase_or_spaced_filename_is_flagged_kebab_case(self):
        body = "---\nstatus: active\ndate: 2026-01-01\ntopic: something\n---\n\nbody\n"
        for name in ("MyPlan.md", "my plan.md"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root / name).write_text(body)
                    findings = check_plans.run(self._cfg(root))
                    self.assertEqual([f.key for f in findings], [f"name:{name}"])

    def test_readme_and_backlog_are_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("junk, no frontmatter\n")
            (root / "backlog.md").write_text("junk, no frontmatter\n")
            findings = check_plans.run(self._cfg(root))
            self.assertEqual(findings, [])


class CheckImportsTests(unittest.TestCase):
    def _pkg(self, root, name):
        pkg = root / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        return pkg

    def test_core_import_produces_no_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = self._pkg(root, "pkg_a")
            (pkg / "mod.py").write_text("from rigops import core\n")
            with mock.patch.object(check_imports, "_package_root", return_value=root):
                findings = check_imports.run({})
            self.assertEqual(findings, [])

    def test_sibling_import_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = self._pkg(root, "pkg_b")
            (pkg / "mod.py").write_text("from rigops import janitor\n")
            with mock.patch.object(check_imports, "_package_root", return_value=root):
                findings = check_imports.run({})
            keys = [f.key for f in findings]
            self.assertIn("import:rigops/pkg_b/mod.py:janitor", keys)

    def test_module_over_cap_is_flagged_and_clears_when_cap_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = self._pkg(root, "pkg_c")
            (pkg / "big.py").write_text("x = 1\n" * 410)
            with mock.patch.object(check_imports, "_package_root", return_value=root):
                findings = check_imports.run({})
                self.assertTrue(any(f.key == "size:rigops/pkg_c/big.py" for f in findings))

                raised_cfg = {"doctor": {"imports": {"max_lines": 500}}}
                findings_raised = check_imports.run(raised_cfg)
                self.assertFalse(
                    any(f.key == "size:rigops/pkg_c/big.py" for f in findings_raised)
                )


class CheckBudgetTests(unittest.TestCase):
    def _make_claude_dir(self, tmp):
        claude_dir = Path(tmp) / "claude"
        (claude_dir / "rules").mkdir(parents=True)
        return claude_dir

    def test_scoped_rule_excluded_unscoped_rule_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_claude_dir(tmp)
            scoped = claude_dir / "rules" / "scoped.md"
            scoped.write_text('---\npaths: ["src/**"]\n---\nscoped body\n')
            always = claude_dir / "rules" / "always.md"
            always.write_text("always-on body with no frontmatter\n")
            with mock.patch.object(core, "CLAUDE_DIR", claude_dir):
                members = check_budget.members({})
            self.assertNotIn(scoped, members)
            self.assertIn(always, members)

    def test_total_over_ceiling_yields_one_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_claude_dir(tmp)
            (claude_dir / "rules" / "rule.md").write_text("x" * 400)
            cfg = {"doctor": {"budget": {"always_on_tokens": 10}}}
            with mock.patch.object(core, "CLAUDE_DIR", claude_dir):
                findings = check_budget.run(cfg)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].key, "always_on")

    def test_total_under_ceiling_yields_no_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_claude_dir(tmp)
            (claude_dir / "rules" / "rule.md").write_text("x" * 40)
            cfg = {"doctor": {"budget": {"always_on_tokens": 10000}}}
            with mock.patch.object(core, "CLAUDE_DIR", claude_dir):
                findings = check_budget.run(cfg)
            self.assertEqual(findings, [])


class CheckPointersTests(unittest.TestCase):
    """Paths matched by PATH_RE must start with ``~`` or ``/Users/<name>``, and the
    default ignore prefixes include ``/var`` (which swallows macOS's own tmp dirs),
    so pointer targets live under the real home dir and ``ignore_prefixes`` is
    overridden to a value that cannot match anything.
    """

    def _cfg(self, source_path, **pointer_overrides):
        pointers_cfg = {
            "sources": [str(source_path)],
            "ignore_prefixes": ["/this-prefix-matches-nothing-doctor-test"],
        }
        pointers_cfg.update(pointer_overrides)
        return {"doctor": {"pointers": pointers_cfg}}

    def _write_source(self, tmp, text):
        source = Path(tmp) / "source.md"
        source.write_text(text)
        return source

    def test_only_missing_path_is_reported(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as home_tmp:
            existing = Path(home_tmp) / "existing-file.txt"
            existing.write_text("x")
            missing = Path(home_tmp) / "missing-file.txt"
            with tempfile.TemporaryDirectory() as tmp:
                source = self._write_source(tmp, f"see {existing} and also {missing}\n")
                findings = check_pointers.run(self._cfg(source))
        path_findings = [f for f in findings if f.key.startswith("path:")]
        self.assertEqual(len(path_findings), 1)
        self.assertIn(str(missing), path_findings[0].key)

    def test_dollar_var_and_placeholder_segment_paths_not_reported(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as home_tmp:
            missing_root = Path(home_tmp) / "does-not-exist"
            with tempfile.TemporaryDirectory() as tmp:
                text = (
                    f"see {missing_root}/$VAR/thing.md and "
                    f"{missing_root}/example/thing.md\n"
                )
                source = self._write_source(tmp, text)
                findings = check_pointers.run(self._cfg(source))
        path_findings = [f for f in findings if f.key.startswith("path:")]
        self.assertEqual(path_findings, [])

    def test_known_agent_not_reported_unknown_agent_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = (
                "the **tdd-guide** agent runs first, then the "
                "**ghost-agent-does-not-exist-xyz** agent runs\n"
            )
            source = self._write_source(tmp, text)
            cfg = self._cfg(source, known_agents=["tdd-guide"])
            findings = check_pointers.run(cfg)
        agent_findings = {f.key for f in findings if f.key.startswith("agent:")}
        self.assertTrue(any("ghost-agent-does-not-exist-xyz" in k for k in agent_findings))
        self.assertFalse(any("tdd-guide" in k for k in agent_findings))

    def test_same_missing_path_twice_on_one_line_dedupes_to_one_finding(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as home_tmp:
            missing = Path(home_tmp) / "dup-missing-file.txt"
            with tempfile.TemporaryDirectory() as tmp:
                source = self._write_source(tmp, f"first {missing} then again {missing} done\n")
                findings = check_pointers.run(self._cfg(source))
        path_findings = [f for f in findings if str(missing) in f.key]
        self.assertEqual(len(path_findings), 1)


if __name__ == "__main__":
    unittest.main()
