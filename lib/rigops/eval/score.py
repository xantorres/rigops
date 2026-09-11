"""Pass rate and latency per group for one run, and the row that records it.
No I/O."""

from __future__ import annotations

from rigops import core

STATUSES = ("pass", "fail", "error")
TOTAL_KEYS = ("passed", "total", "errors", "pass_rate", "p50_ms", "p95_ms")


def _valid_result(result) -> bool:
    ms = result.get("ms") if isinstance(result, dict) else None
    return (
        isinstance(result, dict)
        and isinstance(result.get("group"), str)
        and bool(result["group"].strip())
        and result.get("status") in STATUSES
        and isinstance(ms, int)
        and not isinstance(ms, bool)
        and ms >= 0
        and isinstance(result.get("hash"), str)
        and bool(result["hash"])
    )


def row_problem(row) -> str:
    """Why a recorded row cannot be trusted, or ``""`` when it can.

    A hand-edited or truncated row must stop the diff rather than score quietly:
    a status spelled ``"PASS"`` would otherwise read as a failure on both sides
    and hide a regression.
    """
    if not isinstance(row, dict) or not isinstance(row.get("run_id"), str) or not row["run_id"]:
        return "a row needs a run_id"
    totals, cases = row.get("totals"), row.get("cases")
    if not isinstance(totals, dict) or any(key not in totals for key in TOTAL_KEYS):
        return f"run {row['run_id']}: totals needs {', '.join(TOTAL_KEYS)}"
    if not isinstance(cases, dict):
        return f"run {row['run_id']}: cases must be an object"
    for case_id, result in cases.items():
        if not _valid_result(result):
            return f"run {row['run_id']}: case {case_id} needs group, status, ms and hash"
    return ""


def stats(results) -> dict:
    """Aggregate case results, each a dict with ``status`` and ``ms``.

    An errored case counts against the pass rate but not the latency: a refused
    connection returns in a millisecond and a timeout takes the whole budget, and
    neither says how fast the model answers.
    """
    results = list(results)
    passed = sum(1 for r in results if r["status"] == "pass")
    latencies = sorted(r["ms"] for r in results if r["status"] != "error")
    return {
        "passed": passed,
        "total": len(results),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "pass_rate": round(passed / len(results), 4) if results else None,
        "p50_ms": round(core.percentile(latencies, 0.50)) if latencies else None,
        "p95_ms": round(core.percentile(latencies, 0.95)) if latencies else None,
    }


def summarize(cases: dict) -> tuple:
    """Return ``(per_group, totals)`` for a ``{case_id: result}`` mapping."""
    groups = {}
    for result in cases.values():
        groups.setdefault(result["group"], []).append(result)
    return {name: stats(rows) for name, rows in sorted(groups.items())}, stats(cases.values())


def build_row(*, run_id, ts, label, endpoint, model, temperature, suite, system, git_sha, cases):
    per_group, totals = summarize(cases)
    return {
        "run_id": run_id,
        "ts": ts,
        "label": label,
        "endpoint": endpoint,
        "model": model,
        "temperature": temperature,
        "suite": suite,
        "system": system,
        "git_sha": git_sha,
        "totals": totals,
        "groups": per_group,
        "cases": cases,
    }
