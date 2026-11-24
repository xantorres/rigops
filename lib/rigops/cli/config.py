from __future__ import annotations

import argparse
import json
import sys

from rigops import cli
from rigops import config as rigops_config
from rigops import config_spec
from rigops.errors import RigopsError

_MISSING = object()


def cmd_get(args) -> int:
    cfg = rigops_config.load()
    value = rigops_config.get(cfg, args.key, _MISSING)
    if value is _MISSING:
        return 1
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))
    return 0


def cmd_path(_args) -> int:
    print(rigops_config.config_path())
    return 0


def cmd_check(_args) -> int:
    path = rigops_config.config_path()
    try:
        cfg = rigops_config.load()
    except RigopsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for key, note in rigops_config.unknown_keys(cfg):
        if note is None:
            print(f"warning: unknown config key: {key}", file=sys.stderr)
        else:
            print(f"removed in {config_spec.REMOVED_RELEASE}: {key} ({note})", file=sys.stderr)
    print(f"config: OK ({path})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rigops config", description=cli.COMMANDS["config"])
    sub = parser.add_subparsers(dest="command", required=True)

    get_parser = sub.add_parser("get", help="print a dotted config key")
    get_parser.add_argument("key")
    get_parser.set_defaults(func=cmd_get)

    path_parser = sub.add_parser("path", help="print the resolved config file path")
    path_parser.set_defaults(func=cmd_path)

    check_parser = sub.add_parser("check", help="load config and report status")
    check_parser.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)

