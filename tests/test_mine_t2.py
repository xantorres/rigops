from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import mine, mine_redact  # noqa: E402
from rigops.detector import BUILTIN  # noqa: E402
from rigops.retrieval import index, roots  # noqa: E402

from tests.support import es_detector  # noqa: E402
from tests.test_mine import NOW, _excluded, _registry, _user, _write  # noqa: E402

_ES = es_detector()


def _c(session: str, text: str, project: str = "-work-alpha") -> mine.Correction:
    return mine.Correction(session=session, project=project, ts=NOW, text=text)


class ChainTests(unittest.TestCase):
    def test_a_like_b_and_b_like_c_does_not_join_a_with_c(self):
        a = _c("s1", "no, kiwi lemon mango papaya guava")
        b = _c("s2", "no, mango papaya guava banana cherry")
        c = _c("s3", "no, guava banana cherry grape melon")
        groups = mine.group([a, b, c])
        for g in groups:
            members = {m.text for m in g.members}
            self.assertFalse({a.text, c.text} <= members)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sessions, 2)

    def test_a_cross_session_pair_survives_a_closer_same_session_neighbour(self):
        x = _c("s1", "no, kiwi lemon mango papaya")
        x2 = _c("s1", "no, kiwi lemon mango papaya guava cherry")
        y = _c("s2", "no, kiwi lemon banana")
        groups = mine.group([x, x2, y])
        self.assertEqual(len(groups), 1)
        self.assertEqual({m.text for m in groups[0].members}, {x.text, y.text})
        self.assertEqual(groups[0].sessions, 2)

    def test_a_pair_left_over_by_an_earlier_clique_still_groups(self):
        x = _c("s1", "no, kiwi lemon mango papaya")
        x2 = _c("s1", "no, kiwi lemon mango papaya guava cherry")
        y = _c("s2", "no, kiwi lemon banana")
        z = _c("s3", "no, guava cherry mango fig")
        groups = mine.group([x, x2, y, z])
        pairs = [{m.text for m in g.members} for g in groups]
        self.assertIn({x.text, y.text}, pairs)
        self.assertIn({x2.text, z.text}, pairs)

    def test_a_clique_still_groups_in_full(self):
        items = [_c(f"s{n}", "no, run the lint step before opening the pull request")
                 for n in range(4)]
        self.assertEqual(mine.group(items)[0].sessions, 4)


class SpanishStatementTests(unittest.TestCase):
    def test_real_rebukes_are_not_dropped(self):
        for text in ("no hace falta repetir el plan, solo di el siguiente paso",
                     "no quiero que muevas ese archivo otra vez",
                     "no es lo que pedi, vale", "no es eso, usa la otra",
                     "no lo toques", "no lo hagas asi", "no se toca el core",
                     "no hay que tocar el config"):
            with self.subTest(text=text):
                self.assertFalse(_excluded(text))

    def test_pushback_shapes_are_not_dropped(self):
        for text in ("no voy a leer nada de eso", "no lo puedes hacer tu o que",
                     "no seria mejor usar la cola", "no iré a leer todo eso"):
            with self.subTest(text=text):
                self.assertFalse(_excluded(text))

    def test_statements_still_dropped(self):
        for text in ("no se como hacerlo", "no hay nada de eso", "no tengo tiempo",
                     "no puedo abrirlo", "no hace nada", "no lo se", "no lo puedes bajar tu",
                     "no seria mejor cerrar ese pr?"):
            with self.subTest(text=text):
                self.assertTrue(_excluded(text))


class SpanishDetectorTests(unittest.TestCase):
    def test_detects_spanish_corrections_the_shared_pattern_misses(self):
        for text in ("eso no, usa la otra rama", "Así no, quiero el diff completo",
                     "mal, el archivo es otro", "ya te dije que no uses mocks",
                     "te dije que sin comentarios", "deja de resumir cada paso",
                     "nunca mas hagas push sin avisar",
                     "ojo, eso rompe el build", "sigue roto, no has arreglado nada",
                     "oye porque no has puesto el nombre del modulo en la tabla"):
            with self.subTest(text=text):
                self.assertEqual(_ES.classify(text), "extra")

    def test_bare_demonstrative_no_is_a_correction(self):
        for text in ("eso no", "Así no.", "esto no", "asi no!", "esto no...", "eso no\ncontinua",
                     "eso no, usa la otra rama", "esto no, hazlo con la cola"):
            with self.subTest(text=text):
                self.assertTrue(_ES.is_correction(text))

    def test_demonstrative_no_inside_a_question_is_not(self):
        for text in ("esto no me obliga a nada no?", "eso no?", "esto no lo hace el servidor no"):
            with self.subTest(text=text):
                self.assertFalse(_ES.is_correction(text))

    def test_ordinary_prompts_are_not_corrections(self):
        for text in ("eso es todo por hoy", "mal tiempo hoy en Madrid, revisa la reserva",
                     "ojo de pez en la foto", "hazme un resumen de los cambios",
                     "puedes mirar el PR?", "te dije hola ayer", "esto no me obliga a nada no?",
                     "esto no lo tenemos en el servidor no", "esto no gasta memoria?",
                     "si no has terminado, sigue con el parche",
                     "avisame hasta que no has visto el registro"):
            with self.subTest(text=text):
                self.assertFalse(_ES.is_correction(text))

    def test_shared_pattern_is_unchanged(self):
        self.assertTrue(BUILTIN.is_correction("no, otra cosa"))
        self.assertFalse(BUILTIN.is_correction("eso no"))

    def test_collect_adds_spanish_only_corrections_and_counts_them(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _write(root / "-p" / "a.jsonl", _user("s1", "así no, quiero el diff completo"),
               _user("s1", "no, rename the column"), _user("s1", "hazme un resumen"))
        harvest = mine.collect(None, root, detector=_ES)
        self.assertEqual(harvest.extra_added, 1)
        self.assertEqual(len(harvest.kept), 2)
        self.assertEqual(harvest.detected, 2)


class RealmInheritanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.repo = tmp / "clientwork" / "alpha"
        (self.repo / "packages" / "core").mkdir(parents=True)
        self.registry = _registry(tmp, {"roots": [
            {"path": str(self.repo), "realm": "client", "scope": "repo", "repo": True}]})
        self.enc = roots.encode_project(self.repo)

    def test_sibling_sharing_the_name_prefix_does_not_inherit(self):
        self.assertIsNone(mine.realm_for_project(self.registry, self.enc + "-sandbox"))

    def test_worktree_under_a_hidden_dir_inherits(self):
        self.assertEqual(
            mine.realm_for_project(self.registry, self.enc + "--worktrees-t2"), "client")

    def test_existing_subdir_inherits(self):
        self.assertEqual(
            mine.realm_for_project(self.registry, self.enc + "-packages-core"), "client")
        self.assertEqual(mine.realm_for_project(self.registry, self.enc), "client")


class RedactionRuleTests(unittest.TestCase):
    def test_broken_rules_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp) / "rules.txt"
            rules.write_text("# c\n\ni good[a-z]*\ni (broken\nx badflag\ns\n")
            self.assertEqual(mine_redact.broken_rules([rules]), 3)
            self.assertEqual(mine_redact.broken_rules([rules, Path(tmp) / "absent.txt"]), 4)

    def test_render_reports_the_count(self):
        registry = _registry(Path(tempfile.mkdtemp()), {"roots": []})
        stats = {k: 0 for k in ("files", "prompts", "detected", "extra_added",
                                "excluded", "remaining", "groups", "deduped",
                                "dedupe_skipped")}
        out = mine.render(mine.Report(stats, [], [], broken_rules=3), registry, "90d")
        self.assertIn("redaction rules skipped as broken or unreadable: 3", out)


class RedactionGapTests(unittest.TestCase):
    def _out(self, text):
        return mine.redact(text, pattern_files=[])

    def test_amounts_ids_and_addresses_are_masked(self):
        cases = {
            "use €123.456 all-in": "123",
            "pay 7777 EUR now": "7777",
            "Tax id: " + "00000000" + "T ok": "00000000",
            "page 1234567890 here": "1234567890",
            "Address: Invented Str 1, " + "Flat 2, 00000 Nowhere, Utopia": "Invented",
            "see ~/docs/07-admin/notes/README.md": "07-admin",
            "applied at example-co.io and sample-corp.cloud": "example-co",
        }
        for text, leaked in cases.items():
            with self.subTest(text=text):
                self.assertNotIn(leaked, self._out(text))

    def test_the_memory_path_shape_survives(self):
        shown = "~/.state/projects/[path]/memory/note.md:7"
        self.assertEqual(self._out(shown), shown)

    def test_ordinary_numbers_and_words_survive(self):
        text = "sprint 2615 starts in 2027, step 3 of 12, use package.json and README.md"
        self.assertEqual(self._out(text), text)


class RedactionMoreGapTests(unittest.TestCase):
    def _out(self, text):
        return mine.redact(text, pattern_files=[])

    def test_more_shapes_are_masked(self):
        cases = {
            "it was 123.000 euros gross": "123.000",
            "offer of 123k\u20ac": "123k",
            "salary around 200k": "200k",
            "200k salary expected": "200k",
            "pagan 25k al a\u00f1o": "25k",
            "iban ES" + "00" + " 0000 0000 0000 0000 0000 ok": "0000 0000",
            "iban ES" + "0000000000000000000000 ok": "ES00000000",
            "call " + "+34" + " 600 000 000 now": "600 000",
            "call 600 000 000 now": "600 000",
            "call 600 00 00 00 now": "600 00",
            "built by example-co.ai": "example-co",
            "uk arm example-co.co.uk": "example-co",
            "copy to $HOME/docs/notes": "docs",
            "copy to ${HOME}/docs/notes": "docs",
            "open C:\\Users\\someone\\Desktop\\a.txt": "someone",
            "vive en Calle " + "Inventada" + " 12, 3B": "Inventada",
            "Avenida de la " + "Prueba" + " 7, 2\u00ba A, 00000 Nowhere": "Prueba",
        }
        for text, leaked in cases.items():
            with self.subTest(text=text):
                self.assertNotIn(leaked, self._out(text))

    def test_masks_keep_their_label(self):
        self.assertIn("[amount]", self._out("salary around 200k"))
        self.assertIn("salary", self._out("salary around 200k"))
        self.assertIn("[iban]", self._out("ES" + "00 0000 0000 0000 0000 0000"))
        self.assertIn("[phone]", self._out("+34" + " 600 000 000"))
        self.assertIn("[address]", self._out("Calle " + "Inventada" + " 12, 3B"))

    def test_versions_numbers_and_refs_survive(self):
        for text in ("bump to v0.3.0 and python 3.11.4", "see PR #2615 and issue 12",
                     "look at mine.py:213 and tests/test_mine.py:51-60",
                     "diff +120 -45 across 12 files", "context of 25k tokens and 200 tests",
                     "ISO 27001 step 3B of 12", "pytest 8.2.1 on 3.12", "ratio 1.5k lines",
                     "use self.run() and config.toml", "elapsed 168 ms, 30 s"):
            with self.subTest(text=text):
                self.assertEqual(self._out(text), text)


class RedactionFinalGapTests(unittest.TestCase):
    def _out(self, text):
        return mine.redact(text, pattern_files=[])

    def test_more_money_phone_iban_address_and_domain_shapes_are_masked(self):
        cases = {
            "salary is 85000": "85000",
            "salary: 90.000": "90.000",
            "offer 120.000 gross": "120.000",
            "making 90k a year": "90k",
            "about 90k a year": "90k",
            "90 mil euros": "90",
            "call (" + "415" + ") 555 " + "0100 now": "555",
            "call " + "415" + "-555-" + "0100 now": "555",
            "iban " + "es00" + " 0000 0000 0000 0000 0000 ok": "0000 0000",
            "vive en calle " + "inventada" + " 12": "inventada",
            "vive en c/ " + "Inventada" + " 12, 3B": "Inventada",
            "ship to " + "12 " + "Invented" + " Road, London": "Invented",
            "nie " + "X" + "0000000" + "T ok": "X0000000",
            "see example-co.xyz and sample.de and a.fr, b.tech, c.me": "example-co",
            "see sample.de and a.fr": "sample",
            "b.tech and c.me": "tech",
            "visit example-co.com/in/jdoe": "jdoe",
            "visit example-co.com/jdoe/repo": "jdoe",
            "write someone at example-co dot com": "example-co",
            "key abcd-1234 here": "abcd",
        }
        for text, leaked in cases.items():
            with self.subTest(text=text):
                self.assertNotIn(leaked, self._out(text))

    def test_code_and_measures_near_pay_words_survive(self):
        for text in ("pay attention to 200k tokens", "bonus: 5m timeout", "gross 30m rows",
                     "pago 3 veces en 20k filas", "see L10-20 and #L51-60", "use utf-8 and gpt-4",
                     "step-3 then pr-45", "edit Dockerfile.dev first", "call this.app.use(x)",
                     "v0.3.0 -> v0.4.0", "merged PR #45", "tests/test_mine.py:51-60",
                     "at mine.py:213", "on 2026-11-04 at 10:30", "ports 8080-8090",
                     "pages 12-14", "host 192.168.0.1", "about 3.5k tokens"):
            with self.subTest(text=text):
                self.assertEqual(self._out(text), text)

    def test_accepted_over_masks_stay_masked(self):
        for text in ("schema.ai", "foo.co", "docker.co.run"):
            with self.subTest(text=text):
                self.assertIn("[domain]", self._out(text))


class RealmOnlyDedupeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        parent = self.tmp / "repos"
        self.memory_root = self.tmp / "memory-root"
        for name, body in (("alpha", "Deploys use the blue pipeline only."),
                           ("beta", "## Lint before pull request\nAlways run the lint step "
                            "before opening a pull request because the pipeline rejects "
                            "unlinted branches.\n")):
            (parent / name).mkdir(parents=True)
            memory = self.memory_root / roots.encode_project(parent / name) / "memory"
            memory.mkdir(parents=True)
            (memory / f"{name}.md").write_text(body)
        self.registry = _registry(self.tmp, {
            "realms": ["client", "personal", "mixed"],
            "scopes": ["memory", "repo"],
            "memory_root": {"path": str(self.memory_root)},
            "index": {"db": str(self.tmp / "idx.sqlite")},
            "roots": [{"path": f"{parent}/*", "realm": "personal", "scope": "repo",
                       "repo": True, "index": False, "cwd_scopes": ["memory", "repo"]}],
        })
        index.build(self.registry)

    def test_memory_of_any_repo_of_the_realm_is_searched(self):
        hit = mine.nearest_memory(
            self.registry, "always run the lint step before opening a pull request",
            ["personal"])
        self.assertIsNotNone(hit)
        self.assertTrue(hit.path.endswith("beta.md"))


class PrefetchGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        repo = self.tmp / "repos" / "alpha"
        repo.mkdir(parents=True)
        self.repo = repo
        self.memory_root = self.tmp / "memory-root"
        memory = self.memory_root / roots.encode_project(repo) / "memory"
        memory.mkdir(parents=True)
        (memory / "lint.md").write_text(
            "## Lint before pull request\nAlways run the lint step before opening a pull "
            "request because the pipeline rejects unlinted branches.\n")
        self.registry = _registry(self.tmp, {
            "realms": ["client", "personal", "mixed"],
            "scopes": ["memory", "repo"],
            "memory_root": {"path": str(self.memory_root)},
            "index": {"db": str(self.tmp / "idx.sqlite")},
            "roots": [{"path": str(repo), "realm": "personal", "scope": "repo", "repo": False,
                       "index": False, "cwd_scopes": ["memory", "repo"]}],
        })
        index.build(self.registry)

    def _run(self, text_a, text_b):
        projects = self.tmp / "tr" / roots.encode_project(self.repo)
        _write(projects / "a.jsonl", _user("s1", text_a))
        _write(projects / "b.jsonl", _user("s2", text_b))
        return mine.run(NOW - dt.timedelta(days=90), self.tmp / "tr", self.registry,
                        max_rank=0.0)

    def test_a_single_shared_word_does_not_drop_a_group(self):
        report = self._run("no, rename the database column created_at lint everywhere",
                           "no, rename the database column created_at lint please")
        self.assertEqual(report.stats["groups"], 1)
        self.assertEqual(report.stats["deduped"], 0)
        self.assertEqual(len(report.kept), 1)

    def test_a_single_correction_is_annotated_never_dropped(self):
        projects = self.tmp / "tr" / roots.encode_project(self.repo)
        _write(projects / "a.jsonl", _user(
            "s1", "no, always run the lint step before opening a pull request"))
        report = mine.run(NOW - dt.timedelta(days=90), self.tmp / "tr", self.registry,
                          max_rank=0.0, min_sessions=1)
        self.assertEqual(report.stats["deduped"], 0)
        self.assertEqual(len(report.kept), 1)
        self.assertEqual(report.kept[0].group.sessions, 1)
        self.assertIsNotNone(report.kept[0].hit)
        out = mine.render(report, self.registry, "90d")
        self.assertIn("nearest memory:", out)
        self.assertIn("score", out)

    def test_the_dropped_section_says_only_groups_are_dropped(self):
        out = mine.render(mine.Report({k: 0 for k in (
            "files", "prompts", "detected", "extra_added", "excluded", "remaining",
            "groups", "deduped", "dedupe_skipped")}, [], []), self.registry, "90d")
        self.assertIn("groups only", out)

    def test_a_real_match_still_drops(self):
        report = self._run("no, always run the lint step before opening a pull request",
                           "no, run the lint step before you open a pull request")
        self.assertEqual(report.stats["deduped"], 1)


if __name__ == "__main__":
    unittest.main()
