"""Provider lookups. Each returns list[Work] (empty = the provider answered "no record") or
raises (= provider failure). 404 on an id lookup is an answer, anything else non-200 raises."""

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from claimtrace.core.http import redact, request
from claimtrace.core.schemas import Reference, Work
from claimtrace.core.settings import get_settings
from claimtrace.refaudit.match import (
    ARXIV_ID,
    RETRACTED_PREFIX,
    arxiv_from_doi,
    family_name,
    norm_arxiv,
    norm_doi,
)

CROSSREF = "https://api.crossref.org"
OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
ARXIV = "https://export.arxiv.org/api/query"
S2_FIELDS = "title,authors,year,externalIds,venue,url,abstract,openAccessPdf"
# Crossref update types meaning the work no longer stands
RETRACTING_UPDATES = {"retraction", "partial_retraction", "withdrawal", "removal"}
# inline markup (CO<sub>2</sub>, <i>E. coli</i>) is dropped without a space
_INLINE_TAG = re.compile(
    r"</?(jats:)?(sub|sup|i|b|em|strong|italic|bold|sc|u|underline)\b[^>]*>", re.I
)


class ProviderError(RuntimeError):
    pass


class Gate:
    """Concurrency limit + minimum spacing between request starts."""

    def __init__(self, n: int, interval: float = 0.0):
        self.sem, self.interval, self.next = asyncio.Semaphore(n), interval, 0.0

    async def __aenter__(self):
        await self.sem.acquire()
        if self.interval:
            now = asyncio.get_running_loop().time()
            wait, self.next = self.next - now, max(now, self.next) + self.interval
            if wait > 0:
                await asyncio.sleep(wait)

    async def __aexit__(self, *exc):
        self.sem.release()


async def _get(client: httpx.AsyncClient, gate: Gate, url: str, **kw) -> httpx.Response | None:
    """GET; None on 404, raises ProviderError on any other non-200."""
    async with gate:
        resp = await request(client, "GET", url, **kw)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code}")
    return resp


def _clean(s: str | None) -> str | None:
    """Strip JATS/HTML tags and entities, collapse whitespace."""
    if not s:
        return None
    s = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", _INLINE_TAG.sub("", s))).split())
    return s or None


def _retracted_title(title: str | None) -> bool:
    return bool(title and RETRACTED_PREFIX.match(title))


def _search_query(ref: Reference) -> str:
    parts = [ref.title or ""]
    if ref.authors:
        parts.append(family_name(ref.authors[0]))
    if ref.year:
        parts.append(str(ref.year))
    return " ".join(p for p in parts if p)


# ---------- Crossref ----------


def parse_crossref(item: dict) -> Work:
    title = _clean((item.get("title") or [None])[0])
    authors = [
        f"{a['family']}, {a['given']}" if a.get("given") else a["family"]
        for a in item.get("author") or []
        if a.get("family")
    ]
    year = None
    for key in ("issued", "published-print", "published-online", "published"):
        parts = (item.get(key) or {}).get("date-parts") or [[None]]
        if parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    updates = (item.get("updated-by") or []) + (item.get("update-to") or [])
    doi = norm_doi(item.get("DOI"))
    return Work(
        provider="crossref",
        provider_id=doi,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        arxiv_id=arxiv_from_doi(doi),
        venue=_clean((item.get("container-title") or [None])[0]),
        url=item.get("URL"),
        abstract=_clean(item.get("abstract")),
        is_retracted=_retracted_title(title)
        or any(str(u.get("type", "")).lower() in RETRACTING_UPDATES for u in updates),
    )


async def crossref(client: httpx.AsyncClient, gate: Gate, ref: Reference) -> list[Work]:
    mail = get_settings().contact_email
    params = {"mailto": mail} if mail else {}
    if doi := norm_doi(ref.doi):
        resp = await _get(client, gate, f"{CROSSREF}/works/{quote(doi, safe='/')}", params=params)
        if resp:
            return [parse_crossref(resp.json()["message"])]
    if not ref.title:
        return []
    params |= {"query.bibliographic": _search_query(ref), "rows": 5}
    resp = await _get(client, gate, f"{CROSSREF}/works", params=params)
    return [parse_crossref(i) for i in resp.json()["message"]["items"]] if resp else []


# ---------- OpenAlex ----------


def _abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos = sorted((i, w) for w, idx in inv.items() for i in idx)
    return " ".join(w for _, w in pos) or None


def parse_openalex(r: dict) -> Work:
    ids = r.get("ids") or {}
    pmid = (ids.get("pmid") or "").rstrip("/").rsplit("/", 1)[-1] or None
    pmc = (ids.get("pmcid") or "").rstrip("/").rsplit("/", 1)[-1]
    pmcid = (pmc if pmc.upper().startswith("PMC") else f"PMC{pmc}") if pmc else None
    doi = norm_doi(r.get("doi"))
    title = r.get("title") or r.get("display_name")
    source = (r.get("primary_location") or {}).get("source") or {}
    return Work(
        provider="openalex",
        provider_id=r.get("id"),
        title=_clean(title),
        authors=[
            a["author"]["display_name"]
            for a in r.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        year=r.get("publication_year"),
        doi=doi,
        arxiv_id=arxiv_from_doi(doi),
        pmid=pmid,
        pmcid=pmcid,
        venue=source.get("display_name"),
        url=redact(r["id"]) if r.get("id") else None,
        abstract=_abstract(r.get("abstract_inverted_index")),
        is_retracted=bool(r.get("is_retracted")) or _retracted_title(title),
        oa_pdf_url=(r.get("best_oa_location") or {}).get("pdf_url"),
    )


async def openalex(client: httpx.AsyncClient, gate: Gate, ref: Reference) -> list[Work]:
    mail = get_settings().contact_email
    params = {"mailto": mail} if mail else {}
    if doi := norm_doi(ref.doi):
        resp = await _get(
            client, gate, f"{OPENALEX}/works/doi:{quote(doi, safe='/')}", params=params
        )
        if resp:
            return [parse_openalex(resp.json())]
    if not ref.title:
        return []
    # OpenAlex search treats some punctuation as syntax; words are enough
    params |= {"search": " ".join(re.findall(r"\w+", ref.title)), "per_page": 5}
    resp = await _get(client, gate, f"{OPENALEX}/works", params=params)
    return [parse_openalex(r) for r in resp.json()["results"]] if resp else []


# ---------- Semantic Scholar ----------


def parse_s2(p: dict) -> Work:
    ext = p.get("externalIds") or {}
    pmc = str(ext.get("PubMedCentral") or "")
    doi = norm_doi(ext.get("DOI"))
    return Work(
        provider="semanticscholar",
        provider_id=p.get("paperId"),
        title=_clean(p.get("title")),
        authors=[a["name"] for a in p.get("authors") or [] if a.get("name")],
        year=p.get("year"),
        doi=doi,
        arxiv_id=norm_arxiv(ext.get("ArXiv")) or arxiv_from_doi(doi),
        pmid=str(ext["PubMed"]) if ext.get("PubMed") else None,
        pmcid=(pmc if pmc.upper().startswith("PMC") else f"PMC{pmc}") if pmc else None,
        venue=p.get("venue") or None,
        url=p.get("url"),
        abstract=_clean(p.get("abstract")),
        is_retracted=_retracted_title(p.get("title")),
        oa_pdf_url=(p.get("openAccessPdf") or {}).get("url") or None,
    )


async def semanticscholar(client: httpx.AsyncClient, gate: Gate, ref: Reference) -> list[Work]:
    key = get_settings().semantic_scholar_api_key.get_secret_value()
    kw = {"params": {"fields": S2_FIELDS}, "headers": {"x-api-key": key} if key else {}}
    ids = []
    if doi := norm_doi(ref.doi):
        ids.append(f"DOI:{doi}")
    if aid := norm_arxiv(ref.arxiv_id):
        ids.append(f"ARXIV:{aid}")
    for pid in ids:
        resp = await _get(client, gate, f"{S2}/paper/{quote(pid, safe='/:')}", **kw)
        if resp:
            return [parse_s2(resp.json())]
    if not ref.title:
        return []
    kw["params"] = kw["params"] | {"query": ref.title}
    resp = await _get(client, gate, f"{S2}/paper/search/match", **kw)  # 404 = no match
    return [parse_s2(p) for p in resp.json().get("data") or []] if resp else []


# ---------- arXiv (batched: one request for many ids, ≤ 1 request / 3 s) ----------

_ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def parse_arxiv(xml: str) -> list[Work]:
    out = []
    for e in ET.fromstring(xml).findall("a:entry", _ATOM):
        eid = e.findtext("a:id", "", _ATOM)
        if "/abs/" not in eid:  # error entries have an /api/errors id
            raise ProviderError("arXiv error entry")
        aid = norm_arxiv(eid.split("/abs/", 1)[1])
        published = e.findtext("a:published", "", _ATOM)
        out.append(
            Work(
                provider="arxiv",
                provider_id=aid,
                title=_clean(e.findtext("a:title", None, _ATOM)),
                authors=[n.text.strip() for n in e.findall("a:author/a:name", _ATOM) if n.text],
                year=int(published[:4]) if published[:4].isdigit() else None,
                doi=norm_doi(e.findtext("x:doi", None, _ATOM)),
                arxiv_id=aid,
                venue=_clean(e.findtext("x:journal_ref", None, _ATOM)),
                url=f"https://arxiv.org/abs/{aid}",
                abstract=_clean(e.findtext("a:summary", None, _ATOM)),
                oa_pdf_url=f"https://arxiv.org/pdf/{aid}",
            )
        )
    return out


async def arxiv_batch(
    client: httpx.AsyncClient, gate: Gate, ids: list[str]
) -> dict[str, list[Work] | Exception]:
    """Look up many arXiv ids. Result per id: [Work] / [] (no record) / the exception."""
    out: dict[str, list[Work] | Exception] = {}
    ids = sorted({i for i in ids if ARXIV_ID.match(i)})
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        params = {"id_list": ",".join(chunk), "max_results": len(chunk)}
        try:
            resp = await _get(client, gate, ARXIV, params=params)
            if resp is None:
                raise ProviderError("HTTP 404")
            found = {w.arxiv_id: w for w in parse_arxiv(resp.text)}
        except Exception as e:  # noqa: BLE001 - a failed chunk marks its ids as failed
            out.update(dict.fromkeys(chunk, e))
        else:
            out.update({i: [found[i]] if i in found else [] for i in chunk})
    return out
