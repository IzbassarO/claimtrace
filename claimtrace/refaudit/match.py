"""Deterministic reference matching. Pure functions, no I/O, no LLM.

Title normalisation here is for *matching titles only* (accents/ligatures folded via NFKD).
It is never used for verbatim quotes (see AGENTS.md).
"""

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import unquote

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


_GREEK = dict(
    zip(
        "αβγδεζηθικλμνξοπρσςτυφχψω",
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho "
        "sigma sigma tau upsilon phi chi psi omega".split(),
        strict=True,
    )
)


def _tfold(s: str) -> str:
    """_fold for titles only: Greek letters spelled out (TNF-α == TNF-alpha). Never quotes."""
    return "".join(_GREEK.get(c, c) for c in _fold(s))


def strip_retracted(title: str) -> str:
    return RETRACTED_PREFIX.sub("", title)


def normalize_title(title: str) -> str:
    t = _tfold(strip_retracted(title))
    return " ".join(re.findall(r"\w+", t.replace("'", "")))


def is_notice(title: str | None) -> bool:
    return bool(title and _NOTICE_PREFIX.match(title))


def strip_notice(title: str | None) -> str | None:
    return _NOTICE_PREFIX.sub("", title).strip(" :.-–—\"“”'") if title else title


def negations(title: str) -> set[str]:
    out = set()
    toks = re.findall(r"\w+(?:'\w+)?", _tfold(title))
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
    for t in re.findall(r"\w+", _tfold(title)):
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


# function words whose change never alters what a title claims; negations stay content words
_STOP = set(
    "a an the of in on at to for from by with into onto among amongst between within via as and "
    "or but is are was were be been being do does did can could may might will would should has "
    "have had it its their our we this that these those which what when where why how who whose "
    "than vs versus".split()
)


def _canon(w: str) -> str:
    """Fold spelling variants of one folded word: number words, plural -s/-es/-ies, British/
    American (tumour, analyse, modelling, centre, paediatric, defence, sulphur). Explicit
    rewrites, not edit distance: infection/injection or hypotension/hypertension stay apart."""
    w = _NUMBER_WORDS.get(w) or _ROMAN.get(w) or w
    if len(w) > 3:
        w = re.sub(r"ies$", "y", w)
        w = re.sub(r"(?<![siu])s$", "", w)  # -es plurals: the trailing e goes below
    for old, new in (("ae", "e"), ("oe", "e"), ("our", "or"), ("ll", "l"), ("z", "s"), ("ph", "f")):
        w = w.replace(old, new)
    w = re.sub(r"([tb])re$", r"\1er", w)
    w = re.sub(r"ence$", "ense", w)
    w = re.sub(r"ogue$", "og", w)
    return w[:-1] if len(w) > 3 and w.endswith("e") else w


def _content_words(title: str) -> list[str]:
    t = re.sub(r"\bcan'?t\b|\bcannot\b", "can not", _tfold(title))
    t = re.sub(r"n't\b", " not", t).replace("'", "")
    return [_canon(w) for w in re.findall(r"\w+", t) if w not in _STOP]


# belt and braces: the content-word check below already rejects these substitutions
_ANTONYMS = {
    _canon(w)
    for w in "increase increases increased decrease decreases decreased higher lower more less "
    "positive negative improves worsens improve worsen benefit harm benefits harms enhances "
    "reduces enhance reduce up down mice mouse humans human rats rat patients patient".split()
}


def _same_content(x: str, y: str) -> bool:
    """Every content word of one title is in the other, in order; differences may only be
    function words or hyphenation/joining ('non-linear' vs 'nonlinear', 'data set' vs 'dataset')."""
    wx, wy = _content_words(x), _content_words(y)
    if set(wx) & _ANTONYMS != set(wy) & _ANTONYMS:
        return False
    ops = SequenceMatcher(None, wx, wy, autojunk=False).get_opcodes()
    return all("".join(wx[i1:i2]) == "".join(wy[j1:j2]) for _, i1, i2, j1, j2 in ops)


def title_similarity(a: str, b: str) -> float:
    a, b = strip_retracted(a), strip_retracted(b)
    return max(_ratio(x, y, (x, y) == (a, b)) for x, y in _pairs(a, b))


def title_match(cited: str | None, found: str | None) -> bool:
    if not cited or not found:
        return False
    a, b = strip_retracted(cited), strip_retracted(found)
    # negations are compared on the FULL titles: dropping a subtitle never hides "no evidence".
    # ponytail: numbers stay per pair so an omitted "a 10-year study" subtitle still matches
    # (existing must-match case); move numbers() up here if that should become a mismatch
    if is_notice(a) != is_notice(b) or negations(a) != negations(b):
        return False
    return any(
        numbers(x) == numbers(y)
        and _same_content(x, y)
        and _ratio(x, y, (x, y) == (a, b)) >= TITLE_THRESHOLD
        for x, y in _pairs(a, b)
    )


def word_coverage(cited: str, found: str) -> float:
    """Share of the cited title's content words (>= 4 of them) that the found title has: a
    reworded citation of the same title ('Theano: a CPU and GPU math expression compiler' vs
    'Theano: A CPU and GPU Math Compiler in Python')."""
    wc, wf = _content_words(cited), set(_content_words(found))
    return sum(w in wf for w in wc) / len(wc) if len(wc) >= 4 else 0.0


# a venue glued onto the title by GROBID / the LLM: ". In ICML'13", ". In Proceedings of ...",
# ". Deep Learning and Unsupervised Feature Learning NIPS 2012 Workshop", ". J. Mach. Learn. Res."
_VENUE_TAIL = re.compile(
    r"(?<=[.?!])\s+(?:In\s+[A-Z]|(?:J|Proc|Trans|Ann|Rev|Phys)\.\s"
    r"|[^.?!]*\b(?:Workshop|Conference|Symposium|Proceedings)\b).*$"
)


# a full stop after these ends an abbreviation, not the title ('the U.S. FDA Workshop')
_ABBREV = {"inc", "ltd", "corp", "vs", "etc", "al", "fig", "vol", "dept", "univ", "natl", "intl"}


def strip_venue(title: str | None) -> str | None:
    """The title without a trailing venue segment; for search and matching only, never shown.
    Unchanged unless the head keeps >= 3 words, the cut follows a word of >= 3 letters that
    is no abbreviation ('U.S.', 'St.', 'E. coli'), and the tail has no negation ('. In girls,
    no such effect')."""
    m = _VENUE_TAIL.search(title or "")
    if not m:
        return title
    head, tail = title[: m.start()], title[m.start() :]
    last = (re.findall(r"\w+", head) or [""])[-1]
    if len(last) < 3 or last.casefold() in _ABBREV or negations(tail):
        return title
    head = head.rstrip(" .")
    return head if len(head.split()) >= 3 else title


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


_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\u2060\ufeff]")  # soft hyphen, zero-width, BOM


def norm_doi(doi: str | None) -> str | None:
    """Lowercase bare DOI. Percent-decoded once, invisible chars dropped. A whitespace-separated
    DOI list (arXiv's article + erratum) normalises to its first DOI; see `all_dois`."""
    return next(iter(all_dois(doi)), None)


def all_dois(doi: str | None) -> list[str]:
    d = _INVISIBLE.sub("", unquote(doi or "")).strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return [t for t in (t.rstrip(".,;") for t in d.split()) if t]


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


def arxiv_of(w: Work) -> str | None:
    return norm_arxiv(w.arxiv_id) or arxiv_from_doi(w.doi)


_NOT_A_NAME = {"", "et", "al", "others"}


def missing_author(cited: list[str], found: list[str]) -> str | None:
    """The first cited author not confirmed by `found`: the first author must be equal, every
    co-author's family name must appear among the found names. A found list shorter than the
    cited one may be truncated (big consortia): only that many cited authors are compared."""
    if not first_author_equal(cited, found):
        return cited[0] if cited else None
    names = {t for a in found for t in re.findall(r"\w+", _fold(a))}
    return next(
        (a for a in cited[1 : len(found)] if family_name(a) not in _NOT_A_NAME | names),
        None,
    )


# a title as exactly one whole field of a raw reference: after start / ". " / ") " / "). " /
# an opening quote (not ": ", which starts a subtitle, nor a bare year), before end / "." /
# a closing quote (IEEE '"Title," Journal'); "?" / "!" only when the field ends there too
_FIELD_START = r"(?:^|[.)]\s+|[\"'“”]\s*)"
_FIELD_END = r"(?:[.?!]?(?:$|[\"'“”])|\.(?=\s)|[?!](?=\.\s)|,[\"'“”])"


def _in_raw(raw: str, title: str) -> bool:
    """The found title (>= 3 words) is a whole field of the raw reference string, with no
    negation word right before it, and the raw string has no negation the title lacks ('No
    evidence that <title>'). An issue number 'No. 3' also counts: that only leaves the title
    unchecked. A sub-span of a longer cited title is not a field."""
    words = re.findall(r"\w+", _tfold(strip_retracted(title)))
    if len(words) < 3 or negations(raw) != negations(title):
        return False
    r = _tfold(raw).strip()
    pat = _FIELD_START + r"[\W_]+".join(map(re.escape, words)) + r"\s*" + _FIELD_END
    return any(not negations(" ".join(r[: m.start()].split()[-1:])) for m in re.finditer(pat, r))


def compare(ref: Reference, works: list[Work]) -> tuple[list[Mismatch], list[str]]:
    """Check a citation against the records of one work. A field is checked when some record
    has it (a missing field is never an advantage) and mismatches when no record agrees.
    Returns (mismatches, labels of the checked fields)."""
    out: list[Mismatch] = []
    checked: list[str] = []

    def check(label, has, ok, cited, found):
        rs = [w for w in works if has(w)]
        if rs:
            checked.append(label)
            if not any(ok(w) for w in rs):
                out.append(Mismatch(field=label.split()[0], cited=cited, found=found(rs[0])))

    d1 = norm_doi(ref.doi)
    a_doi = arxiv_from_doi(d1)
    if d1:
        # an arXiv-DOI record next to a cited journal DOI is a version, not a contradiction;
        # a cited arXiv DOI is fine only if some record of this work carries that arXiv id
        check(
            "doi",
            lambda w: (
                (norm_doi(w.doi) and (a_doi or not arxiv_from_doi(w.doi)))
                or (a_doi and arxiv_of(w))
            ),
            lambda w: norm_doi(w.doi) == d1 or bool(a_doi and arxiv_of(w) == a_doi),
            ref.doi or d1,
            lambda w: w.doi or f"arXiv:{arxiv_of(w)}",
        )
    if a1 := norm_arxiv(ref.arxiv_id):
        check("arxiv_id", arxiv_of, lambda w: arxiv_of(w) == a1, ref.arxiv_id or a1, arxiv_of)
    if ref.title:
        check(
            "title",
            lambda w: w.title,
            lambda w: title_match(ref.title, w.title),
            ref.title,
            lambda w: w.title,
        )
    elif ref.raw and any(w.title and _in_raw(ref.raw, w.title) for w in works):
        checked.append("title (in raw reference)")  # found title stated verbatim in the raw
    if ref.authors and (fa := max((w.authors for w in works), key=len, default=[])):
        # the most complete list: a record listing fewer authors never hides a co-author
        checked.append(f"authors ({len(ref.authors)})")
        if miss := missing_author(ref.authors, fa):
            found = fa[0] if not first_author_equal(ref.authors, fa) else ", ".join(fa[:6])
            out.append(Mismatch(field="authors", cited=miss, found=found))
    if ref.year is not None:
        check(
            "year",
            lambda w: w.year is not None,
            lambda w: year_ok(ref.year, w.year),
            str(ref.year),
            lambda w: str(w.year),
        )
    order = ["title", "authors", "year", "doi", "arxiv_id"]
    out.sort(key=lambda m: order.index(m.field))
    return out, checked


def mismatches(ref: Reference, w: Work) -> list[Mismatch]:
    return compare(ref, [w])[0]
