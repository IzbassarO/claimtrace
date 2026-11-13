"""Verbatim quote gate: the product's core guarantee (AGENTS.md).

normalize() may only collapse whitespace, drop soft hyphens / zero-width chars, straighten
quotes and lowercase. No NFKC/NFKD: digits, signs, sub/superscripts and indices stay as they are.
A quote passes only if it has >= 5 words, no ellipsis, and occurs in the source at token
boundaries (it may not start or end in the middle of a word or number). A word hyphenated
across a line break ("un-\nchanged") counts as one word for the boundary check only; the text
itself is never rejoined.
"""

import re

MIN_WORDS = 5

_INV = "\u00ad\u200b\u200c\u200d\u2060\ufeff"
_INVISIBLE = dict.fromkeys(map(ord, _INV))
_QUOTES = str.maketrans(
    {c: "'" for c in "\u2018\u2019\u201a\u201b"} | {c: '"' for c in "\u201c\u201d\u201e\u201f"}
)
_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.\s*\.\s*\.|\u2026")

# Characters that glue two alphanumeric runs into one token: "1.5", "mg/kg", "didn't", "5-fold".
_JOIN = set(".,/:^_'~-\u2010\u2011\u2012\u2013\u2212\u00d7\u00b7\u2044")
# Characters that change the meaning of a number they precede: "-5", "<0.05", "±2", "10—20".
_SIGN = set("+-\u2212\u00b1<>=\u2264\u2265~\u2248\u2014")
# Characters that change the meaning of a number they follow: "50%", "10⁻³", "5×", "10—20".
_UNIT = set("%\u2030\u207a\u207b\u207c\u207d\u207e\u208a\u208b\u00d7\u2014")
# Whitespace run after a line-end hyphen: replaced by _BREAK in a same-length copy of the source.
_LINE_BREAK = re.compile(rf"(?<=[-\u2010\u2011\u00ad])[\s{_INV}]*\n[\s{_INV}]*")
_BREAK = "\x00"


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
    if c.isdigit() and p1 == " " and p2 in _SIGN:  # "− 5"
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
    # Same positions as src, but line-break hyphenation gaps are _BREAK (inside a word).
    marked = normalize(_LINE_BREAK.sub(_BREAK, source))
    i = src.find(q)
    while i != -1:
        j = i + len(q)
        inside_word = _BREAK in (marked[i - 1 : i], marked[j : j + 1])
        if not inside_word and _start_ok(q, src, i) and _end_ok(q, src, j):
            return True
        i = src.find(q, i + 1)
    return False
