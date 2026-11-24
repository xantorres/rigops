#!/usr/bin/env python3
from __future__ import annotations

import sys

from rigops import cli  # noqa: E402
from rigops import mine  # noqa: E402

if __name__ == "__main__":
    sys.exit(cli.run(mine.main))
