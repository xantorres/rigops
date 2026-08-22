from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EIT_SCRIPT = REPO_ROOT / "libexec" / "rigops-eit"


def _write_line(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


def _turn(msg_id: str, ts: str, session: str, model: str, input_tokens: int, tools=None) -> dict:
    return {
        "type": "assistant",
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
        [sys.executable, str(EIT_SCRIPT), *args],
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
