"""Compare two recorded runs and name every regression. No I/O.

Only cases present in both runs with the same content hash are compared. A case
whose prompt or assertions changed between the runs is a different test, and
scoring it against its old self would let a loosened expectation pass as a fix.
"""

from __future__ import annotations

from fractions import Fraction

from .score import stats

META = ("run_id", "ts", "label", "model", "suite", "system", "git_sha")


def resolve(rows: list, ref: str):
    """Return the row whose ``run_id`` is ``ref``, else the latest labelled ``ref``."""
    for row in rows:
        if row.get("run_id") == ref:
            return row
    for row in reversed(rows):
        if row.get("label") == ref:
            return row
    return None


def _unchanged(old: dict, new: dict) -> bool:
    digest = old.get("hash")
    return isinstance(digest, str) and digest != "" and digest == new.get("hash")


def _slower(before, after, tolerance) -> bool:
    """Exact arithmetic, so a p95 sitting on the bound never trips it by rounding."""
    return Fraction(after) * 100 > Fraction(before) * (100 + Fraction(str(tolerance)))


def compare(base: dict, head: dict, latency_tolerance=None) -> dict:
    """Diff ``head`` against ``base``, group by group.

    Every compared case that passed in ``base`` and does not pass in ``head`` is a
    regression, reported under its group. A case that starts passing never offsets
    one that stops: a prompt that leaks one secret while guarding another is not
    even. A case that errored in ``base`` and does not pass in ``head`` cannot be
    judged either way, so it is listed as unverified; one that passed in ``base``
    and is missing or edited in ``head`` is listed as dropped, because deleting or
    loosening a failing case is the cheapest way to hide it. With ``latency_tolerance``
    (percent), a group whose p95 grows by more than that regresses too; latency
    never gates by default, because a local model shares the machine with
    whatever else is running.
    """
    old, new = base["cases"], head["cases"]
    common = sorted(set(old) & set(new))
    shared = [c for c in common if _unchanged(old[c], new[c])]
    newly_failing = [
        {"case": c, "group": new[c]["group"], "status": new[c]["status"],
         "reason": new[c].get("reason", "")}
        for c in shared if old[c]["status"] == "pass" and new[c]["status"] != "pass"
    ]
    by_group = {}
    for case_id in shared:
        by_group.setdefault(new[case_id]["group"], []).append(case_id)

    groups, regressions = {}, []
    for name in sorted(by_group):
        ids = by_group[name]
        before, after = stats(old[c] for c in ids), stats(new[c] for c in ids)
        groups[name] = {"base": before, "head": after}
        lost = [f["case"] for f in newly_failing if f["group"] == name]
        if lost:
            regressions.append({
                "group": name, "metric": "newly_failing", "cases": lost,
                "total": after["total"], "base": before["passed"], "head": after["passed"],
            })
        slow_before, slow_after = before["p95_ms"], after["p95_ms"]
        if (
            latency_tolerance is not None
            and slow_before is not None
            and slow_after is not None
            and _slower(slow_before, slow_after, latency_tolerance)
        ):
            growth = round((slow_after / slow_before - 1) * 100, 1) if slow_before else None
            regressions.append({
                "group": name, "metric": "p95_ms", "base": slow_before, "head": slow_after,
                "pct": growth,
            })

    removed = sorted(set(old) - set(new))
    changed = [c for c in common if not _unchanged(old[c], new[c])]
    return {
        "base": {key: base.get(key) for key in META},
        "head": {key: head.get(key) for key in META},
        "shared": len(shared),
        "added": sorted(set(new) - set(old)),
        "removed": removed,
        "changed": changed,
        "dropped": sorted(c for c in (*removed, *changed) if old[c]["status"] == "pass"),
        "groups": groups,
        "newly_failing": newly_failing,
        "newly_passing": [
            {"case": c, "group": new[c]["group"]}
            for c in shared if old[c]["status"] != "pass" and new[c]["status"] == "pass"
        ],
        "unverified": [
            c for c in shared if old[c]["status"] == "error" and new[c]["status"] != "pass"
        ],
        "regressions": regressions,
    }
