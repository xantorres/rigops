from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "memory_path_sweep", ROOT / "tools" / "memory-path-sweep.py")
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


def _store(tmp, notes):
    mem = Path(tmp) / "-proj" / "memory"
    (mem / "archive").mkdir(parents=True)
    for name, body in notes.items():
        (mem / name).write_text(body, encoding="utf-8")
    return tmp


class SweepTest(unittest.TestCase):
    def run_modes(self, notes, skip=()):
        with tempfile.TemporaryDirectory() as tmp:
            _store(tmp, notes)
            return {m: sweep.scan(tmp, m, skip) for m in ("none", "plain", "default")}

    def test_dead_path_counts_in_every_mode_and_archive_is_scanned(self):
        out = self.run_modes({"archive/a.md": "see ~/definitely-missing-dir/x.md\n"})
        for mode in out:
            self.assertEqual(len(out[mode][0]), 1, mode)

    def test_exemptions_only_in_default(self):
        body = "tmp /tmp/gone-dir/x and tpl ~/docs/YYYY/none/x and brace ~/nope/{a,b}.sh\n"
        out = self.run_modes({"n.md": body})
        self.assertGreater(len(out["none"][0]), len(out["default"][0]))
        classes = out["default"][1]
        self.assertEqual(classes["tmp"], 1)
        self.assertEqual(classes["template"], 1)
        self.assertEqual(len(out["default"][0]), 1)

    def test_scope_marker_and_bad_value(self):
        good = "---\nname: a\npaths-scope: remote\n---\nsee ~/gone-vm/x\n"
        bad = "---\nname: b\npaths-scope: nonsense\n---\n"
        out = self.run_modes({"a.md": good, "b.md": bad})["default"]
        self.assertEqual(out[1]["scope"], 1)
        self.assertEqual([f[2] for f in out[0]], ["paths-scope: nonsense"])

    def test_secret_paths_skipped_before_stat(self):
        body = "~/.env.local ~/work/token-store/x ~/.private-area/y\n"
        out = self.run_modes({"n.md": body}, skip=["~/.private-area"])["default"]
        self.assertEqual(out[0], [])
        self.assertEqual(out[1]["secret-skip"], 3)

    def test_cut_name_needs_sibling_with_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "data"
            (real / "Big Folder").mkdir(parents=True)
            _store(tmp, {"n.md": f"see {real}/Big Folder/ and {real}/Bigger thing\n"})
            found, classes = sweep.scan(tmp, "default")
        self.assertEqual(classes["cut"], 1)
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
