"""Attribution of matched processes: who still owns a process rooted in a tree."""

from __future__ import annotations

from pathlib import Path

from rigops.reap_procs import _filter_killable


def owned_pids(pids: set[int], snap: dict[int, dict]) -> set[int]:
    """Matched pids that something alive outside the matched set still owns.

    Walk each pid up through parents that are themselves matched; the top-most
    matched ancestor must have been reparented to init (ppid 1), or a live
    editor or shell outside the tree may still be using the process.
    """
    owned: set[int] = set()
    for pid in pids:
        if pid not in snap:
            continue
        top, seen = pid, {pid}
        while snap[top]["ppid"] in pids and snap[top]["ppid"] in snap \
                and snap[top]["ppid"] not in seen:
            top = snap[top]["ppid"]
            seen.add(top)
        if snap[top]["ppid"] != 1:
            owned.add(pid)
    return owned


def split_owned(rooted: set[int], snap: dict[int, dict]) -> tuple[set[int], set[int]]:
    """(killable, owned): the signalable subset of rooted, split by attribution."""
    killable = _filter_killable(rooted, snap)
    owned = owned_pids(rooted, snap) & killable
    return killable - owned, owned


def owned_entries(label: str, owned: set[int], snap: dict[int, dict]) -> list[dict]:
    entries = []
    for pid in sorted(owned):
        tokens = snap[pid]["command"].split()
        entries.append({"pid": pid, "comm": Path(tokens[0]).name if tokens else "?",
                        "reason": "owned", "parent": snap[pid]["ppid"], "tree": label})
    return entries
