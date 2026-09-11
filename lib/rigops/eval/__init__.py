"""Versioned eval cases run against an OpenAI-compatible endpoint, scored per
group, recorded as one append-only row per run, and compared run to run.

``client`` is the only module that touches the network and ``cases`` the only
one that reads files; ``checks``, ``score`` and ``diff`` do no I/O at all, so a
regression verdict can be table-tested without a model.
"""

from __future__ import annotations

from .cases import Case, CaseError, Suite, digest, load_suite, parse_case, read_text_file
from .checks import evaluate, extract_json, printable
from .client import complete
from .diff import compare, resolve
from .score import build_row, row_problem, stats, summarize

__all__ = [
    "printable",
    "read_text_file",
    "row_problem",
    "Case",
    "CaseError",
    "Suite",
    "digest",
    "load_suite",
    "parse_case",
    "evaluate",
    "extract_json",
    "complete",
    "compare",
    "resolve",
    "build_row",
    "stats",
    "summarize",
]
