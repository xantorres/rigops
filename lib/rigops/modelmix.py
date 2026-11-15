"""Main-thread model share against the default model that settings name."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from rigops import transcripts


def family(model: str) -> str:
    m = re.match(r"^(?:claude-)?([a-z]+)", model or "")
    return m.group(1) if m else "other"


def default_family(settings_path: Path) -> str | None:
    try:
        value = json.loads(Path(settings_path).read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError, AttributeError):
        return None
    # A `*plan` alias switches model by mode, so no single family is the default.
    if not isinstance(value, str) or value in ("", "default") or value.endswith("plan"):
        return None
    return family(value)


def ledger_fields(main_turns: list, settings_path: Path) -> dict:
    default = default_family(settings_path)
    eit_by_family: dict[str, float] = defaultdict(float)
    for t in main_turns:
        eit_by_family[family(t["model"])] += transcripts.eit(t)
    total = sum(eit_by_family.values())
    pct = round(eit_by_family[default] / total * 100, 1) if default and total else None
    return {"default_model": default, "main_default_model_pct": pct}
