"""Spanish handling for the miner: statements the shared pattern misreads, corrections it misses."""

from __future__ import annotations

import re

from rigops import transcripts

# "no se", "no hay" and friends read as a leading "no" but are statements or questions, except
# the rule-shaped forms listed in the lookaheads.
ES_STATEMENT_RE = re.compile(
    r"^\s*no\s+(?:"
    r"se\b(?!\s+(?:toca|tocan|debe|deben|puede|pueden|hace\s+falta)\b)"
    r"|sé\b|tengo\b|puedo\b|(?:seria|sería)\b(?=[^?]*\?\s*$)"
    r"|es\b(?!\s+(?:eso|esto|ese|esa|lo\s+que|asi|así|lo\s+mismo|correcto|ahi|ahí)\b)"
    r"|hay\b(?!\s+que\b)"
    r"|hace\b(?!\s+falta\b)"
    r"|lo\s+puedes\b(?!.*\bo\s+qu[eé]\W*$)"
    r"|lo\s+(?:se|sé|tengo|puedo|sabes|encuentro|veo|entiendo|consigo|recuerdo)\b)",
    re.IGNORECASE,
)
ES_CORRECTION_RE = re.compile(
    r"^\s*(?:"
    r"(?P<demo>(?:eso|esto|as[ií])\s+no\b)"
    r"|(?P<mal>mal(?=\s*[,.!:;]|\s*$))"
    r"|(?P<esta_mal>est[aá]\s+(?:mal|incorrecto)\b|(?:incorrecto|falso)(?=\s*[,.!:;]|\s*$))"
    r"|(?P<te_dije>(?:ya\s+)?te\s+(?:dije|he\s+dicho)\s+que\b)"
    r"|(?P<deja_de>deja\s+de\b)"
    r"|(?P<nunca_mas>(?:nunca|jam[aá]s)\s+m[aá]s\b)"
    r"|(?P<ojo>ojo(?=\s*[,.!:;])|cuidado(?=\s*[,.!:;]))"
    r")",
    re.IGNORECASE,
)
BARE_NO_RE = re.compile(r"^\s*(?:eso|esto|as[ií])\s+no\s*[.!]*\s*$", re.IGNORECASE)
REPROACH_RE = re.compile(
    r"(?<!\bsi\s)(?<!\bhasta\sque\s)\bno\s+(?:has|hiciste|pusiste|arreglaste|entendiste)\s+\w+",
    re.IGNORECASE,
)
REPROACH_WINDOW = 160
QUESTION_END_RE = re.compile(r"(?:\?|\bno)\W*$", re.IGNORECASE)


def statement(text: str) -> bool:
    return bool(ES_STATEMENT_RE.match(transcripts._first_nonblank_line(text)))


def correction(text: str) -> bool:
    """A Spanish correction the shared pattern does not accept; it never overrides that one."""
    line = transcripts._first_nonblank_line(text)
    if BARE_NO_RE.match(line):
        return True
    if QUESTION_END_RE.search(line):
        return False
    return bool(ES_CORRECTION_RE.match(line) or REPROACH_RE.search(line[:REPROACH_WINDOW]))
