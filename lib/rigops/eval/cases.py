"""Case files: one JSON object per file, the file stem as the case id.

A case is a prompt plus the assertions its reply must satisfy. One case per file
means two cases can never share an id, and editing a case is a one-file diff.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .checks import printable, reject_constant

TEXT_CHECKS = ("contains", "not_contains", "regex", "not_regex")
EXPECT_KEYS = (*TEXT_CHECKS, "json", "json_keys")
CASE_KEYS = ("group", "prompt", "expect", "max_tokens", "timeout_s", "note")
DEFAULT_MAX_TOKENS = 512
DEFAULT_TIMEOUT_S = 120.0
MAX_TIMEOUT_S = 3600
MAX_FILE_BYTES = 1024 * 1024


class CaseError(ValueError):
    """A case file that cannot be run as written."""


@dataclass(frozen=True)
class Case:
    id: str
    group: str
    prompt: str
    expect: dict
    hash: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class Suite:
    cases: tuple
    hash: str


def read_text_file(path) -> str:
    """Read a regular UTF-8 file of at most ``MAX_FILE_BYTES``, never through a symlink.

    In CI the suite comes from the change under review, and a case or system
    prompt linked to a file on the runner would ship that file to the endpoint.
    A FIFO would block the run forever.
    """
    if os.path.islink(path):
        raise ValueError("is a symlink; case files and the system prompt must be regular files")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("is not a regular file")
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"is larger than {MAX_FILE_BYTES} bytes")
    return data.decode("utf-8")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def case_hash(data: dict) -> str:
    """Identity of a case as authored. ``note`` is commentary, so rewording it
    keeps the case comparable with runs recorded before the edit."""
    body = {key: value for key, value in data.items() if key != "note"}
    return digest(json.dumps(body, sort_keys=True, separators=(",", ":")))


def _text(value, key: str) -> str:
    if isinstance(value, list) and value and all(isinstance(line, str) for line in value):
        value = "\n".join(value)
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"`{key}` must be a non-blank string or a list of strings")
    return value


def _strings(value, key: str) -> tuple:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items or not all(isinstance(i, str) and i for i in items):
        raise CaseError(f"`expect.{key}` must be a non-empty string or a list of them")
    return tuple(items)


def _expect(value) -> dict:
    if not isinstance(value, dict) or not value:
        raise CaseError("`expect` must be an object with at least one assertion")
    unknown = sorted(set(value) - set(EXPECT_KEYS))
    if unknown:
        raise CaseError(f"unknown assertion `{unknown[0]}` (known: {', '.join(EXPECT_KEYS)})")
    expect = {
        key: _strings(value[key], key) for key in (*TEXT_CHECKS, "json_keys") if key in value
    }
    for key in ("regex", "not_regex"):
        for pattern in expect.get(key, ()):
            try:
                re.compile(pattern)
            except (re.error, OverflowError, RecursionError) as exc:
                message = f"`expect.{key}` pattern {pattern!r} does not compile: {exc}"
                raise CaseError(message) from exc
    if "json" in value:
        if not isinstance(value["json"], dict) or not value["json"]:
            raise CaseError("`expect.json` must be an object mapping field to expected value")
        expect["json"] = value["json"]
    return expect


def _positive(data: dict, key: str, default, integer: bool = False, most=None):
    value = data.get(key, default)
    kinds = int if integer else (int, float)
    try:
        ok = (
            not isinstance(value, bool)
            and isinstance(value, kinds)
            and math.isfinite(value)
            and value > 0
            and (most is None or value <= most)
        )
    except OverflowError:
        ok = False
    if not ok:
        limit = f" no greater than {most}" if most is not None else ""
        raise CaseError(f"`{key}` must be a positive {'integer' if integer else 'number'}{limit}")
    return value


def parse_case(case_id: str, data) -> Case:
    if not isinstance(data, dict):
        raise CaseError("a case file holds one JSON object")
    unknown = sorted(set(data) - set(CASE_KEYS))
    if unknown:
        raise CaseError(f"unknown key `{unknown[0]}` (known: {', '.join(CASE_KEYS)})")
    for key in ("group", "prompt", "expect"):
        if key not in data:
            raise CaseError(f"missing required key `{key}`")
    group = data["group"]
    if not isinstance(group, str) or not group.strip():
        raise CaseError("`group` must be a non-blank string")
    if not isinstance(data.get("note", ""), str):
        raise CaseError("`note` must be a string")
    return Case(
        id=case_id,
        group=group.strip(),
        prompt=_text(data["prompt"], "prompt"),
        expect=_expect(data["expect"]),
        hash=case_hash(data),
        max_tokens=_positive(data, "max_tokens", DEFAULT_MAX_TOKENS, integer=True),
        timeout_s=float(_positive(data, "timeout_s", DEFAULT_TIMEOUT_S, most=MAX_TIMEOUT_S)),
    )


def load_suite(directory) -> Suite:
    """Load every ``*.json`` directly under ``directory``, in name order.

    Every broken file is reported, not only the first, so a batch of new cases
    gets fixed in one pass. Dotfiles are skipped: macOS writes ``._name.json``
    sidecars onto network and exFAT volumes.
    """
    root = Path(directory)
    if not root.is_dir():
        raise CaseError(f"cases directory not found: {root}")
    files = sorted(p for p in root.glob("*.json") if not p.name.startswith("."))
    if not files:
        raise CaseError(f"no case files (*.json) in {root}")
    cases, errors = [], []
    for path in files:
        try:
            if not path.stem.isprintable():
                raise CaseError("the file name holds control characters")
            data = json.loads(read_text_file(path), parse_constant=reject_constant)
            cases.append(parse_case(path.stem, data))
        except (OSError, ValueError, OverflowError, RecursionError) as exc:
            errors.append(f"{printable(path.name)}: {exc}")
    if errors:
        raise CaseError("\n".join(errors))
    ordered = sorted(cases, key=lambda c: c.id)
    return Suite(cases=tuple(cases), hash=digest("\n".join(f"{c.id} {c.hash}" for c in ordered)))
