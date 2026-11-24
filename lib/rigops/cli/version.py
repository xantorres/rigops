from __future__ import annotations

import argparse

from rigops import __version__, cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rigops version", description=cli.COMMANDS["version"])
    parser.parse_args(argv)
    print(__version__)
    return 0
