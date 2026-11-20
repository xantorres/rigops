"""Text report and ignored-file summaries for reap."""

from __future__ import annotations

from pathlib import Path

from rigops.reap_guards import human

REAP = "reap"
PRUNE = "prune"


IGNORED_LIST_CAP = 10


def top_level_entries(paths: list[str]) -> list[str]:
    """Ignored paths collapsed to their first segment; a directory keeps a trailing slash."""
    return sorted({p.split("/", 1)[0] + "/" if "/" in p.rstrip("/") else p for p in paths})


def worktrees_left(res: dict, preview: bool) -> int:
    """Linked worktrees left once this run's removals and prunes, made or previewed, land."""
    gone = (PRUNE, REAP) if preview else (PRUNE,)
    return sum(1 for w in res["worktrees"]
               if w["verdict"] not in gone and not (w.get("applied") or {}).get("removed"))


def capped(entries: list[str], limit: int = IGNORED_LIST_CAP) -> str:
    shown = ", ".join(entries[:limit])
    return shown if len(entries) <= limit else f"{shown} and {len(entries) - limit} more"


def render(results: list[dict], apply_mode: bool, cap: int = 0) -> str:
    lines: list[str] = []
    counts: dict[str, int] = {}
    reclaim = 0

    for res in results:
        interesting = [w for w in res["worktrees"] if w["verdict"] != "skip: bare"]
        if not interesting and not res["note"]:
            continue
        repo_label = str(Path(res["repo"])).replace(str(Path.home()), "~")
        header = f"\n{repo_label}"
        if res["target"]:
            header += f"  (target: {res['target']})"
        lines.append(header)
        if res["note"]:
            lines.append(f"  ! {res['note']}")
        if res.get("over_cap"):
            lines.append(f"  ! {res['worktrees_left']} linked worktrees, over the cap of {cap}")
        for wt in interesting:
            counts[wt["verdict"]] = counts.get(wt["verdict"], 0) + 1
            if wt["verdict"] == REAP:
                reclaim += wt.get("size", 0)
            name = Path(wt["path"]).name
            branch = wt["branch"] or "(detached)"
            age = f"{wt['age_days']}d" if wt.get("age_days") is not None else "-"
            size = f"  {human(wt['size'])}" if wt.get("size") else ""
            lines.append(f"  {wt['verdict']:<18} {age:>5}  {name:<34} {branch}{size}")
            if wt.get("precious_file"):
                lines.append(f"      kept because of {wt['precious_file']}; "
                             "change the reaper.precious config key to release it")
            elif wt["verdict"] == REAP and wt.get("ignored_files"):
                lines.append(f"      {'removed' if apply_mode else 'removes'} ignored: "
                             f"{capped(wt['ignored_files'])}")

    lines.append("")
    if counts:
        summary = "  ".join(f"{v}={counts[v]}" for v in sorted(counts))
        lines.append(f"totals: {summary}")
    else:
        lines.append("totals: nothing found")
    if reclaim:
        verb = "reclaimed" if apply_mode else "reclaimable"
        lines.append(
            f"{verb}: {human(reclaim)} (apparent size; copy-on-write means actual is lower)"
        )
    return "\n".join(lines)
