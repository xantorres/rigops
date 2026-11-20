"""One correction and denial detector shared by the ledger metrics and `rigops mine`."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rigops import config as rigops_config

# Config patterns change what the metric counts; config_sha records them, so ledger diff flags it.
_DENIAL_ALTERNATIVES = (
    r"permission (for this action was )?deni",
    r"denied by the .* classifier",
    r"user (has )?denied",
    r"blocked by .*hook",
    r"requires approval",
    r"operation blocked",
    r"PreToolUse:.*(deny|blocked)",
    r"permission to use .{0,160}denied",
    r"has been denied",
    r"user doesn'?t want to proceed",
    r"tool use was rejected",
    r"hook.{0,40}(denied|blocked)",
    r"(denied|blocked).{0,40}hook",
)
DENIAL_PATTERNS = re.compile("|".join(_DENIAL_ALTERNATIVES), re.IGNORECASE)
CORRECTION_RE = re.compile(
    r"^\s*(?:no(?![\w-])|nope\b|wrong\b|not what\b|undo\b|revert\b|"
    r"stop(?=\s*(?:[-,.!:;]|$))|wait(?=\s*(?:[-,.!:;]|$)))",
    re.IGNORECASE,
)

KEYS = ("extra_correction_patterns", "correction_exclude_patterns", "extra_denial_patterns")


def first_nonblank_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _compile(cfg, key: str) -> tuple:
    path = f"ledger.{key}"
    raw = rigops_config.get(cfg, path, [])
    if not isinstance(raw, list):
        raise SystemExit(f"error: {path}: must be a list of regex strings")
    out = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, str):
            raise SystemExit(f"error: {path}[{i}]: must be a string")
        try:
            out.append(re.compile(entry, re.IGNORECASE))
        except re.error as exc:
            raise SystemExit(f"error: {path}[{i}]: {exc.msg}") from exc
    return tuple(out)


@dataclass(frozen=True)
class Detector:
    extra_correction: tuple = ()
    correction_exclude: tuple = ()
    extra_denial: tuple = ()

    @classmethod
    def from_config(cls, cfg) -> Detector:
        return cls(*(_compile(cfg, key) for key in KEYS))

    def classify(self, text: str) -> str:
        """none | builtin | extra | excluded, by the first non-blank line of a human prompt."""
        line = first_nonblank_line(text)
        if CORRECTION_RE.match(line):
            found = "builtin"
        elif any(p.search(line) for p in self.extra_correction):
            found = "extra"
        else:
            return "none"
        if any(p.search(line) for p in self.correction_exclude):
            return "excluded"
        return found

    def is_correction(self, text: str) -> bool:
        return self.classify(text) in ("builtin", "extra")

    def is_denial(self, text: str) -> bool:
        return bool(DENIAL_PATTERNS.search(text)) or any(p.search(text) for p in self.extra_denial)


BUILTIN = Detector()
