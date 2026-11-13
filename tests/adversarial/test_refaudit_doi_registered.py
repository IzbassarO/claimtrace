"""A DOI that doi.org knows is evidence the cited work exists; it is not evidence it is right."""

import asyncio

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.core.settings import get_settings


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


@pytest.mark.xfail(
    strict=True,
    reason="BUG: registered DataCite DOI, no title hit -> not_found; doi.org never asked",
)
async def test_registered_doi_without_title_hit_is_not_not_found():
    # Zenodo software release: DataCite DOI, not in Crossref or OpenAlex, title search finds nothing
    ref = Reference(
        id="1",
        raw="Lee K. FooTool: a toolkit for protein folding (v1.2). Zenodo; 2021. "
        "doi:10.5281/zenodo.1234567",
        title="FooTool: a toolkit for protein folding",
        authors=["Lee, K."],
        year=2021,
        doi="10.5281/zenodo.1234567",
    )

    def handler(req):
        if req.url.host == "doi.org":  # the handle exists
            return httpx.Response(200, json={"responseCode": 1, "handle": "10.5281/zenodo.1234567"})
        if "/works/" in req.url.path:
            return httpx.Response(404)
        if req.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"message": {"items": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        [a] = await refaudit.audit_references([ref], client)
    # today: not_found "no matching record in crossref, openalex" for a DOI that resolves
    assert a.status != RefStatus.not_found


@pytest.mark.xfail(strict=True, reason="BUG: registered DOI never compared, still verified")
def test_registered_but_uncompared_doi_is_not_verified():
    # the cited arXiv paper is real, but the cited DOI is some other Zenodo deposit; doi.org only
    # says the handle exists, nothing compares it to the paper
    t = "Sparse attention for long protein sequences"
    ref = Reference(
        id="1",
        raw="",
        title=t,
        authors=["Park, J."],
        year=2023,
        arxiv_id="2301.01234",
        doi="10.5281/zenodo.7777777",
    )
    pre = Work(provider="arxiv", title=t, authors=["Jin Park"], year=2023, arxiv_id="2301.01234")
    outcomes = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex", "arxiv")] + [
        ProviderOutcome(provider="doi.org", ok=True, candidates=1)
    ]
    a = refaudit.decide(ref, [pre], outcomes)
    # today: verified, note "DOI registered (not in Crossref/OpenAlex), not compared"
    assert a.status != RefStatus.verified
