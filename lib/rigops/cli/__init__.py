from __future__ import annotations

import importlib
import sys
from collections.abc import Callable

from rigops import state
from rigops.errors import RigopsError

COMMANDS: dict[str, str] = {
    "authprobe": "Probe whether the headless claude credential still works.",
    "backlog": "Lint or add to the governed one-line backlog.",
    "config": "Get, locate or check the configuration.",
    "doctor": "Health watchdog for the job fleet.",
    "eit": "EIT usage report for session transcripts.",
    "eval": "Run versioned eval cases and diff two runs.",
    "janitor": "Declarative hygiene for rigops-managed state.",
    "ledger": "Write a ledger row, note an intervention, or diff two rows.",
    "mine": "Mine repeated corrections.",
    "reap": "Reap merged, unused git worktrees.",
    "tax": "Fixed context tax: always-loaded config bytes.",
    "version": "Print the installed version.",
}
EXPERIMENTAL = frozenset({"authprobe", "backlog", "eit", "mine"})
_DIRECT = frozenset({"hook"})


def run(main: Callable[[], int]) -> int:
    try:
        return main()
    except (RigopsError, state.MalformedJsonl) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def usage() -> str:
    width = max(map(len, COMMANDS))
    rows = [f"  {name.ljust(width)}  {text}" for name, text in sorted(COMMANDS.items())]
    experimental = ", ".join(sorted(EXPERIMENTAL))
    return "\n".join([
        "usage: rigops <command> [args...]",
        "",
        "commands:",
        *rows,
        "",
        f"experimental (no compatibility promise): {experimental}",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("help", "-h", "--help"):
        print(usage(), end="")
        return 0
    name, rest = args[0], args[1:]
    if name == "--version":
        name = "version"
    if name not in COMMANDS and name not in _DIRECT:
        print(f"error: unknown command '{name}'\n\n{usage()}", end="", file=sys.stderr)
        return 2
    module = importlib.import_module(f"rigops.cli.{name}")
    if name in _DIRECT:
        return module.main(rest)
    return run(lambda: module.main(rest))
