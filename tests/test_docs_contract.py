"""The docs name what the code ships: commands, flags and links."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
DOC_PAGES = (
    [README]
    + sorted((REPO_ROOT / "docs").glob("*.md"))
    + sorted((REPO_ROOT / "plugin" / "commands").glob("*.md"))
)
FLAG_SOURCES = (
    sorted((REPO_ROOT / "libexec").glob("rigops-*"))
    + sorted((REPO_ROOT / "lib" / "rigops").rglob("*.py"))
)

# Flags no doc page needs to name, each with the reason.
UNDOCUMENTED_FLAGS: dict[str, str] = {}

ADD_ARGUMENT = re.compile(r"add_argument\(([^)]*)", re.S)
FLAG = re.compile(r"""["'](--[a-z0-9][a-z0-9-]*)["']""")
SCRIPT_HALF = re.compile(r"the `rigops` CLI \(([^)]*)\)")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
SKIP_DIRS = {".git", ".claude", "fixtures"}


def defined_flags() -> dict[str, set[str]]:
    flags: dict[str, set[str]] = {}
    for source in FLAG_SOURCES:
        for call in ADD_ARGUMENT.finditer(source.read_text(encoding="utf-8")):
            names = call.group(1).split("help=")[0]
            for flag in FLAG.findall(names):
                flags.setdefault(flag, set()).add(str(source.relative_to(REPO_ROOT)))
    return flags


def markdown_files() -> list[Path]:
    return sorted(
        p for p in REPO_ROOT.rglob("*.md")
        if not SKIP_DIRS.intersection(p.relative_to(REPO_ROOT).parts)
    )


class DocsContractTests(unittest.TestCase):
    maxDiff = None

    def test_readme_command_list_matches_libexec(self):
        match = SCRIPT_HALF.search(README.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "README lost its script-half command list")
        listed = set(re.findall(r"`([a-z]+)`", match.group(1)))
        shipped = {p.name[len("rigops-"):] for p in (REPO_ROOT / "libexec").glob("rigops-*")}

        self.assertEqual(listed, shipped)

    def test_every_flag_is_documented_or_allowlisted(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in DOC_PAGES)
        missing = {
            flag: sorted(sources) for flag, sources in defined_flags().items()
            if flag not in UNDOCUMENTED_FLAGS
            and not re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text)
        }

        self.assertEqual(missing, {}, "document these flags or allowlist them with a reason")

    def test_allowlist_holds_only_live_undocumented_flags(self):
        stale = set(UNDOCUMENTED_FLAGS) - set(defined_flags())

        self.assertEqual(stale, set())
        for flag, reason in UNDOCUMENTED_FLAGS.items():
            self.assertTrue(reason.strip(), flag)

    def test_relative_links_resolve(self):
        broken = []
        for page in markdown_files():
            body = FENCE.sub("", page.read_text(encoding="utf-8"))
            for target in LINK.findall(body):
                if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                    continue
                path = target.split("#", 1)[0]
                if not (page.parent / path).exists():
                    broken.append(f"{page.relative_to(REPO_ROOT)}: {target}")

        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
