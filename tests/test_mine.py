from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import mine  # noqa: E402
from rigops.retrieval import index, roots  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
MINE = REPO_ROOT / "bin" / "rigops"
NOW = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)


def _stamp(days_ago: int) -> str:
    return (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _user(session: str, text, days_ago: int = 1, as_list: bool = False) -> dict:
    content = [{"type": "text", "text": text}] if as_list else text
    return {
        "type": "user",
        "timestamp": _stamp(days_ago),
        "sessionId": session,
        "message": {"role": "user", "content": content},
    }


def _write(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _registry(tmp: Path, raw: dict) -> roots.Registry:
    path = tmp / "roots.json"
    path.write_text(json.dumps(raw))
    return roots.load(path)


class SpanishFilterTests(unittest.TestCase):
    def test_statements_are_not_corrections(self):
        for text in ("no se como hacerlo", "No hay nada de eso", "no es lo que pedi, vale",
                     "no tengo tiempo", "no puedo abrirlo"):
            with self.subTest(text=text):
                self.assertTrue(mine.spanish_false_positive(text))

    def test_real_corrections_survive(self):
        for text in ("no, es otra cosa", "no pero usa la otra", "no digas eso", "nope, wrong file",
                     "no mucho mas breve"):
            with self.subTest(text=text):
                self.assertFalse(mine.spanish_false_positive(text))

    def test_only_first_nonblank_line_counts(self):
        self.assertTrue(mine.spanish_false_positive("\n\nno se que hacer\nno, otra cosa"))


class CollectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "projects"

    def tearDown(self):
        self._tmp.cleanup()

    def test_counts_detected_filtered_and_kept_inside_the_window(self):
        f = self.root / "-work-alpha" / "s1.jsonl"
        _write(
            f,
            _user("s1", "no, use the staging flag"),
            _user("s1", "no se como hacerlo"),
            _user("s1", "looks good, continue"),
            _user("s1", "<system-reminder>no, ignore</system-reminder>"),
            _user("s1", "no, too old", days_ago=200),
            _user("s1", "no, list form works", as_list=True),
        )
        _write(self.root / "-work-alpha" / "s1" / "subagents" / "a.jsonl",
               _user("s1", "no, subagent noise"))
        harvest = mine.collect(NOW - dt.timedelta(days=90), self.root)

        self.assertEqual(harvest.files, 1)
        self.assertEqual(harvest.prompts, 4)
        self.assertEqual(harvest.detected, 3)
        self.assertEqual(len(harvest.filtered), 1)
        self.assertEqual(sorted(c.text for c in harvest.kept),
                         ["no, list form works", "no, use the staging flag"])
        self.assertEqual({c.session for c in harvest.kept}, {"s1"})

    def test_text_is_first_line_capped(self):
        f = self.root / "-work-alpha" / "s1.jsonl"
        _write(f, _user("s1", "no, " + "x" * 1200 + "\nsecond line"))
        kept = mine.collect(None, self.root).kept
        self.assertEqual(len(kept[0].text), mine.KEEP_TEXT)
        self.assertNotIn("second", kept[0].text)

    def test_unreadable_lines_are_skipped(self):
        f = self.root / "-work-alpha" / "s1.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text("not json\n" + json.dumps(_user("s1", "no, real one")) + "\n")
        self.assertEqual(len(mine.collect(None, self.root).kept), 1)


class GroupTests(unittest.TestCase):
    def _c(self, session: str, text: str, project: str = "-work-alpha") -> mine.Correction:
        return mine.Correction(session=session, project=project, ts=NOW, text=text)

    def test_similar_text_across_two_sessions_forms_a_group(self):
        items = [
            self._c("s1", "no, always run the lint step before opening the pull request"),
            self._c("s2", "no, run the lint step before you open a pull request"),
            self._c("s3", "no, rename the database column to created_at"),
        ]
        groups = mine.group(items)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sessions, 2)

    def test_same_session_repeated_does_not_qualify(self):
        items = [
            self._c("s1", "no, always run the lint step before opening the pull request"),
            self._c("s1", "no, run the lint step before you open a pull request"),
        ]
        self.assertEqual(mine.group(items), [])

    def test_too_short_to_compare_is_ignored(self):
        items = [self._c("s1", "no tio"), self._c("s2", "no tio")]
        self.assertEqual(mine.group(items), [])

    def test_representative_is_the_most_central_member(self):
        items = [
            self._c("s1", "no, run the lint step before opening the pull request today"),
            self._c("s2", "no, run the lint step before you open a pull request"),
            self._c("s3", "no, run the lint step before opening a pull request"),
        ]
        rep = mine.group(items)[0].representative
        self.assertEqual(rep.session, "s3")


class RedactTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_generic_secrets_and_identifiers_are_masked(self):
        text = ("mail dev@example.com see https://example.com/x?t=1 in /Users/jane "
                "ticket ABC-1234 key sk-abcdefghijklmnopqrstuv end")
        out = mine.redact(text)
        for leaked in ("dev@example.com", "https://example.com", "jane", "ABC-1234",
                       "sk-abcdefghijklmnopqrstuv"):
            self.assertNotIn(leaked, out)
        self.assertIn("mail", out)

    def test_pattern_file_rules_apply_with_flags(self):
        rules = self.tmp / "rules.txt"
        rules.write_text("# c\n\ni acme[a-z]*\ns CaseOnly\ni (broken\n")
        out = mine.redact("Acmecorp and CaseOnly and caseonly", pattern_files=[rules])
        self.assertNotIn("Acmecorp", out)
        self.assertNotIn("CaseOnly", out)
        self.assertIn("caseonly", out)

    def test_registry_client_tokens_are_masked(self):
        registry = _registry(self.tmp, {"client_tokens": ["Globex"], "roots": []})
        self.assertNotIn("globex", mine.redact("ask the GLOBEX team", registry=registry).lower())


class RealmTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.client = self.tmp / "clientwork" / "svc"
        self.mine_dir = self.tmp / "mine" / "tool"
        self.client.mkdir(parents=True)
        self.mine_dir.mkdir(parents=True)
        self.registry = _registry(self.tmp, {"roots": [
            {"path": str(self.client), "realm": "client", "scope": "repo", "repo": True},
            {"path": str(self.mine_dir), "realm": "personal", "scope": "repo", "repo": True},
        ]})

    def tearDown(self):
        self._tmp.cleanup()

    def test_project_dir_maps_to_root_realm(self):
        self.assertEqual(mine.realm_for_project(self.registry, roots.encode_project(self.client)),
                         "client")
        self.assertEqual(mine.realm_for_project(
            self.registry, roots.encode_project(self.mine_dir) + "-sub"), "personal")

    def test_unmapped_project_is_none(self):
        self.assertIsNone(mine.realm_for_project(self.registry, "-elsewhere"))


class DedupeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        repo = self.tmp / "repos" / "alpha"
        repo.mkdir(parents=True)
        self.repo = repo
        self.memory_root = self.tmp / "claude-projects"
        memory = self.memory_root / roots.encode_project(repo) / "memory"
        memory.mkdir(parents=True)
        (memory / "lint.md").write_text(
            "## Lint before pull request\nAlways run the lint step before opening a pull request "
            "because the pipeline rejects unlinted branches.\n"
        )
        self.registry = _registry(self.tmp, {
            "realms": ["client", "personal", "mixed"],
            "scopes": ["memory", "repo"],
            "memory_root": {"path": str(self.memory_root)},
            "index": {"db": str(self.tmp / "idx.sqlite")},
            "roots": [{"path": str(repo), "realm": "personal", "scope": "repo", "repo": False,
                       "index": False, "cwd_scopes": ["memory", "repo"]}],
        })
        index.build(self.registry)

    def tearDown(self):
        self._tmp.cleanup()

    def test_known_fact_is_found_with_a_score(self):
        hit = mine.nearest_memory(
            self.registry, "always run the lint step before opening a pull request", ["personal"])
        self.assertIsNotNone(hit)
        self.assertTrue(hit.path.endswith("lint.md"))
        self.assertIsInstance(hit.rank, float)

    def test_unrelated_text_has_no_hit(self):
        self.assertIsNone(mine.nearest_memory(self.registry, "zebra pineapple harmonica",
                                              ["personal"]))

    def test_run_discards_known_and_keeps_new_groups(self):
        projects = self.tmp / "tr" / roots.encode_project(self.repo)
        _write(projects / "a.jsonl",
               _user("s1", "no, always run the lint step before opening a pull request"),
               _user("s1", "no, rename the database column created_at to inserted_at everywhere"))
        _write(projects / "b.jsonl",
               _user("s2", "no, run the lint step before you open a pull request"),
               _user("s2", "no, rename the database column created_at to inserted_at please"))
        report = mine.run(NOW - dt.timedelta(days=90), self.tmp / "tr", self.registry,
                          max_rank=0.0)

        self.assertEqual(report.stats["groups"], 2)
        self.assertEqual(report.stats["deduped"], 1)
        self.assertEqual(len(report.kept), 1)
        self.assertIn("rename", report.kept[0].group.representative.text)
        self.assertTrue(report.discarded[0].hit.path.endswith("lint.md"))
        self.assertEqual(report.kept[0].group.realm, "personal")

    def test_run_never_modifies_memory(self):
        before = {p: p.read_bytes() for p in self.memory_root.rglob("*") if p.is_file()}
        projects = self.tmp / "tr" / roots.encode_project(self.repo)
        _write(projects / "a.jsonl", _user("s1", "no, use the other flag for deploys now"))
        mine.run(None, self.tmp / "tr", self.registry, max_rank=0.0)
        after = {p: p.read_bytes() for p in self.memory_root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class HardeningTests(DedupeTests):
    def test_render_masks_path_and_snippet_highlights(self):
        registry = _registry(self.tmp, {"client_tokens": ["globex inc"], "roots": []})
        hit = mine.search.Hit(
            path="/x/-Users-jane-projects-globex/memory/f.md", line=3, section="", title="",
            realm="client", scope="memory", repo="", verified="",
            snippet="ask [Globex] [Inc] at dev@[example].com", rank=-20.0)
        grp = mine.Group(members=[], representative=mine.Correction("s", "p", NOW, "no, x"),
                         sessions=2)
        out = mine.render(mine.Report({k: 0 for k in (
            "files", "prompts", "detected", "spanish_filtered", "remaining", "groups", "deduped",
            "dedupe_skipped")}, [mine.Entry(grp, hit)], []), registry, "90d")
        for leaked in ("jane", "Globex", "example"):
            self.assertNotIn(leaked, out)

    def test_archived_memory_is_not_a_duplicate(self):
        live = next(self.memory_root.rglob("lint.md"))
        archive = live.parent / "archive"
        archive.mkdir()
        live.rename(archive / "lint.md")
        index.build(self.registry)
        self.assertIsNone(mine.nearest_memory(
            self.registry, "always run the lint step before opening a pull request", ["personal"]))

    def test_missing_index_is_reported_not_silent(self):
        projects = self.tmp / "tr" / roots.encode_project(self.repo)
        _write(projects / "a.jsonl", _user("s1", "no, always run the lint step first today"))
        _write(projects / "b.jsonl", _user("s2", "no, always run the lint step first please"))
        self.registry.raw["index"]["db"] = str(self.tmp / "absent.sqlite")
        report = mine.run(None, self.tmp / "tr", self.registry)
        self.assertEqual(report.stats["dedupe_skipped"], 1)

    def test_naive_timestamp_and_bad_session_id_do_not_crash(self):
        row = _user("s1", "no, use the other flag")
        row["timestamp"] = "2026-11-01T10:00:00"
        row["sessionId"] = ["x"]
        _write(self.tmp / "tr" / "-p" / "a.jsonl", row)
        self.assertEqual(len(mine.collect(None, self.tmp / "tr").kept), 1)


class CliGuardTests(unittest.TestCase):
    def test_missing_registry_is_an_error(self):
        proc = subprocess.run(
            [str(MINE), "mine", "--dry-run", "--registry", "/nonexistent/roots.json"],
            capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("registry", proc.stderr)

    def test_absurd_since_is_an_error(self):
        proc = subprocess.run([str(MINE), "mine", "--dry-run", "--since", "99999999999d"],
                              capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(proc.returncode, 2)


class SinceTests(unittest.TestCase):
    def test_days(self):
        self.assertEqual(mine.parse_since("90d", NOW), NOW - dt.timedelta(days=90))
        self.assertEqual(mine.parse_since("-7d", NOW), NOW - dt.timedelta(days=7))

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            mine.parse_since("soon", NOW)


class CliTests(unittest.TestCase):
    def _run(self, *args, env=None):
        return subprocess.run([str(MINE), "mine", *args], capture_output=True, text=True,
                              check=False, timeout=60, env=env)

    def test_without_dry_run_is_a_clear_error(self):
        proc = self._run("--since", "90d")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--dry-run", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_dry_run_prints_header_and_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tr = tmp / "tr"
            today = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            for sid, text in (("s1", "no, always pin the dependency version in the manifest"),
                              ("s2", "no, pin the dependency version in the manifest always")):
                row = _user(sid, text)
                row["timestamp"] = today
                _write(tr / "-proj" / f"{sid}.jsonl", row)
            reg = tmp / "roots.json"
            reg.write_text(json.dumps({"roots": []}))
            env = dict(os.environ, RIGOPS_ROOTS=str(reg), RIGOPS_CONFIG=str(tmp / "c.json"))
            proc = self._run("--dry-run", "--since", "90d", "--transcripts-dir", str(tr),
                             env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("transcripts walked: 2", proc.stdout)
        self.assertIn("groups: 1", proc.stdout)
        self.assertIn("sessions: 2", proc.stdout)
        self.assertIn("realm: unmapped", proc.stdout)


if __name__ == "__main__":
    unittest.main()
