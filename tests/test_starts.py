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

from rigops import levers, starts  # noqa: E402

TAX_SCRIPT = Path(__file__).resolve().parent.parent / "libexec" / "rigops-tax"
UTC = dt.timezone.utc


def tool(name: str, pad: int = 0) -> dict:
    return {"name": name, "description": "d" * pad, "input_schema": {"type": "object"}}


SERVER_TOOLS = [tool("mcp__acme-docs__search", 300), tool("mcp__acme-docs__read", 100)]
BUILTIN_TOOLS = [tool("Read", 500), tool("Bash", 900)]


def shape_row(session: str, ts: str, tools: list, system=("system prompt",)) -> dict:
    return {
        "type": "api-request-shape", "sessionId": session, "timestamp": ts,
        "version": "1.0.0", "shapeHash": "h",
        "shape": {"system": [{"type": "text", "text": t} for t in system], "tools": tools},
    }


def write_session(root: Path, project: str, name: str, rows: list) -> Path:
    path = root / project / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def entry_row(entrypoint: str, ts: str) -> dict:
    return {"type": "attachment", "entrypoint": entrypoint, "timestamp": ts}


class ComposeTests(unittest.TestCase):
    def test_sizes_tools_by_their_json_and_groups_servers(self):
        tools = BUILTIN_TOOLS + SERVER_TOOLS
        found = starts.compose({"system": [{"type": "text", "text": "abc"}], "tools": tools})
        self.assertEqual(found["tools"], 4)
        self.assertEqual(found["tool_chars"], sum(len(json.dumps(t)) for t in tools))
        self.assertEqual(found["system_chars"], 3)
        self.assertEqual(found["groups"]["mcp__acme-docs"]["tools"], 2)
        self.assertEqual(found["groups"]["builtin"]["chars"],
                         sum(len(json.dumps(t)) for t in BUILTIN_TOOLS))

    def test_surface_from_entrypoint(self):
        cases = {"acme-desktop": "desktop", "sdk-cli": "headless", "cli": "cli",
                 None: "unknown", "vscode": "vscode"}
        for tag, want in cases.items():
            self.assertEqual(starts.surface(tag), want)


def make_tree(tmp: str) -> Path:
    root = Path(tmp)
    write_session(root, "-Users-alice-app", "sess-desk", [
        entry_row("acme-desktop", "2026-08-12T09:00:00Z"),
        shape_row("sess-desk", "2026-08-12T09:00:01Z", BUILTIN_TOOLS + SERVER_TOOLS),
        shape_row("sess-desk", "2026-08-12T10:00:00Z", BUILTIN_TOOLS),
    ])
    write_session(root, "-Users-alice-app", "sess-bot", [
        shape_row("sess-bot", "2026-08-12T11:00:00Z", []),
        entry_row("sdk-cli", "2026-08-12T11:00:02Z"),
    ])
    write_session(root, "-Users-alice-app", "sess-old", [
        entry_row("cli", "2026-07-01T09:00:00Z"),
        shape_row("sess-old", "2026-07-01T09:00:01Z", BUILTIN_TOOLS),
    ])
    write_session(root / "-Users-alice-app" / "sess-desk", "subagents", "agent-1", [
        entry_row("acme-desktop", "2026-08-12T09:30:00Z"),
        shape_row("sess-desk", "2026-08-12T09:30:01Z", BUILTIN_TOOLS),
    ])
    return root


class CollectStartsTests(unittest.TestCase):

    def test_first_shape_per_main_session_inside_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            found = starts.collect_starts(
                dt.datetime(2026, 8, 10, tzinfo=UTC), dt.datetime(2026, 8, 17, tzinfo=UTC),
                transcripts_dir=root,
            )
        self.assertEqual([(s["session"], s["surface"], s["tools"]) for s in found],
                         [("sess-desk", "desktop", 4), ("sess-bot", "headless", 0)])
        self.assertEqual(found[0]["project"], "-Users-alice-app")

    def test_subagents_and_a_session_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            found = starts.collect_starts(None, None, transcripts_dir=root, subagents=True,
                                          session="sess-desk")
        self.assertEqual(sorted(s["subagent"] for s in found), [False, True])

    def test_ledger_fields_skip_tool_less_and_subagent_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = starts.collect_starts(None, None, transcripts_dir=make_tree(tmp),
                                          subagents=True)
        fields = starts.ledger_fields(found)
        self.assertEqual(fields["start_tools"], 3)
        self.assertEqual(starts.ledger_fields([]), {"start_tools": None, "start_tool_chars": None})

    def test_build_row_records_the_start_medians(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            cfg = {"transcripts_dir": str(root), "ledger": {"watch_projects": {}},
                   "fixed_tax": {"paths": [], "globs": []}}
            row = levers.build_row(dt.date(2026, 8, 15), "", cfg=cfg, transcripts_dir=root)
        self.assertEqual(row["start_tools"], 4)
        self.assertEqual(row["start_tool_chars"],
                         sum(len(json.dumps(t)) for t in BUILTIN_TOOLS + SERVER_TOOLS))


class TaxStartsCliTests(unittest.TestCase):
    def _run(self, root: Path, *args) -> subprocess.CompletedProcess:
        env = {**os.environ, "RIGOPS_CONFIG": str(root / "absent.json")}
        return subprocess.run(
            [sys.executable, str(TAX_SCRIPT), "--starts", "--dir", str(root), *args],
            env=env, capture_output=True, text=True,
        )

    def test_summary_and_one_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            make_tree(str(root))
            summary = self._run(root, "--since", "2026-08-10", "--until", "2026-08-17", "--json")
            single = self._run(root, "--session", "sess-desk")
        self.assertEqual(summary.returncode, 0, summary.stderr)
        payload = json.loads(summary.stdout)
        self.assertEqual(sorted(payload["surfaces"]), ["desktop", "headless"])
        self.assertEqual(payload["groups"][0]["group"], "builtin")
        self.assertEqual(single.returncode, 0, single.stderr)
        self.assertIn("tools 4", single.stdout)
        self.assertIn("mcp__acme-docs", single.stdout)

    def test_unknown_session_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(root, "--session", "nope")
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
