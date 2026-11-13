"""Source text for an identified reference: Europe PMC / arXiv HTML / arXiv PDF / bioRxiv-medRxiv /
Unpaywall / OpenAlex-location full text, else the abstract. Owner: T3.

Every source is tried in order; any exception or empty answer in one source falls through to the
next. Text is only ever copied from what a provider returned, never synthesised. Results are
cached on disk under CACHE_DIR/sources (failures and None never are).
"""

import hashlib
import html
import logging
import os
import re
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import httpx

from claimtrace.core.http import request
from claimtrace.core.schemas import EvidenceKind, ReferenceAudit, SourceText, Work
from claimtrace.core.settings import get_settings

log = logging.getLogger(__name__)

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_PDF_BYTES = 30 * 1024 * 1024
MIN_FULLTEXT_CHARS = 2000
GUARD_WINDOW = 5000

PdfToText = Callable[[bytes], Awaitable[str]]


async def fetch_source(
    audit: ReferenceAudit,
    client: httpx.AsyncClient,
    pdf_to_text: PdfToText,
) -> SourceText | None:
    w = audit.matched
    if w is None:
        return None
    f = _cache_file(w)
    hit = _cache_get(f) if f else None
    if hit:
        return hit.model_copy(update={"ref_id": audit.reference.id})
    got = await _fetch(w, audit.reference.id, client, pdf_to_text)
    if got and f:
        _cache_put(f, got)
    return got


async def _fetch(
    w: Work, ref_id: str, client: httpx.AsyncClient, pdf_to_text: PdfToText
) -> SourceText | None:
    tried: set[str] = set()  # PDF urls already downloaded for this reference
    full_text = [
        ("europepmc", lambda: _europepmc(w, client)),
        ("arxiv", lambda: _arxiv_html(w, client)),
        ("arxiv", lambda: _arxiv(w, client, pdf_to_text, tried)),
        ("biorxiv", lambda: _biorxiv(w, client, pdf_to_text, tried)),
        ("unpaywall", lambda: _unpaywall(w, client, pdf_to_text, tried)),
        ("openalex", lambda: _openalex_pdf(w, client, pdf_to_text, tried)),
    ]
    for origin, get in full_text:
        try:
            got = await get()
        except Exception as e:  # one source failing never ends the search
            log.info("sources: %s failed for %s: %s", origin, ref_id, type(e).__name__)
            continue
        if got is None:
            continue
        text, url = got
        if not looks_like(text, w.title, w.authors):
            log.info("sources: %s text rejected by wrong-document guard for %s", origin, ref_id)
            continue
        return SourceText(
            ref_id=ref_id, kind=EvidenceKind.full_text, origin=origin, url=url, text=text
        )

    abstracts = [
        (w.provider, lambda: _given_abstract(w)),
        ("openalex", lambda: _openalex_abstract(w, client)),
        ("semanticscholar", lambda: _s2_abstract(w, client)),
        ("crossref", lambda: _crossref_abstract(w, client)),
    ]
    for origin, get in abstracts:
        try:
            text = await get()
        except Exception as e:
            log.info("sources: %s abstract failed for %s: %s", origin, ref_id, type(e).__name__)
            continue
        if text and text.strip():
            return SourceText(
                ref_id=ref_id,
                kind=EvidenceKind.abstract,
                origin=origin,
                url=_landing_url(w),
                text=text.strip(),
            )
    return None


# ---------- disk cache ----------

CACHE_MAX_FILES = 2000
# An abstract may be the answer only because full text failed this time (GROBID down, PDF host
# rate-limited), so it is reused for a day; a full text is reused until evicted.
ABSTRACT_TTL = 24 * 3600


def _cache_file(w: Work) -> Path | None:
    aid = _arxiv_id(w)
    if w.doi:
        key = "doi:" + w.doi.strip().lower()
    elif aid:
        key = "arxiv:" + aid.strip().lower()
    elif w.title:  # title alone is ambiguous: add year and first author
        first = _family(w.authors[0]) if w.authors else ""
        key = f"title:{' '.join(_words(w.title))}|{w.year}|{first}"
    else:
        return None
    name = hashlib.sha256(key.encode()).hexdigest() + ".json"
    return Path(get_settings().cache_dir) / "sources" / name


def _cache_get(f: Path) -> SourceText | None:
    try:
        s = SourceText.model_validate_json(f.read_bytes())
        if s.kind == EvidenceKind.abstract and time.time() - f.stat().st_mtime > ABSTRACT_TTL:
            return None
        return s
    except Exception:  # missing, unreadable or corrupt: fetch again
        return None


def _cache_put(f: Path, s: SourceText) -> None:
    """Atomic write, then keep only the newest CACHE_MAX_FILES entries. A cache problem never
    breaks a fetch."""
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as out:
            out.write(s.model_dump_json())
        os.replace(tmp, f)
        files = list(f.parent.glob("*.json"))
        if len(files) > CACHE_MAX_FILES:
            files.sort(key=lambda p: p.stat().st_mtime)
            for old in files[:-CACHE_MAX_FILES]:
                old.unlink(missing_ok=True)
    except OSError as e:
        log.warning("sources: cache write failed: %s", type(e).__name__)


# ---------- wrong-document guard ----------


def _words(s: str) -> list[str]:
    # Guard only (not a quote check), so folding accents here is fine.
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", s)


# Function words only: shared "for the of" must not make another paper look like the cited one.
_STOP = set(
    "the and for with from into onto over under via using its their this that these those are "
    "was were has have had been can does all you how what why when who which our than between "
    "among after before during toward towards about across against within".split()
)
SHORT_TITLE = 3  # fewer content words than this: also require the first author's family name


def _family(author: str) -> str | None:
    """'Family, Given' or 'Given Family' -> longest word of the family name."""
    fam = author.split(",")[0] if "," in author else (author.split() or [""])[-1]
    words = _words(fam)
    return max(words, key=len) if words else None


# Kept local (modules never import refaudit). A title that adds or drops one of these is
# the opposite claim: "X does not reduce Y" is not "X reduces Y".
_NEG = set(
    "no not non without never cannot lack lacks lacking absence absent fail fails failed "
    "failure".split()
)
TITLE_SIM = 0.85
MAX_TITLE_LINE = 300  # a longer first line is a text blob, not a title


def _neg(s: str) -> set[str]:
    found = set(_words(s)) & _NEG
    if re.search(r"n['’]t\b", s.casefold()):
        found.add("n't")
    return found


def _digits(s: str) -> set[str]:
    return {w for w in _words(s) if w.isdigit()}


def _same_title(title: str, line: str) -> bool:
    sim = SequenceMatcher(None, _words(title), _words(line), autojunk=False).ratio()
    return sim >= TITLE_SIM and _neg(title) == _neg(line) and _digits(title) == _digits(line)


def looks_like(text: str, title: str | None, authors: list[str] | None = None) -> bool:
    """Full text is accepted only if long enough and it is the cited paper. Our full texts
    (JATS, GROBID TEI) start with the document's own title, so its first line must match the
    cited title word for word (>= 0.85), with the same negation words and numbers. Without a
    title-like first line: most title content words near the start, and the first sentence must
    carry the same negation words. Short titles ('Deep learning') also need the first author's
    family name."""
    if len(text) < MIN_FULLTEXT_CHARS or not title:
        return False
    want = {t for t in _words(title) if (len(t) >= 3 or t.isdigit()) and t not in _STOP}
    if not want:
        return False
    window = text[:GUARD_WINDOW]
    have = set(_words(window))
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first and len(first) <= MAX_TITLE_LINE:
        if not _same_title(title, first):
            return False
    else:
        if 2 * len(want & have) <= len(want):
            return False
        sentence = re.split(r"(?<=[.!?])\s+", window.strip(), maxsplit=1)[0]
        if _neg(sentence) != _neg(title):
            return False
    if len(want) < SHORT_TITLE:
        fam = _family(authors[0]) if authors else None
        return fam is not None and fam in have
    return True


# ---------- Europe PMC (JATS) ----------


async def _europepmc(w: Work, client: httpx.AsyncClient) -> tuple[str, str] | None:
    pmcid = w.pmcid
    if not pmcid:
        if w.doi:
            query = f'DOI:"{w.doi}"'
        elif w.pmid:
            query = f"EXT_ID:{w.pmid} AND SRC:MED"
        else:
            return None
        r = await request(
            client,
            "GET",
            f"{EPMC}/search",
            params={"query": query, "format": "json", "resultType": "lite", "pageSize": 5},
        )
        r.raise_for_status()
        hits = r.json().get("resultList", {}).get("result", [])
        pmcid = next((h["pmcid"] for h in hits if h.get("pmcid")), None)
        if not pmcid:
            return None
    pmcid = pmcid.upper() if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    r = await request(client, "GET", f"{EPMC}/{pmcid}/fullTextXML")
    if r.status_code != 200:  # 404 = not in the open-access subset
        return None
    text = jats_to_text(r.content)
    return (text, f"https://europepmc.org/article/PMC/{pmcid}") if text else None


_MARK = {"sup": "^", "sub": "_"}  # 10<sup>5</sup> -> "10^5", never "105"
_SKIP = {"ref-list", "back", "xref-list", "fn-group", "ack", "glossary", "app-group"}
_BLOCKS = {"article-title", "title", "p", "tr", "label"}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el: ET.Element) -> str:
    parts = [_MARK.get(_local(el.tag), ""), el.text or ""]
    for child in el:
        parts += [_text(child), child.tail or ""]
    return "".join(parts)


def _flat(el: ET.Element) -> str:
    if _local(el.tag) == "tr":
        cells = (" ".join(_text(c).split()) for c in el)
        return " | ".join(c for c in cells if c)
    return " ".join(_text(el).split())


def _collect(el: ET.Element, out: list[str]) -> None:
    tag = _local(el.tag)
    if tag in _SKIP:
        return
    if tag in _BLOCKS:
        s = _flat(el)
        if s:
            out.append(s)
        return
    for child in el:
        _collect(child, out)


def jats_to_text(xml: bytes) -> str:
    """Title, author surnames, abstract, body, figure/table captions of a JATS article;
    no reference list. Surnames let the wrong-document guard check short titles."""
    root = ET.fromstring(xml)
    out: list[str] = []
    surnames = ".//article-meta/contrib-group/contrib/name/surname"
    paths = (".//title-group/article-title", surnames, ".//abstract", ".//body", ".//floats-group")
    for path in paths:
        if path == surnames:
            out.append(" ".join(_flat(s) for s in root.iterfind(path)))
            continue
        for el in root.iterfind(path):
            _collect(el, out)
            break  # first match only (sub-articles repeat these)
    return "\n\n".join(o for o in out if o)


# ---------- arXiv HTML (LaTeXML) ----------

# Inside <article> only; these subtrees are dropped (references, footnotes, author contact notes).
_HTML_SKIP_TAGS = {"nav", "script", "style", "annotation", "annotation-xml", "button", "math"}
_HTML_SKIP_CLASSES = {"ltx_bibliography", "ltx_note", "ltx_author_notes", "ltx_author_before"}
_HTML_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section", "figcaption", "li", "tr"}
_HTML_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "wbr"}


class _ArxivHtml(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []  # (tag, starts a skipped subtree)
        self.article = self.skip = 0
        self.buf: list[str] = []
        self.lines: list[str] = []
        self.title_at: int | None = None  # index of the document title line

    def _flush(self):
        line = " ".join("".join(self.buf).split())
        if line:
            self.lines.append(line)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in _HTML_VOID:
            return
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        skip = tag in _HTML_SKIP_TAGS or bool(classes & _HTML_SKIP_CLASSES)
        if tag == "sup" and "ltx_note_mark" in classes:
            skip = True
        live = self.article and not self.skip
        if live and tag == "math":  # LaTeX source of the formula, never the flattened MathML
            self.buf.append(a.get("alttext") or "")
        elif live and tag in _HTML_BLOCKS:
            self._flush()
            if "ltx_title_document" in classes and self.title_at is None:
                self.title_at = len(self.lines)
        elif live and tag in ("sup", "sub") and not skip:
            self.buf.append(_MARK[tag])  # 10<sup>5</sup> -> "10^5", never "105"
        elif live and tag in ("td", "th"):
            self.buf.append(" ")
        self.stack.append((tag, skip))
        self.skip += skip
        self.article += tag == "article"

    def handle_endtag(self, tag):
        if tag not in (t for t, _ in self.stack):
            return
        while self.stack:
            t, skip = self.stack.pop()
            self.skip -= skip
            self.article -= t == "article"
            if t == tag:
                break
        if self.article and not self.skip and tag in _HTML_BLOCKS:
            self._flush()

    def handle_data(self, data):
        if self.article and not self.skip:
            self.buf.append(data)


def arxiv_html_to_text(page: str) -> str:
    """Title, author names, abstract, section headings, paragraphs, captions and equations
    (as their LaTeX alttext) of an arXiv LaTeXML page; no references, footnotes or page chrome.
    Text is kept as served, only whitespace collapsed."""
    p = _ArxivHtml()
    p.feed(page)
    p.close()
    p._flush()
    lines = p.lines
    if p.title_at is not None and p.title_at < len(lines):  # title first (a licence note
        lines.insert(0, lines.pop(p.title_at))  # can precede it): the guard reads line one
    return "\n\n".join(lines)


async def _arxiv_html(w: Work, client: httpx.AsyncClient) -> tuple[str, str] | None:
    aid = _arxiv_id(w)
    if not aid:
        return None
    url = f"https://arxiv.org/html/{aid}"
    r = await request(client, "GET", url)
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "").lower():
        return None
    text = arxiv_html_to_text(r.text)
    return (text, url) if text else None


# ---------- PDFs: arXiv, Unpaywall ----------


_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)


def _arxiv_id(w: Work) -> str | None:
    if w.arxiv_id:
        return w.arxiv_id
    m = _ARXIV_DOI.match(w.doi or "")
    return m.group(1) if m else None


async def _get_pdf(client: httpx.AsyncClient, url: str) -> bytes | None:
    """Download a PDF, refusing non-PDF answers (landing pages) and anything over 30 MB."""
    async with client.stream("GET", url) as r:
        if r.status_code != 200 or "pdf" not in r.headers.get("content-type", "").lower():
            return None
        if int(r.headers.get("content-length") or 0) > MAX_PDF_BYTES:
            return None
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) > MAX_PDF_BYTES:
                return None
    return bytes(buf) if buf.startswith(b"%PDF") else None


async def _pdf_text(client, url: str, pdf_to_text: PdfToText) -> tuple[str, str] | None:
    pdf = await _get_pdf(client, url)
    if pdf is None:
        return None
    text = (await pdf_to_text(pdf)).strip()
    return (text, url) if text else None


async def _first_pdf(
    urls, w: Work, client, pdf_to_text: PdfToText, tried: set[str]
) -> tuple[str, str] | None:
    """First candidate url whose PDF converts to text that passes the wrong-document guard.
    A url already tried for this reference is skipped; any failure moves to the next url."""
    for u in dict.fromkeys(u for u in urls if u):
        if u in tried:
            continue
        tried.add(u)
        try:
            got = await _pdf_text(client, u, pdf_to_text)
        except Exception as e:
            log.info("sources: pdf %s failed: %s", u, type(e).__name__)
            continue
        if not got:
            continue
        if looks_like(got[0], w.title, w.authors):
            return got
        # wrong document at this url: the next candidate may still be the right one
        log.info("sources: pdf %s rejected by wrong-document guard", u)
    return None


# ponytail: no arXiv rate limiter here (pipeline runs <= 6 fetches at once); add a shared
# 3 s spacing if arXiv starts answering 403/429 for PDF downloads.
async def _arxiv(w: Work, client, pdf_to_text: PdfToText, tried) -> tuple[str, str] | None:
    aid = _arxiv_id(w)
    if not aid:
        return None
    return await _first_pdf([f"https://arxiv.org/pdf/{aid}"], w, client, pdf_to_text, tried)


# bioRxiv / medRxiv DOIs: 10.1101/2020.03.22.002386 or old style 10.1101/123456. Other 10.1101
# DOIs (Genome Research "gr.…", Genes & Dev "gad.…") belong to journals, not preprint servers.
_PREPRINT_DOI = re.compile(r"^10\.1101/(?:\d{4}\.\d{2}\.\d{2}\.)?\d{6,}$")


async def _biorxiv(w: Work, client, pdf_to_text: PdfToText, tried) -> tuple[str, str] | None:
    doi = (w.doi or "").lower()
    if not _PREPRINT_DOI.match(doi):
        return None
    for server in ("biorxiv", "medrxiv"):
        r = await request(client, "GET", f"https://api.biorxiv.org/details/{server}/{doi}")
        if r.status_code != 200:
            continue
        versions = [int(v["version"]) for v in r.json().get("collection") or []]
        if versions:
            url = f"https://www.{server}.org/content/{doi}v{max(versions)}.full.pdf"
            return await _first_pdf([url], w, client, pdf_to_text, tried)
    return None


async def _unpaywall(w: Work, client, pdf_to_text: PdfToText, tried) -> tuple[str, str] | None:
    url = None
    email = get_settings().contact_email
    if w.doi and email:
        try:
            r = await request(
                client,
                "GET",
                f"https://api.unpaywall.org/v2/{quote(w.doi)}",
                params={"email": email},
            )
            if r.status_code == 200:
                url = ((r.json().get("best_oa_location") or {}).get("url_for_pdf")) or None
        except Exception as e:  # Unpaywall down: still try the OA url refaudit found
            log.info("sources: unpaywall lookup failed: %s", type(e).__name__)
    return await _first_pdf([url, w.oa_pdf_url], w, client, pdf_to_text, tried)


MAX_OA_LOCATIONS = 4  # bounds downloads for works with many repository copies


async def _openalex_work(w: Work, client: httpx.AsyncClient) -> dict | None:
    """Free OpenAlex entity lookup by DOI (cached by core.http; never a paid search)."""
    if not w.doi:
        return None
    r = await request(client, "GET", f"https://api.openalex.org/works/doi:{quote(w.doi)}")
    return r.json() if r.status_code == 200 else None


async def _openalex_pdf(w: Work, client, pdf_to_text: PdfToText, tried) -> tuple[str, str] | None:
    d = await _openalex_work(w, client)
    if not d:
        return None
    locs = [d.get("best_oa_location"), d.get("primary_location"), *(d.get("locations") or [])]
    urls = [(loc or {}).get("pdf_url") for loc in locs]
    urls = [u for u in dict.fromkeys(u for u in urls if u) if u not in tried]
    return await _first_pdf(urls[:MAX_OA_LOCATIONS], w, client, pdf_to_text, tried)


# ---------- abstracts ----------


MARKUP_PROVIDERS = {"crossref"}  # JATS abstracts; arXiv / S2 / OpenAlex give plain text


async def _given_abstract(w: Work) -> str | None:
    if not w.abstract:
        return None
    return _strip_tags(w.abstract) if w.provider in MARKUP_PROVIDERS else w.abstract


# A real tag: a name right after "<" or "</", attributes only as name="value".
# "doses < 10 mg ... > 50 mg" has no such tag and is left alone.
_ATTRS = r"""(?:\s+[\w:.-]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>"""


def _tag(names: str) -> str:
    return rf"<(?:{names}){_ATTRS}"


def _strip_tags(s: str) -> str:
    s = re.sub(_tag("(?:jats:)?title") + r".*?</(?:jats:)?title\s*>", " ", s, flags=re.S)
    s = re.sub(_tag("/?(?:jats:)?(?:p|sec|title|list-item)"), " ", s)  # block tags
    s = re.sub(_tag("(?:jats:)?sup"), "^", s)  # 10<sup>5</sup> -> "10^5", never "105"
    s = re.sub(_tag("(?:jats:)?sub"), "_", s)
    s = re.sub(_tag(r"/?[A-Za-z][\w:.-]*"), "", s)  # other inline tags (italic, ...)
    return " ".join(html.unescape(s).split())


def rebuild_inverted(index: dict[str, list[int]]) -> str:
    pos = {i: word for word, idxs in index.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))


async def _openalex_abstract(w: Work, client: httpx.AsyncClient) -> str | None:
    d = await _openalex_work(w, client)
    inv = (d or {}).get("abstract_inverted_index")
    return rebuild_inverted(inv) if inv else None


async def _s2_abstract(w: Work, client: httpx.AsyncClient) -> str | None:
    if not w.doi:
        return None
    key = get_settings().semantic_scholar_api_key.get_secret_value()
    r = await request(
        client,
        "GET",
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(w.doi)}",
        params={"fields": "abstract"},
        headers={"x-api-key": key} if key else None,
    )
    if r.status_code != 200:
        return None
    return r.json().get("abstract") or None


async def _crossref_abstract(w: Work, client: httpx.AsyncClient) -> str | None:
    if not w.doi:
        return None
    email = get_settings().contact_email
    r = await request(
        client,
        "GET",
        f"https://api.crossref.org/works/{quote(w.doi)}",
        params={"mailto": email} if email else None,
    )
    if r.status_code != 200:
        return None
    raw = r.json().get("message", {}).get("abstract")
    return _strip_tags(raw) if raw else None


def _landing_url(w: Work) -> str | None:
    if w.doi:
        return f"https://doi.org/{w.doi}"
    if w.arxiv_id:
        return f"https://arxiv.org/abs/{w.arxiv_id}"
    return w.url
