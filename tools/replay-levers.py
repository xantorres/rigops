#!/usr/bin/env python3
"""Replay the ledger through the doctor lever rules, one row at a time.

Each row is judged as if it were the newest, with only the notes dated on or
before it, so a threshold change can be checked against real weeks before it
ships. Read-only: nothing under the state directory is written.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import config as rigops_config  # noqa: E402
from rigops import state  # noqa: E402
from rigops.doctor import check_levers  # noqa: E402


def _mask(text: str, groups: list) -> str:
    for i, group in sorted(enumerate(groups), key=lambda ig: -len(ig[1])):
        text = text.replace(group, f"G{i}")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=None, help="default: state ledger.jsonl")
    parser.add_argument("--notes", type=Path, default=None,
                        help="default: interventions.jsonl beside the ledger")
    parser.add_argument("--defaults-only", action="store_true",
                        help="judge the built-in rules alone, ignoring doctor.levers.rules")
    parser.add_argument("--mask", action="store_true",
                        help="print watch_projects groups as G0, G1, ... for sharing")
    parser.add_argument("-v", "--verbose", action="store_true", help="print each finding")
    args = parser.parse_args()

    cfg = rigops_config.load()
    if args.defaults_only:
        cfg = {**cfg, "doctor": {"levers": {"rules": {}}}}
    rules, builtin = check_levers.effective_rules(cfg)
    ledger = args.ledger or state.state_dir() / "ledger.jsonl"
    rows, problem = check_levers.read_rows(ledger)
    if problem:
        print(f"error: {ledger} {problem}", file=sys.stderr)
        return 2
    notes, _ = check_levers.read_rows(args.notes or ledger.parent / "interventions.jsonl")
    groups = sorted(rigops_config.get(cfg, "ledger.watch_projects", {}) or {})
    rows.sort(key=lambda r: r["date"])
    for i, row in enumerate(rows):
        day = date.fromisoformat(row["date"])
        seen = [n for n in notes or [] if str(n.get("date", "")) <= row["date"]]
        found = check_levers.judge(rows[: i + 1], seen, rules, day, ledger, builtin=builtin)
        keys = ", ".join(f.key for f in found) or "-"
        print(f"{row['date']}  {_mask(keys, groups) if args.mask else keys}")
        if args.verbose:
            for f in found:
                line = f"    {f.symptom} | {f.evidence}"
                print(_mask(line, groups) if args.mask else line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
