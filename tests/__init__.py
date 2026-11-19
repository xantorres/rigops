"""Marks the test tree as a package and isolates every run from the real home.

Without the package, `python3 -m unittest tests.test_reap` fails on import while
`unittest discover` works. Importing it points home, state and config at one
throwaway root before any rigops module reads them, so no test can touch the
state a real scheduled job owns.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="rigops-test-")).resolve()
atexit.register(shutil.rmtree, _root, ignore_errors=True)

os.environ["HOME"] = str(_root / "home")
os.environ["RIGOPS_STATE_DIR"] = str(_root / "state")
os.environ["XDG_STATE_HOME"] = str(_root / "xdg-state")
os.environ["XDG_CONFIG_HOME"] = str(_root / "xdg-config")
os.environ.pop("RIGOPS_CONFIG", None)
