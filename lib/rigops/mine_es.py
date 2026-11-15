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
    r"|(?:hiciste|has\s+hecho)\s+nada\s+(?:mal|raro|malo)\b"
    r"|lo\s+puedes\b(?!.*\bo\s+qu[eé]\W*$)"
    r"|lo\s+(?:se|sé|tengo|puedo|sabes|encuentro|veo|entiendo|consigo|recuerdo)\b)",
    re.IGNORECASE,
)
# A bare cue is not enough: each family needs the words that make it a rebuke, not a statement.
_ORDERS = r"(?:hagas|uses|toques|borres|cambies|muevas|pongas|digas|inventes|escribas)"
_DEJA_VERBS = (r"(?:resumir|repetir|explicar|preguntar|hacer|inventar|a[nñ]adir|agregar|borrar|"
               r"tocar|usar|cambiar|escribir|poner|mover|comentar|decir|narrar|listar|pedir|"
               r"confirmar|formatear|romper|ignorar|asumir|suponer|simplificar|reescribir|editar|"
               r"crear|generar)")
ES_CORRECTION_RE = re.compile(
    r"^\s*(?:"
    r"(?P<demo>(?:(?:eso|esto)\s+no(?=\s*[,.!:;]|\s*$)"
    r"|(?:eso|esto)\s+no\s+(?:(?:es|era)\s+(?:lo\s+que|eso|esto|as[ií]|correcto|lo\s+mismo)"
    r"|funciona|sirve|vale|compila|quiero|(?:lo\s+)?" + _ORDERS + r")"
    r"|as[ií]\s+no)\b)"
    r"|(?P<mal>mal(?=\s*[,.!:;]|\s*$))"
    r"|(?P<esta_mal>(?:(?:eso|esto)\s+)?est[aá]\s+(?:mal|incorrecto)\b|(?:incorrecto|falso)(?=\s*[,.!:;]|\s*$))"
    r"|(?P<otra_vez>otra\s+vez\s+(?:lo\s+mismo|igual|mal)\b)"
    r"|(?P<te_dije>(?:ya\s+)?te\s+(?:dije|he\s+dicho)\s+que\s+(?:no|nunca|jam[aá]s|sin|ni)\b)"
    r"|(?P<deja_de>deja\s+de\s+" + _DEJA_VERBS + r"\b)"
    r"|(?P<nunca_mas>(?:nunca|jam[aá]s)\s+m[aá]s\s+(?:me\s+|lo\s+|la\s+)?\w{3,}(?:es|as)\b)"
    r"|(?P<ojo>(?:ojo|cuidado)\s*[,.!:;]\s*(?:que\s+)?(?:eso|esto|as[ií]|rompe|otra\s+vez"
    r"|no\s+(?:me|lo|la|te)\b|no\s+" + _ORDERS + r"|te\s+(?:has|olvid)|(?:lo\s+)?has\b))"
    r")",
    re.IGNORECASE,
)
BARE_NO_RE = re.compile(
    r"^\s*(?:(?:eso|esto|as[ií])\s+no|(?:ya\s+)?te\s+(?:dije|he\s+dicho)\s+que\s+no|para(?:\s+ya)?)\s*[.!]*\s*$",
    re.IGNORECASE,
)
REPROACH_RE = re.compile(
    r"(?:^|[,;:.!?]\s*|\b(?:pero|y|porque|oye|vale|mira)\s+|\bpor\s*qu[eé]\s+)"
    r"no\s+(?:has|hiciste|pusiste|arreglaste|entendiste)\s+(?!(?:\w+\s+)?nada\s+(?:mal|raro|malo)\b)\w+",
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


def counted(text: str) -> bool:
    """The miner's kept rule: shared pattern minus Spanish statements, plus Spanish rebukes."""
    if transcripts.is_correction(text):
        return not statement(text)
    return correction(text)
