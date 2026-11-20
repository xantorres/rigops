from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import config as rigops_config  # noqa: E402
from rigops.detector import Detector  # noqa: E402
from rigops.sources import caveman, ccusage, rtk  # noqa: E402

_ADAPTERS = (rtk, ccusage, caveman)
_stub_dir = None


def no_external_sources(case) -> None:
    """Make the in-process source probes find no tool, so a row build never runs a real one."""
    for adapter in _ADAPTERS:
        patch = mock.patch.object(adapter, "probe", return_value=False)
        patch.start()
        case.addCleanup(patch.stop)


def path_with_failing_sources(path: str) -> str:
    """PATH for a child process where each source tool exists and fails, so none really runs."""
    global _stub_dir
    if _stub_dir is None:
        _stub_dir = tempfile.mkdtemp(prefix="rigops-test-stubs-")
        atexit.register(shutil.rmtree, _stub_dir, ignore_errors=True)
        for adapter in _ADAPTERS:
            stub = Path(_stub_dir) / adapter.__name__.rsplit(".", 1)[-1]
            stub.write_text("#!/bin/sh\nexit 1\n")
            stub.chmod(0o755)
    return f"{_stub_dir}{os.pathsep}{path}"


ES_OVERLAY = Path(__file__).resolve().parent / "fixtures" / "corrections" / "es.json"


def es_detector() -> Detector:
    return Detector.from_config(rigops_config.load(ES_OVERLAY))
