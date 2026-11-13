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
_RETRACTED_PREFIX = re.compile(r"^\s*(retracted|retraction|withdrawn)\s*[:.\-]\s*", re.I)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$")
ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[a-z]{2})?/\d{7})$")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().replace("’", "'").replace("‘", "'")


def strip_retracted(title: str) -> str:
    return _RETRACTED_PREFIX.sub("", title)


def normalize_title(title: str) -> str:
    t = _fold(strip_retracted(title))
    return " ".join(re.findall(r"\w+", t.replace("'", "")))


def negations(title: str) -> set[str]:
    out = set()
    for tok in re.findall(r"\w+(?:'\w+)?", _fold(title)):
        if tok == "cannot" or tok.endswith("n't"):
            out.add("not")
        elif tok in NEGATIONS:
            out.add(tok)
    return out


def numbers(title: str) -> set[str]:
    return {t for t in re.findall(r"\w+", _fold(title)) if any(c.isdigit() for c in t)}


def title_similarity(a: str, b: str) -> float:
    """Best ratio of the full titles or their main parts (before ':'), so a missing subtitle
    still matches."""
    best = 0.0
    for x in {a, a.split(":")[0]}:
        for y in {b, b.split(":")[0]}:
            nx, ny = normalize_title(x), normalize_title(y)
            if (x != a or y != b) and min(len(nx.split()), len(ny.split())) < 3:
                continue  # a 1-2 word main title alone proves nothing
            if nx and ny:
                best = max(best, SequenceMatcher(None, nx, ny).ratio())
    return best


def title_match(cited: str | None, found: str | None) -> bool:
    if not cited or not found:
        return False
    a, b = strip_retracted(cited), strip_retracted(found)
    if negations(a) != negations(b) or numbers(a) != numbers(b):
        return False
    return title_similarity(a, b) >= TITLE_THRESHOLD


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
    return title_match(ref.title, w.title) and (
        year_ok(ref.year, w.year) or first_author_equal(ref.authors, w.authors)
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
