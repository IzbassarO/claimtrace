"""Verbatim quote gate: the product's core guarantee (AGENTS.md).

normalize() may only collapse whitespace, drop soft hyphens / zero-width chars, straighten
quotes and lowercase. No NFKC/NFKD: digits, signs, sub/superscripts and indices stay as they are.
A quote passes only if it has >= 5 words, no ellipsis, and occurs in the source at token
boundaries (it may not start or end in the middle of a word or number).
"""

import re

MIN_WORDS = 5

_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"))
_QUOTES = str.maketrans({c: "'" for c in "‘’‚‛"} | {c: '"' for c in "“”„‟"})
_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.\s*\.\s*\.|…")

# Characters that glue two alphanumeric runs into one token: "1.5", "mg/kg", "didn't", "5-fold".
_JOIN = set(".,/:^_'-‐‑–−×·")
# Characters that change the meaning of a number they precede: "-5", "<0.05", "±2".
_SIGN = set("+-−±<>=≤≥~≈")
# Characters that change the meaning of a number they follow: "50%", "10⁻³".
_UNIT = set("%‰⁺⁻⁼⁽⁾₊₋")


def normalize(text: str) -> str:
    text = text.translate(_INVISIBLE).translate(_QUOTES)
    return _WS.sub(" ", text).strip().lower()


def word_count(text: str) -> int:
    return sum(1 for tok in text.split() if any(c.isalnum() for c in tok))


def _start_ok(q: str, src: str, i: int) -> bool:
    c, p1, p2 = q[0], src[i - 1 : i], src[i - 2 : i - 1]
    if c.isalnum() and p1.isalnum():
        return False
    if c.isalnum() and p1 in _JOIN and p1 and p2.isalnum():
        return False
    if c.isdigit() and p1 and p1 in _SIGN | _JOIN:
        return False
    return not (c in _SIGN | _JOIN and p1.isalnum())


def _end_ok(q: str, src: str, j: int) -> bool:
    c, a1, a2 = q[-1], src[j : j + 1], src[j + 1 : j + 2]
    if c.isalnum() and a1.isalnum():
        return False
    if c.isalnum() and a1 and a1 in _JOIN and a2.isalnum():
        return False
    if c.isdigit() and a1 and a1 in _UNIT:
        return False
    return not (c in _JOIN and a1.isalnum())


def verify_quote(quote: str, source: str) -> bool:
    """True only if `quote` (>= 5 words, no ellipsis) occurs verbatim in `source`."""
    q = normalize(quote).strip('"').strip()
    if not q or _ELLIPSIS.search(q) or word_count(q) < MIN_WORDS:
        return False
    src = normalize(source)
    i = src.find(q)
    while i != -1:
        if _start_ok(q, src, i) and _end_ok(q, src, i + len(q)):
            return True
        i = src.find(q, i + 1)
    return False
