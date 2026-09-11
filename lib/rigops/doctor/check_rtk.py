"""rtk is a token-reduction proxy pinned by version: a silent upgrade changes
the rewrite rules underneath every session, so drift from the pinned version
is a defect rather than routine tool churn. Nothing is pinned by default, and a
host that never pinned rtk has nothing to drift from.
"""

from __future__ import annotations

from rigops import core

AREA = "hooks"
CHECK = "rtk"


def run(cfg):
    expected = core.cfg_get(cfg, "doctor.rtk.version")
    if not expected:
        return []
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
