from __future__ import annotations

import base64
import os
import re
from pathlib import Path

GENERIC_RULES = (
    (re.compile(r"\bBearer\s+\S+", re.IGNORECASE), "[token]"),
    (re.compile(r"\b(?:ghs|github_pat|glpat)[-_A-Za-z0-9]{8,}"), "[key]"),
    (re.compile(r"-Users-[A-Za-z0-9-]+"), "[path]"),
    (re.compile(r"(?:/private|/var|/root|/etc|/opt|/tmp)/\S*"), "[path]"),
    (re.compile(r"\bwww\.[\w.-]+"), "[url]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"https?://\S+"), "[url]"),
    (re.compile(r"(?:/Users|/home)/[^/\s]+(?:/\S*)?"), "[path]"),
    (re.compile(r"\b(?:sk|ghp|gho|xox[a-z]|AKIA)[-_A-Za-z0-9]{12,}"), "[key]"),
    (re.compile(r"\b[A-Za-z0-9_]{1,10}-\d{1,6}\b"), "[ticket]"),
    (re.compile(r"\b[0-9a-fA-F]{24,}\b"), "[hex]"),
)
PUBLIC_PATTERNS = (Path(__file__).resolve().parent.parent.parent / "tools"
                   / "redaction-patterns.public.txt")
PRIVATE_PATTERNS = "~/.config/rigops/redaction-patterns.txt"


def _rule_lines(files) -> list:
    lines = []
    for path in files:
        try:
            lines += Path(path).expanduser().read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):
            continue
    packed = os.environ.get("RIGOPS_REDACTION_PATTERNS_B64")
    if packed:
        try:
            lines += base64.b64decode(packed).decode("utf-8").splitlines()
        except ValueError:
            pass
    return lines


def _rule_patterns(files) -> list:
    compiled = []
    for raw in _rule_lines(files):
        if not raw.strip() or raw.startswith("#"):
            continue
        flag, _, pattern = raw.partition(" ")
        if flag not in ("i", "s") or not pattern:
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE if flag == "i" else 0))
        except re.error:
            continue
    return compiled


def redact(text: str, registry=None, pattern_files=None) -> str:
    """Mask secrets, identifiers and everything the repo's redaction rules or the registry name."""
    for pattern, mask in GENERIC_RULES:
        text = pattern.sub(mask, text)
    if pattern_files is None:
        pattern_files = [PUBLIC_PATTERNS,
                         os.environ.get("RIGOPS_REDACTION_PATTERNS") or PRIVATE_PATTERNS]
    for pattern in _rule_patterns(pattern_files):
        text = pattern.sub("[redacted]", text)
    if registry is not None:
        for token in registry.client_tokens:
            text = re.sub(re.escape(token), "[client]", text, flags=re.IGNORECASE)
    return text
