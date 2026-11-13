"""Adversarial: refaudit._openalex lookup order + core.http.OpenAlexTransport cache/budget.
A search that failed must never become `not_found`, a failure must never be cached as an answer,
and a paid search must not be spent when a free DOI lookup identifies the work."""

import asyncio

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core import http
from claimtrace.core.schemas import Reference, RefStatus
from claimtrace.core.settings import get_settings

CR_EMPTY = {"message": {"items": []}}
TITLE = "Kinetics of protein folding in crowded cellular environments"
REF = Reference(id="1", raw="", title=TITLE, year=2020, authors=["Smith, J."])
OA_WORK = {"id": "https://openalex.org/W1", "title": TITLE, "publication_year": 2020}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CONTACT_EMAIL", "")
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(http, "_run_searches", 0)
    get_settings.cache_clear()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    yield
    get_settings.cache_clear()


async def audit(ref, handler, seen=None):
    def h(req):
        if seen is not None:
            seen.append(req.url.path + ("?search" if "search" in req.url.params else ""))
        return handler(req)

    # the production transport stack: disk cache + budget around the network
    transport = http.OpenAlexTransport(httpx.MockTransport(h))
    async with httpx.AsyncClient(transport=transport) as client:
        [a] = await refaudit.audit_references([ref], client)
    return a


@pytest.mark.parametrize("broken", ["api.openalex.org", "api.crossref.org"])
async def test_search_endpoint_404_is_a_failure_not_not_found(broken):
    # a 404 on /works?search=... (moved endpoint, proxy/CDN error page) is not an answer about
    # this reference; only an id lookup's 404 means "no such record"
    def handler(req):
        if req.url.host == broken:
            return httpx.Response(404, text="Not Found")
        if req.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json=CR_EMPTY)

    a = await audit(REF, handler)
    assert a.status == RefStatus.unchecked


async def test_openalex_search_404_is_not_cached():
    def down(req):
        if req.url.host == "api.openalex.org":
            return httpx.Response(404, text="Not Found")
        return httpx.Response(200, json=CR_EMPTY)

    def up(req):
        if req.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": [OA_WORK]})
        return httpx.Response(200, json=CR_EMPTY)

    await audit(REF, down)  # one bad answer from OpenAlex
    seen = []
    a = await audit(REF, up, seen)  # OpenAlex is fine again and has the paper
    assert "/works?search" in seen
    assert a.status == RefStatus.verified


async def test_cited_arxiv_id_is_looked_up_free_before_any_paid_search():
    # journal DOI has a typo (unknown everywhere); the cited arXiv id is right and OpenAlex
    # knows it under its free DataCite DOI. arXiv itself is down, Crossref has no preprints.
    ref = Reference(
        id="v",
        raw="",
        title="Attention is all you need",
        year=2017,
        doi="10.9999/typo.123",
        arxiv_id="1706.03762",
    )
    oa_preprint = {
        "id": "https://openalex.org/W2",
        "doi": "https://doi.org/10.48550/arxiv.1706.03762",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
    }

    def handler(req):
        if req.url.host == "api.openalex.org":
            if req.url.path == "/works/doi:10.48550/arxiv.1706.03762":
                return httpx.Response(200, json=oa_preprint)
            if req.url.path.startswith("/works/"):
                return httpx.Response(404)
            return httpx.Response(200, json={"results": []})
        if req.url.host == "export.arxiv.org":
            return httpx.Response(503)
        if req.url.path.startswith("/works/"):
            return httpx.Response(404)
        return httpx.Response(200, json=CR_EMPTY)

    seen = []
    a = await audit(ref, handler, seen)
    assert "/works?search" not in seen  # $0.001 spent where a free lookup identifies the work
    assert a.status != RefStatus.unchecked and a.matched and a.matched.arxiv_id == "1706.03762"
