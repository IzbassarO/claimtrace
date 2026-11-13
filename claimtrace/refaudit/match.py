"""Deterministic reference matching. Pure functions, no I/O, no LLM.

Title normalisation here is for *matching titles only* (accents/ligatures folded via NFKD).
It is never used for verbatim quotes (see AGENTS.md).
"""

import re
import unicodedata
from difflib import SequenceMatcher

from claimtrace.core.schemas import Mismatch, Reference, Work

TITLE_THRESHOLD = 0.9
NEGATIONS = {"not", "no", "without", "cannot", "never", "non", "nor", "none", "neither"}
# "RETRACTED ARTICLE: ", "Retraction Note: ", "WITHDRAWN: ", "Retracted - ", ...
RETRACTED_PREFIX = re.compile(
    r"^\s*(retracted(\s+article)?|retraction(\s+(note|notice))?(\s+to)?|notice\s+of\s+retraction"
    r"|withdrawn(\s+article)?)\s*[:.\-–—]\s*",
    re.I,
)
# a correction notice is a different record from the paper it corrects
_NOTICE_PREFIX = re.compile(
    r"^\s*((author|publisher)\s+)?(correction|erratum|corrigendum|addendum)s?\b\s*(to\b|[:.\-–—])",
    re.I,
)
# words that flip a title's meaning beyond plain not/no: "lack of", "absence of", "fail to"
_NEG_LACK = {"lack", "lacks", "lacking", "absence", "absent"}
_NEG_FAIL = {"fail", "fails", "failed", "failing"}
_NUMBER_WORDS = {
    w: str(i)
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
_ROMAN = {
    r: str(i)
    for i, r in enumerate(
        "i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx".split(), 1
    )
    if r != "x"  # "X-ray" far more often than ten
}
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$")
ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[a-z]{2})?/\d{7})$")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().replace("’", "'").replace("‘", "'")


def strip_retracted(title: str) -> str:
    return RETRACTED_PREFIX.sub("", title)


def normalize_title(title: str) -> str:
    t = _fold(strip_retracted(title))
    return " ".join(re.findall(r"\w+", t.replace("'", "")))


def is_notice(title: str | None) -> bool:
    return bool(title and _NOTICE_PREFIX.match(title))


def strip_notice(title: str | None) -> str | None:
    return _NOTICE_PREFIX.sub("", title).strip(" :.-–—\"“”'") if title else title


def negations(title: str) -> set[str]:
    out = set()
    toks = re.findall(r"\w+(?:'\w+)?", _fold(title))
    for tok, nxt in zip(toks, toks[1:] + [""], strict=True):
        if tok == "cannot" or tok.endswith("n't"):
            out.add("not")
        elif tok in NEGATIONS:
            out.add(tok)
        elif tok.startswith("non") and len(tok) > 5:  # nonsignificant == non-significant
            out.add("non")
        elif tok in _NEG_LACK:
            out.add("lack")
        elif tok in _NEG_FAIL and nxt == "to":
            out.add("fail")
    return out


def numbers(title: str) -> set[str]:
    out = set()
    for t in re.findall(r"\w+", _fold(title)):
        if any(c.isdigit() for c in t):
            out.add(t)
        elif n := _NUMBER_WORDS.get(t) or _ROMAN.get(t):
            out.add(n)
    return out


def _pairs(a: str, b: str):
    """Title pairs to compare: the full titles, and a side's main part (before ':') when only
    the other side lacks a subtitle. Two different subtitles never reduce to equal mains."""
    yield a, b
    if ":" in a and ":" not in b:
        yield a.split(":")[0], b
    if ":" in b and ":" not in a:
        yield a, b.split(":")[0]


def _ratio(x: str, y: str, full: bool) -> float:
    nx, ny = normalize_title(x), normalize_title(y)
    if not (nx and ny) or (not full and min(len(nx.split()), len(ny.split())) < 3):
        return 0.0  # a 1-2 word main title alone proves nothing
    return SequenceMatcher(None, nx, ny).ratio()


def title_similarity(a: str, b: str) -> float:
    a, b = strip_retracted(a), strip_retracted(b)
    return max(_ratio(x, y, (x, y) == (a, b)) for x, y in _pairs(a, b))


def title_match(cited: str | None, found: str | None) -> bool:
    if not cited or not found:
        return False
    a, b = strip_retracted(cited), strip_retracted(found)
    if is_notice(a) != is_notice(b):
        return False
    return any(
        negations(x) == negations(y)
        and numbers(x) == numbers(y)
        and _ratio(x, y, (x, y) == (a, b)) >= TITLE_THRESHOLD
        for x, y in _pairs(a, b)
    )


def title_prefix(a: str | None, b: str | None) -> bool:
    """One title's words are a prefix of the other's ('A decomposable attention model' vs '...
    for natural language inference'), with >= 3 words on the shorter side."""
    if not a or not b:
        return False
    wa, wb = normalize_title(a).split(), normalize_title(b).split()
    n = min(len(wa), len(wb))
    return n >= 3 and wa[:n] == wb[:n]


def truncated_title(cited: str | None, found: str | None) -> bool:
    """A truncated citation of the found title: prefix relation, and the added words bring no
    negation, number or correction-notice marker."""
    if not title_prefix(cited, found):
        return False
    a, b = strip_retracted(cited), strip_retracted(found)
    return (
        is_notice(a) == is_notice(b) and negations(a) == negations(b) and numbers(a) == numbers(b)
    )


def year_ok(cited: int | None, found: int | None) -> bool:
    return cited is not None and found is not None and abs(cited - found) <= 1


def family_name(author: str) -> str:
    """'Family, Given' | 'Given Family' | 'Family GA' (Vancouver) -> folded family name."""
    a = author.strip()
    if "," in a:
        fam = a.split(",")[0]
    else:
        toks = a.split()
        # drop initials: 'J.', 'JA', 'A.B.' (Vancouver puts them after the family name)
        names = [t for t in toks if not (t.endswith(".") or (t.isupper() and len(t) <= 3))]
        fam = (names or toks or [""])[-1]
    words = re.findall(r"\w+", _fold(fam))
    return words[-1] if words else ""


def first_author_equal(cited: list[str], found: list[str]) -> bool:
    return bool(cited and found) and family_name(cited[0]) == family_name(found[0]) != ""


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.rstrip(".,;") or None


def norm_arxiv(aid: str | None) -> str | None:
    if not aid:
        return None
    a = aid.strip().lower()
    a = re.sub(r"^(https?://)?(www\.)?arxiv\.org/(abs|pdf)/", "", a)
    a = re.sub(r"^arxiv:\s*", "", a)
    a = re.sub(r"(\.pdf)?$", "", a)
    return re.sub(r"v\d+$", "", a) or None


def arxiv_from_doi(doi: str | None) -> str | None:
    m = _ARXIV_DOI.match(norm_doi(doi) or "")
    return norm_arxiv(m.group(1)) if m else None


def id_equal(ref: Reference, w: Work) -> bool:
    d1, d2 = norm_doi(ref.doi), norm_doi(w.doi)
    a1 = norm_arxiv(ref.arxiv_id) or arxiv_from_doi(ref.doi)
    a2 = norm_arxiv(w.arxiv_id) or arxiv_from_doi(w.doi)
    return bool(d1 and d1 == d2) or bool(a1 and a1 == a2)


def identifies(ref: Reference, w: Work) -> bool:
    if id_equal(ref, w):
        return True
    if title_match(ref.title, w.title):
        return year_ok(ref.year, w.year) or first_author_equal(ref.authors, w.authors)
    # a truncated title needs both year and first author; mismatches() still flags the title
    return (
        truncated_title(ref.title, w.title)
        and year_ok(ref.year, w.year)
        and first_author_equal(ref.authors, w.authors)
    )


def mismatches(ref: Reference, w: Work) -> list[Mismatch]:
    out = []
    if ref.title and w.title and not title_match(ref.title, w.title):
        out.append(Mismatch(field="title", cited=ref.title, found=w.title))
    if ref.authors and w.authors and not first_author_equal(ref.authors, w.authors):
        out.append(Mismatch(field="authors", cited=ref.authors[0], found=w.authors[0]))
    if ref.year is not None and w.year is not None and not year_ok(ref.year, w.year):
        out.append(Mismatch(field="year", cited=str(ref.year), found=str(w.year)))
    d1, d2 = norm_doi(ref.doi), norm_doi(w.doi)
    # a preprint's arXiv DOI next to a journal DOI is a version difference, not an error
    if d1 and d2 and d1 != d2 and not (arxiv_from_doi(d1) or arxiv_from_doi(d2)):
        out.append(Mismatch(field="doi", cited=ref.doi or d1, found=w.doi or d2))
    a1, a2 = norm_arxiv(ref.arxiv_id), norm_arxiv(w.arxiv_id)
    if a1 and a2 and a1 != a2:
        out.append(Mismatch(field="arxiv_id", cited=ref.arxiv_id or a1, found=w.arxiv_id or a2))
    return out
