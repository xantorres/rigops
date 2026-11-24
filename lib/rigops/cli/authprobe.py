from __future__ import annotations

import argparse
import json
import sys

from rigops import cli
from rigops import authprobe
from rigops import config as rigops_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rigops authprobe", description=cli.COMMANDS["authprobe"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--force", action="store_true", help="run even when authprobe.enabled is false"
    )
    args = ap.parse_args(argv)

    cfg = rigops_config.load()
    defaults = rigops_config.DEFAULTS["authprobe"]
    enabled = rigops_config.get(cfg, "authprobe.enabled", defaults["enabled"])

    if not enabled and not args.force:
        if args.json:
            print(json.dumps({
                "version": 1,
                "ok": None,
                "detail": "disabled (set authprobe.enabled true or pass --force)",
                "enabled": False,
            }))
        else:
            print("authprobe disabled (set authprobe.enabled true or pass --force)")
        return 0

    rigops_config.require_private(rigops_config.config_path())
    ok, detail = authprobe.probe(cfg)

    if args.json:
        print(json.dumps({"version": 1, "ok": ok, "detail": detail, "enabled": enabled}))
    elif ok:
        print("authprobe: ok")
    else:
        print(f"authprobe: FAIL: {detail}", file=sys.stderr)

    return 0 if ok else 1

