from __future__ import annotations

import base64
import os
import re
from pathlib import Path

PAY = (r"(?:salary|salario|sueldo|pay|paid|paga|pagan|pago|bruto|neto|gross|offer|oferta|bonus|"
       r"earns?|earning|making|makes?|ganar|gano|gana|ganas|cobro|cobrar|cobra|euros?|dollars?)")
FILLER = (r"(?:is|was|are|of|around|about|at|over|approx|aprox|de|es|son|unos|to|"
          r"up\s+to|more\s+than|mas\s+de)")
UNITS = (r"(?:tokens?|rows?|filas?|lines?|l[ií]neas|files?|archivos|requests?|calls?|bytes?|"
         r"ms|secs?|seconds?|mins?|minutes?|timeouts?|users?|items?|records?|tests?)")
SUM = (r"(?:\d{1,3}(?:[.,]\d{3})+|\d[\d.,]*[kKmM]|\d+\s?(?:mil|thousand|million|millones)"
       r"|\d{4,})")
TLDS = (r"(?:co\.uk|com|net|org|io|cloud|eu|es|cy|ai|uk|co|xyz|de|fr|tech|me|"
        r"(?:dev|app)(?!\.\w))")
GENERIC_RULES = (
    (re.compile(r"\bBearer\s+\S+", re.IGNORECASE), "[token]"),
    (re.compile(r"\b(?:ghs|github_pat|glpat)[-_A-Za-z0-9]{8,}"), "[key]"),
    (re.compile(r"-Users-[A-Za-z0-9-]+"), "[path]"),
    (re.compile(r"(?:/private|/var|/root|/etc|/opt|/tmp)/\S*"), "[path]"),
    (re.compile(r"\bwww\.[\w.-]+"), "[url]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"https?://\S+"), "[url]"),
    (re.compile(r"(?:/Users|/home)/[^/\s]+(?:/\S*)?"), "[path]"),
    (re.compile(r"\$\{?HOME\}?(?:/\S*)?"), "[path]"),
    (re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s]+(?:\\\S*)?"), "[path]"),
    (re.compile(r"\b(?:sk|ghp|gho|xox[a-z]|AKIA)[-_A-Za-z0-9]{12,}"), "[key]"),
    (re.compile(r"\b(?!(?:UTF|GPT|SHA|ISO|TLS|SSL|UTC|GMT|MD)-\d)(?=[a-z]*[A-Z])[A-Za-z]{2,10}"
                r"-\d{1,6}\b|\b[a-z]{2,10}-\d{3,6}\b"), "[ticket]"),
    (re.compile(r"\b[0-9a-fA-F]{24,}\b"), "[hex]"),
    (re.compile(r"~/(?!\.[a-z]+/projects/\[path\]/)\S*"), "[path]"),
    (re.compile(r"[€$£]\s?\d[\d.,]*[kKmM]?|\b\d[\d.,]*(?:\s?(?:mil|thousand|million|millones)|\s?[kKmM])?"
                r"\s?(?:€|euros?|EUR|USD|GBP|dollars?)(?!\w)", re.IGNORECASE), "[amount]"),
    (re.compile(rf"(\b{PAY}\b[:=]?\s*(?:(?:{FILLER})\s+){{0,2}})(?!(?:19|20)\d\d\b){SUM}(?!\w)"
                rf"(?!\s*{UNITS}\b)", re.IGNORECASE), r"\1[amount]"),
    (re.compile(rf"\b\d[\d.,]*[kKmM]\b(?!\s*{UNITS}\b)(?=[^.\n]{{0,20}}?\b{PAY}\b)",
                re.IGNORECASE), "[amount]"),
    (re.compile(r"\b\d[\d.,]*\s?[kKmM]\b(?=\s*(?:a|per|al|/)\s*(?:year|yr|month|mo|mes|año|ano)\b)",
                re.IGNORECASE), "[amount]"),
    (re.compile(r"\b[a-z]{2}\d{2}(?:\s?(?=[a-z0-9]{0,3}\d)[a-z0-9]{4}){2}(?:\s?[a-z0-9]{4}){0,5}"
                r"(?:\s?[a-z0-9]{1,3})?\b", re.IGNORECASE), "[iban]"),
    (re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\d{2,4}){3,5}\b"), "[phone]"),
    (re.compile(r"(?<!\w)\(\d{3}\)\s?\d{3}[\s.-]?\d{4}\b"
                r"|(?<![\w.])\d{3}[\s.-]\d{3}[\s.-]\d{4}(?![\w.])"), "[phone]"),
    (re.compile(r"\b[6-9]\d{2}(?:[\s.-]\d{3}[\s.-]\d{3}|[\s.-]\d{2}[\s.-]\d{2}[\s.-]\d{2})\b"),
     "[phone]"),
    (re.compile(rf"\b[\w-]+(?:\.[\w-]+)*(?<!Dockerfile)(?<!compose)\.{TLDS}\b(?!\()",
                re.IGNORECASE), "[domain]"),
    (re.compile(rf"\b[\w-]+(?:\s+dot\s+[\w-]+)*\s+dot\s+{TLDS}\b", re.IGNORECASE), "[domain]"),
    (re.compile(r"\[domain\](?:/[\w.~%+@=-]*)+"), "[domain]"),
    (re.compile(r"\b\d{7,}[A-Za-z]?\b|\b[XYZ]\d{7}[A-Z]\b", re.IGNORECASE), "[id]"),
    (re.compile(r"\b[A-Z][\w'.-]*\s+(?:Str|Street|St|Ave|Avenue|Rd|Road|Calle|Carrer)\.?\s+\d+\w*"
                r"(?:,\s*(?:Flat|Apt|Piso|Floor)\s*\w+)?(?:,\s*\d{4,5}\s+[A-Z]\w+)?"), "[address]"),
    (re.compile(r"\b(?:Calle|Carrer|Avenida|Avda\.?|Paseo|Plaza|Camino|Ronda)\s+"
                r"(?:(?:de|del)\s+(?:la\s+|los\s+|las\s+)?)?[A-ZÁÉÍÓÚÑ][\w'.-]*"
                r"(?:\s+[A-ZÁÉÍÓÚÑ][\w'.-]*){0,2},?\s+(?:n[º°.]?\s*)?\d+"
                r"(?:,?\s*\d{1,2}\s*[º°]?\s*[A-Za-z]?\b)?(?:,\s*\d{5}\s+[A-Z]\w+)?"), "[address]"),
    (re.compile(r"(?:\b(?:calle|carrer|avenida|avda|av)\b\.?|\bc/|\bcl\.)\s*"
                r"(?:(?:de|del)\s+(?:la\s+|los\s+|las\s+)?)?[^\W\d_][\w'.-]*"
                r"(?:\s+[^\W\d_][\w'.-]*){0,2},?\s+(?:n[º°.]?\s*)?\d+"
                r"(?:,?\s*\d{1,2}\s*[º°]?\s*[A-Za-z]?\b)?", re.IGNORECASE), "[address]"),
    (re.compile(r"\b\d{1,4}[A-Za-z]?\s+[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2}\s+"
                r"(?:Str|Street|St|Ave|Avenue|Rd|Road|Lane|Ln|Drive|Dr|Blvd|Boulevard)\b\.?"
                r"(?:,\s*[A-Z]\w+)?"), "[address]"),
)
PUBLIC_PATTERNS = (Path(__file__).resolve().parent.parent.parent / "tools"
                   / "redaction-patterns.public.txt")
PRIVATE_PATTERNS = "~/.config/rigops/redaction-patterns.txt"


def _rule_lines(files) -> tuple:
    lines, unreadable = [], 0
    for path in files:
        try:
            lines += Path(path).expanduser().read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):
            unreadable += 1
    packed = os.environ.get("RIGOPS_REDACTION_PATTERNS_B64")
    if packed:
        try:
            lines += base64.b64decode(packed).decode("utf-8").splitlines()
        except ValueError:
            unreadable += 1
    return lines, unreadable


def _compile_rules(files) -> tuple:
    compiled, broken = [], 0
    lines, unreadable = _rule_lines(files)
    for raw in lines:
        if not raw.strip() or raw.startswith("#"):
            continue
        flag, _, pattern = raw.partition(" ")
        if flag not in ("i", "s") or not pattern:
            broken += 1
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE if flag == "i" else 0))
        except re.error:
            broken += 1
    return compiled, broken + unreadable


def _rule_patterns(files) -> list:
    return _compile_rules(files)[0]


def default_files() -> list:
    return [PUBLIC_PATTERNS, os.environ.get("RIGOPS_REDACTION_PATTERNS") or PRIVATE_PATTERNS]


def broken_rules(pattern_files=None) -> int:
    """Rules that redact skips: bad flag, bad regex, or a rule source it cannot read."""
    return _compile_rules(default_files() if pattern_files is None else pattern_files)[1]


def redact(text: str, registry=None, pattern_files=None) -> str:
    """Mask secrets, identifiers and everything the repo's redaction rules or the registry name."""
    for pattern, mask in GENERIC_RULES:
        text = pattern.sub(mask, text)
    if pattern_files is None:
        pattern_files = default_files()
    for pattern in _rule_patterns(pattern_files):
        text = pattern.sub("[redacted]", text)
    if registry is not None:
        for token in registry.client_tokens:
            text = re.sub(re.escape(token), "[client]", text, flags=re.IGNORECASE)
    return text
