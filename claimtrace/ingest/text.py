"""Deterministic text handling: reference section split, regex fields, sentences, citations."""

import re
import unicodedata

from claimtrace.core.schemas import Citation, Reference

# ---------- reference section ----------

_REF_HEADING = re.compile(
    r"^[ \t#*]*(?:\d+\.?[ \t]+)?(?:references|bibliography|literature cited|works cited)"
    r"[ \t*:]*$",
    re.I | re.M,
)
_PREFIX = {
    "bracket": re.compile(r"^[ \t]*\[(\d{1,4})\][ \t]*", re.M),
    "dot": re.compile(r"^[ \t]*(\d{1,4})[.)][ \t]+", re.M),
}


_TRAILER = re.compile(
    r"\n[ \t]*\n[ \t#*]*(?:appendix|appendices|supplementary|supplemental|supporting information)"
    r"\b[^\n]{0,80}(?=\n|$)",
    re.I,
)


def split_manuscript(text: str) -> tuple[str, str | None, str | None]:
    """(body, reference section, warning) split at the LAST reference heading line before any
    appendix / supplement heading that follows the references. A trailing appendix goes back
    into the body; a trailing supplement with its own reference list is dropped (its [n] would
    resolve against the wrong list) and reported in the warning."""
    heads = list(_REF_HEADING.finditer(text))
    if not heads:
        return text, None, None
    trailer = _TRAILER.search(text, heads[0].end())
    end = trailer.start() if trailer else len(text)
    main = [h for h in heads if h.start() < end][-1]
    body, section, tail = text[: main.start()], text[main.end() : end], text[end:]
    if not tail.strip():
        return body, section, None
    if _REF_HEADING.search(tail):
        return body, section, "Text after the reference list has its own references; ignored"
    return body.rstrip() + "\n\n" + tail.strip("\n"), section, None


def guess_title(body: str) -> str | None:
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip() or None
        return line if len(line) <= 200 and line[-1] not in ".:;!?" else None
    return None


def split_entries(section: str) -> list[tuple[str | None, str]]:
    """Reference section -> [(number or None, raw string)].

    Numbered (`[n]`, `n.`, `n)`) when the first entry starts with such a prefix; only prefixes
    carrying the next expected number split, so a wrapped line starting with `12.` stays put.
    Otherwise one entry per blank-line block (if any block spans lines) or per line."""
    section = section.strip("\n")
    for pat in _PREFIX.values():
        first = pat.match(section.lstrip("\n"))
        if not first:
            continue
        starts, expected = [], int(first.group(1))
        for m in pat.finditer(section):
            if int(m.group(1)) == expected:
                starts.append(m)
                expected += 1
        out = []
        for i, m in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(section)
            raw = section[m.end() : end].strip()
            if raw:
                out.append((m.group(1), raw))
        return out
    blocks = [b.strip() for b in re.split(r"\n[ \t]*\n", section) if b.strip()]
    if len(blocks) == 1 or not any("\n" in b for b in blocks):
        blocks = []
        for ln in (ln.strip() for ln in section.splitlines() if ln.strip()):
            if blocks and _CONTINUATION.match(ln):  # hard-wrapped line: never starts an entry
                blocks[-1] += "\n" + ln
            else:
                blocks.append(ln)
    return [(None, b) for b in blocks]


# ---------- regex fields (deterministic) ----------

# SICI DOIs contain <>;:() ; stop at whitespace, straight/smart quotes, ']' (Markdown
# '[doi:...](url)') and HTML tags ('<br>', '</a>'; SICI '<857::...>' has a digit after '<')
_DOI_CHARS = r"""(?:[^\s"\]“”‘’«»<]|<(?![a-z/]))"""
_DOI = re.compile(rf"10\.\d{{4,9}}/{_DOI_CHARS}+", re.I)
# a DOI never ends in '-': PDF hard wrap after a hyphen ('S0140-\n6736...') is joined
_WRAPPED = re.compile(rf"(10\.\d{{4,9}}/{_DOI_CHARS}*-)[ \t]*\r?\n[ \t]*(?=[\w(])", re.I)
_HYPHENS = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2212", "-"))  # PDF hyphens
_IN_URL = re.compile(r"://\S*$")
_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\u2060\ufeff]")  # soft hyphen, zero-width, BOM
_OPEN = {")": "(", "]": "[", ">": "<"}
_ARXIV = re.compile(
    r"(?:arxiv[:\s]\s*|arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.)"
    r"([a-z\-]+(?:\.[a-z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.I,
)
_URL = re.compile(r"https?://\S+", re.I)
_YEAR = re.compile(r"(?<![\w/.-])((?:19|20)\d{2})[a-z]?(?![\w/]|\s*[-–]\s*(?:19|20)\d{2})")
_PAREN_YEAR = re.compile(r"\(((?:19|20)\d{2})[a-z]?\)")
_IMPRINT_YEAR = re.compile(r"(?<![\w/.-])((?:19|20)\d{2})[a-z]?\s*;\s*\d")  # 'Lancet. 2017;390'


def find_doi(raw: str) -> str | None:
    text = _WRAPPED.sub(r"\1", _INVISIBLE.sub("", raw).translate(_HYPHENS))
    m = _DOI.search(text)
    if not m:
        return None
    doi = m.group(0)
    if _IN_URL.search(text[: m.start()]):  # query / fragment of a link is never part of a DOI
        doi = re.split(r"[?#]", doi, maxsplit=1)[0]
    doi = doi.rstrip(".,;:")
    while doi[-1] in _OPEN and doi.count(doi[-1]) > doi.count(_OPEN[doi[-1]]):
        doi = doi[:-1].rstrip(".,;:")
    return doi.lower()


def find_arxiv(raw: str) -> str | None:
    m = _ARXIV.search(raw)
    return m.group(1) if m else None


def year_candidates(raw: str) -> list[int]:
    """Plausible publication years, outside DOIs/URLs/arXiv ids and year ranges. Imprint years
    ('2017;390') first, then parenthesised years, then the rest."""
    clean = _ARXIV.sub(" ", _DOI.sub(" ", _URL.sub(" ", raw)))
    found = [int(y) for p in (_IMPRINT_YEAR, _PAREN_YEAR, _YEAR) for y in p.findall(clean)]
    return list(dict.fromkeys(found))


# ---------- sentences ----------

_ABBREV = {
    "al", "e.g", "i.e", "fig", "figs", "eq", "eqs", "ref", "refs", "vs", "cf", "dr", "mr", "mrs",
    "ms", "no", "nos", "approx", "ca", "resp", "tab", "sect", "sec", "ch", "vol", "pp", "p",
    "et", "etc", "st", "prof", "inc", "ltd", "jr", "sr", "suppl", "viz",
}  # fmt: skip
# numeric markers right after the full stop ('... mortality. [1] Next') stay with their sentence
_BOUNDARY = re.compile(r"[.!?][\"'”’)\]]*(?:\s*\[\d[\d,\s–—-]*\])*\s+(?=[A-Z0-9\"“‘'(\[])")
_TOKEN_BEFORE = re.compile(r"([\w.]+)\.$")


def sentences(text: str) -> list[str]:
    """Split into sentences, verbatim slices of `text` (stripped). Paragraph breaks and markdown
    heading lines always split; `et al.`, `e.g.`, `Fig.`, initials and decimals do not."""
    blocks, cur = [], []
    for para in re.split(r"\n[ \t]*\n", text):
        for line in para.split("\n"):
            if line.lstrip().startswith("#"):
                blocks += ["\n".join(cur), line]
                cur = []
            else:
                cur.append(line)
        blocks.append("\n".join(cur))
        cur = []
    out = []
    for block in blocks:
        start = 0
        for m in _BOUNDARY.finditer(block):
            dot = m.start()
            if block[dot] == ".":
                tok = _TOKEN_BEFORE.search(block[start : dot + 1])
                word = tok.group(1).lower() if tok else ""
                if word in _ABBREV or (len(word) == 1 and word.isalpha()):
                    continue
            s = block[start : m.end()].strip()
            if s:
                out.append(s)
            start = m.end()
        s = block[start:].strip()
        if s:
            out.append(s)
    return out


# ---------- citation markers ----------

_NUMERIC = re.compile(r"\[(\s*\d+(?:\s*[-–—]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–—]\s*\d+)?)*\s*)\]")
_LATEX = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
_PAREN = re.compile(r"\(([^()]*\d{4}[^()]*)\)")
_PARTICLES = r"(?:(?:van|von|de|der|den|di|da|du|del|dos|la|le)\s+)*"
_NAME = _PARTICLES + r"[A-ZÀ-ÖØ-Þ][\w'’\-]+"
# a line starting lowercase (not 'van Dijk') continues the previous reference entry
_CONTINUATION = re.compile(rf"(?!{_PARTICLES}[A-ZÀ-ÖØ-Þ])[a-z]")
_NARRATIVE = re.compile(
    rf"(?<![\w.])(?P<name>{_NAME})(?:\s+et\s+al\.?|(?P<co>,?\s+(?:and|&)\s+{_NAME}))?\s*"
    r"\((?P<years>(?:19|20)\d{2}[a-z]?(?:\s*[,;]\s*(?:(?:19|20)\d{2}[a-z]?|[a-z]\b))*)\)"
)
_LIST_BEFORE = re.compile(rf"{_NAME},\s*$")  # 'Jones, | Brown and Smith (2020)'
# a year with its suffix, or a bare suffix letter continuing it ('2020a, b')
_PART_YEAR = re.compile(r"\b((?:19|20)\d{2})([a-z]?)\b|(?<!\w)([a-z])(?=\s*(?:[,;)]|$))")
_LEAD = re.compile(
    r"^(?:(?:see(?:\s+also)?|e\.g\.|i\.e\.|cf\.|for\s+example|reviewed\s+in)[,:]?\s+)+", re.I
)
_NAMES_OK = re.compile(rf"^{_NAME}(?:[\s.,&'’\-]|\w)*$")


def fold(s: str) -> str:
    """Case- and diacritics-insensitive key for NAME comparison only (never for quotes)."""
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def family_keys(name: str) -> set[str]:
    """Keys of a family name: whole and last word ('van der Berg' -> {'van der berg', 'berg'})."""
    name = fold(name).strip(" .,")
    return {name, name.split()[-1]} if name else set()


def family(author: str) -> str:
    author = author.strip()
    return author.split(",")[0].strip() if "," in author else (author.split() or [""])[-1]


def first_family(names: str) -> str:
    return re.split(r"\s+et\s+al\b|\s*&\s*|\s+and\s+|,", names, maxsplit=1)[0].strip()


def ref_family_keys(ref: Reference) -> set[str]:
    keys = set()
    if ref.authors:
        keys |= family_keys(family(ref.authors[0]))
    lead = re.match(r"\s*([^\d,.(]{2,60}?)\s*(?:,|\(|\s[A-Z]\.|\s[A-Z]{1,3}\b|\.)", ref.raw)
    if lead:
        keys |= family_keys(lead.group(1))
    return keys


def _years(s: str):
    """'2019, 2020a, b' -> (2019, ''), (2020, 'a'), (2020, 'b')."""
    last = None
    for m in _PART_YEAR.finditer(s):
        if m.group(1):
            last = int(m.group(1))
            yield last, m.group(2)
        elif last is not None and m.group(3):
            yield last, m.group(3)


def _expand(spec: str) -> list[str]:
    ids = []
    for part in spec.split(","):
        nums = [int(n) for n in re.findall(r"\d+", part)]
        if len(nums) == 2 and 0 < nums[1] - nums[0] <= 100:
            ids += [str(n) for n in range(nums[0], nums[1] + 1)]
        else:
            ids += [str(n) for n in nums]
    return ids


def _bib_keys(spec: str) -> list[str]:
    return [k.strip() for k in spec.split(",") if k.strip()]


class _Resolver:
    def __init__(self, refs: list[Reference]):
        self.ids = {r.id for r in refs}
        self.refs = refs

    def author_year(self, names: str, year: int, suffix: str) -> list[str]:
        keys = family_keys(first_family(names))
        hits = [
            r
            for r in self.refs
            if keys & ref_family_keys(r)
            and year in ([r.year] if r.year else year_candidates(r.raw))
        ]
        if suffix:
            hits = [r for r in hits if re.search(rf"\b{year}{suffix}\b", r.raw)]
        return [h.id for h in hits] if len(hits) == 1 else []


def _paren_parts(content: str):
    """'(Smith et al., 2020; Doe & Roe, 2019a, 2019b)' -> [(names, year, suffix), ...]."""
    names = None
    for part in content.split(";"):
        first = re.search(r"\b(?:19|20)\d{2}[a-z]?\b", part)
        if not first:
            continue
        lead = _LEAD.sub("", part[: first.start()].strip()).strip(" ,")
        if lead:
            if not _NAMES_OK.match(lead):
                continue
            names = lead
        if names is None:
            continue
        for year, suffix in _years(part[first.start() :]):
            yield names, year, suffix


def find_citations(body: str, refs: list[Reference]) -> tuple[list[Citation], list[str]]:
    """One Citation per citing sentence (document order); unresolved markers -> warnings."""
    res = _Resolver(refs)
    cites, warnings = [], []
    for sent in sentences(body):
        markers, ids, missing = [], [], []
        for pat, keys_of, label in ((_NUMERIC, _expand, "[{}]"), (_LATEX, _bib_keys, "{}")):
            for m in pat.finditer(sent):
                keys = keys_of(m.group(1))
                if "0" in keys:  # '[0, 1]' is an interval, not a citation
                    continue
                markers.append(m.group(0))
                ids.extend(k for k in keys if k in res.ids and k not in ids)
                missing += [label.format(k) for k in keys if k not in res.ids]
        for m in _PAREN.finditer(sent):
            parts = list(_paren_parts(m.group(1)))
            if not parts:
                continue
            found = []
            for names, year, suffix in parts:
                hit = res.author_year(names, year, suffix)
                found += hit
                if not hit:
                    missing.append(f"{first_family(names)} {year}{suffix}")
            markers.append(m.group(0))
            ids.extend(i for i in found if i not in ids)
        for m in _NARRATIVE.finditer(sent):
            # 'Jones, Brown and Smith (2020)': the match starts at the 2nd author; the real
            # first author is unknown here, so leave it unresolved rather than guess
            in_list = m.group("co") and _LIST_BEFORE.search(sent[: m.start()])
            for year, suffix in _years(m.group("years")):
                hit = [] if in_list else res.author_year(m.group("name"), year, suffix)
                ids.extend(i for i in hit if i not in ids)
                if not hit:
                    missing.append(f"{m.group('name')} {year}{suffix}")
            markers.append(m.group(0))
        if not markers:
            continue
        cid = f"c{len(cites) + 1}"
        cites.append(Citation(id=cid, sentence=sent, ref_ids=ids, marker=" ".join(markers)))
        if missing:
            warnings.append(f"Citation {cid}: unresolved {', '.join(dict.fromkeys(missing))}")
    return cites, warnings
