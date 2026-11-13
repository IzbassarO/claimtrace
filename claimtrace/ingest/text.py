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


def split_manuscript(text: str) -> tuple[str, str | None]:
    """(body, reference section) split at the LAST reference heading line."""
    heads = list(_REF_HEADING.finditer(text))
    if not heads:
        return text, None
    return text[: heads[-1].start()], text[heads[-1].end() :]


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
        blocks = [ln.strip() for ln in section.splitlines() if ln.strip()]
    return [(None, b) for b in blocks]


# ---------- regex fields (deterministic) ----------

_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
_ARXIV = re.compile(
    r"(?:arxiv[:\s]\s*|arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.)"
    r"([a-z\-]+(?:\.[a-z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.I,
)
_URL = re.compile(r"https?://\S+", re.I)
_YEAR = re.compile(r"(?<![\w/.-])((?:19|20)\d{2})[a-z]?(?![\w/])")
_PAREN_YEAR = re.compile(r"\(((?:19|20)\d{2})[a-z]?\)")


def find_doi(raw: str) -> str | None:
    m = _DOI.search(raw)
    if not m:
        return None
    doi = m.group(0).rstrip(".,;:")
    while doi[-1] in ")]" and doi.count(doi[-1]) > doi.count("(" if doi[-1] == ")" else "["):
        doi = doi[:-1].rstrip(".,;:")
    return doi.lower()


def find_arxiv(raw: str) -> str | None:
    m = _ARXIV.search(raw)
    return m.group(1) if m else None


def year_candidates(raw: str) -> list[int]:
    """Plausible publication years, outside DOIs/URLs/arXiv ids. Parenthesised years first."""
    clean = _ARXIV.sub(" ", _DOI.sub(" ", _URL.sub(" ", raw)))
    found = [int(y) for y in _PAREN_YEAR.findall(clean)] + [int(y) for y in _YEAR.findall(clean)]
    return list(dict.fromkeys(found))


# ---------- sentences ----------

_ABBREV = {
    "al", "e.g", "i.e", "fig", "figs", "eq", "eqs", "ref", "refs", "vs", "cf", "dr", "mr", "mrs",
    "ms", "no", "nos", "approx", "ca", "resp", "tab", "sect", "sec", "ch", "vol", "pp", "p",
    "et", "etc", "st", "prof", "inc", "ltd", "jr", "sr", "suppl", "viz",
}  # fmt: skip
_BOUNDARY = re.compile(r"[.!?][\"'”’)\]]*\s+(?=[A-Z0-9\"“‘'(\[])")
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
_NARRATIVE = re.compile(
    rf"(?<![\w.])(?P<name>{_NAME})(?:\s+et\s+al\.?|,?\s+(?:and|&)\s+{_NAME})?\s*"
    r"\((?P<years>(?:19|20)\d{2}[a-z]?(?:\s*[,;]\s*(?:19|20)\d{2}[a-z]?)*)\)"
)
_PART_YEAR = re.compile(r"\b((?:19|20)\d{2})([a-z]?)\b")
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
        years = list(_PART_YEAR.finditer(part))
        if not years:
            continue
        lead = _LEAD.sub("", part[: years[0].start()].strip()).strip(" ,")
        if lead:
            if not _NAMES_OK.match(lead):
                continue
            names = lead
        if names is None:
            continue
        for y in years:
            yield names, int(y.group(1)), y.group(2)


def find_citations(body: str, refs: list[Reference]) -> tuple[list[Citation], list[str]]:
    """One Citation per citing sentence (document order); unresolved markers -> warnings."""
    res = _Resolver(refs)
    cites, warnings = [], []
    for sent in sentences(body):
        markers, ids, missing = [], [], []
        for pat, keys_of, label in ((_NUMERIC, _expand, "[{}]"), (_LATEX, _bib_keys, "{}")):
            for m in pat.finditer(sent):
                keys = keys_of(m.group(1))
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
            for y in _PART_YEAR.finditer(m.group("years")):
                hit = res.author_year(m.group("name"), int(y.group(1)), y.group(2))
                ids.extend(i for i in hit if i not in ids)
                if not hit:
                    missing.append(f"{m.group('name')} {y.group(0)}")
            markers.append(m.group(0))
        if not markers:
            continue
        cid = f"c{len(cites) + 1}"
        cites.append(Citation(id=cid, sentence=sent, ref_ids=ids, marker=" ".join(markers)))
        if missing:
            warnings.append(f"Citation {cid}: unresolved {', '.join(dict.fromkeys(missing))}")
    return cites, warnings
