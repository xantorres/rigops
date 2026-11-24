"""Marks the test tree as a package and isolates every run from the real home.

Without the package, `python3 -m unittest tests.test_reap` fails on import while
`unittest discover` works. Importing it points home, state and config at one
throwaway root before any rigops module reads them, so no test can touch the
state a real scheduled job owns.

A fake `launchctl` sits first on PATH: `list` reaches the real tool (read-only),
every other subcommand is recorded in `LAUNCHCTL_LOG` and fails, so no test can
bootstrap, bootout, enable or kickstart a job the machine really runs.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="rigops-test-")).resolve()
atexit.register(shutil.rmtree, _root, ignore_errors=True)

_bin = _root / "bin"
_bin.mkdir(parents=True)
LAUNCHCTL_LOG = _root / "launchctl.log"
_fake = _bin / "launchctl"
_fake.write_text(
    "#!/bin/sh\n"
    'if [ "$1" = list ]; then exec /bin/launchctl "$@"; fi\n'
    f'echo "$*" >> "{LAUNCHCTL_LOG}"\n'
    "exit 1\n"
)
_fake.chmod(0o755)
os.environ["PATH"] = f"{_bin}{os.pathsep}{os.environ.get('PATH', '')}"

os.environ["HOME"] = str(_root / "home")
os.environ["RIGOPS_STATE_DIR"] = str(_root / "state")
os.environ["XDG_STATE_HOME"] = str(_root / "xdg-state")
os.environ["XDG_CONFIG_HOME"] = str(_root / "xdg-config")
os.environ.pop("RIGOPS_CONFIG", None)
