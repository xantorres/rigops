from __future__ import annotations

import unittest

from rigops import core
from rigops.doctor.pointer_grammar import (
    AGENT_BULLET_RE,
    AGENT_HEADING_RE,
    AGENT_RE,
    MCP_RE,
    PATH_RE,
    RELATIVE_LINK_RE,
    SKILL_RE,
)


def captures(patterns, text):
    return [hit for rx in patterns for hit in rx.findall(text)]


class PathReTests(unittest.TestCase):
    def test_extracts_the_path_and_stops_at_prose_punctuation(self):
        cases = [
            ("see ~/.claude/CLAUDE.md, ok", "~/.claude/CLAUDE.md"),
            ("open `/usr/local/bin/x.sh`", "/usr/local/bin/x.sh"),
            ("(/opt/homebrew/bin/rg)", "/opt/homebrew/bin/rg"),
            ("~/docs/a.md:12", "~/docs/a.md"),
            ("/etc/hosts;", "/etc/hosts"),
            ("/tmp/x|y", "/tmp/x"),
            ('"/var/log/a b"', "/var/log/a"),
            ("[~/notes/a.md]", "~/notes/a.md"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(PATH_RE.findall(text), [want])

    def test_every_listed_root_is_a_path_start(self):
        for root in ("opt", "usr", "etc", "srv", "var", "tmp", "private", "bin", "sbin",
                     "Applications", "Library", "Volumes"):
            with self.subTest(root=root):
                self.assertEqual(PATH_RE.findall(f"at /{root}/x/y.md"), [f"/{root}/x/y.md"])

    def test_a_longer_token_does_not_start_a_path(self):
        for text in (".config/bin/run.sh", "foo/usr/bin/x", "path/~/x", "a~/x", "-/usr/x"):
            with self.subTest(text=text):
                self.assertEqual(PATH_RE.findall(text), [])

    def test_a_bare_root_or_tilde_is_not_a_path(self):
        for text in ("/usr", "x ~ y", "/unknown/dir/x", "relative/dir/x.md"):
            with self.subTest(text=text):
                self.assertEqual(PATH_RE.findall(text), [])

    def test_trailing_slash_keeps_a_directory_pointer(self):
        self.assertEqual(PATH_RE.findall("in /usr/ and ~/"), ["/usr/", "~/"])

    def test_two_paths_in_one_line_are_both_found(self):
        self.assertEqual(
            PATH_RE.findall("cp ~/a.md /tmp/b.md"), ["~/a.md", "/tmp/b.md"],
        )

    def test_a_sibling_home_is_read_off_this_rig(self):
        parent = core.HOME.parent
        if str(parent) == "/":
            self.skipTest("home sits directly under the filesystem root")
        path = f"{parent}/alice/app/x.md"
        self.assertEqual(PATH_RE.findall(f"see {path} now"), [path])


class AgentReTests(unittest.TestCase):
    def test_name_before_the_word_agent(self):
        cases = [
            ("the `reviewer` agent", "reviewer"),
            ("**code-reviewer** subagent", "code-reviewer"),
            ("`a:b_c-d` agent.", "a:b_c-d"),
            ("`plugin:thing` agent", "plugin:thing"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(captures(AGENT_RE, text), [want])

    def test_subagent_type_assignment(self):
        cases = [
            ('subagent_type: "caveman:cavecrew-reviewer"', "caveman:cavecrew-reviewer"),
            ("subagent_type=`planner`", "planner"),
            ('"subagent_type": "explorer"', "explorer"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(captures(AGENT_RE, text), [want])

    def test_prose_and_reversed_order_are_not_pointers(self):
        for text in ("the agent auto-invokes", "agent `x-y`", "an agent named reviewer",
                     "`reviewer` agents", "`reviewer`agent"):
            with self.subTest(text=text):
                self.assertEqual(captures(AGENT_RE, text), [])

    def test_name_shape_is_lowercase_and_at_least_three_chars(self):
        for text in ("`ab` agent", "`Foo` agent", "`1ab` agent", "`-ab` agent"):
            with self.subTest(text=text):
                self.assertEqual(captures(AGENT_RE, text), [])


class RelativeLinkReTests(unittest.TestCase):
    def test_target_of_a_relative_markdown_link(self):
        cases = [
            ("see [x](../common/x.md) now", "../common/x.md"),
            ("[x](./a.md#top)", "./a.md"),
            ("[x](sub/dir/)", "sub/dir/"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(RELATIVE_LINK_RE.findall(text), [want])

    def test_urls_anchors_and_absolute_targets_are_not_relative_links(self):
        for text in ("[x](https://a.org/b.md)", "[x](#top)", "[x](/etc/hosts)",
                     "[x](~/a.md)", "[x](mailto:a@b.org)", "a[0](1)", "[x]()"):
            with self.subTest(text=text):
                self.assertEqual(RELATIVE_LINK_RE.findall(text), [])


class AgentBulletReTests(unittest.TestCase):
    def test_heading_that_opens_an_agent_section(self):
        for text in ("## Agent Support", "### Agents", "# The agent list"):
            with self.subTest(text=text):
                self.assertTrue(AGENT_HEADING_RE.match(text))
        for text in ("## Mocking", "Agent Support", "## Agentic loops"):
            with self.subTest(text=text):
                self.assertFalse(AGENT_HEADING_RE.match(text))

    def test_bullet_leading_with_a_bold_name(self):
        self.assertEqual(AGENT_BULLET_RE.findall("- **e2e-runner** - Playwright"), ["e2e-runner"])
        self.assertEqual(AGENT_BULLET_RE.findall("  * `plan-it`: x"), ["plan-it"])
        self.assertEqual(AGENT_BULLET_RE.findall("- Use the **reviewer** agent"), [])


class SkillReTests(unittest.TestCase):
    def test_each_of_the_three_shapes(self):
        cases = [
            ("skill `foo-bar`", "foo-bar"),
            ("skills **x1y**", "x1y"),
            ("the `docs-vault` skill", "docs-vault"),
            ('Skill("superpowers:brainstorming")', "superpowers:brainstorming"),
            ("Skill( 'abc')", "abc"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(captures(SKILL_RE, text), [want])

    def test_unquoted_or_uppercase_names_are_not_pointers(self):
        for text in ("skill foo", "`Foo` skill", "skillset `abc`", "`ab` skill",
                     "Skill(foo)", "Skill()"):
            with self.subTest(text=text):
                self.assertEqual(captures(SKILL_RE, text), [])

    def test_the_word_boundary_keeps_a_longer_word_out(self):
        self.assertEqual(captures(SKILL_RE, "`foo-bar` skills"), [])
        self.assertEqual(captures(SKILL_RE, "`foo-bar` skillful"), [])


class McpReTests(unittest.TestCase):
    def test_each_of_the_two_shapes(self):
        cases = [
            ("the `github` MCP", "github"),
            ("`Linear_2` MCP server", "Linear_2"),
            ("`1pw-x` MCP.", "1pw-x"),
            ("MCP server `figma`", "figma"),
            ("MCP server **my-srv**", "my-srv"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(captures(MCP_RE, text), [want])

    def test_plain_prose_is_not_a_pointer(self):
        for text in ("MCP server figma", "`ab` MCP", "the `mcp` MCPs", "`foo` mcp",
                     "MCP servers `figma`", "an MCP `x`"):
            with self.subTest(text=text):
                self.assertEqual(captures(MCP_RE, text), [])

    def test_name_may_start_with_a_digit_or_capital(self):
        self.assertEqual(captures(MCP_RE, "`Slack` MCP and `9router` MCP"), ["Slack", "9router"])


if __name__ == "__main__":
    unittest.main()
