"""Adversarial: preprint vs journal version and arXiv DOIs."""

import asyncio
from pathlib import Path

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.core.settings import get_settings

FIX = Path(__file__).parent.parent / "fixtures" / "refaudit"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CONTACT_EMAIL", "")
    get_settings.cache_clear()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    yield
    get_settings.cache_clear()


def test_journal_year_with_arxiv_id_is_not_a_year_mismatch():
    t = "Sequential Monte Carlo samplers for Bayesian model selection"
    ref = Reference(id="1", raw="", title=t, year=2017, authors=["Smith J"], arxiv_id="1405.01234")
    preprint = Work(
        provider="arxiv", title=t, year=2014, authors=["John Smith"], arxiv_id=ref.arxiv_id
    )
    journal = Work(provider="crossref", title=t, year=2017, authors=["Smith, John"], doi="10.1/j")
    ok = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex", "arxiv")]
    a = refaudit.decide(ref, [preprint, journal], ok)
    assert a.status == RefStatus.verified  # today: metadata_mismatch "year 2017 vs 2014"


async def test_arxiv_doi_is_looked_up_on_arxiv():
    ref = Reference(
        id="v",
        raw="",
        title="Attention Is All You Need",
        year=2017,
        authors=["Vaswani A"],
        doi="10.48550/arXiv.1706.03762",
    )

    def empty(req):
        if "/works/" in req.url.path:
            return httpx.Response(404)
        key = "results" if req.url.host == "api.openalex.org" else "message"
        return httpx.Response(200, json={key: [] if key == "results" else {"items": []}})

    routes = {
        "api.crossref.org": empty,
        "api.openalex.org": empty,
        "export.arxiv.org": lambda r: httpx.Response(
            200, text=(FIX / "arxiv_query.xml").read_text()
        ),
    }
    transport = httpx.MockTransport(lambda req: routes[req.url.host](req))
    async with httpx.AsyncClient(transport=transport) as client:
        [a] = await refaudit.audit_references([ref], client)
    assert a.status == RefStatus.verified  # today: not_found, arXiv never queried
