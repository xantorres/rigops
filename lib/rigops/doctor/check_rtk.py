"""rtk is a token-reduction proxy pinned by version: a silent upgrade changes
the rewrite rules underneath every session, so drift from the pinned version
is a defect rather than routine tool churn.
"""

from __future__ import annotations

from rigops import core

AREA = "hooks"
CHECK = "rtk"

DEFAULT_VERSION = "0.42.4"


def run(cfg):
    expected = core.cfg_get(cfg, "doctor.rtk.version", DEFAULT_VERSION)
    out = core.run(["rtk", "--version"]).strip()

    if not out:
        return [core.Finding(
            check=CHECK, area=AREA, key="missing",
            symptom="rtk is not installed or `rtk --version` produced no output",
            evidence="rtk --version = (empty)",
            fix=f"install rtk and pin it to {expected}",
        )]

    actual = out.split()[-1]
    if actual != expected:
        return [core.Finding(
            check=CHECK, area=AREA, key="version",
            symptom=f"rtk version is {actual}, expected {expected}",
            evidence=f"rtk --version = {out}",
            fix=f"pin rtk back to {expected} or update doctor.rtk.version deliberately",
        )]

    return []
