from __future__ import annotations

import json
import sys
import tempfile
import time
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

    def test_quoted_and_commented_frontmatter_values_read_as_their_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "quoted.md").write_text(
                '---\nstatus: "done"\ndate: "2026-01-01"  # closed\ntopic: something\n---\n'
            )
            findings = check_plans.run(self._cfg(root))
        self.assertEqual([f.key for f in findings], ["unarchived:quoted.md"])

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

    def test_sibling_reached_by_a_dotted_or_relative_import_is_flagged(self):
        forms = {
            "dotted": "from rigops.judge import judge_job\n",
            "plain": "import rigops.janitor\n",
            "climbing": "from ..judge import judge_job\n",
            "parenthesised": "from rigops import (\n    reap,\n)\n",
        }
        for name, source in forms.items():
            with self.subTest(form=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    pkg = self._pkg(root, "pkg_d")
                    (pkg / "mod.py").write_text(source)
                    with mock.patch.object(check_imports, "_package_root", return_value=root):
                        findings = check_imports.run({})
                self.assertTrue(
                    any(f.key.startswith("import:rigops/pkg_d/mod.py:") for f in findings),
                    findings,
                )

    def test_import_inside_the_module_own_package_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = self._pkg(root, "pkg_e")
            (pkg / "mod.py").write_text(
                "from . import check_budget\nfrom rigops.pkg_e import other\n"
            )
            with mock.patch.object(check_imports, "_package_root", return_value=root):
                findings = check_imports.run({})
        self.assertEqual([f for f in findings if f.key.startswith("import:")], [])

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
    """Pointer targets live under the real home directory and ``ignore_prefixes``
    is overridden to a value nothing can match, so a temporary directory under the
    system temp tree is never mistaken for an ignored path.
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

    def test_missing_path_outside_the_home_directory_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._write_source(tmp, "run /opt/homebrew/bin/doctor-test-absent now\n")
            findings = check_pointers.run(self._cfg(source))
            expected = f"path:/opt/homebrew/bin/doctor-test-absent:{core.expand(source)}"
        self.assertEqual([f.key for f in findings], [expected])

    def test_relative_path_is_not_read_as_an_absolute_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._write_source(tmp, "the hooks live in .claude/bin/absent.sh here\n")
            findings = check_pointers.run(self._cfg(source))
        self.assertEqual([f for f in findings if f.key.startswith("path:")], [])

    def test_empty_ignore_prefix_list_is_honoured(self):
        default_first = check_pointers.DEFAULT_IGNORE_PREFIXES[0]
        cfg = {"doctor": {"pointers": {"ignore_prefixes": []}}}
        self.assertEqual(
            check_pointers._cfg(cfg, "ignore_prefixes", check_pointers.DEFAULT_IGNORE_PREFIXES),
            [],
        )
        self.assertEqual(
            check_pointers._cfg({}, "ignore_prefixes", check_pointers.DEFAULT_IGNORE_PREFIXES)[0],
            default_first,
        )

    def test_source_glob_keeps_the_literal_prefix_before_the_star(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep-me.md").write_text("x\n")
            (root / "skip-me.md").write_text("x\n")
            cfg = {"doctor": {"pointers": {"sources": [str(root / "keep*.md")]}}}
            paths, missing = check_pointers._sources(cfg)
        self.assertEqual([p.name for p in paths], ["keep-me.md"])
        self.assertEqual(missing, [])

    def test_configured_source_that_is_gone_is_reported_but_a_default_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"doctor": {"pointers": {"sources": [str(Path(tmp) / "absent.md")]}}}
            _, missing = check_pointers._sources(cfg)
        self.assertEqual([f.key.split(":")[0] for f in missing], ["source"])
        default_absent = [
            s for s in check_pointers.DEFAULT_SOURCES if not core.expand(s).exists()
        ]
        if default_absent:
            _, default_missing = check_pointers._sources({})
            self.assertEqual(default_missing, [])

    def test_space_escape_applies_only_where_the_match_was_cut_at_a_space(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as home_tmp:
            (Path(home_tmp) / "report extra.md").write_text("x\n")
            stem = Path(home_tmp) / "report"
            with tempfile.TemporaryDirectory() as tmp:
                cut = self._write_source(tmp, f"open {stem} extra.md now\n")
                self.assertEqual(
                    [f for f in check_pointers.run(self._cfg(cut)) if f.key.startswith("path:")],
                    [],
                )
            with tempfile.TemporaryDirectory() as tmp2:
                whole = self._write_source(tmp2, f"open {stem}, the summary\n")
                findings = check_pointers.run(self._cfg(whole))
        self.assertEqual(len([f for f in findings if f.key.startswith("path:")]), 1)

    def test_recursive_glob_pointer_stops_at_its_anchor(self):
        known = {"placeholders": [], "ignore": []}
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as home_tmp:
            started = time.monotonic()
            self.assertTrue(check_pointers._path_ok(f"{home_tmp}/**/absent.md", known))
            self.assertFalse(check_pointers._path_ok(f"{home_tmp}/gone/**/absent.md", known))
        self.assertLess(time.monotonic() - started, 5)

    def test_two_segment_launchd_label_is_checked_and_a_filename_is_not(self):
        self.assertEqual(
            check_pointers.LAUNCHD_RE.findall("local.rigops-doctor"), ["local.rigops-doctor"]
        )
        self.assertEqual(check_pointers.LAUNCHD_RE.findall("settings.local.json"), [])

    def test_launchd_rule_stands_down_when_no_labels_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._write_source(tmp, "the com.example-rig.absent job runs nightly\n")
            with mock.patch.object(core, "run", return_value=""):
                findings = check_pointers.run(self._cfg(source))
        self.assertEqual([f for f in findings if f.key.startswith("launchd:")], [])

    def test_namespaced_pointer_of_an_uninstalled_namespace_is_left_alone(self):
        known = {"namespaces": {"caveman"}, "skills": {"cavecrew"}}
        self.assertFalse(check_pointers._unregistered("runtime-only:absent", known, "skills"))
        self.assertTrue(check_pointers._unregistered("caveman:absent", known, "skills"))
        self.assertFalse(check_pointers._unregistered("caveman:cavecrew", known, "skills"))

    def test_finding_id_survives_the_line_moving(self):
        first = check_pointers._finding("path", "~/gone.md", "~/a.md:3", "fix")
        moved = check_pointers._finding("path", "~/gone.md", "~/a.md:41", "fix")
        elsewhere = check_pointers._finding("path", "~/gone.md", "~/b.md:3", "fix")
        self.assertEqual(first.id, moved.id)
        self.assertNotEqual(first.id, elsewhere.id)
        self.assertEqual(moved.evidence, "~/a.md:41")

    def test_settings_slice_carries_grants_and_overrides_without_a_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(json.dumps({
                "permissions": {
                    "additionalDirectories": ["/opt/homebrew/absent-grant"],
                    "deny": ["Read(/opt/homebrew/absent-deny)"],
                },
                "skillOverrides": {"absent-skill-xyz": "name-only"},
            }))
            findings = check_pointers.run(self._cfg(settings))
            label = str(core.expand(settings))
        keys = {f.key for f in findings}
        self.assertIn(f"path:/opt/homebrew/absent-grant:{label}", keys)
        self.assertIn(f"skill:absent-skill-xyz:{label}", keys)
        self.assertFalse(any("absent-deny" in k for k in keys))
        self.assertTrue(all(f.evidence == label for f in findings))


class RunChecksTests(unittest.TestCase):
    def test_a_check_that_raises_becomes_a_finding_and_the_others_still_run(self):
        def explode(_cfg):
            raise RuntimeError("check is broken")

        with mock.patch.object(check_budget, "run", explode):
            findings = doctor.run_checks({}, only={"budget", "plans"})
        self.assertEqual([f.key for f in findings if f.check == "budget"], ["crashed"])


if __name__ == "__main__":
    unittest.main()
