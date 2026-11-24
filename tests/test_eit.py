from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import cli_argv

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_line(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


def _turn(
    msg_id: str, ts: str, session: str, model: str, input_tokens: int, tools=None,
    entrypoint=None,
) -> dict:
    return {
        "type": "assistant",
        **({"entrypoint": entrypoint} if entrypoint else {}),
        "timestamp": ts,
        "sessionId": session,
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 1,
            },
            "content": [{"type": "tool_use", "name": name} for name in (tools or [])],
        },
    }


def _run_eit(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*cli_argv("eit"), *args],
        env=env, capture_output=True, text=True, check=False,
    )


class CtxBucketBoundaryTests(unittest.TestCase):
    def test_boundaries_half_open_and_p90(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "transcripts"
            edges = [50_000, 100_000, 200_000, 300_000, 500_000]
            for i, ctx_val in enumerate(edges):
                _write_line(
                    root / "proj" / "s.jsonl",
                    _turn(
                        f"msg-{i}", f"2026-08-11T12:00:0{i}Z", "sess",
                        "claude-sonnet-5-20260101", ctx_val,
                    ),
                )
            result = _run_eit(["--since", "-3650d", "--dir", str(root), "--json"], dict(os.environ))
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            hist = payload["histogram"]
            self.assertEqual(hist["<50k"]["turns"], 0)
            self.assertEqual(hist["50-100k"]["turns"], 1)
            self.assertEqual(hist["100-200k"]["turns"], 1)
            self.assertEqual(hist["200-300k"]["turns"], 1)
            self.assertEqual(hist["300-500k"]["turns"], 1)
            self.assertEqual(hist[">500k"]["turns"], 1)
            self.assertEqual(payload["ctx_p90"], 420000.0)


class ByGroupingAndTopToolsTests(unittest.TestCase):
    def test_top_tools_by_grouping_and_json_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "transcripts"
            _write_line(root / "proj-a" / "s.jsonl", _turn(
                "t1", "2026-08-11T12:00:00Z", "sess-1", "claude-sonnet-5-20260101", 1000,
                ["Read", "Read"],
            ))
            _write_line(root / "proj-a" / "s.jsonl", _turn(
                "t2", "2026-08-11T12:01:00Z", "sess-1", "claude-opus-5-20260101", 2000, ["Write"]
            ))
            _write_line(root / "proj-b" / "s.jsonl", _turn(
                "t3", "2026-08-11T12:02:00Z", "sess-2", "claude-opus-5-20260101", 3000,
                ["Bash", "Bash", "Bash", "Bash"],
            ))
            _write_line(root / "proj-b" / "s.jsonl", _turn(
                "t4", "2026-08-11T12:03:00Z", "sess-2", "claude-sonnet-5-20260101", 500, ["Read"]
            ))

            env = dict(os.environ)
            by_project = _run_eit(
                ["--since", "-3650d", "--dir", str(root), "--json", "--by", "project"], env
            )
            self.assertEqual(by_project.returncode, 0)
            payload = json.loads(by_project.stdout)

            self.assertEqual(payload["top_tools"][0], ["Bash", 4])
            self.assertEqual(payload["top_tools"][1], ["Read", 3])
            self.assertEqual(payload["top_tools"][2], ["Write", 1])

            self.assertEqual(payload["by"], "project")
            groups = {g["key"]: g for g in payload["top_groups"]}
            self.assertEqual(groups["proj-b"]["eit"], 3500.0)
            self.assertEqual(groups["proj-b"]["turns"], 2)
            self.assertEqual(groups["proj-a"]["eit"], 3000.0)

            for key in (
                "turns", "eit_total", "eit_per_turn", "output_tokens_total", "cache_hit_pct",
                "ctx_mean", "ctx_p50", "ctx_p90", "ctx_max", "eit_share_over_300k_pct",
                "top_tools", "by", "top_groups", "histogram", "window",
            ):
                self.assertIn(key, payload)

            by_model = _run_eit(
                ["--since", "-3650d", "--dir", str(root), "--json", "--by", "model"], env
            )
            self.assertEqual(by_model.returncode, 0)
            model_groups = {g["key"]: g["eit"] for g in json.loads(by_model.stdout)["top_groups"]}
            self.assertEqual(model_groups["claude-sonnet-5-20260101"], 1500.0)
            self.assertEqual(model_groups["claude-opus-5-20260101"], 5000.0)


class ProjectDirAndGroupTests(unittest.TestCase):
    def _corpus(self, tmp: str) -> Path:
        root = Path(tmp) / "projects"
        app = root / "-Users-alice-app--claude-worktrees-blue"
        _write_line(app / "s1.jsonl", _turn("a1", "2026-08-11T12:00:00Z", "s1", "m", 1000))
        _write_line(app / "s1" / "subagents" / "agent-1.jsonl",
                    _turn("a2", "2026-08-11T12:01:00Z", "s1", "m", 200))
        _write_line(root / "-Users-alice-lib" / "s2.jsonl",
                    _turn("b1", "2026-08-11T12:02:00Z", "s2", "m", 30))
        return root

    def _groups(self, args: list, env: dict) -> dict:
        result = _run_eit(["--since", "-3650d", "--json", *args], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {g["key"]: (g["eit"], g["turns"]) for g in json.loads(result.stdout)["top_groups"]}

    def test_a_single_project_dir_reads_main_and_subagent_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._corpus(tmp)
            one = root / "-Users-alice-app--claude-worktrees-blue"
            got = self._groups(["--dir", str(one)], dict(os.environ))
            self.assertEqual(got, {"-Users-alice-app": (1200.0, 2)})
            whole = self._groups(["--dir", str(root)], dict(os.environ))
            self.assertEqual(whole["-Users-alice-app"], (1200.0, 2))
            self.assertEqual(whole["-Users-alice-lib"], (30.0, 1))

    def test_a_stray_file_in_the_root_keeps_root_mode_and_is_skipped_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._corpus(tmp)
            _write_line(root / "stray.jsonl", _turn("x1", "2026-08-11T12:03:00Z", "s3", "m", 5))
            got = self._groups(["--dir", str(root)], dict(os.environ))
            self.assertEqual(got, {"-Users-alice-app": (1200.0, 2), "-Users-alice-lib": (30.0, 1)})

    def test_by_group_maps_paths_onto_watch_projects_with_other_for_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._corpus(tmp)
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"ledger": {"watch_projects": {"app": "alice-app"}}}))
            env = {**os.environ, "RIGOPS_CONFIG": str(cfg)}
            got = self._groups(["--dir", str(root), "--by", "group"], env)
            self.assertEqual(got, {"app": (1200.0, 2), "other": (30.0, 1)})
            one = root / "-Users-alice-app--claude-worktrees-blue"
            self.assertEqual(self._groups(["--dir", str(one), "--by", "group"], env),
                             {"app": (1200.0, 2)})


class SurfaceAndDayTests(unittest.TestCase):
    def test_by_surface_and_day_with_cost_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "transcripts"
            _write_line(root / "proj-a" / "s.jsonl", _turn(
                "t1", "2026-08-11T12:00:00Z", "sess-1", "m", 1000, entrypoint="claude-desktop",
            ))
            _write_line(root / "proj-a" / "s.jsonl", _turn(
                "t2", "2026-08-12T12:00:00Z", "sess-1", "m", 1000,
            ))
            _write_line(root / "proj-b" / "s.jsonl", _turn(
                "t3", "2026-08-12T12:00:00Z", "sess-2", "m", 3000, entrypoint="cli",
            ))
            _write_line(root / "proj-c" / "s.jsonl", _turn(
                "t4", "2026-08-12T12:00:00Z", "sess-3", "m", 500,
            ))
            env = dict(os.environ, TZ="UTC")
            out = _run_eit(
                ["--since", "-3650d", "--dir", str(root), "--json", "--by", "surface"], env
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            payload = json.loads(out.stdout)
            groups = {g["key"]: g for g in payload["top_groups"]}
            # The tag is per file: t2 inherits t1's, proj-c has none.
            self.assertEqual(groups["desktop"]["turns"], 2)
            self.assertEqual(groups["cli"]["cost"], 3005.0)
            self.assertEqual(groups["unknown"]["turns"], 1)
            self.assertEqual(payload["cost"], 5520.0)
            self.assertAlmostEqual(payload["cost_pct"]["output"], 20 / 5520 * 100)

            by_day = _run_eit(
                ["--since", "-3650d", "--dir", str(root), "--json", "--by", "day"], env
            )
            days = [g["key"] for g in json.loads(by_day.stdout)["top_groups"]]
            self.assertEqual(days, ["2026-08-11", "2026-08-12"])


class DirValidationTests(unittest.TestCase):
    def test_nonexistent_dir_exits_2(self):
        result = _run_eit(
            ["--dir", "/definitely/not/a/real/rigops/eit/dir", "--json"], dict(os.environ)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr.lower())

    def test_dir_expanduser_and_expandvars_applied_before_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["RIGOPS_EIT_TEST_VAR"] = tmp
            result = _run_eit(["--dir", "$RIGOPS_EIT_TEST_VAR/missing-subdir", "--json"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn(str(Path(tmp) / "missing-subdir"), result.stderr)

        home_result = _run_eit(
            ["--dir", "~/__rigops_eit_missing_test__", "--json"], dict(os.environ)
        )
        self.assertEqual(home_result.returncode, 2)
        self.assertNotIn("~", home_result.stderr)


if __name__ == "__main__":
    unittest.main()
