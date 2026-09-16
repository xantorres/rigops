"""Score retrieval against labelled questions instead of against impressions.

Each probe is a question plus the path that answers it. A probe is a hit when
that path is in the top three. Negative probes are the other half of the suite:
they run a prefetch from one realm's working directory and assert that no row
from another realm comes back, which is the property this layer exists to keep.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from rigops import core

from . import search as search_mod

DEFAULT_SPEC = "~/rig/eval/retrieval-probes.json"
TARGET_PRECISION = 0.8


def _expand(value) -> str:
    return os.path.expanduser(str(value))


def load(path=None) -> dict:
    target = Path(_expand(path or os.environ.get("RIGOPS_PROBES") or DEFAULT_SPEC))
    if not target.is_file():
        raise SystemExit(f"error: probe suite not found: {target}")
    return json.loads(target.read_text())


def _is_hit(expect, paths) -> bool:
    """A probe may accept more than one path: a split document keeps its router."""
    wanted = expect if isinstance(expect, (list, tuple)) else [expect]
    for item in wanted:
        target = _expand(item).rstrip("/")
        for path in paths:
            got = _expand(path)
            if got == target or got.startswith(target + "/"):
                return True
    return False


def run(registry, spec, top=3, logger=None, db_path=None) -> dict:
    """Run every probe; return per-realm precision and the negative-probe verdicts."""
    by_realm = {}
    misses = []
    for probe in spec.get("probes", []):
        realm = probe.get("realm", "?")
        cwd = _expand(probe.get("cwd", "~"))
        result = search_mod.query(
            registry, probe["question"], cwd=cwd, scopes=("all",), top=top,
            budget=probe.get("budget"), db_path=db_path,
        )
        if logger:
            logger(result, "probe", {"probe": probe.get("id"), "probe_realm": realm})
        paths = [hit.short_path for hit in result.hits[:top]]
        hit = _is_hit(probe["expect"], paths)
        bucket = by_realm.setdefault(realm, {"n": 0, "hits": 0})
        bucket["n"] += 1
        bucket["hits"] += 1 if hit else 0
        if not hit:
            misses.append({"id": probe.get("id"), "realm": realm,
                           "question": probe["question"], "expect": probe["expect"],
                           "got": paths})
    for bucket in by_realm.values():
        bucket["precision_at_3"] = round(bucket["hits"] / bucket["n"], 3) if bucket["n"] else None

    negatives = []
    for probe in spec.get("negatives", []):
        cwd = _expand(probe["cwd"])
        result = search_mod.query(registry, probe["query"], cwd=cwd, prefetch=True,
                                  top=10, db_path=db_path)
        if logger:
            logger(result, "probe-negative", {"probe": probe.get("id")})
        forbidden = probe.get("forbid_realm")
        allowed = result.realms[0] if result.realms else None
        leaked = [hit.as_dict() for hit in result.hits
                  if hit.realm == forbidden or (allowed and hit.realm != allowed)]
        negatives.append({
            "id": probe.get("id"), "cwd": probe["cwd"], "forbid_realm": forbidden,
            "rows": len(result.hits), "realms_seen": sorted({hit.realm for hit in result.hits}),
            "leaked": leaked, "pass": not leaked,
        })

    target = spec.get("target_precision", TARGET_PRECISION)
    min_per_realm = spec.get("min_probes_per_realm", 5)
    thin = [realm for realm, bucket in by_realm.items() if bucket["n"] < min_per_realm]
    ok = (
        bool(by_realm)
        and not thin
        and all(bucket["precision_at_3"] >= target for bucket in by_realm.values())
        and all(item["pass"] for item in negatives)
    )
    return {
        "by_realm": by_realm, "misses": misses, "negatives": negatives,
        "target_precision": target, "thin_realms": thin, "pass": ok,
        "probes": sum(bucket["n"] for bucket in by_realm.values()),
    }


def report(outcome: dict) -> str:
    lines = []
    for realm in sorted(outcome["by_realm"]):
        bucket = outcome["by_realm"][realm]
        lines.append(
            f"{realm:22s} {bucket['hits']}/{bucket['n']}  "
            f"precision_at_3 {bucket['precision_at_3']}"
        )
    for item in outcome["negatives"]:
        state = "pass" if item["pass"] else f"LEAK {item['realms_seen']}"
        lines.append(f"negative {item['id']:<13s} {item['cwd']}  rows={item['rows']}  {state}")
    for miss in outcome["misses"]:
        lines.append(
            f"miss #{miss['id']} [{miss['realm']}] {miss['expect']} not in top 3: {miss['got']}"
        )
    if outcome["thin_realms"]:
        lines.append(
            f"thin realms (under the per-realm minimum): {', '.join(outcome['thin_realms'])}"
        )
    lines.append("GATE " + ("pass" if outcome["pass"] else "fail"))
    return "\n".join(lines)


__all__ = ["load", "run", "report", "DEFAULT_SPEC", "core"]
