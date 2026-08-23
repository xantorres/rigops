"""Source adapters mined for ledger annotations (rtk, ccusage, custom).

Every adapter exposes two functions:

  probe() -> bool
      Cheap availability check (e.g. shutil.which), called before window()
      so an absent tool never spawns a subprocess.

  window(since, until) -> dict | None
      since/until are datetimes; snapshot-only sources (whole-history totals
      rather than a windowed slice) may ignore them.

Any failure -- missing binary, timeout, nonzero exit, invalid JSON, unexpected
shape -- returns None. Adapters never raise, never print. External tools'
JSON shapes drift across versions, so known keys are extracted defensively;
an unrecognized shape becomes None rather than a crash.
"""

from __future__ import annotations
