"""Source text for an identified reference: Europe PMC / arXiv / Unpaywall full text, else the
abstract. Owner: T3.

Every source is tried in order; any exception or empty answer in one source falls through to the
next. Text is only ever copied from what a provider returned, never synthesised.
"""

import html
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
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
    ref_id = audit.reference.id
    full_text = [
        ("europepmc", lambda: _europepmc(w, client)),
        ("arxiv", lambda: _arxiv(w, client, pdf_to_text)),
        ("unpaywall", lambda: _unpaywall(w, client, pdf_to_text)),
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
        if not looks_like(text, w.title):
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


# ---------- wrong-document guard ----------


def _words(s: str) -> list[str]:
    # Guard only (not a quote check), so folding accents here is fine.
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", s)


def looks_like(text: str, title: str | None) -> bool:
    """Full text is accepted only if long enough and most title words occur near its start."""
    if len(text) < MIN_FULLTEXT_CHARS or not title:
        return False
    want = {t for t in _words(title) if len(t) >= 3 or t.isdigit()}
    if not want:
        return False
    have = set(_words(text[:GUARD_WINDOW]))
    return 2 * len(want & have) > len(want)


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


_SKIP = {"ref-list", "back", "xref-list", "fn-group", "ack", "glossary", "app-group"}
_BLOCKS = {"article-title", "title", "p", "tr", "label"}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _flat(el: ET.Element) -> str:
    if _local(el.tag) == "tr":
        cells = (" ".join("".join(c.itertext()).split()) for c in el)
        return " | ".join(c for c in cells if c)
    return " ".join("".join(el.itertext()).split())


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
    """Title, abstract, body, figure/table captions of a JATS article; no reference list."""
    root = ET.fromstring(xml)
    out: list[str] = []
    for path in (".//title-group/article-title", ".//abstract", ".//body", ".//floats-group"):
        for el in root.iterfind(path):
            _collect(el, out)
            break  # first match only (sub-articles repeat these)
    return "\n\n".join(out)


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


# ponytail: no arXiv rate limiter here (pipeline runs <= 6 fetches at once); add a shared
# 3 s spacing if arXiv starts answering 403/429 for PDF downloads.
async def _arxiv(w: Work, client, pdf_to_text: PdfToText) -> tuple[str, str] | None:
    aid = _arxiv_id(w)
    if not aid:
        return None
    return await _pdf_text(client, f"https://arxiv.org/pdf/{aid}", pdf_to_text)


async def _unpaywall(w: Work, client, pdf_to_text: PdfToText) -> tuple[str, str] | None:
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
    for u in dict.fromkeys(u for u in (url, w.oa_pdf_url) if u):
        try:
            got = await _pdf_text(client, u, pdf_to_text)
        except Exception as e:
            log.info("sources: pdf %s failed: %s", u, type(e).__name__)
            continue
        if got:
            return got
    return None


# ---------- abstracts ----------


async def _given_abstract(w: Work) -> str | None:
    return _strip_tags(w.abstract) if w.abstract else None


def _strip_tags(s: str) -> str:
    s = re.sub(r"<(jats:)?title>.*?</(jats:)?title>", " ", s, flags=re.S)
    s = re.sub(r"</?(?:jats:)?(?:p|sec|title|list-item)\b[^>]*>", " ", s)  # block tags
    s = re.sub(r"<[^>]+>", "", s)  # inline tags (italic, sup, ...) vanish without a space
    return " ".join(html.unescape(s).split())


def rebuild_inverted(index: dict[str, list[int]]) -> str:
    pos = {i: word for word, idxs in index.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))


async def _openalex_abstract(w: Work, client: httpx.AsyncClient) -> str | None:
    if not w.doi:
        return None
    r = await request(client, "GET", f"https://api.openalex.org/works/doi:{quote(w.doi)}")
    if r.status_code != 200:
        return None
    inv = r.json().get("abstract_inverted_index")
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
