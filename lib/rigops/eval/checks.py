"""Assertions over one model reply. No I/O: a reply and an ``expect`` block in,
a verdict out."""

from __future__ import annotations

import itertools
import json
import re
import unicodedata

MAX_JSON_STARTS = 50
_BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def printable(text: str) -> str:
    """Escape what a terminal would act on rather than print: control characters,
    line separators, lone surrogates and bidi overrides. Everything else, NBSP
    and joiners included, is text and stays as it is."""
    return "".join(
        repr(c)[1:-1] if unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") or c in _BIDI else c
        for c in text
    )


def reject_constant(name: str):
    """``parse_constant`` hook: NaN and Infinity are Python's extension, not JSON."""
    raise ValueError(f"{name} is not valid JSON")


_DECODER = json.JSONDecoder(parse_constant=reject_constant)


def extract_json(text: str):
    """Return the first JSON object in ``text``, bare, fenced or wrapped in prose.

    Only the first few ``{`` are tried: every attempt can scan to the end of the
    reply, so an unbounded search is quadratic on a hostile one.
    """
    for match in itertools.islice(re.finditer(r"\{", text), MAX_JSON_STARTS):
        try:
            value, _ = _DECODER.raw_decode(text, match.start())
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            return value
    return None


def same(actual, expected) -> bool:
    """JSON equality that keeps ``true`` and ``1`` apart, which ``==`` does not."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(same(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(same(a, e) for a, e in zip(actual, expected))
        )
    return actual == expected


def _show(value) -> str:
    text = printable(json.dumps(value, ensure_ascii=False))
    return text if len(text) <= 60 else text[:57] + "..."


def _json_failures(expect: dict, text: str) -> list:
    data = extract_json(text)
    if data is None:
        return ["no JSON object in reply"]
    failures = []
    if "json_keys" in expect:
        wanted = set(expect["json_keys"])
        missing = sorted(wanted - set(data))
        extra = sorted(set(data) - wanted)
        if missing:
            failures.append(f"json keys missing {', '.join(map(printable, missing))}")
        if extra:
            failures.append(f"json keys extra {', '.join(map(printable, extra))}")
    for field, expected in expect.get("json", {}).items():
        if field not in data:
            failures.append(f"field {field} missing")
        elif not same(data[field], expected):
            failures.append(f"field {field} expected {_show(expected)}, got {_show(data[field])}")
    return failures


def evaluate(expect: dict, text: str) -> tuple:
    """Return ``(passed, reason)``; ``reason`` names every failed assertion.

    ``contains`` and ``not_contains`` ignore case, the way a reader would;
    ``regex`` is ``re.search`` as written, so case and anchoring are the
    pattern's own business (``(?i)``, ``(?m)``).
    """
    failures = []
    folded = text.casefold()
    for needle in expect.get("contains", ()):
        if needle.casefold() not in folded:
            failures.append(f"missing {_show(needle)}")
    for needle in expect.get("not_contains", ()):
        if needle.casefold() in folded:
            failures.append(f"forbidden {_show(needle)}")
    for pattern in expect.get("regex", ()):
        if not re.search(pattern, text):
            failures.append(f"no match for /{printable(pattern)}/")
    for pattern in expect.get("not_regex", ()):
        if re.search(pattern, text):
            failures.append(f"forbidden match /{printable(pattern)}/")
    if "json" in expect or "json_keys" in expect:
        failures.extend(_json_failures(expect, text))
    return not failures, "; ".join(failures)
