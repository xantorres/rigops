from __future__ import annotations

import base64
import os
import re
from pathlib import Path

PAY = (r"(?:salary|salario|sueldo|pay|paid|paga|pagan|pago|bruto|neto|gross|offer|oferta|bonus|"
       r"earns?|earning|making|makes?|ganar|gano|gana|ganas|cobro|cobrar|cobra|euros?|dollars?)")
PAY_MORE = (r"(?:paying|pays|payroll|compensation|raise|fee|rate|gan[eéó]|cobr[eé]|aumento)")
RATE = r"(?:per|/|a)\s*(?:s|sec|second|ms|min|minute)"
FILLER = (r"(?:is|was|are|of|around|about|at|over|approx|aprox|de|es|son|unos|to|"
          r"up\s+to|more\s+than|mas\s+de)")
UNITS = (r"(?:tokens?|rows?|filas?|lines?|l[ií]neas|files?|archivos|requests?|calls?|bytes?|"
         r"ms|secs?|seconds?|mins?|minutes?|timeouts?|users?|items?|records?|tests?|reqs?|rps|qps)")
SUM = (r"(?:\d{1,3}(?:[.,]\d{3})+|\d[\d.,]*[kKmM]|\d+\s?(?:mil|thousand|million|millones)"
       r"|\d{4,})")
TLDS = (r"(?:co\.uk|com|net|org|io|cloud|eu|es|cy|ai|uk|co|xyz|de|fr|tech|me|nl|it|ch|us|biz|pt|mx|"
        r"ca|(?<!\bto\.)be|ie|pl|se|gov|edu|(?<!\blogger\.)(?<!\blog\.)(?<!\bconsole\.)info|"
        r"(?:dev|app)(?!\.\w))")
CURRENCY = r"(?:USD|EUR|GBP|CHF|CAD|AUD|MXN|JPY|SEK|NOK|DKK|PLN|BRL|INR)"
STREET_CAP = (r"(?:Str|Street|St|Ave|Avenue|Rd|Road|Lane|Ln|Drive|Dr|Blvd|Boulevard|Terrace|"
              r"Court|Ct|Way|Close|Place|Square|Crescent)")
HEX = r"[0-9a-f]{1,4}"
PRIVATE_IP = r"(?:10\.|127\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.0\.0\.0)"
OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"


def _iban(match: re.Match) -> str:
    return "[iban]" if sum(c.isdigit() for c in match.group(0)) >= 8 else match.group(0)


GENERIC_RULES = (
    (re.compile(r"\bBearer\s+\S+", re.IGNORECASE), "[token]"),
    (re.compile(r"\b(?:ghs|github_pat|glpat)[-_A-Za-z0-9]{8,}"), "[key]"),
    (re.compile(r"-Users-[A-Za-z0-9-]+"), "[path]"),
    (re.compile(r"(?:/private|/var|/root|/etc|/opt|/tmp)/\S*"), "[path]"),
    (re.compile(r"\bwww\.[\w.-]+"), "[url]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://\S+", re.IGNORECASE), "[url]"),
    (re.compile(r"(\b[\w-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|"
                r"(?:auth|access|refresh)[_-]?token)\b[\"']?\s*[:=]\s*[\"']?)(?![\[\s])(?!(?:string|str|bool|int|number|true|false|null|none|undefined)\b)[^\s\"',;]+",
                re.IGNORECASE), r"\1[secret]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{16,}"), "[key]"),
    (re.compile(r"(?:/Users|/home|/Volumes)/[^/\s]+(?:/\S*)?"), "[path]"),
    (re.compile(r"\$\{?HOME\}?(?:/\S*)?"), "[path]"),
    (re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s]+(?:\\\S*)?"), "[path]"),
    (re.compile(r"\b(?:sk|ghp|gho|xox[a-z]|AKIA)[-_A-Za-z0-9]{12,}"), "[key]"),
    (re.compile(r"\b(?!(?:UTF|GPT|SHA|ISO|TLS|SSL|UTC|GMT|MD)-\d)(?=[a-z]*[A-Z])[A-Za-z]{2,10}"
                r"-\d{1,6}\b|\b[a-z]{2,10}-\d{3,6}\b"), "[ticket]"),
    (re.compile(r"\b[0-9a-fA-F]{24,}\b"), "[hex]"),
    (re.compile(r"~/(?!\.[a-z]+/projects/\[path\]/)\S*"), "[path]"),
    (re.compile(r"(?<![\w/~])~[A-Za-z_][\w.-]*/\S*"), "[path]"),
    (re.compile(r"[€$£]\s?\d[\d.,]*[kKmM]?|\b\d[\d.,]*(?:\s?(?:mil|thousand|million|millones)|\s?[kKmM])?"
                r"\s?(?:€|euros?|EUR|USD|GBP|CHF|AUD|MXN|SEK|NOK|pesos|d[oó]lares)(?!\w)",
                re.IGNORECASE), "[amount]"),
    (re.compile(rf"\b{CURRENCY}\s?(?:\d[\d.,]+[kKmM]?|\d[kKmM]?(?!\s+[a-z]))(?!\w)"), "[amount]"),
    (re.compile(rf"(\b(?:{PAY}|{PAY_MORE})\b[:=]?\s*(?:(?:{FILLER})\s+){{0,2}})"
                rf"(?!(?:19|20)\d\d\b){SUM}(?!\w)(?!\s*{UNITS}\b)(?!\s*{RATE}\b)",
                re.IGNORECASE), r"\1[amount]"),
    (re.compile(rf"\b\d[\d.,]*[kKmM]\b(?!\s*{UNITS}\b)(?=[^.\n]{{0,20}}?\b{PAY}\b)",
                re.IGNORECASE), "[amount]"),
    (re.compile(r"\b\d[\d.,]*\s?[kKmM]\b(?=\s*(?:a|per|al|/)\s*(?:year|yr|month|mo|mes|año|ano)\b)",
                re.IGNORECASE), "[amount]"),
    (re.compile(r"\b[a-z]{2}\d{2}(?:\s?[a-z0-9]{4}){2,7}(?:\s?[a-z0-9]{1,3})?\b", re.IGNORECASE),
     _iban),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[id]"),
    (re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b|\b\d{4}[ -]\d{6}[ -]\d{4,5}\b"), "[card]"),
    (re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\d{2,4}|[\s.-]\d(?=[\s.-]\d{2})){3,6}\b"),
     "[phone]"),
    (re.compile(r"(?<!\w)\(\d{3}\)\s?\d{3}[\s.-]?\d{4}\b"
                r"|(?<![\w.])\d{3}[\s.-]\d{3}[\s.-]\d{4}(?![\w.])"), "[phone]"),
    (re.compile(r"\b0[1-9](?:[\s.-]\d{2}){4}\b|\b0\d{3,4}\s\d{5,6}\b|\b0\d{2,3}\s\d{3,4}\s\d{4}\b"
                r"|\b[89]\d[\s.-]\d{3}[\s.-]\d{2}[\s.-]\d{2}\b"), "[phone]"),
    (re.compile(r"\b[6-9]\d{2}(?:[\s.-]\d{3}[\s.-]\d{3}|[\s.-]\d{2}[\s.-]\d{2}[\s.-]\d{2})\b"),
     "[phone]"),
    (re.compile(rf"\b[\w-]+(?:\.[\w-]+)*(?<!Dockerfile)(?<!compose)\.{TLDS}\b(?!\()",
                re.IGNORECASE), "[domain]"),
    (re.compile(rf"\b[\w-]+(?:\s+dot\s+[\w-]+)*\s+dot\s+{TLDS}\b", re.IGNORECASE), "[domain]"),
    (re.compile(r"\b[\w-]+(?:\s?(?:\[\.\]|\(\.\)|\[dot\]|\(dot\))\s?[\w-]+)+", re.IGNORECASE),
     "[domain]"),
    (re.compile(rf"(?<![\w.])(?!{PRIVATE_IP})(?:{OCTET}\.){{3}}{OCTET}(?![\w]|\.\d)"), "[ip]"),
    (re.compile(rf"(?<![\w:])(?=[\w:]*\d)(?:{HEX}:){{7}}{HEX}(?![\w:])"
                rf"|(?<![\w:])(?<!\w\[)(?=[\w:]*\d){HEX}(?::{HEX}){{0,5}}::(?:{HEX}(?::{HEX}){{0,5}})?(?![\w:])",
                re.IGNORECASE), "[ip]"),
    (re.compile(r"\[domain\](?:/[\w.~%+@=-]*)+"), "[domain]"),
    (re.compile(r"\b\d{7,}[A-Za-z]?\b|\b(?:IE)?\d{7}(?-i:[A-Z]{2})\b|\b[A-Z]\d{7,8}[A-Z]?\b|\b\d{1,2}\.\d{3}\.\d{3}-?[A-Z]\b"
                r"|\b(?:AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|GB|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK)"
                r"[A-Z0-9]{0,2}\d{8,12}[A-Z0-9]{0,3}\b", re.IGNORECASE), "[id]"),
    (re.compile(rf"\b[A-Z][\w'.-]*\s+{STREET_CAP}\.?\s+\d+\w*"
                r"(?:,\s*(?:Flat|Apt|Piso|Floor)\s*\w+)?(?:,\s*\d{4,5}\s+[A-Z]\w+)?"), "[address]"),
    (re.compile(r"\b(?:Calle|Carrer|Avenida|Avinguda|Avda\.?|Paseo|Passeig|Plaza|Pla[cç]a|Camino|Ronda|"
                r"Rambla|Gran\s+V[ií]a|Traves[ií]a|Carretera|Glorieta)\s+"
                r"(?:(?:de|del)\s+(?:la\s+|los\s+|las\s+)?)?[A-ZÁÉÍÓÚÑ][\w'.-]*"
                r"(?:\s+[A-ZÁÉÍÓÚÑ][\w'.-]*){0,2},?\s+(?:n[º°.]?\s*)?(?:\d+|s/n)"
                r"(?:,?\s*\d{1,2}\s*[º°]?\s*[A-Za-z]?\b)?(?:,\s*\d{5}\s+[A-Z]\w+)?"), "[address]"),
    (re.compile(r"\bGran\s+V[ií]a,?\s+(?:n[º°.]?\s*)?\d+(?:,?\s*\d{1,2}\s*[º°]?\s*[A-Za-z]?\b)?",
                re.IGNORECASE), "[address]"),
    (re.compile(r"(?:\b(?:calle|carrer|avenida|avda|av|passeig|rambla|avinguda|gran\s+v[ií]a|traves[ií]a)"
                r"\b\.?|\bc/|\bcl\.)\s*"
                r"(?:(?:de|del)\s+(?:la\s+|los\s+|las\s+)?)?[^\W\d_][\w'.-]*"
                r"(?:\s+[^\W\d_][\w'.-]*){0,2},?\s+(?:n[º°.]?\s*)?(?:\d+|s/n)"
                r"(?:,?\s*\d{1,2}\s*[º°]?\s*[A-Za-z]?\b)?", re.IGNORECASE), "[address]"),
    (re.compile(rf"\b\d{{1,4}}[A-Za-z]?\s+[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){{0,2}}\s+{STREET_CAP}\b\.?"
                r"(?:,\s*[A-Z]\w+)?"), "[address]"),
    (re.compile(r"\b\d{1,4}[A-Za-z]?\s+[A-Za-z][\w'-]*\s+(?:street|avenue|road|boulevard)\b\.?"
                r"(?:,\s*[A-Z]\w+)?", re.IGNORECASE), "[address]"),
    (re.compile(r"\b\d{1,4}(?:\s?(?:bis|ter))?,?\s+(?:rue|avenue|boulevard|bd|impasse|chemin|all[ée]e|quai)\s+"
                r"(?:(?:de|du|des|d')\s*(?:la\s+|l')?)?[\w'-]+(?:\s+[\w'-]+){0,2}", re.IGNORECASE),
     "[address]"),
    (re.compile(r"\b[A-ZÄÖÜ]\w*(?:weg|stra(?:ss|ß)e|str\.|allee|gasse|platz)\s*\d{1,4}[a-z]?\b"
                r"|\b\w+(?:stra(?:ss|ß)e|allee|gasse)\s*\d{1,4}[a-z]?\b", re.IGNORECASE),
     "[address]"),
    (re.compile(r"\bP\.?\s?O\.?\s?Box\s+\d+\b|\bapartado\s+(?:(?:de\s+correos|postal)\s+\d+|\d{3,})\b",
                re.IGNORECASE), "[address]"),
)
PUBLIC_PATTERNS = Path(__file__).resolve().parent / "redaction-patterns.public.txt"
PRIVATE_DEFAULTS = ("~/.config/rigops/redaction-patterns.txt",
                    "~/.config/rigops/redaction-patterns.rigops.txt")


def _rule_lines(files, packed=None) -> tuple:
    lines, unreadable = [], 0
    for path in files:
        try:
            lines += Path(path).expanduser().read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):
            unreadable += 1
    if packed:
        try:
            lines += base64.b64decode(packed).decode("utf-8").splitlines()
        except ValueError:
            unreadable += 1
    return lines, unreadable


# Gate rules run under grep -E; `re` reads a bracket class as a literal set and never matches.
POSIX_CLASSES = {"[:alnum:]": "0-9A-Za-zÀ-ÿ", "[:alpha:]": "A-Za-zÀ-ÿ"}


def _python_pattern(pattern: str) -> str:
    for posix, chars in POSIX_CLASSES.items():
        pattern = pattern.replace(posix, chars)
    return pattern


def _compile_rules(files, packed=None) -> tuple:
    compiled, broken = [], 0
    lines, unreadable = _rule_lines(files, packed)
    for raw in lines:
        if not raw.strip() or raw.startswith("#"):
            continue
        flag, _, pattern = raw.partition(" ")
        if flag not in ("i", "s") or not pattern:
            broken += 1
            continue
        try:
            flags = re.IGNORECASE if flag == "i" else 0
            compiled.append(re.compile(_python_pattern(pattern), flags))
        except re.error:
            broken += 1
    return compiled, broken + unreadable


def default_layers() -> tuple:
    """Rule files and base64 layer that tools/redaction-gate.sh loads, as (files, packed)."""
    packed = os.environ.get("RIGOPS_REDACTION_PATTERNS_B64")
    if packed:
        return [PUBLIC_PATTERNS], packed
    named = os.environ.get("RIGOPS_REDACTION_PATTERNS")
    if named:
        return [PUBLIC_PATTERNS] + [p for p in named.split(":") if p], None
    present = [p for p in PRIVATE_DEFAULTS if Path(p).expanduser().is_file()]
    return [PUBLIC_PATTERNS] + present, None


def _layers(pattern_files) -> tuple:
    return default_layers() if pattern_files is None else (pattern_files, None)


def broken_rules(pattern_files=None) -> int:
    """Rules that redact skips: bad flag, bad regex, or a rule source it cannot read."""
    return _compile_rules(*_layers(pattern_files))[1]


def redact(text: str, pattern_files=None) -> str:
    """Mask secrets, identifiers and everything the redaction rules name."""
    for pattern, mask in GENERIC_RULES:
        text = pattern.sub(mask, text)
    for pattern in _compile_rules(*_layers(pattern_files))[0]:
        text = pattern.sub("[redacted]", text)
    return text
