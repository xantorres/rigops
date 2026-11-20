from __future__ import annotations

import sys
from collections.abc import Callable

from rigops import state
from rigops.errors import RigopsError


def run(main: Callable[[], int]) -> int:
    try:
        return main()
    except (RigopsError, state.MalformedJsonl) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
