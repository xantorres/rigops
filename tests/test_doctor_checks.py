from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import core, doctor  # noqa: E402
from rigops.doctor import (  # noqa: E402
    check_budget,
    check_imports,
    check_levers,
    check_plans,
    check_pointers,
    check_render,
    check_rtk,
    pointer_grammar,
)


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
            (Path(home_tmp) / "existing-file.txt").write_text("x")
            base = f"~/{Path(home_tmp).name}"
            with tempfile.TemporaryDirectory() as tmp:
                text = f"see {base}/existing-file.txt and also {base}/missing-file.txt\n"
                source = self._write_source(tmp, text)
                findings = check_pointers.run(self._cfg(source))
        path_findings = [f for f in findings if f.key.startswith("path:")]
        self.assertEqual(len(path_findings), 1)
        self.assertIn(f"{base}/missing-file.txt", path_findings[0].key)

    @unittest.skipIf(core.HOME.parent == Path("/"), "a home directly under / has no absolute form")
    def test_absolute_home_path_is_a_pointer(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as home_tmp:
            missing = Path(home_tmp) / "missing-file.txt"
            with tempfile.TemporaryDirectory() as tmp:
                source = self._write_source(tmp, f"see {missing}\n")
                findings = check_pointers.run(self._cfg(source))
        self.assertEqual(
            [f.key.split(":")[1] for f in findings if f.key.startswith("path:")], [str(missing)]
        )

    def test_home_directly_under_root_drops_the_absolute_form(self):
        with mock.patch.object(core, "HOME", Path("/root")):
            self.assertEqual(pointer_grammar._home_root(), "")
        with mock.patch.object(core, "HOME", Path("/home/someone")):
            self.assertEqual(pointer_grammar._home_root(), "|/home/[A-Za-z0-9._-]+")

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
            missing = f"~/{Path(home_tmp).name}/dup-missing-file.txt"
            with tempfile.TemporaryDirectory() as tmp:
                source = self._write_source(tmp, f"first {missing} then again {missing} done\n")
                findings = check_pointers.run(self._cfg(source))
        path_findings = [f for f in findings if missing in f.key]
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
            stem = f"~/{Path(home_tmp).name}/report"
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


class CheckRtkTests(unittest.TestCase):
    PINNED = {"doctor": {"rtk": {"version": "0.42.4"}}}

    def test_unpinned_host_is_not_checked(self):
        with mock.patch.object(core, "run") as run:
            findings = check_rtk.run({})
        self.assertEqual(findings, [])
        run.assert_not_called()

    def test_missing_binary_yields_missing_finding(self):
        with mock.patch.object(core, "run", return_value=""):
            findings = check_rtk.run(self.PINNED)
        self.assertEqual([f.key for f in findings], ["missing"])

    def test_matching_version_yields_no_finding(self):
        with mock.patch.object(core, "run", return_value="rtk 0.42.4\n"):
            findings = check_rtk.run(self.PINNED)
        self.assertEqual(findings, [])

    def test_mismatched_version_yields_version_finding(self):
        with mock.patch.object(core, "run", return_value="rtk 0.41.0\n"):
            findings = check_rtk.run(self.PINNED)
        self.assertEqual([f.key for f in findings], ["version"])

    def test_configured_expected_version_is_honoured(self):
        cfg = {"doctor": {"rtk": {"version": "0.41.0"}}}
        with mock.patch.object(core, "run", return_value="rtk 0.41.0\n"):
            findings = check_rtk.run(cfg)
        self.assertEqual(findings, [])


class CheckRenderTests(unittest.TestCase):
    def test_missing_source_dir_yields_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"render": {"source": str(Path(tmp) / "does-not-exist")}}
            self.assertEqual(check_render.run(cfg), [])


class OverlayTests(unittest.TestCase):
    """Content handed to the checks instead of what is on disk."""

    def tearDown(self):
        core.set_overlay({})

    def test_read_text_prefers_the_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.md"
            path.write_text("on disk\n")
            core.set_overlay({path: b"staged\n"})
            self.assertEqual(core.read_text(path), "staged\n")
            self.assertEqual(core.size(path), len("staged\n"))

    def test_read_text_falls_back_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.md"
            path.write_text("on disk\n")
            core.set_overlay({Path(tmp) / "other.md": b"staged\n"})
            self.assertEqual(core.read_text(path), "on disk\n")


class StagedIndexTests(unittest.TestCase):
    def _repo(self, tmp):
        root = Path(tmp) / "repo"
        root.mkdir()
        core.git(root, "init", "-q", "-b", "main")
        core.git(root, "config", "user.email", "t@example.com")
        core.git(root, "config", "user.name", "t")
        return root

    def test_only_staged_content_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "staged.md").write_text("indexed\n")
            (root / "dirty.md").write_text("never staged\n")
            core.git(root, "add", "staged.md")
            (root / "staged.md").write_text("working tree\n")
            found_root, files = core.staged_index(root)
        self.assertEqual(found_root, core.expand(root))
        self.assertEqual(files, {str(core.expand(root / "staged.md")): b"indexed\n"})

    def test_outside_a_repository_there_is_nothing_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            found_root, files = core.staged_index(Path(tmp))
        self.assertIsNone(found_root)
        self.assertEqual(files, {})


class CheckPlansRootTests(unittest.TestCase):
    """A configured root that is gone is a defect; a default one that is gone is not."""

    def test_configured_plans_dir_that_is_gone_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "absent"
            findings = check_plans.run({"doctor": {"plans": {"dir": str(absent)}}})
        self.assertEqual([f.key for f in findings], [f"dir:{core.expand(absent)}"])

    def test_default_plans_dir_that_is_gone_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = str(Path(tmp) / "absent")
            with mock.patch.object(check_plans, "DEFAULT_DIR", absent):
                findings = check_plans.run({})
        self.assertEqual(findings, [])


class CheckPointersRootTests(unittest.TestCase):
    def test_configured_skill_root_that_is_gone_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "absent-skills"
            cfg = {"doctor": {"pointers": {"sources": [], "skill_roots": [str(absent)]}}}
            findings = check_pointers.run(cfg)
        self.assertEqual(
            [f.key for f in findings],
            [f"root:{core.expand(absent)}:doctor.pointers.skill_roots"],
        )

    def test_default_roots_that_are_gone_are_silent(self):
        findings = check_pointers.run({"doctor": {"pointers": {"sources": []}}})
        self.assertEqual([f for f in findings if f.key.startswith("root:")], [])


class CheckPointersMemoryTests(unittest.TestCase):
    """The fact store is checked; only the session transcripts under it are ignored."""

    SESSION = "~/.claude/projects/-doctor-test/0f0e0d0c-0b0a-4009-8008-070605040302"
    NOTE = "~/.claude/projects/-doctor-test/memory/absent-note.md"

    def test_transcripts_are_ignored_and_a_memory_pointer_is_checked(self):
        text = (
            f"transcript {self.SESSION}.jsonl and {self.SESSION}/subagents/a.jsonl "
            f"but the note {self.NOTE} is a pointer\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text(text)
            findings = check_pointers.run({"doctor": {"pointers": {"sources": [str(source)]}}})
            label = core.expand(source)
        self.assertEqual(
            [f.key for f in findings if f.key.startswith("path:")],
            [f"path:{self.NOTE}:{label}"],
        )


class CheckPointersMemoryNoteTests(unittest.TestCase):
    """A memory note is held to the path rule alone, under this rig's own roots."""

    NAMES = (
        "dispatch the `doctor-test-ghost` agent, run skill `doctor-test-ghost`, "
        "install `ghost-tool@ghost-market`, query the `doctor-test-ghost` MCP, "
        "load local.doctor-test-ghost\n"
    )
    LABELS = "PID\tStatus\tLabel\n-\t0\tlocal.doctor-test-present\n"

    def _run(self, text, memory=True, **pointer_overrides):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / ".claude" / "projects" / "-doctor-test" / "memory"
            parent = parent if memory else Path(tmp)
            parent.mkdir(parents=True, exist_ok=True)
            source = parent / "note.md"
            source.write_text(text)
            pointers = {"sources": [str(source)], "ignore_prefixes": []}
            pointers.update(pointer_overrides)
            with mock.patch.object(core, "run", return_value=self.LABELS):
                return check_pointers.run({"doctor": {"pointers": pointers}})

    def test_only_paths_under_a_memory_root_are_judged(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as home_tmp:
            # A home directly under / has no absolute-home branch in the grammar.
            root = f"~/{Path(home_tmp).name}/rig-root"
            core.expand(root).mkdir()
            (core.expand(root) / "present.md").write_text("x")
            text = (
                f"gone {root}/absent.md, kept {root}/present.md, another host's "
                f"~/{Path(home_tmp).name}/elsewhere/absent.conf and /etc/doctor-test-absent.conf\n"
            )
            findings = self._run(text, memory_roots=[root])
        self.assertEqual(
            [f.key.split(":")[1] for f in findings if f.key.startswith("path:")],
            [f"{root}/absent.md"],
        )

    def test_default_roots_are_the_rig_trees(self):
        self.assertIn("~/.claude/projects/*/memory/*.md", check_pointers.DEFAULT_SOURCES)
        findings = self._run(
            "retired ~/.claude/doctor-test-absent/gone.sh, a host's ~/doctor-test-absent/gone.sh\n"
        )
        self.assertEqual(
            [f.key.split(":")[1] for f in findings if f.key.startswith("path:")],
            ["~/.claude/doctor-test-absent/gone.sh"],
        )

    def test_names_in_a_memory_note_are_history(self):
        self.assertEqual(self._run(self.NAMES), [])
        kinds = {f.key.split(":")[0] for f in self._run(self.NAMES, memory=False)}
        self.assertEqual(kinds, {"agent", "skill", "plugin", "mcp", "launchd"})


class CheckPointersPluginIdTests(unittest.TestCase):
    def _plugin_findings(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text(text)
            cfg = {"doctor": {"pointers": {"sources": [str(source)], "ignore_prefixes": []}}}
            findings = check_pointers.run(cfg)
        return [f for f in findings if f.key.startswith("plugin:")]

    def test_unknown_plugin_id_is_reported(self):
        findings = self._plugin_findings("install `ghost-tool@ghost-market` before the run\n")
        self.assertEqual([f.key.split(":")[1] for f in findings], ["ghost-tool@ghost-market"])

    def test_version_specifiers_and_ssh_hosts_are_not_plugin_ids(self):
        text = (
            "pin node@20, pnpm@9.1.0 and vite@latest, clone "
            "git@github-host:acme/repo.git, write to dev@example.com\n"
        )
        self.assertEqual(self._plugin_findings(text), [])


class CheckImportsPolicyTests(unittest.TestCase):
    """The line cap is a repository rule; the sibling rule stays a package rule."""

    def test_flat_module_over_the_cap_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flat.py").write_text("x = 1\n" * 410)
            with mock.patch.object(check_imports, "_package_root", return_value=root):
                findings = check_imports.run({})
        self.assertEqual([f.key for f in findings], ["size:rigops/flat.py"])

    def test_flat_module_may_import_a_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flat.py").write_text("from rigops import janitor\n")
            with mock.patch.object(check_imports, "_package_root", return_value=root):
                findings = check_imports.run({})
        self.assertEqual(findings, [])

    def test_module_with_a_recorded_allowance_is_flagged_only_when_it_grows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / "legacy.py"
            with mock.patch.object(check_imports, "_package_root", return_value=root), \
                 mock.patch.object(check_imports, "LINE_ALLOWANCES", {"rigops/legacy.py": 500}):
                module.write_text("x = 1\n" * 500)
                self.assertEqual(check_imports.run({}), [])
                module.write_text("x = 1\n" * 501)
                findings = check_imports.run({})
        self.assertEqual([f.key for f in findings], ["size:rigops/legacy.py"])


class CheckBudgetProjectFileTests(unittest.TestCase):
    """The per-project instruction file a session also pays counts toward the ceiling."""

    def _rig(self, tmp, repos):
        source = Path(tmp) / "rig"
        (source / "registry").mkdir(parents=True)
        (source / "registry" / "roots.json").write_text(
            json.dumps({"pointer_repos": [str(r) for r in repos]})
        )
        return source

    def _repo(self, tmp, name, size):
        repo = Path(tmp) / name
        repo.mkdir()
        (repo / "CLAUDE.md").write_text("x" * size)
        return repo

    def test_largest_registered_repo_instruction_file_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / "claude"
            (claude_dir / "rules").mkdir(parents=True)
            small = self._repo(tmp, "repo-small", 40)
            big = self._repo(tmp, "repo-big", 400)
            cfg = {
                "render": {"source": str(self._rig(tmp, [small, big]))},
                "doctor": {"budget": {"always_on_tokens": 10}},
            }
            with mock.patch.object(core, "CLAUDE_DIR", claude_dir):
                members = check_budget.members(cfg)
                findings = check_budget.run(cfg)
        self.assertIn(core.expand(big / "CLAUDE.md"), members)
        self.assertNotIn(core.expand(small / "CLAUDE.md"), members)
        self.assertEqual([f.key for f in findings], ["always_on"])
        self.assertIn(str(core.expand(big / "CLAUDE.md")), findings[0].extra["paths"])

    def test_without_a_root_registry_only_this_tree_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / "claude"
            (claude_dir / "rules").mkdir(parents=True)
            (claude_dir / "rules" / "rule.md").write_text("x" * 40)
            cfg = {"render": {"source": str(Path(tmp) / "absent-rig")}}
            with mock.patch.object(core, "CLAUDE_DIR", claude_dir):
                members = check_budget.members(cfg)
        self.assertEqual(members, [claude_dir / "rules" / "rule.md"])


class CheckLeversTests(unittest.TestCase):
    LEDGER = Path("/state/ledger.jsonl")
    RULES = {
        "denials_headless": {"max": 0},
        "out_per_turn": {"rise_pct": 15},
        "cache_hit_pct": {"drop_pct": 3},
    }

    def _rows(self, **series):
        """One weekly row per position, the last one dated 2026-09-14."""
        count = len(next(iter(series.values())))
        rows = []
        for i in range(count):
            day = date(2026, 9, 14) - timedelta(days=7 * (count - 1 - i))
            rows.append({"date": day.isoformat(), **{k: v[i] for k, v in series.items()}})
        return rows

    def _judge(self, rows, notes=(), rules=None, today=date(2026, 9, 16)):
        return check_levers.judge(rows, list(notes), rules or self.RULES, today, self.LEDGER)

    def test_headless_denial_above_zero_fires(self):
        rows = self._rows(denials_headless=[0, 0, 10], out_per_turn=[1000] * 3,
                          cache_hit_pct=[97.0] * 3)
        findings = self._judge(rows)
        self.assertEqual([f.key for f in findings], ["denials_headless:max"])
        self.assertIn("10", findings[0].symptom)

    def test_output_rise_past_the_band_fires(self):
        rows = self._rows(denials_headless=[0] * 5, out_per_turn=[1056, 958, 1015, 1109, 1315],
                          cache_hit_pct=[97.0] * 5)
        findings = self._judge(rows)
        self.assertEqual([f.key for f in findings], ["out_per_turn:rise_pct"])
        self.assertIn("27%", findings[0].symptom)

    def test_weekly_fluctuation_inside_the_bands_is_quiet(self):
        rows = self._rows(
            denials_headless=[0] * 8,
            out_per_turn=[1047, 1131, 1117, 1078, 1056, 958, 1015, 1109],
            cache_hit_pct=[96.2, 97.1, 95.8, 97.1, 97.0, 96.7, 97.1, 96.2],
        )
        self.assertEqual(self._judge(rows), [])

    def test_accepted_level_is_quiet_and_becomes_the_baseline(self):
        rows = self._rows(denials_headless=[0] * 6, cache_hit_pct=[97.0] * 6,
                          out_per_turn=[1000, 1000, 1000, 1400, 1400, 1420])
        accepted = {"date": rows[3]["date"], "text": "longer reviews", "accept": ["out_per_turn"]}
        self.assertEqual([f.key for f in self._judge(rows)], ["out_per_turn:rise_pct"])
        self.assertEqual(self._judge(rows[:4], [accepted], today=date(2026, 9, 1)), [])
        self.assertEqual(self._judge(rows, [accepted]), [])

    def test_acceptance_leaves_later_weeks_judged(self):
        rows = self._rows(denials_headless=[0, 10, 4], out_per_turn=[1000] * 3,
                          cache_hit_pct=[97.0] * 3)
        accepted = {"date": rows[1]["date"], "text": "known deny", "accept": ["denials_headless"]}
        self.assertEqual(self._judge(rows[:2], [accepted], today=date(2026, 9, 8)), [])
        self.assertEqual([f.key for f in self._judge(rows, [accepted])], ["denials_headless:max"])

    def test_a_note_without_accept_silences_nothing(self):
        rows = self._rows(denials_headless=[0, 10], out_per_turn=[1000] * 2,
                          cache_hit_pct=[97.0] * 2)
        note = {"date": rows[1]["date"], "text": "denials_headless is fine"}
        self.assertEqual([f.key for f in self._judge(rows, [note])], ["denials_headless:max"])

    def test_lever_below_floor_fires(self):
        rows = self._rows(cache_hit_pct=[95.0, 95.0, 40.0])
        rules = {"cache_hit_pct": {"min": 50}}
        findings = self._judge(rows, rules=rules)
        self.assertEqual([f.key for f in findings], ["cache_hit_pct:min"])
        self.assertIn("40", findings[0].symptom)

    def test_lever_at_or_above_floor_is_quiet(self):
        rows = self._rows(cache_hit_pct=[95.0, 95.0, 50.0])
        rules = {"cache_hit_pct": {"min": 50}}
        self.assertEqual(self._judge(rows, rules=rules), [])

    def test_min_not_judged_on_or_before_accept_note_date(self):
        rows = self._rows(cache_hit_pct=[95.0, 40.0, 30.0])
        rules = {"cache_hit_pct": {"min": 50}}
        accepted = {"date": rows[1]["date"], "text": "low cache accepted",
                    "accept": ["cache_hit_pct"]}
        self.assertEqual(
            self._judge(rows[:2], [accepted], rules=rules, today=date(2026, 9, 8)), []
        )
        self.assertEqual(
            [f.key for f in self._judge(rows, [accepted], rules=rules)], ["cache_hit_pct:min"]
        )

    def test_single_row_judges_limits_but_not_bands(self):
        rows = self._rows(denials_headless=[3], out_per_turn=[9999], cache_hit_pct=[1.0])
        self.assertEqual([f.key for f in self._judge(rows)], ["denials_headless:max"])

    def test_overlapping_rows_count_as_one_week(self):
        rows = [
            {"date": "2026-09-01", "out_per_turn": 1000},
            {"date": "2026-09-06", "out_per_turn": 5000},
            {"date": "2026-09-08", "out_per_turn": 1000},
            {"date": "2026-09-15", "out_per_turn": 1100},
        ]
        rules = {"out_per_turn": {"rise_pct": 15}}
        self.assertEqual([r["date"] for r in check_levers.weekly(rows)],
                         ["2026-09-01", "2026-09-08", "2026-09-15"])
        self.assertEqual(self._judge(rows, rules=rules), [])

    def test_missing_value_on_the_latest_row_is_not_judged(self):
        rows = self._rows(denials_headless=[5, None], out_per_turn=[1000, None],
                          cache_hit_pct=[97.0, None])
        self.assertEqual(self._judge(rows), [])

    def test_stale_ledger_is_reported(self):
        rows = self._rows(denials_headless=[0], out_per_turn=[1000], cache_hit_pct=[97.0])
        findings = self._judge(rows, today=date(2026, 10, 1))
        self.assertEqual([f.key for f in findings], ["stale"])

    def test_unknown_lever_and_bad_rule_are_reported(self):
        rows = self._rows(out_per_turn=[1000])
        rules = {"out_per_trun": {"rise_pct": 15}, "out_per_turn": {"rise": 15}}
        keys = sorted(f.key for f in self._judge(rows, rules=rules))
        self.assertEqual(keys, ["out_per_trun:column", "out_per_turn:rule"])

    def test_every_finding_is_a_warning_about_the_ledger(self):
        rows = self._rows(denials_headless=[0, 0, 9], out_per_turn=[1000, 1000, 2000],
                          cache_hit_pct=[97.0, 97.0, 50.0])
        findings = self._judge(rows, today=date(2026, 12, 1))
        self.assertEqual(len(findings), 4)
        self.assertEqual({f.severity for f in findings}, {"warn"})
        self.assertEqual({tuple(f.extra["paths"]) for f in findings}, {(str(self.LEDGER),)})

    def _run(self, tmp, ledger_text=None, notes_text=None, rules=None):
        state = Path(tmp)
        if ledger_text is not None:
            (state / "ledger.jsonl").write_text(ledger_text)
        if notes_text is not None:
            (state / "interventions.jsonl").write_text(notes_text)
        cfg = {"doctor": {"levers": {"rules": self.RULES if rules is None else rules}}}
        with mock.patch.dict(os.environ, {"RIGOPS_STATE_DIR": str(state)}):
            return check_levers.run(cfg, today=date(2026, 9, 16))

    def test_no_rules_configured_reads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, rules={}), [])

    def test_missing_ledger_is_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            findings = self._run(tmp)
        self.assertEqual([(f.key, f.severity) for f in findings], [("unreadable", "warn")])
        self.assertIn("does not exist", findings[0].symptom)

    def test_malformed_ledger_is_a_warning_not_a_crash(self):
        for text in ('{"date": "2026-09-14"}\n{"date": "2026-09-1', '[1, 2]\n', '{"turns": 3}\n'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                findings = self._run(tmp, ledger_text=text)
                self.assertEqual([f.key for f in findings], ["unreadable"])

    def test_run_reads_acceptance_from_the_intervention_notes(self):
        row = json.dumps({"date": "2026-09-14", "denials_headless": 10,
                          "out_per_turn": 1000, "cache_hit_pct": 97.0})
        note = json.dumps({"date": "2026-09-16", "text": "known", "accept": ["denials_headless"]})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([f.key for f in self._run(tmp, row + "\n")], ["denials_headless:max"])
            self.assertEqual(self._run(tmp, row + "\n", note + "\n"), [])

    def test_unreadable_notes_are_reported_and_levers_still_judged(self):
        row = json.dumps({"date": "2026-09-14", "denials_headless": 10})
        with tempfile.TemporaryDirectory() as tmp:
            findings = self._run(tmp, row + "\n", "not json\n",
                                 rules={"denials_headless": {"max": 0}})
        self.assertEqual(sorted(f.key for f in findings), ["denials_headless:max", "notes"])


class RunChecksTests(unittest.TestCase):
    def test_a_check_that_raises_becomes_a_finding_and_the_others_still_run(self):
        def explode(_cfg):
            raise RuntimeError("check is broken")

        with mock.patch.object(check_budget, "run", explode):
            findings = doctor.run_checks({}, only={"budget", "plans"})
        self.assertEqual([f.key for f in findings if f.check == "budget"], ["crashed"])


if __name__ == "__main__":
    unittest.main()
