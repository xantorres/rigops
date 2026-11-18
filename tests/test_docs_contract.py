"""The docs name what the code ships: commands, flags and links."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
PLUGIN_COMMANDS = REPO_ROOT / "plugin" / "commands"
DOC_PAGES = (
    [README]
    + sorted((REPO_ROOT / "docs").glob("*.md"))
    + sorted(PLUGIN_COMMANDS.glob("*.md"))
)
FLAG_SOURCES = (
    sorted((REPO_ROOT / "libexec").glob("rigops-*"))
    + sorted((REPO_ROOT / "lib" / "rigops").rglob("*.py"))
)

# "<command> <flag>" pairs no doc page needs to name, each with the reason.
UNDOCUMENTED_FLAGS: dict[str, str] = {}

ADD_ARGUMENT = re.compile(r"add_argument\(([^)]*)", re.S)
FLAG = re.compile(r"""["'](--[a-z0-9][a-z0-9-]*)["']""")
SCRIPT_HALF = re.compile(r"the `rigops` CLI \(([^)]*)\)")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
LINK_DEFINITION = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(\S+)", re.M)
FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1", re.S | re.M)
CODE_SPAN = re.compile(r"`[^`\n]*`")
HEADING = re.compile(r"^#{1,6} +(.+?)[ #]*$", re.M)
SKIP_DIRS = {".git", ".claude", "fixtures"}


def command_of(source: Path) -> str:
    # lib/rigops/<module>.py parses the arguments of `rigops <module>`.
    return source.name[len("rigops-"):] if source.parent.name == "libexec" else source.stem


def defined_flags() -> dict[tuple[str, str], str]:
    flags: dict[tuple[str, str], str] = {}
    for source in FLAG_SOURCES:
        for call in ADD_ARGUMENT.finditer(source.read_text(encoding="utf-8")):
            names = call.group(1).split("help=")[0]
            for flag in FLAG.findall(names):
                flags[(command_of(source), flag)] = str(source.relative_to(REPO_ROOT))
    return flags


def sections(text: str) -> list[str]:
    fenced = [m.span() for m in FENCE.finditer(text)]
    cuts = [m.start() for m in HEADING.finditer(text)
            if not any(start <= m.start() < end for start, end in fenced)]
    bounds = [0] + cuts + [len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def mentions(text: str, word: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", text) is not None


def documented(command: str, flag: str, doc_sections: list[tuple[Path, str]]) -> bool:
    return any(
        mentions(chunk, flag)
        and (page == PLUGIN_COMMANDS / f"{command}.md"
             or mentions(chunk, f"rigops {command}") or mentions(chunk, f"rigops-{command}"))
        for page, chunk in doc_sections
    )


def anchors(page: Path) -> set[str]:
    seen: dict[str, int] = {}
    for heading in HEADING.findall(FENCE.sub("", page.read_text(encoding="utf-8"))):
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        if count:
            seen[f"{slug}-{count}"] = 1
    return set(seen)


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

    def test_every_flag_is_documented_with_its_command(self):
        doc_sections = [(page, chunk) for page in DOC_PAGES
                        for chunk in sections(page.read_text(encoding="utf-8"))]
        missing = sorted(
            f"{command} {flag} ({source})"
            for (command, flag), source in defined_flags().items()
            if f"{command} {flag}" not in UNDOCUMENTED_FLAGS
            and not documented(command, flag, doc_sections)
        )

        self.assertEqual(missing, [], "name each flag in a section that names `rigops <command>`, "
                                      "or allowlist it with a reason")

    def test_allowlist_holds_only_live_undocumented_flags(self):
        live = {f"{command} {flag}" for command, flag in defined_flags()}

        self.assertEqual(set(UNDOCUMENTED_FLAGS) - live, set())
        for pair, reason in UNDOCUMENTED_FLAGS.items():
            self.assertTrue(reason.strip(), pair)

    def test_relative_links_resolve(self):
        broken = []
        for page in markdown_files():
            body = CODE_SPAN.sub("", FENCE.sub("", page.read_text(encoding="utf-8")))
            for target in LINK.findall(body) + LINK_DEFINITION.findall(body):
                if re.match(r"[a-z][a-z0-9+.-]*:", target):
                    continue
                path, _, fragment = target.partition("#")
                resolved = (page.parent / path) if path else page
                if not resolved.exists():
                    broken.append(f"{page.relative_to(REPO_ROOT)}: {target}")
                elif fragment and resolved.suffix == ".md" and fragment not in anchors(resolved):
                    broken.append(f"{page.relative_to(REPO_ROOT)}: {target} (no such heading)")

        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
