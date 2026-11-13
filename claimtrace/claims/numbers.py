"""Number check: a `supported` verdict needs the claim's numbers in the verified quote.

Deterministic. Numbers are taken from the citing sentence after dropping citation markers,
author-year citations and figure/table/section refs. Digits glued to letters (COVID-19, IL-6,
CD4, H1N1, 3-kinase, 5-HT), ratios/lists (1/2, 3,4-), bare years and bare integers after an id
word or acronym ("type 1", "grade 2", "MEK 1") are ignored. When unsure a token is ignored:
we cap only on clear numbers. Values are compared by magnitude (sign and comparator are the
skeptic's job), so 10 != 110 and 0.05 != 0.5, while 50% == 50 percent and 1.5-fold == 1.5 fold.
"""

import re
from decimal import Decimal

_SKIP_SPANS = re.compile(
    r"\[[^\]]*\]"  # [12], [3-5]
    r"|\([^()]*[A-Z][^()]*\b(?:1[89]|20)\d\d[a-z]?\)"  # (Smith, 2020), (Lee et al. 2019; ...)
    r"|\b[A-Z][\w'-]+(?: et al\.?)?,? (?:1[89]|20)\d\d[a-z]?\b"  # Smith et al. 2020
    r"|(?i:\b(?:fig(?:ure)?|table|tab|section|sec|eq|equation|appendix|chapter|refs?)s?\.?"
    r"\s*S?\d+[a-z]?)"  # Fig. 2, Table 3, Sections 4
)
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_LETTERS = re.compile(r"-?([^\W\d_]+)")
_GLUE = set(".,/:")
_HYPHENS = set("-\u2010\u2011")
UNITS = set(
    "percent fold times x mg g kg µg μg ug ng pg ml l dl µl μl mmhg mmol µmol μmol nmol mol "
    "mm µm μm nm cm m km iu kda bp kb hz year years yr yrs y month months mo week weeks wk day "
    "days d hour hours h hr hrs min minute minutes s sec ms".split()
)
_ID_WORDS = set(
    "type types stage grade phase class step chromosome exon codon group arm cohort wave "
    "version study trial experiment line".split()
)
_WORDS = {
    w: i
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
_WORD_NUM = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.I)


def _tokens(text: str, strict: bool) -> list[tuple[str, Decimal]]:
    """-> [(as written, value)]. strict=True adds the claim-side filters (years, id words)."""
    out = []
    for m in _NUM.finditer(text):
        s, e, raw = m.start(), m.end(), m.group()
        p1, p2 = text[s - 1 : s], text[s - 2 : s - 1]
        n1, n2 = text[e : e + 1], text[e + 1 : e + 2]
        if p1.isalnum() or p1 == "." or (p1 in _GLUE and p2.isdigit()):
            continue  # CD4, 1.2.3, 1/2
        if p1 in _HYPHENS and p2.isalpha():
            continue  # COVID-19, IL-6
        if n1 in _GLUE and n2.isdigit():
            continue  # 3,4-biphosphate, 1/2, 1:1000
        unit = _LETTERS.match(text, e)
        if unit and (unit.group().startswith("-") or n1.isalpha()):
            if unit.group(1).casefold() not in UNITS:
                continue  # 4T1, 3-kinase, 5-HT
        else:
            unit = None
        if strict and not unit and "." not in raw:
            if re.fullmatch(r"(?:1[89]|20)\d\d", raw) and n1 != "%":
                continue  # a year used as a date
            prev = re.search(r"(\S+)\s+$", text[:s])
            word = prev.group(1) if prev else ""
            if word.casefold() in _ID_WORDS or sum(c.isupper() for c in word) >= 2:
                continue  # type 1, grade 2, MEK 1, HPV 16
        shown = raw + (unit.group() if unit else "%" if n1 == "%" else "")
        out.append((shown, Decimal(raw.replace(",", ""))))
    return out


def claim_numbers(sentence: str) -> list[tuple[str, Decimal]]:
    return _tokens(_SKIP_SPANS.sub(" ", sentence), strict=True)


def quote_values(quote: str) -> set[Decimal]:
    words = {Decimal(_WORDS[w.casefold()]) for w in _WORD_NUM.findall(quote)}
    return {v for _, v in _tokens(quote, strict=False)} | words


def cap_numbers(verdict: str, quote: str | None, sentence: str) -> tuple[str, str | None]:
    """supported -> partly_supported if a claim number is missing from the quote. -> (v, note)."""
    if verdict != "supported" or not quote:
        return verdict, None
    have = quote_values(quote)
    missing = list(dict.fromkeys(t for t, v in claim_numbers(sentence) if v not in have))
    if not missing:
        return verdict, None
    return "partly_supported", (
        f"number in the claim ({', '.join(missing)}) not found in the quoted evidence"
    )
