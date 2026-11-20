"""One read of every transcript, handed to each consumer a ledger row needs."""

from __future__ import annotations

from dataclasses import dataclass, field

from rigops import rebuilds, starts, transcripts
from rigops.detector import BUILTIN


@dataclass
class Corpus:
    turns: list = field(default_factory=list)
    friction: dict = field(default_factory=transcripts.new_friction)
    starts: list = field(default_factory=list)
    rebuilds: list = field(default_factory=list)
    rebuild_eit: float = 0.0
    lines_seen: int = 0
    lines_parsed: int = 0


def scan(root, since, until, headless_projects=(), detector=BUILTIN) -> Corpus:
    """Turns up to ``until`` (history before ``since`` feeds first-turn stats); the rest
    inside [since, until).
    """
    out = Corpus()
    counts = {"seen": 0, "parsed": 0}
    for path in transcripts.find_transcripts(root):
        lines = transcripts.read_lines(path)
        if lines is None:
            continue
        out.turns.extend(transcripts.turns_in_file(path, root, None, until, lines, counts))
        transcripts.friction_in_file(
            path, root, since, until, lines, out.friction, headless_projects, detector)
        start = starts.start_in_window(path, root, since, until, lines=lines)
        if start is not None:
            out.starts.append(start)
        hits, spent = rebuilds.scan_file(path, root, since, until, lines)
        out.rebuilds.extend(hits)
        out.rebuild_eit += spent
    out.starts.sort(key=lambda s: s["ts"])
    out.rebuilds.sort(key=lambda h: h["ts"])
    out.lines_seen, out.lines_parsed = counts["seen"], counts["parsed"]
    return out
